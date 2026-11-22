#!/usr/bin/env python3
# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0
"""Render the guarded grants that let the manual deploy identity upgrade monitoring, OpenSearch and the OTel demo.

The `demo-apply` credential lives in a GitHub environment. With Kubernetes RBAC alone, the
rights a monitoring upgrade needs (escalate and bind on chart roles, hook role and binding
creation, patches to workloads that run under powerful service accounts) would let it make
itself cluster-admin. Every write it can make is therefore fenced by ValidatingAdmissionPolicies
that match only its user name:

* each write needs the shared deployment lock held by a manual run (`manual/<run>/<attempt>`);
* each object that carries privilege must keep its current security surface or take the one
  in this commit's reviewed, server-defaulted render: Role and ClusterRole rules, binding
  subjects and role references, workload pod specs and admission webhooks;
* the twelve monitoring hooks must match the reviewed hooks exactly, as for the executor;
* Secret writes must be canonical Helm history records of the four releases, apart from the
  three chart-owned monitoring Secrets.

The operator renders the package from a clean checkout of the reviewed commit, reviews it,
and applies the guards before the grants. See docs/manual-upgrade-access.md.
"""

import argparse
import base64
from collections import defaultdict
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zlib

import monitoring_hooks as hooks
import permissions as r

import yaml

import access_matrix
import scheduled_adapter as adapter
import scheduled_inputs

ROOT = r.ROOT
PREFIX = "jaeger-demo-manual"
ALL_COMPONENTS = ("oci-monitoring", "opensearch", "otel-demo")
COMPONENT_RELEASES = {"oci-monitoring": ("oci-monitoring",), "opensearch": ("opensearch", "opensearch-dashboards"),
                      "otel-demo": ("otel-demo",)}
COMPONENT_NAMESPACES = {"oci-monitoring": "default", "opensearch": "opensearch", "otel-demo": "otel-demo"}
# The components the package covers; selected() narrows them for one upgrade.
COMPONENTS = ALL_COMPONENTS
RELEASE_KEYS = ("oci-monitoring", "opensearch", "opensearch-dashboards", "otel-demo")
NAMESPACES = ("default", "opensearch", "otel-demo")
# The Secret policy matches every Secret write by the user, including its Stage 1 Jaeger history writes,
# so it admits the canonical records of all six releases.
HISTORY = r.HISTORY
ARCHIVE = "jaeger-demo-manual-monitoring-crds"
HOLDER_PATTERN = "^manual/[0-9]+/[0-9]+$"
RBAC_GROUP = "rbac.authorization.k8s.io"
REQUIRED = object()
WEBHOOKS = {"MutatingWebhookConfiguration": ("admissionregistration.k8s.io", "mutatingwebhookconfigurations"),
            "ValidatingWebhookConfiguration": ("admissionregistration.k8s.io", "validatingwebhookconfigurations")}
# The privilege-bearing fields each pinned kind must keep or take from the reviewed render.
SURFACE = {
    "Role": [(("rules",), []), (("aggregationRule",), None)],
    # Any ClusterRole label can be selected by an aggregationRule (aggregate-to-view, -edit, -admin), which
    # copies this role's rules into the selecting role.
    "ClusterRole": [(("rules",), []), (("aggregationRule",), None), (("metadata", "labels"), {})],
    "RoleBinding": [(("roleRef",), REQUIRED), (("subjects",), [])],
    "ClusterRoleBinding": [(("roleRef",), REQUIRED), (("subjects",), [])],
    "Deployment": [(("spec", "template", "spec"), REQUIRED)],
    "DaemonSet": [(("spec", "template", "spec"), REQUIRED)],
    "StatefulSet": [(("spec", "template", "spec"), REQUIRED)],
    # The operator turns these specs into StatefulSets: containers, volumes and service account included.
    "Prometheus": [(("spec",), REQUIRED)],
    "Alertmanager": [(("spec",), REQUIRED)],
    # externalIPs can intercept a cluster IP's traffic (CVE-2020-8554); type and externalName change exposure and routing.
    "Service": [(("spec", "type"), "ClusterIP"), (("spec", "externalIPs"), []), (("spec", "externalName"), None)],
}
# StatefulSet storage identity never changes through this identity, whatever the pod spec does: a retention
# policy of Delete with fewer replicas lets the controller delete data PVCs (scheduled_adapter.validate_change).
STATEFUL_STORAGE = [(("spec", "volumeClaimTemplates"), []), (("spec", "persistentVolumeClaimRetentionPolicy"), None),
                    (("spec", "selector"), REQUIRED), (("spec", "serviceName"), None), (("spec", "replicas"), 1)]
OPERATOR_KINDS = {"Prometheus": ("monitoring.coreos.com", "prometheuses"), "Alertmanager": ("monitoring.coreos.com", "alertmanagers")}
KINDS = {**{kind: gvr for gvr, kind in r.GVR.items() if kind in SURFACE}, **OPERATOR_KINDS, **WEBHOOKS}
PINNED = {gvr: kind for kind, gvr in KINDS.items()}
MAX_EXPRESSION = 48000
# An upgrade that keeps every object needs no create or delete beyond hooks and history.
NO_CHANGES = {"added": [], "removed": [], "retained": []}


@contextlib.contextmanager
def selected(components):
    """Render for some components only, so an upgrade of one chart grants nothing on the others."""
    global COMPONENTS, RELEASE_KEYS, NAMESPACES
    chosen = tuple(c for c in ALL_COMPONENTS if c in set(components))
    r.require(chosen and len(chosen) == len(set(components)), "Unknown or empty component selection")
    saved = COMPONENTS, RELEASE_KEYS, NAMESPACES
    COMPONENTS = chosen
    RELEASE_KEYS = tuple(k for c in chosen for k in COMPONENT_RELEASES[c])
    NAMESPACES = tuple(COMPONENT_NAMESPACES[c] for c in chosen)
    try:
        yield
    finally:
        COMPONENTS, RELEASE_KEYS, NAMESPACES = saved


def subjects_for(user):
    r.require(isinstance(user, str) and 0 < len(user) <= 256 and user.isprintable() and user.strip() == user
              and not user.startswith("system:"), "Expected the manual deploy identity's Kubernetes user name")
    return [{"kind": "User", "apiGroup": RBAC_GROUP, "name": user}]


# The API server resolves a binding's parameter before its match conditions. Without the checkpoint every
# binding denies matching writes by every client, so the checkpoint itself stays outside them all to keep
# it recoverable. Recreating it grants nothing beyond taking the lock.
CHECKPOINT_RULE = {"apiGroups": [""], "apiVersions": ["v1"], "operations": ["*"], "resources": ["configmaps"],
                   "resourceNames": [r.CHECKPOINT]}


def policy_builder(user):
    """A permissions.policies replacement: matches only the manual user and requires the manual lock first."""
    holder = json.dumps(r.HOLDER)
    lock = {"expression": f"has(params.metadata.annotations) && {holder} in params.metadata.annotations && "
                          f"params.metadata.annotations[{holder}].matches({json.dumps(HOLDER_PATTERN)})",
            "message": "A manual deployment lock holder is required"}

    def policies(name, group, target, operations, expression, message, *, condition=None, variables=None, running=True):
        # `running` is the executor's daily-request gate. A manual run has no request; the lock replaces it.
        conditions = [{"name": "manual-deployer", "expression": "request.userInfo.username == " + json.dumps(user)}]
        if condition:
            conditions.append({"name": "reviewed-target", "expression": condition})
        spec = {"failurePolicy": "Fail", "paramKind": {"apiVersion": "v1", "kind": "ConfigMap"},
                "matchConstraints": {"resourceRules": [{"apiGroups": [group], "apiVersions": ["v1"],
                                     "operations": operations, "resources": [target]}]},
                "matchConditions": conditions,
                "validations": [dict(lock), {"expression": expression, "message": message}]}
        if variables:
            spec["variables"] = variables
        return [r.resource(r.ADMISSION, "ValidatingAdmissionPolicy", name, spec=spec),
                r.resource(r.ADMISSION, "ValidatingAdmissionPolicyBinding", name, spec={
                    "policyName": name, "validationActions": ["Deny"],
                    "paramRef": {"namespace": "default", "name": r.CHECKPOINT, "parameterNotFoundAction": "Deny"},
                    "matchResources": {"excludeResourceRules": [CHECKPOINT_RULE]}})]
    return policies


def records():
    """Reviewed matrix rows of the three components: (scope, group, resource, name) -> row."""
    rows = {}
    for row in r.inventory():
        if row["component"] in COMPONENTS:
            rows[row["scope"], row["apiGroup"], row["resource"], row["name"]] = row
    return rows


def current(changes=NO_CHANGES):
    """The reviewed records this commit's charts render. The matrix keeps records of dropped objects while a
    rollback target needs them (access_matrix.py); those get no grant, pin or lock gate here."""
    retained = {tuple(k) for k in changes.get("retained", ())}
    return {key: row for key, row in records().items() if key not in retained}


def ident(obj, default_namespace=None):
    group, target = KINDS[obj["kind"]]
    scope = "cluster" if obj["kind"].startswith("Cluster") or obj["kind"] in WEBHOOKS else obj["metadata"].get("namespace", default_namespace)
    return scope, group, target, obj["metadata"]["name"]


def at(obj, path, default=REQUIRED):
    for key in path:
        if not isinstance(obj, (dict, list)) or (isinstance(obj, dict) and key not in obj) or (isinstance(obj, list) and key >= len(obj)):
            r.require(default is not REQUIRED, "Reviewed render lacks " + "/".join(map(str, path)))
            return default
        obj = obj[key]
    return default if obj is None and default is not REQUIRED else obj


def cel(root, path):
    return "dyn(" + root + ")" + "".join("[" + json.dumps(p) + "]" for p in path)


def value(root, path, default):
    if default is REQUIRED:
        return "dyn(" + cel(root, path) + ")"
    # Fields without omitempty, such as Role.rules, reach admission absent, empty or null depending on the writer.
    return (f"({json.dumps(path[-1])} in {cel(root, path[:-1])} && {cel(root, path)} != null"
            f" ? dyn({cel(root, path)}) : dyn({r.literal(default)}))")


def webhook_checks(count, reference):
    parts = [f'"webhooks" in dyn(object) && size(dyn(object)["webhooks"]) == {count}']
    for i in range(count):
        parts.append(r.same_except(cel("object", ("webhooks", i)), reference(("webhooks", i)), ["clientConfig"]))
        parts.append(r.same_except(cel("object", ("webhooks", i, "clientConfig")), reference(("webhooks", i, "clientConfig")), ["caBundle"]))
    return "(" + " && ".join(parts) + ")"


def surface(obj):
    """The pinned field values of an object, for comparing two reviewed forms."""
    if obj["kind"] in WEBHOOKS:
        return json.dumps(obj.get("webhooks") or [], sort_keys=True)
    return json.dumps([at(obj, p, d) for p, d in SURFACE[obj["kind"]]], sort_keys=True)


def take_expression(reviewed):
    if reviewed["kind"] in WEBHOOKS:
        return webhook_checks(len(reviewed.get("webhooks") or []), lambda p: r.literal(at(reviewed, p)))
    return " && ".join(f"{value('object', p, d)} == dyn({r.literal(at(reviewed, p, d))})" for p, d in SURFACE[reviewed["kind"]])


def surface_expression(reviewed, *, created=False, applied=None):
    """CEL: the update keeps the current security surface, or takes exactly the reviewed one. An object
    this commit adds (`created`) must also be created with exactly the reviewed surface. `applied` is the same
    render server-side applied onto the live object: Helm 4 merges into the live object for a release that uses
    server-side apply, so the result can keep fields other managers own or a different list order."""
    kind = reviewed["kind"]
    if kind in WEBHOOKS:
        count = len(reviewed.get("webhooks") or [])
        keep = f'"webhooks" in dyn(oldObject) && size(dyn(oldObject)["webhooks"]) == {count} && ' + webhook_checks(count, lambda p: cel("oldObject", p))
    else:
        keep = " && ".join(f"{value('object', p, d)} == {value('oldObject', p, d)}" for p, d in SURFACE[kind])
    take = take_expression(reviewed)
    if applied is not None and surface(applied) != surface(reviewed):
        take = "(" + take + ") || (" + take_expression(applied) + ")"
    expression = "(" + keep + ") || (" + take + ")"
    if kind == "StatefulSet":
        storage = " && ".join(f"{value('object', p, d)} == {value('oldObject', p, d)}" for p, d in STATEFUL_STORAGE)
        expression = "(" + storage + ") && (" + expression + ")"
    if created:
        fresh = "(" + take + ")"
        if kind == "StatefulSet":
            fresh += " && " + " && ".join(f"{value('object', p, d)} == dyn({r.literal(at(reviewed, p, d))})" for p, d in STATEFUL_STORAGE)
        expression = 'request.operation == "CREATE" ? (' + fresh + ") : (" + expression + ")"
    r.require(len(expression.encode()) <= MAX_EXPRESSION, f"{kind}/{reviewed['metadata']['name']} is too large for a native content policy")
    return expression


def pins(defaulted, policies, added=(), rows=None, applied=None):
    """One UPDATE policy per pinned object, which also covers CREATE for an object this commit adds."""
    rows = records() if rows is None else rows
    expected = {key for key, row in rows.items() if (key[1], key[2]) in PINNED and not row.get("hookEvents")}
    seen = set()
    guards = []
    for obj in defaulted:
        key = ident(obj)
        r.require(key in expected and key not in seen, "Rendered privileged object outside the reviewed access matrix: " + "/".join(key))
        seen.add(key)
        scope, group, target, name = key
        condition = r.REQUEST_NS + " == " + json.dumps("" if scope == "cluster" else scope) + " && request.name == " + json.dumps(name)
        tag = PREFIX + "-pin-" + hashlib.sha256("/".join(key).encode()).hexdigest()[:16]
        created = key in added
        guards += policies(tag, group, target, ["CREATE", "UPDATE"] if created else ["UPDATE"],
                           surface_expression(obj, created=created, applied=(applied or {}).get(key)), PIN_DENIED,
                           condition=condition)
    missing = sorted(expected - seen)
    r.require(not missing, "Reviewed privileged objects missing from the render: " + ", ".join("/".join(k) for k in missing))
    return guards


def ordinary(rows):
    """Named non-hook rows the user may patch: (scope, group, resource) -> names."""
    named = defaultdict(set)
    for (scope, group, target, name), row in rows.items():
        if not row.get("hookEvents") and "patch" in row["verbs"]:
            named[scope, group, target].add(name)
    return named


def lock_gates(rows, policies):
    """Every ordinary named patch needs the manual lock, even where no field is pinned."""
    guards = []
    for (scope, group, target), names in sorted(ordinary(rows).items()):
        prefix = "" if scope == "cluster" else scope
        condition = r.REQUEST_NS + ' + "/" + request.name in ' + json.dumps(sorted(prefix + "/" + n for n in names))
        name = PREFIX + "-lock-" + hashlib.sha256(f"{scope}/{group}/{target}".encode()).hexdigest()[:16]
        guards += policies(name, group, target, ["UPDATE"], "true", LOCK_DENIED, condition=condition)
    return guards


def chart_secrets(changes=NO_CHANGES):
    """The chart-owned monitoring Secrets whose in-place update skips the history check. Only those this commit
    still renders qualify: their named lock gate is what guards the update. A dropped or rollback-only one keeps
    the history check, so a leftover cannot change without the lock."""
    rows = current(changes)
    return [n for n in r.MONITORING_SECRETS if "patch" in rows.get(("default", "", "secrets", n), {}).get("verbs", ())]


def history(policies, changes=NO_CHANGES):
    expression = r.history_expression(HISTORY)
    # A chart Secret this commit adds may be created, and one it drops deleted, inside this lock-bearing
    # policy. Every other write to them, an update included, must still be a canonical history record.
    for operation, keys in (("DELETE", changes["removed"]), ("CREATE", changes["added"])):
        names = sorted(k[0] + "/" + k[3] for k in keys if k[1:3] == ("", "secrets"))
        if names:
            expression = (f'(request.operation == "{operation}" && (request.namespace + "/" + request.name) in '
                          + json.dumps(names) + ") || (" + expression + ")")
    guards = policies(PREFIX + "-history", "", "secrets", ["CREATE", "UPDATE", "DELETE"], expression,
                      HISTORY_DENIED,
                      condition=r.CHART_SECRET_UPDATE + json.dumps(chart_secrets(changes)) + ")",
                      variables=[{"name": "records", "expression": r.HISTORY_RECORDS}])
    # The three chart-owned monitoring Secrets may change with the chart (documented residual risk);
    # the lock gate on their named patch still applies.
    return guards


def by_target(keys):
    grouped = defaultdict(set)
    for scope, group, target, name in keys:
        grouped[scope, group, target].add(name)
    return grouped


def release_changes(deployed, rendered=None):
    """Objects of the releases' deployed manifests (hooks excluded) against this commit's render: what the
    upgrade creates, what Helm deletes, and the reviewed records kept only as rollback targets. Supplements are
    applied outside Helm and never counted. Without `rendered`, every reviewed record counts as rendered."""
    rows = records()
    reviewed = {k for k, row in rows.items() if not row.get("hookEvents") and row["release"] != "supplement"}
    rendered = reviewed if rendered is None else {tuple(k) for k in rendered}
    unreviewed = sorted(rendered - reviewed)
    r.require(not unreviewed, "Rendered objects without a reviewed record: " + ", ".join("/".join(k) for k in unreviewed))
    deployed = {tuple(k) for k in deployed}
    kinds = {(k[1], k[2]) for k in rows}
    removed = deployed - rendered
    for key in sorted(removed):
        r.require((key[1], key[2]) in kinds and key[0] in NAMESPACES + ("kube-system", "cluster"),
                  "Removing this deployed object needs an operator: " + "/".join(key))
    return {"added": sorted(rendered - deployed), "removed": sorted(removed), "retained": sorted(reviewed - rendered)}


def change_gates(changes, policies, hook_names):
    """CREATE only for the names this commit adds (and the hooks of the same kind); DELETE of dropped objects
    under the lock. Privileged added objects are also pinned on CREATE by pins()."""
    guards = []
    for (scope, group, target), names in sorted(create_targets(changes, hook_names).items()):
        allowed = sorted(names)
        where = r.REQUEST_NS + " == " + json.dumps("" if scope == "cluster" else scope)
        tag = PREFIX + "-create-" + hashlib.sha256(f"{scope}/{group}/{target}".encode()).hexdigest()[:16]
        guards += policies(tag, group, target, ["CREATE"], "request.name in " + json.dumps(allowed), CREATE_DENIED, condition=where)
    for (scope, group, target), names in sorted(by_target(changes["removed"]).items()):
        prefix = "" if scope == "cluster" else scope
        condition = r.REQUEST_NS + ' + "/" + request.name in ' + json.dumps(sorted(prefix + "/" + n for n in names))
        tag = PREFIX + "-retire-" + hashlib.sha256(f"{scope}/{group}/{target}".encode()).hexdigest()[:16]
        guards += policies(tag, group, target, ["DELETE"], "true", LOCK_DENIED, condition=condition)
    return guards


def create_targets(changes, hook_names):
    """Added objects that need their own create gate. Where the hook inventory or the history policy already
    matches the same creates (they admit the added names as exempt), a second gate would make the denial
    message ambiguous, since the API server reports one denying policy."""
    gated = {}
    for (scope, group, target), names in by_target(changes["added"]).items():
        if (scope, group, target) in hook_names or ((group, target) == ("", "secrets") and scope in NAMESPACES):
            continue
        gated[scope, group, target] = names
    return gated


def grants(defaulted, subjects, changes=NO_CHANGES):
    rows = current(changes)
    rules = defaultdict(list)
    for (scope, group, target), names in sorted(ordinary(rows).items()):
        rules[scope].append(r.rule(group, target, ["get", "patch"], names))
        if target in ("roles", "clusterroles"):
            # Patching a role's rules needs escalate unless the caller already holds them. The pins bound it.
            rules[scope].append(r.rule(group, target, ["escalate"], names))
    bind = defaultdict(set)
    for obj in defaulted:
        if obj["kind"] in ("RoleBinding", "ClusterRoleBinding"):
            ref = obj["roleRef"]
            # Kubernetes checks bind in the binding's own namespace, so a RoleBinding to a ClusterRole needs
            # bind there only. Cluster scope would let any other binding grant, such as one Stage 1 can
            # create in jaeger, reference the ClusterRole.
            scope = ident(obj)[0]
            bind[scope, "clusterroles" if ref["kind"] == "ClusterRole" else "roles"].add(ref["name"])
    for (scope, target), names in sorted(bind.items()):
        rules[scope].append(r.rule(RBAC_GROUP, target, ["bind"], names))
    reads = defaultdict(set)
    for scope, group, target, _ in rows:
        reads[scope].add((group, target))
    for ns in NAMESPACES:
        reads[ns].update({("", "pods"), ("apps", "replicasets"), ("", "secrets")})
    for scope, kinds in sorted(reads.items()):
        rules[scope] += [r.rule(group, target, ["get", "list", "watch"]) for group, target in sorted(kinds)]
    for ns in NAMESPACES:
        rules[ns].append(r.rule("", "secrets", ["create", "delete", "update"]))
    for (scope, group, target), names in sorted(by_target(changes["added"]).items()):
        # RBAC cannot name a created object; the create gate and the pins limit it. A server-side apply that
        # creates is authorized as a named patch as well as a create.
        rules[scope].append(r.rule(group, target, ["create"]))
        rules[scope].append(r.rule(group, target, ["patch"], names))
        if target in ("roles", "clusterroles"):
            # Escalation for a CREATE is checked against the collection, which has no resource name.
            rules[scope].append(r.rule(group, target, ["escalate"], [""]))
    for (scope, group, target), names in sorted(by_target(changes["removed"]).items()):
        rules[scope].append(r.rule(group, target, ["delete", "get"], names))
    return [item for scope, scoped in sorted(rules.items()) for item in r.bindings(PREFIX + "-upgrade", scope, scoped, (), subjects=subjects)]


def package(user, defaulted, defaulted_hooks, changes=NO_CHANGES, applied=None):
    """Return (guards, grants) for already server-defaulted pinned objects and hooks, and the objects the
    upgrade adds and removes (release_changes)."""
    r.require("oci-monitoring" in COMPONENTS or not defaulted_hooks, "Monitoring hooks outside a monitoring package")
    subjects = subjects_for(user)
    policies = policy_builder(user)
    hook_names = hook_targets(defaulted_hooks)
    rows = current(changes)
    guards = (pins(defaulted, policies, set(map(tuple, changes["added"])), rows, applied) + lock_gates(rows, policies)
              + history(policies, changes) + change_gates(changes, policies, hook_names))
    hook_guards, hook_grants = [], []
    if "oci-monitoring" in COMPONENTS:
        hook_guards, hook_grants = hooks.controls(defaulted_hooks, policies=policies, lock=None, archive_name=ARCHIVE, prefix=PREFIX,
                                                  subjects=subjects, account_guards=False, scoped=True,
                                                  exempt_create=by_target(changes["added"]), exempt_delete=by_target(changes["removed"]))
    return guards + hook_guards, grants(defaulted, subjects, changes) + hook_grants


def hook_targets(defaulted_hooks):
    grouped = defaultdict(set)
    for hook in defaulted_hooks:
        group, target = hooks.KINDS[hook["kind"]]
        grouped[hook["metadata"].get("namespace", "cluster"), group, target].add(hook["metadata"]["name"])
    return grouped


def review_copy(obj, namespace):
    """A renamed copy: the real names exist, and no pinned field contains the object's own name."""
    copy_ = copy.deepcopy(obj)
    meta = copy_["metadata"]
    meta["name"] = "review-" + hashlib.sha256(meta["name"].encode()).hexdigest()[:12]
    for field in ("resourceVersion", "uid", "creationTimestamp", "managedFields"):
        meta.pop(field, None)
    if obj["kind"] in SURFACE and not obj["kind"].startswith("Cluster"):
        meta["namespace"] = meta.get("namespace", namespace)
    return copy_


def review_name(obj):
    return "review-" + hashlib.sha256((obj["kind"] + "/" + obj["metadata"]["name"]).encode()).hexdigest()[:12]


def restore_name(value, trial, original):
    """Put the original name back wherever server defaulting derived it from the trial name (Job labels)."""
    if isinstance(value, dict):
        return {k: restore_name(v, trial, original) for k, v in value.items()}
    if isinstance(value, list):
        return [restore_name(v, trial, original) for v in value]
    return original if value == trial else value


def dry_admitted_hooks(definitions, kube):
    """hooks.dry_admitted under renamed copies: a failed hook stays behind (no hook-failed delete policy)
    and would make an original-name dry-run fail with AlreadyExists, blocking the retry package."""
    result = []
    for hook in definitions:
        original = hook["metadata"]["name"]
        trial = copy.deepcopy(hook)
        trial["metadata"]["name"] = review_name(hook)
        admitted, = hooks.dry_admitted([trial], kube)
        result.append(restore_name(admitted, trial["metadata"]["name"], original))
    return result


def server_defaults(objects, kube):
    result = []
    for obj, namespace in objects:
        trial = review_copy(obj, namespace)
        completed = subprocess.run([*kube, "create", "--dry-run=server", "--field-manager=helm", "-f", "-", "-o", "json"],
                                   input=adapter.encode(trial), capture_output=True, timeout=30)
        if completed.returncode:
            raise RuntimeError("Server dry-run failed for " + obj["kind"] + "/" + obj["metadata"]["name"])
        admitted = json.loads(completed.stdout)
        r.require(admitted["kind"] == obj["kind"] and admitted["metadata"]["name"] == trial["metadata"]["name"], "Unexpected admitted object")
        # Charts may place objects outside the release namespace (kube-system Services), so keep the trial's namespace.
        r.require(admitted["metadata"].get("namespace") == trial["metadata"].get("namespace"), "Admitted object changed namespace")
        admitted["metadata"]["name"] = obj["metadata"]["name"]
        result.append(admitted)
    return result


HELM_MANAGER = "helm"
PREVIEW_HELPER = "jaeger-helm-preview"


def apply_manager(live):
    """Helm's field manager if Helm last server-side applied the object itself, else None. Other actors apply too,
    such as the operator on a resource's status subresource, and do not make the release server-side."""
    applied = {f["manager"] for f in live["metadata"].get("managedFields") or []
               if f.get("operation") == "Apply" and not f.get("subresource")}
    return HELM_MANAGER if HELM_MANAGER in applied else None


def release_methods(kube):
    """Each release's Helm apply method from its latest stored record, which `--server-side auto` reuses.
    Managed fields cannot tell: a release once applied server-side and later client-side keeps Helm's Apply entries."""
    methods = {}
    for key in RELEASE_KEYS:
        namespace, release = adapter.RELEASES[key][:2]
        found = json.loads(subprocess.run([*kube, "-n", namespace, "get", "secrets", "-l", f"owner=helm,name={release}", "-o", "json"],
                                          capture_output=True, timeout=60, check=True).stdout)["items"]
        r.require(found, "No Helm release record for " + release)
        latest = max(found, key=lambda item: int(item["metadata"]["labels"]["version"]))
        raw = base64.b64decode(base64.b64decode(latest["data"]["release"], validate=True), validate=True)
        if raw.startswith(b"\x1f\x8b"):
            # The manual user can write canonical history records; bound the decompression as helm_record does.
            decoder = zlib.decompressobj(31)
            raw = decoder.decompress(raw, adapter.LIMIT + 1)
            r.require(decoder.eof and not decoder.unused_data, "Invalid compressed Helm record")
        r.require(len(raw) <= adapter.LIMIT, "Oversized Helm record")
        method = json.loads(raw).get("apply_method", "") or "csa"
        r.require(method in ("csa", "ssa"), "Unknown Helm release apply method")
        methods[key] = method
    return methods


def helm_target(obj, namespace, release):
    """The object as Helm sends it: its namespace defaulted, with Helm's ownership labels and annotations."""
    target = copy.deepcopy(obj)
    meta = target["metadata"]
    if namespace:
        meta.setdefault("namespace", namespace)
    # Charts can render `labels: null` or `annotations: null`.
    for field in ("labels", "annotations"):
        if not meta.get(field):
            meta[field] = {}
    meta["labels"]["app.kubernetes.io/managed-by"] = "Helm"
    meta["annotations"].update({"meta.helm.sh/release-name": release, "meta.helm.sh/release-namespace": namespace})
    return target


def applied_forms(objects, defaulted, deployed, methods, kube, helper=PREVIEW_HELPER):
    """For each pinned object the release already has, the object Helm's upgrade of this render produces, as a
    nonpersistent server dry-run, the way the executor previews it (scheduled_approvals.preview). A server-side
    release is a dry-run apply under Helm's field manager. A client-side release is Helm 4.3.2's own patch, from
    jaeger-helm-preview (ops/executor/preview): a three-way strategic merge for built-in kinds and a two-way JSON
    merge for custom resources, applied to the live object."""
    result = {}
    for (obj, namespace), reviewed in zip(objects, defaulted):
        key = ident(reviewed)
        if key not in deployed:
            continue
        owner, prior = deployed[key]
        release_ns, release = adapter.RELEASES[owner][:2]
        cluster = key[0] == "cluster"
        where = [] if cluster else ["-n", key[0]]
        target = helm_target(obj, None if cluster else key[0], release)
        if cluster:
            target["metadata"]["annotations"]["meta.helm.sh/release-namespace"] = release_ns
        if methods[owner] == "ssa":
            command = [*kube, "apply", "--server-side", "--dry-run=server", "--force-conflicts=false", "--field-manager=" + HELM_MANAGER,
                       "-f", "-", "-o", "json"]
            completed = subprocess.run(command, input=adapter.encode(target), capture_output=True, timeout=30)
        else:
            original = copy.deepcopy(prior)
            if not cluster:
                original["metadata"].setdefault("namespace", key[0])
            live = subprocess.run([*kube, *where, "get", resource_arg(key[1], key[2]), key[3], "-o", "json"],
                                  capture_output=True, timeout=30, check=True).stdout
            preview = subprocess.run([helper], input=json.dumps({"original": original, "desired": target, "live": json.loads(live)}).encode(),
                                     capture_output=True, timeout=60)
            r.require(not preview.returncode, "jaeger-helm-preview failed for " + "/".join(key))
            patch = json.loads(preview.stdout)
            r.require(set(patch) == {"type", "patch"} and patch["type"] in ("strategic", "merge") and isinstance(patch["patch"], dict),
                      "Invalid offline Helm patch preview")
            if not patch["patch"]:
                result[key] = json.loads(live)
                continue
            completed = subprocess.run([*kube, *where, "patch", resource_arg(key[1], key[2]), key[3], "--type=" + patch["type"],
                                        "--patch-file=/dev/stdin", "--dry-run=server", "--field-manager=" + HELM_MANAGER, "-o", "json"],
                                       input=json.dumps(patch["patch"]).encode(), capture_output=True, timeout=30)
        if completed.returncode:
            raise RuntimeError("Upgrade preview failed for " + "/".join(key))
        admitted = json.loads(completed.stdout)
        r.require(ident(admitted) == key, "Unexpected previewed object")
        result[key] = admitted
    return result


def verified_archives(located):
    """A caller-supplied chart directory is untrusted: each rendered archive must match the reviewed checksum lock."""
    locks = json.loads((ROOT / scheduled_inputs.LOCK).read_bytes())["charts"]
    for key in RELEASE_KEYS:
        path = located[key]
        r.require(path.name in locks and path.is_file() and not path.is_symlink()
                  and hashlib.sha256(path.read_bytes()).hexdigest() == locks[path.name],
                  "Chart archive differs from the reviewed checksum: " + path.name)
    return located


def render_pinned(charts, scratch):
    """Render the four releases and supplements. Returns ([(pinned object, namespace)], {release key: archive},
    {identity: Helm-managed rendered object}); supplements are applied outside Helm and not in the last map."""
    kube_version = json.loads(r.MATRIX.read_bytes())["targetKubernetes"]
    env = {"PATH": os.environ["PATH"], "KUBECONFIG": os.devnull, "HOME": str(scratch)}
    located = verified_archives(access_matrix.packages(Path(charts), ROOT))
    pinned, rendered = [], {}
    for key in RELEASE_KEYS:
        namespace = adapter.RELEASES[key][0]
        supplements = adapter.supplemental_for(key, ROOT) if key in COMPONENTS else []
        charted = access_matrix.render(key, located[key], ROOT, kube_version, env)
        for obj in charted + supplements:
            if obj["kind"] in KINDS and not access_matrix.hook_events(obj):
                pinned.append((obj, namespace))
        for obj in charted:
            if not access_matrix.hook_events(obj):
                rendered[identity(key, obj, namespace)] = obj
    return pinned, located, rendered


def identity(key, obj, namespace):
    return tuple(access_matrix.row_key(key, adapter.identity(obj, namespace), False)[2:])


def deployed_objects(kubeconfig, context, scratch):
    """Each release's last successful manifest by identity, with the release key. After a failed upgrade Helm
    takes the original for its patch and its deletions from the last deployed or superseded revision, not the
    failed one (as scheduled_adapter.history does). `helm get manifest` leaves out hooks."""
    env = {"PATH": os.environ["PATH"], "HOME": str(scratch)}
    found = {}
    for key in RELEASE_KEYS:
        namespace, release = adapter.RELEASES[key][:2]
        where = ["--namespace", namespace, "--kubeconfig", kubeconfig, "--kube-context", context]
        rows = json.loads(subprocess.run(["helm", "history", release, *where, "--max", "256", "-o", "json"], env=env,
                                         capture_output=True, timeout=120, check=True).stdout)
        successful = [row["revision"] for row in rows if row["status"] in ("deployed", "superseded")]
        r.require(successful, "No successful revision of " + release + "; recover it before rendering")
        manifest = subprocess.run(["helm", "get", "manifest", release, *where, "--revision", str(max(successful))],
                                  env=env, capture_output=True, timeout=120, check=True).stdout
        found.update({identity(key, obj, namespace): (key, obj) for obj in yaml.safe_load_all(manifest) if obj})
    return found


PIN_DENIED = "Privileged fields differ from the reviewed render of this commit"
CREATE_DENIED = "Only objects this commit adds may be created"
LOCK_DENIED = "A manual deployment lock holder is required"
HOOK_DENIED = "Hook differs from the independently reviewed monitoring baseline"
INVENTORY_DENIED = "Only the twelve reviewed monitoring hooks may be created or deleted"
HISTORY_DENIED = "Only canonical Helm history records of the six releases may change"
PROBE = "jaeger-demo-readiness-probe"
PROBE_LABEL = "jaegertracing.io/readiness-probe"


def resource_arg(group, target):
    return target + ("." + group if group else "")


def pin_probe(obj):
    """A dry-run patch that neither keeps nor takes the reviewed surface, so only this object's pin can deny it."""
    kind = obj["kind"]
    if kind in ("Role", "ClusterRole"):
        rule = {"apiGroups": ["readiness.invalid"], "resources": ["probes"], "verbs": ["get"]}
        return "merge", {"rules": (obj.get("rules") or []) + [rule]}
    if kind.endswith("Binding"):
        return "merge", {"subjects": (obj.get("subjects") or []) + [{"kind": "User", "apiGroup": RBAC_GROUP, "name": "readiness.invalid"}]}
    if kind in ("Deployment", "DaemonSet", "StatefulSet"):
        return "merge", {"spec": {"template": {"spec": {"terminationGracePeriodSeconds": 7919}}}}
    if kind in OPERATOR_KINDS:
        return "merge", {"spec": {"replicas": (obj["spec"].get("replicas") or 1) + 7}}
    if kind == "Service":
        return "merge", {"spec": {"externalIPs": ["192.0.2.10"]}}
    timeout = obj["webhooks"][0].get("timeoutSeconds", 10)
    return "json", [{"op": "replace", "path": "/webhooks/0/timeoutSeconds", "value": timeout % 30 + 1}]


def merged(target, change):
    if not isinstance(change, dict) or not isinstance(target, dict):
        return copy.deepcopy(change)
    return {**target, **{k: merged(target.get(k), v) for k, v in change.items()}}


def creatable(key, body, name=None):
    """A rendered object as Helm would send it, in its namespace, optionally renamed."""
    obj = copy.deepcopy(body)
    if key[0] != "cluster":
        obj["metadata"]["namespace"] = key[0]
    if name:
        obj["metadata"] = {k: v for k, v in obj["metadata"].items() if k in ("namespace", "labels", "annotations")}
        obj["metadata"]["name"] = name
    return obj


def readiness_probes(defaulted, definitions, changes=NO_CHANGES, bodies=None):
    """probes.json for manual_upgrade_verify.py: one content probe per pin, hook, inventory target, create gate
    and history namespace under a manual holder, and one lock probe per lock, create and retire gate without one.
    An object this commit adds is probed with a create, since it does not exist yet."""
    added = {tuple(k) for k in changes["added"]}
    bodies = bodies or {}
    probes = []
    for obj in defaulted:
        scope, group, target, name = ident(obj)
        if obj["kind"] in WEBHOOKS and not obj.get("webhooks"):
            continue
        kind_of_patch, change = pin_probe(obj)
        if (scope, group, target, name) in added:
            body = creatable((scope, group, target, name), bodies[scope, group, target, name])
            if kind_of_patch == "merge":
                body = merged(body, change)
            else:
                body["webhooks"][0]["timeoutSeconds"] = change[0]["value"]
            probes.append({"phase": "held", "label": f"pin on create {obj['kind']} {scope}/{name}", "verb": "create",
                           "namespace": None, "body": body, "expect": PIN_DENIED})
            continue
        probes.append({"phase": "held", "label": f"pin {obj['kind']} {scope}/{name}", "verb": "patch", "resource": resource_arg(group, target),
                       "namespace": None if scope == "cluster" else scope, "name": name, "type": kind_of_patch,
                       "patch": change, "expect": PIN_DENIED})
    first = {}
    for hook in definitions:
        changed = copy.deepcopy(hook)
        changed["metadata"].setdefault("labels", {})[PROBE_LABEL] = "true"
        probes.append({"phase": "held", "label": f"hook {hook['kind']} {hook['metadata']['name']}", "verb": "create",
                       "namespace": None, "body": changed, "expect": HOOK_DENIED})
        first.setdefault(hooks.KINDS[hook["kind"]], hook)
    for (group, target), hook in sorted(first.items()):
        stray = copy.deepcopy(hook)
        stray["metadata"] = {k: v for k, v in hook["metadata"].items() if k in ("namespace", "labels", "annotations")}
        stray["metadata"]["name"] = PROBE
        probes.append({"phase": "held", "label": f"hook inventory {target}", "verb": "create", "namespace": None,
                       "body": stray, "expect": INVENTORY_DENIED})
    for namespace in NAMESPACES:
        secret = r.resource("v1", "Secret", PROBE, namespace, stringData={"probe": "true"})
        probes.append({"phase": "held", "label": f"history {namespace}", "verb": "create", "namespace": None,
                       "body": secret, "expect": HISTORY_DENIED})
    for (scope, group, target), names in sorted(ordinary(current(changes)).items()):
        existing = sorted(n for n in names if (scope, group, target, n) not in added)
        if not existing:
            continue
        probes.append({"phase": "unlocked", "label": f"lock {target} {scope}", "verb": "patch", "resource": resource_arg(group, target),
                       "namespace": None if scope == "cluster" else scope, "name": existing[0], "type": "merge",
                       "patch": {"metadata": {"labels": {PROBE_LABEL: "true"}}}, "expect": LOCK_DENIED})
    for (scope, group, target), names in sorted(create_targets(changes, hook_targets(definitions)).items()):
        first = (scope, group, target, sorted(names)[0])
        probes.append({"phase": "held", "label": f"create gate {target} {scope}", "verb": "create", "namespace": None,
                       "body": creatable(first, bodies[first], PROBE), "expect": CREATE_DENIED})
        probes.append({"phase": "unlocked", "label": f"lock on create {target} {scope}", "verb": "create", "namespace": None,
                       "body": creatable(first, bodies[first]), "expect": LOCK_DENIED})
    for (scope, group, target), names in sorted(by_target(changes["removed"]).items()):
        probes.append({"phase": "unlocked", "label": f"lock on retire {target} {scope}", "verb": "delete",
                       "resource": resource_arg(group, target), "namespace": None if scope == "cluster" else scope,
                       "name": sorted(names)[0], "expect": LOCK_DENIED})
    return probes


def clean_commit():
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                           text=True, capture_output=True, check=True).stdout
    r.require(not dirty, "Render from a clean checkout of the reviewed commit")
    return head


def render(user, charts, kubeconfig, context, scratch, helper=None):
    """The package for the selected components against the cluster's deployed releases: guards, grants,
    probes, the objects the upgrade adds and drops, and the archive digests."""
    kube = ["kubectl", "--kubeconfig", kubeconfig, "--context", context, "--request-timeout=15s"]
    pinned, located, rendered = render_pinned(charts, scratch)
    deployed = deployed_objects(kubeconfig, context, scratch)
    changes = release_changes(deployed, rendered)
    defaulted = server_defaults(pinned, kube)
    applied = applied_forms(pinned, defaulted, deployed, release_methods(kube), kube,
                            helper or shutil.which(PREVIEW_HELPER) or PREVIEW_HELPER)
    definitions = hooks.pinned_hooks(located["oci-monitoring"]) if "oci-monitoring" in COMPONENTS else []
    defaulted_hooks = dry_admitted_hooks(definitions, kube) if definitions else []
    guards, granted = package(user, defaulted, defaulted_hooks, changes, applied)
    return {"guards": guards, "grants": granted, "changes": changes, "pinnedObjects": len(defaulted), "hooks": len(defaulted_hooks),
            "defaulted": defaulted, "applied": applied, "rendered": rendered,
            "probes": readiness_probes(defaulted, definitions, changes, {k: rendered[k] for k in changes["added"]}),
            "archives": {key: hashlib.sha256(located[key].read_bytes()).hexdigest() for key in RELEASE_KEYS}}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user", required=True, help="Kubernetes user name of the demo-apply credential (not committed)")
    parser.add_argument("--components", nargs="+", choices=ALL_COMPONENTS, default=list(ALL_COMPONENTS),
                        help="Only the components this upgrade changes (default: all three)")
    parser.add_argument("--charts", type=Path, help="Verified chart archives from scheduled_inputs.charts_for; fetched when omitted")
    parser.add_argument("--preview-helper", default=shutil.which(PREVIEW_HELPER) or PREVIEW_HELPER,
                        help="jaeger-helm-preview built from ops/executor/preview, for client-side releases")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--cluster-uid", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New operator-only directory for guards, grants and summary")
    args = parser.parse_args()
    commit = clean_commit()
    subjects_for(args.user)
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.context, "--request-timeout=15s"]
    found = subprocess.run([*kube, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"],
                           text=True, capture_output=True, timeout=25, check=True).stdout
    r.require(found == args.cluster_uid and bool(found), "Cluster identity mismatch")
    kube_version = json.loads(r.MATRIX.read_bytes())["targetKubernetes"]
    with tempfile.TemporaryDirectory(prefix="manual-upgrade-") as scratch, selected(args.components):
        charts = args.charts
        if charts is None:
            staging = Path(scratch) / "sources"
            staging.mkdir()
            scheduled_inputs.charts_for(ROOT, staging, scheduled_inputs.public_env(staging))
            charts = staging / "charts"
        built = render(args.user, charts, args.kubeconfig, args.context, scratch, args.preview_helper)
    changes = built["changes"]
    args.output.mkdir(mode=0o700)
    summary = {"configurationSHA": commit, "clusterUID": args.cluster_uid, "kubernetes": kube_version, "components": args.components,
               "chartArchiveSHA256": built["archives"], "pinnedObjects": built["pinnedObjects"], "hooks": built["hooks"],
               "guards": len(built["guards"]), "grants": len(built["grants"]), "probes": len(built["probes"]),
               "added": ["/".join(k) for k in changes["added"]], "removed": ["/".join(k) for k in changes["removed"]],
               "retained": ["/".join(k) for k in changes["retained"]],
               "order": "Apply guards.json, then grants.json. Remove grants.json before guards.json."}
    for name in ("guards", "grants"):
        (args.output / (name + ".json")).write_bytes(adapter.encode({"apiVersion": "v1", "kind": "List", "items": built[name]}))
    (args.output / "probes.json").write_text(json.dumps(built["probes"], indent=1) + "\n")
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()

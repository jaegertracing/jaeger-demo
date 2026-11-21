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
from collections import defaultdict
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import monitoring_hooks as hooks
import permissions as r

import access_matrix
import scheduled_adapter as adapter
import scheduled_inputs

ROOT = r.ROOT
PREFIX = "jaeger-demo-manual"
COMPONENTS = ("oci-monitoring", "opensearch", "otel-demo")
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
    return obj


def cel(root, path):
    return "dyn(" + root + ")" + "".join("[" + json.dumps(p) + "]" for p in path)


def value(root, path, default):
    if default is REQUIRED:
        return "dyn(" + cel(root, path) + ")"
    return f"({json.dumps(path[-1])} in {cel(root, path[:-1])} ? dyn({cel(root, path)}) : dyn({r.literal(default)}))"


def webhook_checks(count, reference):
    parts = [f'"webhooks" in dyn(object) && size(dyn(object)["webhooks"]) == {count}']
    for i in range(count):
        parts.append(r.same_except(cel("object", ("webhooks", i)), reference(("webhooks", i)), ["clientConfig"]))
        parts.append(r.same_except(cel("object", ("webhooks", i, "clientConfig")), reference(("webhooks", i, "clientConfig")), ["caBundle"]))
    return "(" + " && ".join(parts) + ")"


def surface_expression(reviewed):
    """CEL: the update keeps the current security surface, or takes exactly the reviewed one."""
    kind = reviewed["kind"]
    if kind in WEBHOOKS:
        count = len(reviewed.get("webhooks") or [])
        keep = f'"webhooks" in dyn(oldObject) && size(dyn(oldObject)["webhooks"]) == {count} && ' + webhook_checks(count, lambda p: cel("oldObject", p))
        take = webhook_checks(count, lambda p: r.literal(at(reviewed, p)))
    else:
        keep = " && ".join(f"{value('object', p, d)} == {value('oldObject', p, d)}" for p, d in SURFACE[kind])
        take = " && ".join(f"{value('object', p, d)} == dyn({r.literal(at(reviewed, p, d))})" for p, d in SURFACE[kind])
    expression = "(" + keep + ") || (" + take + ")"
    if kind == "StatefulSet":
        storage = " && ".join(f"{value('object', p, d)} == {value('oldObject', p, d)}" for p, d in STATEFUL_STORAGE)
        expression = "(" + storage + ") && (" + expression + ")"
    r.require(len(expression.encode()) <= MAX_EXPRESSION, f"{kind}/{reviewed['metadata']['name']} is too large for a native content policy")
    return expression


def pins(defaulted, policies):
    """One UPDATE policy per pinned object."""
    rows = records()
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
        guards += policies(tag, group, target, ["UPDATE"], surface_expression(obj),
                           "Privileged fields differ from the reviewed render of this commit", condition=condition)
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
        guards += policies(name, group, target, ["UPDATE"], "true", "A manual deployment lock holder is required", condition=condition)
    return guards


def history(policies):
    guards = policies(PREFIX + "-history", "", "secrets", ["CREATE", "UPDATE", "DELETE"], r.history_expression(HISTORY),
                      "Only canonical Helm history records of the six releases may change",
                      condition=r.CHART_SECRET_UPDATE + json.dumps(r.MONITORING_SECRETS) + ")",
                      variables=[{"name": "records", "expression": r.HISTORY_RECORDS}])
    # The three chart-owned monitoring Secrets may change with the chart (documented residual risk);
    # the lock gate on their named patch still applies.
    return guards


def grants(defaulted, subjects):
    rows = records()
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
            scope = "cluster" if ref["kind"] == "ClusterRole" else ident(obj)[0]
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
    return [item for scope, scoped in sorted(rules.items()) for item in r.bindings(PREFIX + "-upgrade", scope, scoped, (), subjects=subjects)]


def package(user, defaulted, defaulted_hooks):
    """Return (guards, grants) for already server-defaulted pinned objects and hooks."""
    subjects = subjects_for(user)
    policies = policy_builder(user)
    guards = pins(defaulted, policies) + lock_gates(records(), policies) + history(policies)
    hook_guards, hook_grants = hooks.controls(defaulted_hooks, policies=policies, lock=None, archive_name=ARCHIVE,
                                              prefix=PREFIX, subjects=subjects, account_guards=False, scoped=True)
    return guards + hook_guards, grants(defaulted, subjects) + hook_grants


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
    """Render the four releases and supplements. Returns ([(pinned object, namespace)], {release key: archive})."""
    kube_version = json.loads(r.MATRIX.read_bytes())["targetKubernetes"]
    env = {"PATH": os.environ["PATH"], "KUBECONFIG": os.devnull, "HOME": str(scratch)}
    located = verified_archives(access_matrix.packages(Path(charts), ROOT))
    pinned = []
    for key in RELEASE_KEYS:
        namespace = adapter.RELEASES[key][0]
        supplements = adapter.supplemental_for(key, ROOT) if key in COMPONENTS else []
        for obj in access_matrix.render(key, located[key], ROOT, kube_version, env) + supplements:
            if obj["kind"] in KINDS and not access_matrix.hook_events(obj):
                pinned.append((obj, namespace))
    return pinned, located


def clean_commit():
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                           text=True, capture_output=True, check=True).stdout
    r.require(not dirty, "Render from a clean checkout of the reviewed commit")
    return head


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user", required=True, help="Kubernetes user name of the demo-apply credential (not committed)")
    parser.add_argument("--charts", type=Path, help="Verified chart archives from scheduled_inputs.charts_for; fetched when omitted")
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
    with tempfile.TemporaryDirectory(prefix="manual-upgrade-") as scratch:
        charts = args.charts
        if charts is None:
            staging = Path(scratch) / "sources"
            staging.mkdir()
            scheduled_inputs.charts_for(ROOT, staging, scheduled_inputs.public_env(staging))
            charts = staging / "charts"
        pinned, located = render_pinned(charts, scratch)
        defaulted = server_defaults(pinned, kube)
        defaulted_hooks = dry_admitted_hooks(hooks.pinned_hooks(located["oci-monitoring"]), kube)
        guards, granted = package(args.user, defaulted, defaulted_hooks)
        archives = {key: hashlib.sha256(located[key].read_bytes()).hexdigest() for key in RELEASE_KEYS}
    args.output.mkdir(mode=0o700)
    summary = {"configurationSHA": commit, "clusterUID": args.cluster_uid, "kubernetes": kube_version,
               "chartArchiveSHA256": archives, "pinnedObjects": len(defaulted), "hooks": len(defaulted_hooks),
               "guards": len(guards), "grants": len(granted),
               "order": "Apply guards.json, then grants.json. Remove grants.json before guards.json."}
    for name, items in (("guards", guards), ("grants", granted)):
        (args.output / (name + ".json")).write_bytes(adapter.encode({"apiVersion": "v1", "kind": "List", "items": items}))
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()

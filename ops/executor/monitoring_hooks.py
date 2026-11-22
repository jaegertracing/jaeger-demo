#!/usr/bin/env python3
# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0
"""Derive monitoring hook admission and grants from a reviewed chart and server-defaulted hook specs."""

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import permissions as r

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/demo"))
import scheduled_adapter as adapter

ARCHIVE = "jaeger-demo-reviewed-monitoring-crds"
REQUEST = "daily-hosted-update"
KINDS = {"ServiceAccount": ("", "serviceaccounts"), "ConfigMap": ("", "configmaps"),
         "ClusterRole": ("rbac.authorization.k8s.io", "clusterroles"), "ClusterRoleBinding": ("rbac.authorization.k8s.io", "clusterrolebindings"),
         "Role": ("rbac.authorization.k8s.io", "roles"), "RoleBinding": ("rbac.authorization.k8s.io", "rolebindings"), "Job": ("batch", "jobs")}


def pinned_hooks(archive):
    locks = json.loads((ROOT / "ops/executor/chart-archives.json").read_bytes())["charts"]
    r.require(archive.name.startswith("kube-prometheus-stack-") and archive.name in locks
              and hashlib.sha256(archive.read_bytes()).hexdigest() == locks[archive.name], "Unexpected monitoring chart checksum")
    with tempfile.TemporaryDirectory() as home:
        env = {"PATH": os.environ["PATH"], "HOME": home, "KUBECONFIG": os.devnull}
        r.require(adapter.command(["helm", "version", "--template={{.Version}}"], env=env) == b"v4.3.2", "Unexpected Helm version")
        values = adapter.values_for("oci-monitoring", ROOT, {})
        data = adapter.command(["helm", "template", "prometheus", archive, "-n", "default", "--kube-version=1.37.0",
                                "--is-upgrade", "--values", "-"], body=adapter.encode(values), env=env)
    hooks = [obj for obj in adapter.docs(data) if "helm.sh/hook" in (obj["metadata"].get("annotations") or {})]
    targets = {(s["scope"], s["resource"], s["name"]) for s in r.inventory() if s.get("hookEvents")}
    actual = set()
    for obj in hooks:
        scope = "cluster" if obj["kind"].startswith("Cluster") else obj["metadata"].setdefault("namespace", "default")
        actual.add((scope, KINDS[obj["kind"]][1], obj["metadata"]["name"]))
        if obj["kind"] == "Job":
            r.require(all("@sha256:" in ref for ref in adapter.image_references(obj)), "Hook must use reviewed image digests")
    r.require(len(hooks) == len(actual) == 12 and actual == targets, "Hook inventory changed")
    return hooks


def dry_admitted(hooks, kube):
    # Explicit context is independently checked by the caller. No object is stored.
    result = []
    for hook in hooks:
        command = [*kube, "create", "--dry-run=server", "--field-manager=helm", "-f", "-", "-o", "json"]
        completed = subprocess.run(command, input=adapter.encode(hook), capture_output=True, timeout=30)
        if completed.returncode:
            raise RuntimeError("Dry-run failed for " + hook["kind"] + "/" + hook["metadata"]["name"])
        obj = json.loads(completed.stdout)
        r.require(obj["kind"] == hook["kind"] and obj["metadata"]["name"] == hook["metadata"]["name"], "Unexpected admitted hook")
        result.append(obj)
    return result


def hook_account_guards():
    """Constrain the chart's existing hook identities during a daily transaction."""
    annotation = "jaegertracing.io/deployment-lock-holder"
    pending = (f"has(params.metadata.annotations) && {json.dumps(annotation)} in params.metadata.annotations && "
               f"params.metadata.annotations[{json.dumps(annotation)}].startsWith('daily/')")
    metadata = """['annotations','labels','ownerReferences','finalizers'].all(k,
       (k in dyn(object.metadata)) == (k in dyn(oldObject.metadata)) &&
       (!(k in dyn(object.metadata)) || dyn(object.metadata)[k] == dyn(oldObject.metadata)[k]))"""
    tls_sa = "prometheus-kube-prometheus-admission"
    for group, target, account, expr in (
        ("apiextensions.k8s.io", "customresourcedefinitions", "prometheus-crds-upgrade",
         "request.operation == 'UPDATE' && request.name in " + json.dumps(sorted(r.CRDS)) +
         " && object.spec == oldObject.spec && " + metadata),
        *[("admissionregistration.k8s.io", target, tls_sa,
           "request.operation == 'UPDATE' && request.name == 'prometheus-kube-prometheus-admission' && "
           "object.webhooks == oldObject.webhooks && " + metadata)
          for target in ("mutatingwebhookconfigurations", "validatingwebhookconfigurations")],
        ("", "secrets", tls_sa, "false"),
    ):
        name = "jaeger-demo-hook-account-" + target
        spec = {"policyName": name, "validationActions": ["Deny"], "paramRef": {
                "namespace": "default", "name": "jaeger-demo-deployment-revisions", "parameterNotFoundAction": "Deny"}}
        yield r.resource(r.ADMISSION, "ValidatingAdmissionPolicy", name, spec={
            "failurePolicy": "Fail", "paramKind": {"apiVersion": "v1", "kind": "ConfigMap"},
            "matchConstraints": {"resourceRules": [{"apiGroups": [group], "apiVersions": ["v1"],
                                 "operations": ["CREATE", "UPDATE", "DELETE"], "resources": [target]}]},
            "matchConditions": [{"name": "pinned-hook-account",
                "expression": "request.userInfo.username == " + json.dumps("system:serviceaccount:default:" + account)}],
            "validations": [{"expression": f"!({pending}) || ({expr})",
                    "message": "The daily monitoring hook must preserve existing CRD, webhook and certificate state"}]})
        yield r.resource(r.ADMISSION, "ValidatingAdmissionPolicyBinding", name, spec=spec)


def controls(objects, *, policies=r.policies, lock=r.checkpoint_lock, archive_name=ARCHIVE, prefix="jaeger-demo",
             subjects=None, account_guards=True, scoped=False, exempt_create=None, exempt_delete=None):
    """Guards and grants for the twelve hooks. The defaults render the executor's package.

    ops/executor/manual_upgrade.py passes its own policy builder, which matches the manual
    deploy identity and checks the manual lock in every policy, so it needs no separate lock.
    It also sets `scoped`: that identity keeps its Stage 1 Jaeger and recovery rights, so the
    inventory and preservation policies cover only the hooks' namespace and roles. `exempt_create` and
    `exempt_delete` names, per (scope, group, resource), are other objects that package creates or deletes
    with their own guards; each is admitted only for its own operation.
    """
    by_target = defaultdict(list)
    grant_rules = defaultdict(list)
    archive, = [obj["binaryData"] for obj in objects if obj["kind"] == "ConfigMap"]
    guards = [r.resource("v1", "ConfigMap", archive_name, r.OPS, immutable=True, binaryData=archive)]
    for obj in objects:
        kind, meta = obj["kind"], obj["metadata"]
        group, target = KINDS[kind]
        name, scope = meta["name"], meta.get("namespace", "cluster")
        by_target[scope, group, target].append(name)
        fields = sorted(set(obj) - {"apiVersion", "kind", "metadata", "status", "binaryData"})
        meta_fields = sorted(set(meta) - r.SYSTEM_META)
        template = {k: v for k, v in obj.items() if k != "binaryData"}
        exact = r.exact_state(template, fields, meta_fields, external_fields=("binaryData",) if kind == "ConfigMap" else ())
        if kind == "Job":
            uid = meta["uid"]
            r.require(obj["spec"]["selector"]["matchLabels"]["batch.kubernetes.io/controller-uid"] == uid,
                      "Unexpected Kubernetes Job selector default")
            exact = exact.replace(json.dumps(uid), "string(s.metadata.uid)")
        if kind == "ConfigMap":
            # The reviewed CRD archive exceeds 256 KiB. CEL compares the bytes to a separate immutable
            # operator-owned ConfigMap; its body never becomes a policy or last-applied annotation.
            exact = "(" + exact + " && s.binaryData == params.binaryData)"
        tag = prefix + "-hook-" + hashlib.sha256((kind + "/" + name).encode()).hexdigest()[:16]
        predicate = "request.name == " + json.dumps(name) + (" && request.namespace == 'default'" if scope != "cluster" else "")
        match = [{"name": "records", "expression": "request.operation == 'DELETE' ? [oldObject] : [object]"}]
        # Helm 4 creates hooks with server-side apply when the release uses it: a PATCH that the manual package
        # grants by name, so there an update must match the reviewed hook exactly too.
        operations = ["CREATE", "UPDATE", "DELETE"] if scoped else ["CREATE","DELETE"]
        policy, binding = policies(tag, group, target, operations, "variables.records.all(s, " + exact + ")",
                                   "Hook differs from the independently reviewed monitoring baseline", condition=predicate, variables=match)
        if kind == "ConfigMap":
            # The separate inventory gate below also requires a running protected request
            # (or, for the manual package, the manual lock).
            policy["spec"]["validations"] = policy["spec"]["validations"][1:]
            binding["spec"]["paramRef"].update(namespace=r.OPS, name=archive_name)
        guards.extend([policy, binding])
        if kind == "ConfigMap" and scoped:
            # The inventory gate holds the lock for CREATE and DELETE only; the manual package's named patch
            # needs the same lock for an UPDATE, which this comparison against the archive cannot check.
            guards.extend(policies(tag + "-update-lock", group, target, ["UPDATE"], "true",
                                   "A manual deployment lock holder is required", condition=predicate))
    for (scope, group, target), names in sorted(by_target.items()):
        valid = "request.name in " + json.dumps(sorted(names))
        created = sorted(set(names) | set((exempt_create or {}).get((scope, group, target), ())))
        deleted = sorted(set(names) | set((exempt_delete or {}).get((scope, group, target), ())))
        if created != sorted(names) or deleted != sorted(names):
            # A dropped name must never be recreated: no pin covers it.
            valid = ('(request.operation == "CREATE" ? request.name in ' + json.dumps(created)
                     + " : request.name in " + json.dumps(deleted) + ")")
        if scope != "cluster":
            valid = "request.namespace == 'default' && " + valid
        # The hook grants give collection create in default and at cluster scope only.
        where = "request.namespace == 'default'" if scoped and scope != "cluster" else None
        guards.extend(policies(prefix + "-hook-inventory-" + target, group, target, ["CREATE", "DELETE"],
                               valid, "Only the twelve reviewed monitoring hooks may be created or deleted", condition=where))
        if lock:
            guards.extend(lock(prefix + "-hook-holder-" + target, group, target, ["CREATE", "DELETE"], None))
        grant_rules[scope].append(r.rule(group, target, ["create"]))
        grant_rules[scope].append(r.rule(group, target, ["delete", "get", "patch"] if scoped else ["get","delete"], names))
        if target in {"roles","clusterroles"}:
            grant_rules[scope].append(r.rule(group, target, ["bind"], names))
            # Kubernetes checks escalation for a CREATE against the collection URL, which has
            # no resourceName. Raw CREATE and UPDATE are gated independently of this extra verb.
            grant_rules[scope].append(r.rule(group, target, ["escalate"], [""]))
            if scoped:
                # A server-side apply that creates the role is a PATCH on its name, so escalation is checked there.
                grant_rules[scope].append(r.rule(group, target, ["escalate"], names))
            same = ('object.rules == oldObject.rules && has(object.aggregationRule) == has(oldObject.aggregationRule) '
                    '&& (!has(object.aggregationRule) || object.aggregationRule == oldObject.aggregationRule)'
                    if target == "clusterroles" else 'object.rules == oldObject.rules')
            same += " && has(object.metadata.labels) == has(oldObject.metadata.labels) && (!has(object.metadata.labels) || object.metadata.labels == oldObject.metadata.labels)"
            # Scoped, the manual package's per-object pins admit reviewed rule changes on other roles.
            only = "request.name in " + json.dumps(sorted(names))
            hook_roles = (only if scope == "cluster" else "request.namespace == 'default' && " + only) if scoped else None
            guards.extend(policies(prefix + "-hook-preserve-" + target, group, target, ["UPDATE"], same,
                                   "Existing RBAC rules and aggregation must be preserved", condition=hook_roles))
    if account_guards:
        guards.extend(hook_account_guards())
    grant_rules[r.OPS].append(r.rule("", "configmaps", ["get"], [archive_name]))
    grants = [item for scope, rules in grant_rules.items()
              for item in r.bindings(prefix + "-monitoring-hook", scope, rules, (r.EXECUTOR,), subjects=subjects)]
    return guards, grants


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--cluster-uid", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New operator-only directory for separate guards and grants")
    args = parser.parse_args()
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.context, "--request-timeout=15s"]
    result = subprocess.run([*kube, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"],
                            text=True, capture_output=True, timeout=25, check=True)
    r.require(result.stdout == args.cluster_uid and bool(result.stdout), "Cluster identity mismatch")
    groups = controls(dry_admitted(pinned_hooks(args.archive), kube))
    args.output.mkdir(mode=0o700)
    for name, group in zip(("guards", "grants"), groups):
        (args.output / (name + ".json")).write_bytes(adapter.encode({"apiVersion":"v1", "kind":"List", "items": group}))


if __name__ == "__main__":
    main()

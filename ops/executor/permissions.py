#!/usr/bin/env python3
# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0
"""Render the independent admission and RBAC stages for the private script executor."""

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/all-five-chart-access.json"
OPS = "jaeger-demo-operations"
PREPARER, EXECUTOR = "jaeger-demo-preparer", "jaeger-demo-executor"
USER = f"system:serviceaccount:{OPS}:{EXECUTOR}"
RBAC = "rbac.authorization.k8s.io/v1"
ADMISSION = "admissionregistration.k8s.io/v1"
REQUEST_NS = '(has(request.namespace) ? request.namespace : "")'
NAMESPACES = ("default", "jaeger", "opensearch", "otel-demo")
HISTORY = ("default/jaeger", "default/prometheus", "jaeger/jaeger", "opensearch/opensearch",
           "opensearch/opensearch-dashboards", "otel-demo/otel-demo")
CRDS = [x + ".monitoring.coreos.com" for x in (
    "alertmanagerconfigs", "alertmanagers", "podmonitors", "probes", "prometheusagents",
    "prometheuses", "prometheusrules", "scrapeconfigs", "servicemonitors", "thanosrulers")]
GVR = {("apps", "deployments"): "Deployment", ("apps", "statefulsets"): "StatefulSet",
       ("apps", "daemonsets"): "DaemonSet", ("batch", "cronjobs"): "CronJob",
       ("", "services"): "Service", ("networking.k8s.io", "ingresses"): "Ingress",
       ("policy", "poddisruptionbudgets"): "PodDisruptionBudget",
       ("rbac.authorization.k8s.io", "roles"): "Role", ("rbac.authorization.k8s.io", "rolebindings"): "RoleBinding",
       ("rbac.authorization.k8s.io", "clusterroles"): "ClusterRole",
       ("rbac.authorization.k8s.io", "clusterrolebindings"): "ClusterRoleBinding"}
# These are API-server-owned on the main endpoint. UID is bound separately for updates.
SYSTEM_META = {"resourceVersion", "managedFields", "generation", "creationTimestamp", "uid"}
SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9.-]*")


def require(test, explanation):
    if not test:
        raise ValueError(explanation)


def resource(api, kind, name, namespace=None, **fields):
    meta = {"name": name}
    if namespace and namespace != "cluster":
        meta["namespace"] = namespace
    return {"apiVersion": api, "kind": kind, "metadata": meta, **fields}


def bindings(name, scope, rules, accounts, *, subjects=None):
    cluster = scope == "cluster"
    kind = "ClusterRole" if cluster else "Role"
    if subjects is None:
        subjects = [{"kind": "ServiceAccount", "name": who, "namespace": OPS} for who in accounts]
    return [resource(RBAC, kind, name, scope, rules=rules),
            resource(RBAC, kind + "Binding", name, scope, subjects=subjects,
                     roleRef={"apiGroup": RBAC.split("/")[0], "kind": kind, "name": name})]


def rule(group, target, verbs, names=()):
    r = {"apiGroups": [group], "resources": [target], "verbs": sorted(verbs)}
    if names:
        r["resourceNames"] = sorted(names)
    return r


def inventory():
    return json.loads(MATRIX.read_bytes())["records"]


def read_access():
    names = defaultdict(set)
    for row in inventory():
        if not row["name"].startswith("sh.helm.release.v1."):
            names[row["scope"], row["apiGroup"], row["resource"]].add(row["name"])
    names["cluster", "", "namespaces"].add("kube-system")
    names["cluster", "apiextensions.k8s.io", "customresourcedefinitions"].update(CRDS)
    names["opensearch", "", "persistentvolumeclaims"].add("opensearch-cluster-single-opensearch-cluster-single-0")
    names["opensearch", "", "pods"].add("opensearch-cluster-single-0")
    names["default", "", "secrets"].add("prometheus-kube-prometheus-admission")
    reads = defaultdict(list)
    for (scope, group, target), resources in sorted(names.items()):
        reads[scope].append(rule(group, target, ["get"], resources))
    reads["cluster"].append({"nonResourceURLs": ["/version"], "verbs": ["get"]})
    for scope, rules in reads.items():
        yield from bindings("jaeger-demo-workload-observe", scope, rules, (PREPARER, EXECUTOR))
    for scope in ("default", "jaeger"):
        yield from bindings("jaeger-demo-prior-snapshot-pods", scope,
                            [rule("", "pods", ["list"]), rule("apps", "replicasets", ["get"])], (PREPARER,))
    for scope in NAMESPACES:
        # Helm history LIST returns every Secret payload in that namespace; resourceNames cannot constrain it.
        yield from bindings("jaeger-demo-release-ledger-read", scope, [rule("", "secrets", ["get", "list"])], (PREPARER, EXECUTOR))
        groups = [("apps", "deployments"), ("apps", "replicasets"), ("", "pods")]
        if scope == "opensearch":
            groups += [("apps", "statefulsets")]
        if scope in ("default", "otel-demo"):
            groups += [("apps", "daemonsets")]
        if scope == "default":
            groups += [("batch", "jobs")]
        yield from bindings("jaeger-demo-workload-readiness", scope,
                            [rule(g, t, ["get", "list", "watch"]) for g, t in groups], (EXECUTOR,))


def policies(name, group, target, operations, expression, message, *, condition=None, variables=None, running=True):
    conditions = [{"name": "trusted-executor", "expression": "request.userInfo.username == " + json.dumps(USER)}]
    if condition:
        conditions.append({"name": "reviewed-target", "expression": condition})
    spec = {"failurePolicy": "Fail", "paramKind": {"apiVersion": "v1", "kind": "ConfigMap"},
            "matchConstraints": {"resourceRules": [{"apiGroups": [group], "apiVersions": ["v1"],
                                 "operations": operations, "resources": [target]}]},
            "matchConditions": conditions, "validations": [{"expression": expression, "message": message}]}
    if running:
        spec["validations"].insert(0, {"expression": 'params.data.engine == "scripts-v1" && params.data.state == "running" && '
                                                'params.data.slot.matches("^[0-9]{4}-[0-9]{2}-[0-9]{2}T13:00:00Z$")',
                                       "message": "The protected script request must be running"})
    if variables:
        spec["variables"] = variables
    return [resource(ADMISSION, "ValidatingAdmissionPolicy", name, spec=spec),
            resource(ADMISSION, "ValidatingAdmissionPolicyBinding", name,
                     spec={"policyName": name, "validationActions": ["Deny"],
                           "paramRef": {"namespace": OPS, "name": "daily-hosted-update", "parameterNotFoundAction": "Deny"}})]


HISTORY_RECORDS = 'request.operation == "UPDATE" ? [oldObject, object] : [request.operation == "DELETE" ? oldObject : object]'
# Chart-owned Secrets in default that Helm updates in place; every other Secret write must be a history record.
CHART_SECRET_UPDATE = 'request.operation != "UPDATE" || !(request.namespace == "default" && request.name in '


def history_expression(history=HISTORY):
    """CEL that admits only canonical Helm history records of the listed namespace/release pairs."""
    return ('variables.records.all(s, s != null && s.type == "helm.sh/release.v1" && '
            'has(s.data) && size(s.data) == 1 && "release" in s.data && '
            'has(s.metadata.labels) && ["owner","name","version","status"].all(k, k in s.metadata.labels) && '
            's.metadata.labels.owner == "helm" && s.metadata.labels.version.matches("^[1-9][0-9]*$") && '
            's.metadata.name == "sh.helm.release.v1." + s.metadata.labels.name + ".v" + s.metadata.labels.version && '
            '(request.namespace + "/" + s.metadata.labels.name) in ' + json.dumps(list(history)) + ') && '
            '(request.operation != "CREATE" || object.metadata.labels.status in ["pending-upgrade", "pending-rollback"])')


def history_guard():
    # Base envelope is the same one rehearsed with Helm's real upgrade, failure, pruning and rollback.
    history = policies("jaeger-demo-executor-history", "", "secrets", ["CREATE", "UPDATE", "DELETE"],
                    history_expression(), "Only the six canonical hosted Helm history records may change",
                    condition=CHART_SECRET_UPDATE+json.dumps(MONITORING_SECRETS)+')',
                    variables=[{"name": "records", "expression": HISTORY_RECORDS}])
    allowed = 'request.namespace == "default" && request.name in '+json.dumps(MONITORING_SECRETS)
    same = same_except("oldObject","object",["metadata"])+" && "+same_except("oldObject.metadata","object.metadata",["managedFields","resourceVersion","generation"])
    history += policies("jaeger-demo-history-chart-secrets","","secrets",["UPDATE"],same,
                        "Chart Secret contents must be preserved",condition=allowed)
    lock = checkpoint_lock("jaeger-demo-history-holder","","secrets",["CREATE","UPDATE","DELETE"],
                           'request.namespace in '+json.dumps(NAMESPACES))
    return history + lock


def history_grants():
    for scope in NAMESPACES:
        yield from bindings("jaeger-demo-guarded-history", scope,
                            [rule("", "secrets", ["create", "update", "delete"])], (EXECUTOR,))


def literal(x):
    # CEL requires a homogeneous map/list type. dyn preserves the JSON scalar types within it.
    if isinstance(x, dict):
        return "{" + ",".join(json.dumps(k) + ":dyn(" + literal(v) + ")" for k, v in sorted(x.items())) + "}"
    if isinstance(x, list):
        return "[" + ",".join("dyn(" + literal(v) + ")" for v in x) + "]"
    return json.dumps(x, allow_nan=False)


def exact_state(obj, fields, meta, external_fields=()):
    allowed = sorted(set(fields) | set(external_fields) | {"apiVersion", "kind", "metadata", "status"})
    parts = ["dyn(s).all(k, k in " + literal(allowed) + ")",
             "dyn(s.metadata).all(k, k in " + literal(sorted(set(meta) | SYSTEM_META)) + ")"]
    for k in fields:
        parts.append(f'{json.dumps(k)} in dyn(s) && dyn(s)[{json.dumps(k)}] == dyn({literal(obj[k])})')
    for k in meta:
        parts.append(f'{json.dumps(k)} in dyn(s.metadata) && dyn(s.metadata)[{json.dumps(k)}] == dyn({literal(obj["metadata"][k])})')
    return "(" + " && ".join(parts) + ")"


MONITORING_SECRETS = ["prometheus-grafana", "alertmanager-prometheus-kube-prometheus-alertmanager",
                      "prometheus-kube-prometheus-prometheus-scrape-confg"]
HOLDER = "jaegertracing.io/deployment-lock-holder"
PROTOCOL = "jaegertracing.io/deployment-lock-version"
CHECKPOINT = "jaeger-demo-deployment-revisions"
COMPONENTS = ("oci-jaeger", "oci-monitoring", "opensearch", "otel-jaeger", "otel-demo")


def same_except(left, right, keys):
    allowed = json.dumps(sorted(keys))
    return "(" + " && ".join(f"dyn({a}).all(k, k in {allowed} || (k in dyn({b}) && dyn({a})[k] == dyn({b})[k]))"
                             for a, b in ((left, right), (right, left))) + ")"


def ordinary_rows():
    return {(r["scope"], r["apiGroup"], r["resource"], r["name"]) for r in inventory()
            if "patch" in r["verbs"] and not r.get("hookEvents") and r["name"] != CHECKPOINT
            and not r["name"].startswith("sh.helm.release.v1.")}


def lock_policy(scopes):
    result = []
    for (group, target), names in sorted(scopes.items()):
        # Second independent parameter is the live checkpoint. The checkpoint admission
        # gate permits only requested -> acquire and verified -> release for its exact daily holder.
        condition = REQUEST_NS+' + "/" + request.name in '+json.dumps(sorted(names))
        name = "jaeger-demo-lock-" + (group or "core").split(".")[0] + "-" + target
        result += checkpoint_lock(name, group, target, ["UPDATE"], condition)
    return result


def checkpoint_lock(name, group, target, operations, condition):
    expr = ('has(params.metadata.annotations) && ' + json.dumps(PROTOCOL) + ' in params.metadata.annotations && '
            'params.metadata.annotations['+json.dumps(PROTOCOL)+'] == "v1" && '+json.dumps(HOLDER)+' in params.metadata.annotations && '
            'params.metadata.annotations['+json.dumps(HOLDER)+'].matches("^daily/[0-9]{4}-[0-9]{2}-[0-9]{2}T13:00:00Z/.+$")')
    pair = policies(name, group, target, operations, expr, "A daily checkpoint holder is required",
                    condition=condition, running=False)
    pair[1]["spec"]["paramRef"].update(namespace="default", name=CHECKPOINT)
    return pair


def checkpoint_guard():
    h, p = json.dumps(HOLDER), json.dumps(PROTOCOL)
    oa, na = "oldObject.metadata.annotations", "object.metadata.annotations"
    owner = '("daily/" + params.data.slot + "/" + params.metadata.uid)'
    dates = 'params.data.slot.matches("^[0-9]{4}-[0-9]{2}-[0-9]{2}T13:00:00Z$")'
    before, after = '(has(oldObject.data) ? oldObject.data : {})', '(has(object.data) ? object.data : {})'
    changed = 'variables.after.filter(k, !(k in variables.before) || variables.before[k] != variables.after[k])'
    fingerprint = ('variables.before.all(k,k in variables.after) && size('+changed+') <= 1 && '+changed+
                   '.all(k, k in '+json.dumps(COMPONENTS)+' && variables.after[k].matches("^[0-9a-f]{64}$"))')
    variants = [
        f'(params.data.state == "requested" && !({h} in {oa}) && {h} in {na} && {na}[{h}] == variables.owner && variables.before == variables.after)',
        f'(params.data.state == "running" && {h} in {oa} && {h} in {na} && {oa}[{h}] == variables.owner && {na}[{h}] == variables.owner && {fingerprint})',
        f'(params.data.state == "verified" && {h} in {oa} && {oa}[{h}] == variables.owner && !({h} in {na}) && variables.before == variables.after)',
    ]
    expr = " && ".join([dates,'params.data.engine == "scripts-v1"',f'{p} in {oa} && {oa}[{p}] == "v1"',
                        same_except("oldObject","object",["metadata","data"]),
                        same_except("oldObject.metadata","object.metadata",["annotations","managedFields","resourceVersion","generation"]),
                        same_except(oa,na,[HOLDER]),"("+" || ".join(variants)+")"])
    controls = policies("jaeger-demo-executor-checkpoint","", "configmaps", ["UPDATE"], expr,
                    "Only the matching daily lock or one component fingerprint may change",
                    condition='request.namespace == "default" && request.name == '+json.dumps(CHECKPOINT), running=False,
                    variables=[{"name":"owner","expression":owner},{"name":"before","expression":before},{"name":"after","expression":after}])
    # Cross-check the exact opposite record at claim time, then maintain that invariant for
    # *every* actor throughout running. A later mutation cannot substitute yesterday's holder.
    claim = policies("jaeger-demo-checkpoint-at-claim", "", "configmaps", ["UPDATE"],
                     'params.metadata.annotations['+h+'] == "daily/" + object.data.slot + "/" + object.metadata.uid',
                     "The protected request must own this exact lock before it can run",
                     condition='request.namespace == '+json.dumps(OPS)+' && request.name == "daily-hosted-update" && object.data.state == "running"',
                     running=False)
    claim[1]["spec"]["paramRef"].update(namespace="default", name=CHECKPOINT)
    maintain = policies("jaeger-demo-running-holder", "", "configmaps", ["UPDATE","DELETE"],
                        'params.data.state != "running" || (request.operation == "UPDATE" && '+oa+'['+h+'] == '+owner+
                        ' && '+na+'['+h+'] == '+owner+')', "An active daily run must keep its exact holder",
                        condition='request.namespace == "default" && request.name == '+json.dumps(CHECKPOINT),running=False)
    maintain[0]["spec"]["matchConditions"] = maintain[0]["spec"]["matchConditions"][1:]
    return controls + claim + maintain


def routine_snapshot(name):
    old, new = "oldObject", "object"
    comparison = [
        same_except(old,new,["metadata","spec"]), same_except(old+".metadata",new+".metadata",["managedFields","resourceVersion","generation"]),
        same_except(old+".spec",new+".spec",["template"]),
    ]
    o, n = old+".spec.template", new+".spec.template"
    if name == "trace-generator":
        annotation = "kubectl.kubernetes.io/restartedAt"
        a, z = "variables.oldAnnotations", "variables.newAnnotations"
        comparison += [same_except(o,n,["metadata"]),same_except(o+".metadata",n+".metadata",["annotations"]),
                       same_except(a,z,[annotation]), json.dumps(annotation)+" in "+z,
                       z+"["+json.dumps(annotation)+"] == params.data.slot"]
        variables = [{"name":var,"expression":f"has({obj}.metadata.annotations) ? {obj}.metadata.annotations : {{}}"}
                     for var,obj in (("oldAnnotations",o),("newAnnotations",n))]
    else:
        pattern = ("^(docker[.]io/)?jaegertracing/" + ("jaeger-snapshot" if name=="jaeger" else "example-hotrod-snapshot")
                   + ":[0-9a-f]{40}@sha256:[0-9a-f]{64}$")
        oc, nc = o+".spec.containers", n+".spec.containers"
        comparison += [same_except(o,n,["spec"]), same_except(o+".spec",n+".spec",["containers"]),
                       f"size({oc}) == 1 && size({nc}) == 1", f"{oc}[0].name == "+json.dumps(name),
                       same_except(oc+"[0]",nc+"[0]",["image"]), f"{nc}[0].image.matches("+json.dumps(pattern)+")"]
        variables = []
    return "("+" && ".join(comparison)+")", variables


def regular(review=None, *, daily=False):
    if daily:
        import approval_policies as daily_policies
        eligible = daily_policies.t.allowed()
    overrides = {(i["namespace"], i["group"], i["resource"], i["name"]) for i in review["changes"]} if review else set()
    groups = defaultdict(set)
    grants = defaultdict(list)
    guards = []
    lock_scopes = defaultdict(set)
    for ns, group, target, name in ordinary_rows():
        lock_scopes[group,target].add(("" if ns == "cluster" else ns)+"/"+name)
        if (ns,group,target,name) in overrides:
            continue
        groups[ns,group,target].add(name)
    for (ns,group,target), names in sorted(groups.items()):
        prefix = "" if ns == "cluster" else ns
        snapshot = names & {"jaeger","jaeger-hotrod","trace-generator"} if ns in ("default","jaeger") and target=="deployments" else set()
        lock_scopes[group,target].update(prefix+"/"+k for k in names)
        grants[ns].append(rule(group,target,["patch"],names))
        ident = hashlib.sha256(f"{ns}/{group}/{target}".encode()).hexdigest()[:18]
        same = same_except("oldObject","object",["metadata"])+ " && " + same_except("oldObject.metadata","object.metadata",["managedFields","resourceVersion","generation"])
        for tag, selected in [("ordinary",names-snapshot)]+[(n,{n}) for n in sorted(snapshot)]:
            if not selected:
                continue
            expr, variables = (same, [])
            if tag != "ordinary":
                snapshot_expr, variables = routine_snapshot(tag)
                expr = "("+same+") || "+snapshot_expr
            condition = REQUEST_NS+" == "+json.dumps(prefix)+" && request.name in "+json.dumps(sorted(selected))
            label="jaeger-demo-"+tag+"-"+ident
            mutable = {name for name in selected if daily and (ns,group,target,name) in eligible}
            if mutable:
                # Request and holder are independent parameters on separate installed policies.
                guards += policies(label+"-run",group,target,["UPDATE"],"true",
                                   "A daily request must be running",condition=condition)
                # Every meaningful daily write, including a legitimate snapshot image,
                # must match exactly what this independent slot authorized.
                expression = same
                # Public routes and certificate settings remain fixed after the slot is staged.
                if (group, target) != ("networking.k8s.io", "ingresses"):
                    approval = daily_policies.exact_variant(deployment=(group, target) == ("apps", "deployments"))
                    expression = "("+expression+") || (request.name in "+json.dumps(sorted(mutable))+" && "+approval+")"
                vars_ = [{"name":"id","expression":json.dumps((group or "core")+"/"+target+"/")+" + request.name"}]
                pair = policies(label,group,target,["UPDATE"],expression,"Unreviewed change to hosted workload contents",
                                condition=condition,variables=vars_,running=False)
                guards += daily_policies.workload_bindings(pair, ns, group, target, selected)
            else:
                guards += policies(label,group,target,["UPDATE"],expr,
                                   "Unreviewed change to hosted workload contents",condition=condition,variables=variables)
    guards += lock_policy(lock_scopes) + checkpoint_guard()
    grants["default"].append(rule("", "configmaps", ["patch"],[CHECKPOINT]))
    roles = []
    for ns, rules in grants.items():
        roles += bindings("jaeger-demo-existing-workload",ns,rules,(EXECUTOR,))
    return guards, roles


def workload(review):
    # The independent operator reviews server-defaulted object variants. Secret, hook and checkpoint
    # bodies are excluded from literal policies. Detailed private input must never be logged.
    require(set(review) == {"configurationSHA", "bundleSHA256", "slot", "changes"}
            and re.fullmatch("[0-9a-f]{40}", review["configurationSHA"])
            and re.fullmatch("[0-9a-f]{64}", review["bundleSHA256"])
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}T13:00:00Z", review["slot"]), "Invalid independently reviewed receipt")
    rows = {(r["scope"], r["apiGroup"], r["resource"], r["name"]) for r in inventory()
            if "patch" in r["verbs"] and not r.get("hookEvents")}
    seen = set()
    grants = defaultdict(lambda: defaultdict(set))
    guards = []
    require(isinstance(review["changes"], list) and 0 < len(review["changes"]) <= len(rows), "Unexpected number of approved changes")
    for change in review["changes"]:
        require(set(change) == {"namespace", "group", "resource", "name", "states"}, "Invalid approved change fields")
        ns, group, target, name = key = tuple(change[k] for k in ("namespace", "group", "resource", "name"))
        kind = GVR.get((group, target))
        states = change["states"]
        require(kind and key in rows and key not in seen and isinstance(states, list) and 1 <= len(states) <= 3,
                "Expected a distinct, existing, supported matrix target and one to three admitted states")
        seen.add(key)
        uid = states[0]["metadata"]["uid"]
        require(isinstance(uid, str) and SAFE_NAME.fullmatch(uid), "Expected a live Kubernetes UID")
        valid = []
        for obj in states:
            meta = obj["metadata"]
            require(obj["kind"] == kind and obj["apiVersion"] == (group + "/v1" if group else "v1")
                    and meta["name"] == name and meta.get("namespace", "cluster") == ns and meta["uid"] == uid
                    and "deletionTimestamp" not in meta and "helm.sh/hook" not in meta.get("annotations", {}),
                    "Approved state differs in identity, UID, hook or deletion state")
            valid.append(exact_state(obj, sorted(obj.keys() - {"apiVersion", "kind", "metadata", "status"}), sorted(meta.keys() - SYSTEM_META)))
        condition = REQUEST_NS + ' == ' + json.dumps("" if ns == "cluster" else ns) + ' && request.name == ' + json.dumps(name)
        scoped = " && ".join(f"params.data.{k} == {json.dumps(review[k])}" for k in ("slot", "configurationSHA", "bundleSHA256"))
        expr = scoped + " && [oldObject,object].all(s, s.metadata.uid == " + json.dumps(uid) + " && (" + " || ".join(valid) + "))"
        require(len(expr.encode()) <= 48000, "Approved resource is too large for this native content policy")
        tag = "jaeger-demo-state-" + hashlib.sha256("/".join(key).encode()).hexdigest()[:20]
        guards += policies(tag, group, target, ["UPDATE"], expr, "Resource content differs from independently approved states", condition=condition)
        grants[ns][group, target].add(name)
    roles = []
    for ns, collection in grants.items():
        rules = [rule(group, target, ["patch"], names) for (group, target), names in sorted(collection.items())]
        roles += bindings("jaeger-demo-guarded-existing", ns, rules, (EXECUTOR,))
    return guards, roles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("reads", "history-guard", "history-grants", "workload-guards", "workload-grants", "routine-guards", "routine-grants",
                        "daily-guards", "daily-grants", "approval-schema", "approval-seeds", "approval-guards", "approval-grants"))
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="A new local manifest; existing output is preserved")
    args = parser.parse_args()
    if args.stage.startswith("approval") or args.stage.startswith("daily"):
        if args.review:
            parser.error("The daily parameter protocol uses independently staged records")
        if args.stage.startswith("approval"):
            import approval_policies
            items = approval_policies.stages(args.stage)
        else:
            guards, roles = regular(daily=True)
            items = guards if args.stage == "daily-guards" else roles
    elif args.stage.startswith("workload"):
        if not args.review or args.review.stat().st_size > 4 * 1024 * 1024:
            parser.error("Supply a bounded operator-reviewed server-admitted state file")
        guards, roles = workload(json.loads(args.review.read_bytes()))
        items = guards if args.stage == "workload-guards" else roles
    elif args.stage.startswith("routine"):
        review = json.loads(args.review.read_bytes()) if args.review else None
        if review:
            workload(review)
        guards, roles = regular(review)
        items = guards if args.stage == "routine-guards" else roles
    else:
        if args.review:
            parser.error("The fixed read and history stages do not accept a state review")
        items = {"reads": read_access, "history-guard": history_guard, "history-grants": history_grants}[args.stage]()
    result = json.dumps({"apiVersion": "v1", "kind": "List", "items": list(items)}, indent=2).encode() + b"\n"
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
        out.write(result)


if __name__ == "__main__":
    main()

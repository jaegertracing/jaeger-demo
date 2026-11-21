# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0
"""Exercise the manual deploy identity's guarded grants on a disposable API with the real chart renders.

The package is rendered and server-defaulted exactly as an operator does with
ops/executor/manual_upgrade.py. The live objects are created by the fixture
administrator; every probe impersonates the manual user and uses server dry-run.
"""

import copy
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time

REPO = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(REPO / "ops/executor"), str(REPO / "scripts/demo")]
import manual_upgrade as m  # noqa: E402
import monitoring_hooks as hooks  # noqa: E402
import permissions as r  # noqa: E402

USER = "fixture-manual-deployer"
SERVER = ("resourceVersion", "uid", "creationTimestamp", "managedFields", "generation")
CRDS = ("crd-prometheuses.yaml", "crd-alertmanagers.yaml")
PIN = "Privileged fields differ from the reviewed render"
LOCK = "A manual deployment lock holder is required"
HISTORY = "Only canonical Helm history records"
# A stand-in for the hand-applied Stage 1 Roles, which are not in the repository: Jaeger history writes
# and the recovery script's ConfigMap writes. The package must leave both working under a manual lock.
STAGE1 = {"jaeger": [("", "secrets", ["create", "update", "delete"])],
          "opensearch": [("", "configmaps", ["create", "delete"])]}


def stage1_fixture():
    rules = {ns: [r.rule(group, target, verbs) for group, target, verbs in found] for ns, found in STAGE1.items()}
    return [item for ns, scoped in rules.items()
            for item in r.bindings("fixture-stage1-apply", ns, scoped, (), subjects=m.subjects_for(USER))]


def install_operator_crds(archive, kube, scratch):
    """The Prometheus and Alertmanager pins need their CRDs for server defaulting."""
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            if member.isfile() and member.name.endswith(CRDS) and "/crds/" in member.name:
                target = Path(scratch) / Path(member.name).name
                target.write_bytes(bundle.extractfile(member).read())
                kube(["create", "-f", str(target)], timeout=90)
    for crd in ("prometheuses", "alertmanagers"):
        kube(["wait", "--for=condition=Established", "crd/" + crd + ".monitoring.coreos.com", "--timeout=60s"], timeout=70)


def live(obj):
    created = copy.deepcopy(obj)
    for field in SERVER:
        created["metadata"].pop(field, None)
    created.pop("status", None)
    if created["kind"] == "Service" and created["spec"].get("clusterIP") != "None":
        # Separate dry-runs can return the same free address; let the API allocate on create.
        for field in ("clusterIP", "clusterIPs"):
            created["spec"].pop(field, None)
    return created


def eventually(attempt, accept, seconds=20):
    """Admission parameters reach the policy through an informer, so a change applies after a short delay."""
    deadline = time.monotonic() + seconds
    while True:
        result = attempt()
        if accept(result) or time.monotonic() > deadline:
            return result
        time.sleep(.25)


def exercise(charts, kubectl, cfg, kube):
    admin = [kubectl, "--kubeconfig", str(cfg), "--context", "fixture"]
    as_user = ["--as", USER, "--as-group", "system:authenticated"]
    allowed, denied = [], []
    for ns in ("jaeger", "opensearch", "otel-demo", r.OPS):
        kube(["create", "namespace", ns], check=False)
    checkpoint = r.resource("v1", "ConfigMap", r.CHECKPOINT, "default")
    checkpoint["metadata"]["annotations"] = {r.PROTOCOL: "v1", r.HOLDER: "manual/123/1"}
    kube(["create", "-f", "-"], body=json.dumps(checkpoint))

    with tempfile.TemporaryDirectory(prefix="manual-upgrade-") as scratch:
        pinned, located = m.render_pinned(Path(charts), scratch)
        install_operator_crds(located["oci-monitoring"], kube, scratch)
        defaulted = m.server_defaults(pinned, admin)
        definitions = hooks.pinned_hooks(located["oci-monitoring"])
        defaulted_hooks = hooks.dry_admitted(definitions, admin)
    guards, grants = m.package(USER, defaulted, defaulted_hooks)
    for item in guards + grants + stage1_fixture():
        kube(["create", "-f", "-"], body=json.dumps(item))
    for obj in defaulted:
        kube(["create", "-f", "-"], body=json.dumps(live(obj)))
    by_kind = {(o["kind"], o["metadata"]["name"]): o for o in defaulted}

    def user(args, body=None):
        return kube([*as_user, *args], body=body, check=False)

    def patch(kind, name, ns, change, kind_of_patch="json"):
        args = ["patch", kind, name, "--type=" + kind_of_patch, "-p", json.dumps(change), "--dry-run=server"]
        return user(args + (["-n", ns] if ns else []))

    def expect_denied(label, result, text):
        if not result.returncode or text not in result.stderr:
            raise ValueError(f"Expected denial for {label}: {result.stderr[:500]}")
        denied.append(label)

    def expect_allowed(label, result):
        if result.returncode:
            raise ValueError(f"Expected {label} to be admitted: {result.stderr[:700]}")
        allowed.append(label)

    wildcard = [{"op": "add", "path": "/rules/-", "value": {"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}}]
    first = eventually(lambda: patch("clusterrole", "prometheus-kube-prometheus-operator", None, wildcard),
                       lambda result: result.returncode and PIN in result.stderr)
    expect_denied("cluster-admin rule added to an escalatable ClusterRole", first, PIN)

    label = {"metadata": {"labels": {"fixture.jaegertracing.io/touched": "true"}}}
    expect_allowed("metadata-only Deployment patch", patch("deployment", "prometheus-kube-prometheus-operator", "default", label, "merge"))

    # A previous release with fewer rules: restoring the reviewed rules is admitted.
    ksm = by_kind["ClusterRole", "prometheus-kube-state-metrics"]
    older = live(ksm)
    older["rules"] = ksm["rules"][:-1]
    kube(["replace", "-f", "-"], body=json.dumps(older))
    expect_allowed("ClusterRole rules restored to the reviewed render",
                   patch("clusterrole", "prometheus-kube-state-metrics", None, [{"op": "replace", "path": "/rules", "value": ksm["rules"]}]))

    retention = {"spec": {"replicas": 0, "persistentVolumeClaimRetentionPolicy": {"whenScaled": "Delete", "whenDeleted": "Delete"}}}
    expect_denied("OpenSearch PVC retention Delete with zero replicas",
                  patch("statefulset", "opensearch-cluster-single", "opensearch", retention, "merge"), PIN)
    expect_denied("OpenSearch scaled to zero", patch("statefulset", "opensearch-cluster-single", "opensearch",
                  {"spec": {"replicas": 0}}, "merge"), PIN)

    container = "/spec/template/spec/containers/0"
    expect_denied("Deployment image change", patch("deployment", "prometheus-kube-prometheus-operator", "default",
                  [{"op": "replace", "path": container + "/image", "value": "registry.invalid/fixture:wrong"}]), PIN)
    expect_denied("Deployment service account change", patch("deployment", "prometheus-grafana", "default",
                  [{"op": "add", "path": "/spec/template/spec/serviceAccountName", "value": "prometheus-kube-prometheus-operator"}]), PIN)
    expect_denied("privileged node-exporter container", patch("daemonset", "prometheus-prometheus-node-exporter", "default",
                  [{"op": "add", "path": container + "/securityContext", "value": {"privileged": True}}]), PIN)
    expect_denied("hostPath volume on an OTel demo Deployment", patch("deployment", "frontend", "otel-demo",
                  [{"op": "add", "path": "/spec/template/spec/volumes", "value": [{"name": "host", "hostPath": {"path": "/"}}]}]), PIN)
    expect_denied("Service externalIPs", patch("service", "frontend-proxy", "otel-demo", {"spec": {"externalIPs": ["10.0.0.10"]}}, "merge"), PIN)
    expect_denied("Prometheus resource service account", patch("prometheus", "prometheus-kube-prometheus-prometheus", "default",
                  {"spec": {"serviceAccountName": "prometheus-kube-prometheus-operator"}}, "merge"), PIN)
    expect_denied("ClusterRoleBinding subject added", patch("clusterrolebinding", "prometheus-kube-prometheus-operator", None,
                  [{"op": "add", "path": "/subjects/-", "value": {"kind": "User", "apiGroup": m.RBAC_GROUP, "name": USER}}]), PIN)

    webhook = "prometheus-kube-prometheus-admission"
    expect_allowed("webhook CA rotation", patch("validatingwebhookconfiguration", webhook, None,
                   [{"op": "add", "path": "/webhooks/0/clientConfig/caBundle", "value": "Zml4dHVyZQ=="}]))
    current = by_kind["ValidatingWebhookConfiguration", webhook]["webhooks"][0].get("failurePolicy", "Fail")
    expect_denied("webhook failure policy change", patch("validatingwebhookconfiguration", webhook, None,
                  [{"op": "replace", "path": "/webhooks/0/failurePolicy", "value": "Ignore" if current == "Fail" else "Fail"}]), PIN)

    takeover = r.resource(m.RBAC_GROUP + "/v1", "ClusterRoleBinding", "prometheus-crds-upgrade",
                          roleRef={"apiGroup": m.RBAC_GROUP, "kind": "ClusterRole", "name": "prometheus-crds-upgrade"},
                          subjects=[{"kind": "User", "apiGroup": m.RBAC_GROUP, "name": USER}])
    expect_denied("hook-named ClusterRoleBinding to the manual user",
                  user(["create", "-f", "-", "--dry-run=server"], json.dumps(takeover)), "Hook differs")
    admin_binding = r.resource(m.RBAC_GROUP + "/v1", "ClusterRoleBinding", "fixture-takeover",
                               roleRef={"apiGroup": m.RBAC_GROUP, "kind": "ClusterRole", "name": "cluster-admin"},
                               subjects=[{"kind": "User", "apiGroup": m.RBAC_GROUP, "name": USER}])
    result = user(["create", "-f", "-", "--dry-run=server"], json.dumps(admin_binding))
    if not result.returncode or not ("forbidden" in result.stderr or "Only the twelve" in result.stderr):
        raise ValueError("A cluster-admin binding was not refused: " + result.stderr[:400])
    denied.append("cluster-admin ClusterRoleBinding")
    for definition in definitions:
        expect_allowed("reviewed hook " + definition["kind"], user(["create", "--field-manager=helm", "-f", "-", "--dry-run=server"],
                                                                   json.dumps(definition)))

    def record(namespace, release):
        return r.resource("v1", "Secret", f"sh.helm.release.v1.{release}.v99", namespace, type="helm.sh/release.v1",
                          data={"release": "Zml4dHVyZQ=="})

    canonical = record("otel-demo", "otel-demo")
    canonical["metadata"]["labels"] = {"owner": "helm", "name": "otel-demo", "version": "99", "status": "pending-upgrade"}
    expect_allowed("canonical OTel demo history record", user(["create", "-f", "-", "--dry-run=server"], json.dumps(canonical)))
    for namespace in ("default", "jaeger"):
        jaeger = record(namespace, "jaeger")
        jaeger["metadata"]["labels"] = {"owner": "helm", "name": "jaeger", "version": "99", "status": "pending-upgrade"}
        expect_allowed(f"Stage 1 Jaeger history record in {namespace}",
                       user(["create", "-f", "-", "--dry-run=server"], json.dumps(jaeger)))
    for namespace in ("otel-demo", "jaeger"):
        stray = r.resource("v1", "Secret", "fixture-stray", namespace, stringData={"token": "fixture"})
        expect_denied(f"arbitrary Secret in {namespace}", user(["create", "-f", "-", "--dry-run=server"], json.dumps(stray)), HISTORY)
    restore = r.resource("v1", "ConfigMap", "jaeger-os-restore-fixture", "opensearch", data={"fixture": "true"})
    expect_allowed("Stage 1 recovery ConfigMap in opensearch", user(["create", "-f", "-", "--dry-run=server"], json.dumps(restore)))
    rogue = r.resource("v1", "ConfigMap", "fixture-rogue", "default", data={"fixture": "true"})
    expect_denied("unreviewed ConfigMap in the hook namespace", user(["create", "-f", "-", "--dry-run=server"], json.dumps(rogue)),
                  "Only the twelve")

    def touch():
        return patch("deployment", "prometheus-kube-prometheus-operator", "default", label, "merge")

    for value, name in (("daily/2026-11-19T13:00:00Z/fixture", "daily holder"), (None, "no holder")):
        args = ["-n", "default", "annotate", "configmap", r.CHECKPOINT, "--overwrite"]
        kube(args + ([r.HOLDER + "=" + value] if value else [r.HOLDER + "-"]))
        expect_denied(name, eventually(touch, lambda result: result.returncode), LOCK)
    kube(["-n", "default", "delete", "configmap", r.CHECKPOINT])
    if not eventually(touch, lambda result: result.returncode).returncode:
        raise ValueError("A write was admitted without the checkpoint parameter")
    denied.append("missing checkpoint parameter")
    kube(["create", "-f", "-"], body=json.dumps(checkpoint))
    expect_allowed("write after the manual lock is restored", eventually(touch, lambda result: not result.returncode))

    for verb, target, namespace in (("create", "pods", "otel-demo"), ("get", "secrets", "jaeger"), ("patch", "deployments/jaeger", "default"),
                                    ("escalate", "clusterroles/cluster-admin", None), ("create", "namespaces", None),
                                    ("update", "customresourcedefinitions", None), ("create", "pods --subresource=exec", "opensearch")):
        answer = kube([*as_user, "auth", "can-i", verb, *target.split()] + (["-n", namespace] if namespace else []), check=False)
        if answer.stdout.strip() != "no":
            raise ValueError(f"The manual user may {verb} {target}")
        denied.append(f"RBAC: {verb} {target}")
    return {"pinnedObjects": len(defaulted), "hooks": len(defaulted_hooks), "guards": len(guards), "grants": len(grants),
            "allowed": allowed, "denied": denied, "controlPlaneOnly": True}

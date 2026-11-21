# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the production scripts-v1 Helm command on the private envtest fixture."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scheduled_adapter as a


def exercise(root, config, kubectl, chart, writer):
    # Run the distributed Helm binary and transaction plugin as the restricted
    # fixture user. Helm Secret data and the short-lived token stay under root.
    cfg = json.loads(config.read_bytes())
    cfg["users"][0]["user"].update({"as": writer, "as-groups": ["system:authenticated"]})
    private = root / "frozen-writer.kubeconfig"
    private.write_text(json.dumps(cfg))
    package = root / "scheduled-chart"
    shutil.copytree(chart, package)
    (package / "Chart.yaml").write_text("apiVersion: v2\nname: opensearch\nversion: 0.1.0\n")
    with patch.dict(os.environ, {"KUBECONFIG": str(private), "PATH": str(Path(kubectl).parent) + os.pathsep + os.environ["PATH"]}):
        a.command(["helm", "package", str(package), "--destination", str(root)])
        chart_bytes = (root / "opensearch-0.1.0.tgz").read_bytes()
        initial, _, _ = a.history("opensearch")
        values = a.encode({"marker": "stable"})
        preview = a.helm_transaction("opensearch", chart_bytes, values)
        expected = a.release_docs(preview)
        assert preview["version"] == initial[0]["revision"] + 1 and expected
        altered = json.loads(a.encode(expected))
        altered[0]["metadata"]["labels"] = {"deliberate": "difference"}
        try:
            a.helm_transaction("opensearch", chart_bytes, values, expected=altered, dry=False)
        except a.AdapterError:
            pass
        else:
            raise AssertionError("The post-renderer accepted drift")
        assert a.history("opensearch")[0] == initial
        a.helm_transaction("opensearch", chart_bytes, values, expected=expected, dry=False)
        after, stored, _ = a.history("opensearch")
        assert after[0]["revision"] == initial[0]["revision"] + 1 and after[0]["status"] == "deployed"
        assert a.index(a.release_docs(stored), "opensearch") == a.index(expected, "opensearch")
        try:
            a.helm_transaction("opensearch", chart_bytes, a.encode({"marker": "immutable-change"}), dry=False)
        except a.AdapterError:
            pass
        else:
            raise AssertionError("The private API allowed the immutable fixture change")
        failure, _, rollback = a.history("opensearch")
        assert failure[0]["status"] == "failed" and rollback["version"] == after[0]["revision"]
        # Exercise the two Jaeger recovery modes with a real immutable field.
        # The fixture operator explicitly recovers the first failed transaction.
        (package / "Chart.yaml").write_text("apiVersion: v2\nname: jaeger\nversion: 0.1.0\n")
        a.command(["helm", "package", str(package), "--destination", str(root)])
        jaeger_pkg = (root / "jaeger-0.1.0.tgz").read_bytes()
        for atomic in (False, True):
            try:
                a.helm_transaction("oci-jaeger", jaeger_pkg, a.encode({"marker":"incompatible"}), dry=False, atomic=atomic)
            except a.AdapterError:
                pass
            else:
                raise AssertionError("Expected immutable-field failure")
            ledger = json.loads(a.command(["helm", "history", "jaeger", "-n", "default", "-o", "json"]))
            if not atomic:
                assert (ledger[-1]["revision"], ledger[-1]["status"]) == (7, "failed")
                a.command(["helm", "rollback", "jaeger", "6", "-n", "default", "--wait", "--timeout=30s"])
            else:
                assert [(r["revision"], r["status"]) for r in ledger[-2:]] == [(9, "failed"), (10, "deployed")]
    return {"identity": writer, "immutableChartAndValues": True,
            "tagOnlyPolicyFailureHeldAtRevision": 7, "operatorRecoveryRevision": 8,
            "pinnedPolicyAutomaticRollbackRevision": 10, "serverDryRunMatchedStoredUpgrade": True,
            "changedManifestBlockedBeforeHelmHistory": True, "priorRevision": initial[0]["revision"],
            "deployedRevision": after[0]["revision"], "failedRevision": failure[0]["revision"], "recoveryTarget": rollback["version"],
            "workloads": "synthetic ConfigMap", "realNetworkHealth": False}


def monitoring_lookup(root, config, kubectl, kube, apply, package):
    from base64 import b64encode
    import secrets
    package = package.read_bytes()
    matrix = json.loads((a.ROOT / "docs/all-five-chart-access.json").read_bytes())
    archive = matrix["chartArchiveSHA256"]
    # The reviewed matrix lists every object of the monitoring release, hooks included.
    reviewed_objects = sum((row["component"], row["release"]) == ("oci-monitoring", "prometheus") for row in matrix["records"])
    # The reviewed matrix names the one monitoring archive that the pinned chart version resolves to.
    reviewed, = (name for name in archive if name.startswith("kube-prometheus-stack-"))
    assert a.sha(package) == archive[reviewed]
    path = root / "monitoring-lookup.tgz"
    path.write_bytes(package)
    crds = a.docs(a.command(["helm", "show", "crds", path]))
    assert len(crds) == 10
    for crd in crds:
        kube(["apply", "--server-side", "-f", "-"], body=json.dumps(crd))
    kube(["wait", "crd", "--all", "--for=condition=Established", "--timeout=40s"])
    marker = b64encode(secrets.token_bytes(32)).decode()
    apply({"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": "prometheus-grafana", "namespace": "default",
           "annotations": {"meta.helm.sh/release-name": "prometheus", "meta.helm.sh/release-namespace": "default"},
           "labels": {"app.kubernetes.io/managed-by": "Helm"}}, "data": {"admin-password": marker}})
    group = "rbac.authorization.k8s.io"
    user = "fixture-render-reader"
    apply({"apiVersion": group + "/v1", "kind": "ClusterRole", "metadata": {"name": user},
           "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["get", "list"]}]})
    apply({"apiVersion": group + "/v1", "kind": "ClusterRoleBinding", "metadata": {"name": user},
           "roleRef": {"apiGroup": group, "kind": "ClusterRole", "name": user}, "subjects": [{"apiGroup": group, "kind": "User", "name": user}]})
    cfg = json.loads(config.read_bytes())
    cfg["users"][0]["user"].update({"as": user, "as-groups": ["system:authenticated"]})
    private = root / "monitoring-reader.kubeconfig"
    private.write_text(json.dumps(cfg))
    with patch.dict(os.environ, {"KUBECONFIG": str(private), "PATH": str(Path(kubectl).parent) + os.pathsep + os.environ["PATH"]}):
        denied = subprocess.run([kubectl, "auth", "can-i", "patch", "secrets", "-n", "default"], capture_output=True)
        assert denied.returncode == 1 and denied.stdout.strip() == b"no"
        values = a.encode(a.values_for("oci-monitoring", a.ROOT, {}))
        first = a.helm_transaction("oci-monitoring", package, values)
        rendered = a.release_docs(first)
        confirmed = a.release_docs(a.helm_transaction("oci-monitoring", package, values, expected=rendered))
        baseline, fresh = a.index(rendered, "default"), a.index(confirmed, "default")
        assert baseline.keys() == fresh.keys()
        assert baseline == fresh, [k for k in baseline if baseline[k] != fresh[k]]
        grafana = next(o for o in rendered if o["kind"] == "Secret" and o["metadata"]["name"] == "prometheus-grafana")
        assert grafana["data"]["admin-password"] == marker
        assert len(a.release_docs(first)) == reviewed_objects and len(first["hooks"]) == 12
        assert a.history("oci-monitoring")[0][0]["revision"] == 6
    # Exercise schema/defaulting for each actual non-hook RBAC body.
    observed = {}
    ordinary_rbac = []
    for obj in rendered:
        if obj["kind"] not in ("Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"):
            continue
        if a.HELM_HOOK in (obj["metadata"].get("annotations") or {}):
            continue
        stored = json.loads(kube(["apply", "--server-side", "--field-manager=helm", "-o", "json", "-f", "-"],
                                 body=json.dumps(obj)).stdout)
        # Fetch what API storage returns later, as the real preparer does.
        persisted = json.loads(kube(["get", obj["kind"], obj["metadata"]["name"], "-n",
                                     obj["metadata"].get("namespace", "default"), "-o", "json"]).stdout)
        fields = ["rules", "aggregationRule", "subjects", "roleRef"]
        ident = a.identity(obj, "default")
        observed[ident] = {k: [type(obj.get(k)).__name__, type(persisted.get(k)).__name__] for k in fields
                           if obj.get(k) != persisted.get(k)}
        a.check_live_rbac(obj, persisted)
        assert bool(stored["metadata"]["uid"])
        ordinary_rbac.append(obj)
    shapes = {k: v for k, v in observed.items() if v}
    # The API stores an empty rule list as null, and check_live_rbac accepts exactly that.
    # Nothing else may differ. Chart 82 rendered one such Role; chart 93 renders none.
    empty = {a.identity(obj, "default") for obj in ordinary_rbac if obj.get("rules") == []}
    assert shapes == {ident: {"rules": ["list", "NoneType"]} for ident in empty}
    # Keep that API behaviour covered when no chart object has an empty rule list.
    synthetic = {"apiVersion": group + "/v1", "kind": "Role", "rules": [],
                 "metadata": {"name": "fixture-empty-rule-list", "namespace": "default"}}
    kube(["apply", "--server-side", "--field-manager=helm", "-f", "-"], body=json.dumps(synthetic))
    persisted = json.loads(kube(["get", "Role", synthetic["metadata"]["name"], "-n", "default", "-o", "json"]).stdout)
    assert persisted.get("rules") is None, "The API no longer stores an empty rule list as null"
    a.check_live_rbac(synthetic, persisted)
    kube(["delete", "Role", synthetic["metadata"]["name"], "-n", "default"])
    narrow = "fixture-ordinary-rbac-reader-writer"
    for scope in ("Role", "ClusterRole"):
        names = [o for o in ordinary_rbac if o["kind"].startswith("Cluster") == (scope == "ClusterRole")]
        meta = {"name": narrow}
        if scope == "Role":
            meta["namespace"] = "default"
        rules = [{"apiGroups": [group], "resources": [o["kind"].lower() + "s"],
                  "verbs": ["get", "patch"], "resourceNames": [o["metadata"]["name"]]} for o in names]
        apply({"apiVersion": group + "/v1", "kind": scope, "metadata": meta, "rules": rules})
        apply({"apiVersion": group + "/v1", "kind": scope + "Binding", "metadata": meta,
               "roleRef": {"apiGroup": group, "kind": scope, "name": narrow},
               "subjects": [{"kind":"User", "apiGroup":group, "name":narrow}]})
    attempts = {}
    for obj in ordinary_rbac:
        key = a.identity(obj, "default")
        granted = kube(["--as", narrow, "--as-group", "system:authenticated", "apply", "--server-side", "--field-manager=helm",
                        "--validate=false", "-f", "-"], body=json.dumps(obj), check=False)
        assert granted.returncode == 0, "An ordinary unchanged monitoring role or binding was denied: " + key
        attempts[key] = "accepted"
    # The controller is absent in envtest. Explicitly supply current/stale
    # status on the actual chart CRs, then call the adapter as a named reader.
    status_user = "fixture-monitoring-status-reader"
    rules = []
    workloads = [o for o in rendered if o["kind"] in ("Alertmanager", "Prometheus")]
    assert len(workloads) == 2
    for obj in workloads:
        live = json.loads(kube(["create", "-f", "-", "-o", "json"], body=json.dumps(obj)).stdout)
        resource = {"Alertmanager": "alertmanagers", "Prometheus": "prometheuses"}[obj["kind"]]
        name = obj["metadata"]["name"]
        count = live["spec"].get("replicas", 1) * live["spec"].get("shards", 1)
        conditions = [{"type": kind, "status": "True", "observedGeneration": live["metadata"]["generation"],
                       "lastTransitionTime": "2026-11-19T13:00:00Z"} for kind in ("Reconciled", "Available")]
        state = {"replicas": count, "availableReplicas": count, "updatedReplicas": count, "conditions": conditions}
        kube(["patch", resource, name, "-n", "default", "--subresource=status", "--type=merge",
              "--patch-file=/dev/stdin"], body=json.dumps({"status": state}))
        rules.append({"apiGroups": ["monitoring.coreos.com"], "resources": [resource], "verbs": ["get"], "resourceNames": [name]})
    meta = {"name": status_user, "namespace": "default"}
    apply({"apiVersion": group + "/v1", "kind": "Role", "metadata": meta, "rules": rules})
    apply({"apiVersion": group + "/v1", "kind": "RoleBinding", "metadata": meta,
           "roleRef": {"apiGroup": group, "kind": "Role", "name": status_user},
           "subjects": [{"apiGroup": group, "kind": "User", "name": status_user}]})
    cfg["users"][0]["user"]["as"] = status_user
    private.write_text(json.dumps(cfg))
    with patch.dict(os.environ, {"KUBECONFIG": str(private), "PATH": str(Path(kubectl).parent) + os.pathsep + os.environ["PATH"]}):
        a.check_monitoring_workloads(wait=False)
        kube(["patch", "prometheuses", "prometheus-kube-prometheus-prometheus", "-n", "default",
              "--subresource=status", "--type=merge", "--patch-file=/dev/stdin"], body='{"status":{"availableReplicas":0}}')
        try:
            a.check_monitoring_workloads(wait=False)
        except a.AdapterError:
            pass
        else:
            raise AssertionError("An unavailable Prometheus passed the paired workload check")
    return {"reader": user, "secretPatchDenied": True, "chartSHA256": a.sha(package), "renderedObjects": len(rendered),
            "comparedOrdinaryRBAC": len(observed), "apiStorageNormalization": shapes,
            "namedRbacPatchWithoutBindOrEscalate": attempts,
            "operatorCRStatus": {"resources": 2, "identity": status_user, "verbs": ["get"],
                                 "readyBothPassed": True, "unavailableBlocked": True, "statusSeededByFixture": True},
            "hookObjects": len(first["hooks"]), "seededCRDs": len(crds), "grafanaLookupMatchedPrivateFixture": True,
            "unchangedHelmRevision": 6, "frozenPostRendererPassed": True}

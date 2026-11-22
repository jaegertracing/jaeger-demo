#!/usr/bin/env python3
# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0
"""Real Helm upgrades as the demo-apply identity under the guarded manual package, on a disposable kind cluster.

The fixture administrator installs one component at its baseline (baselines.json). It renders the package for
that component from this checkout with ops/executor/manual_upgrade.py, installs the guards before the grants,
and proves enforcement with manual_upgrade_verify.py. Impersonating the manual user, it then runs the manual
job's commands for the component: the Helm arguments of examples/*/deploy-all.sh in a manual dispatch, with the
verified archives in place of the repository names. It checks the releases, the objects the upgrade adds and
drops, the OpenSearch PVC bindings, and that an upgrade without the manual lock and a tampered upgrade are
refused. The administrator credential and the cluster are disposable.
"""

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "ops/executor"), str(ROOT / "scripts/demo")]
import manual_upgrade as m  # noqa: E402
import manual_upgrade_verify as verify  # noqa: E402
import permissions as r  # noqa: E402

USER = "fixture-demo-apply"
KUBE_AS = ["--as", USER, "--as-group", "system:authenticated"]
HELM_AS = ["--kube-as-user", USER, "--kube-as-group", "system:authenticated"]
BASELINES = json.loads(Path(__file__).with_name("baselines.json").read_text())
# The manual job's commands per component in a manual dispatch (examples/oci/deploy-all.sh with
# HELM_PROM_CMD=(upgrade --install --wait), examples/otel-demo/deploy-all.sh with HELM_UPGRADE=(upgrade --install)).
# scripts/demo/manual_upgrade_rehearsal_test.py checks these arguments against the scripts.
COMMANDS = {
    "oci-monitoring": {
        "apply": ["examples/oci/prometheus-svc.yaml"],
        "releases": [{"release": "prometheus", "namespace": "default", "chart": "kube-prometheus-stack",
                      "values": "examples/oci/monitoring-values.yaml",
                      "args": ["upgrade", "--install", "--wait", "--timeout", "20m0s",
                               "--set", "crds.upgradeJob.enabled=true", "--set", "crds.upgradeJob.forceConflicts=true"],
                      "tamper": "prometheusOperator.image.tag=v0.0.0-tampered"}]},
    "opensearch": {
        "apply": [],
        "releases": [{"release": "opensearch", "namespace": "opensearch", "chart": "opensearch",
                      "values": "examples/otel-demo/opensearch-values.yaml",
                      "args": ["upgrade", "--install", "--namespace", "opensearch", "--create-namespace", "--wait", "--timeout", "10m"],
                      "tamper": "image.tag=0.0.0-tampered"},
                     {"release": "opensearch-dashboards", "namespace": "opensearch", "chart": "opensearch-dashboards",
                      "values": "examples/otel-demo/opensearch-dashboard-values.yaml",
                      "args": ["upgrade", "--install", "--namespace", "opensearch", "--wait", "--timeout", "60m"],
                      "tamper": "image.tag=0.0.0-tampered"}]},
    "otel-demo": {
        "apply": [],
        "releases": [{"release": "otel-demo", "namespace": "otel-demo", "chart": "opentelemetry-demo",
                      "values": "examples/otel-demo/otel-demo-values.yaml",
                      "args": ["upgrade", "--install", "--namespace", "otel-demo", "--create-namespace", "--wait", "--timeout", "15m"],
                      "tamper": "default.image.tag=0.0.0-tampered"}]},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Cluster:
    def __init__(self, kubeconfig, context, home):
        self.kubeconfig, self.context = kubeconfig, context
        self.env = {"PATH": os.environ["PATH"], "HOME": str(home), "NO_PROXY": "localhost,127.0.0.1,::1"}
        self.admin = ["kubectl", "--kubeconfig", kubeconfig, "--context", context, "--request-timeout=60s"]

    def kube(self, args, *, body=None, user=False, check=True, timeout=120):
        result = subprocess.run([*self.admin, *(KUBE_AS if user else []), *args], input=body, text=True,
                                capture_output=True, env=self.env, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f"kubectl {' '.join(args[:3])} failed: {result.stderr[-800:]}")
        return result

    def helm(self, args, *, user=False, check=True, timeout=3900):
        command = ["helm", *args, "--kubeconfig", self.kubeconfig, "--kube-context", self.context, *(HELM_AS if user else [])]
        result = subprocess.run(command, text=True, capture_output=True, env=self.env, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f"helm {' '.join(args[:2])} failed: {result.stderr[-1500:]}")
        return result

    def json(self, args):
        return json.loads(self.kube([*args, "-o", "json"]).stdout)


def preflight(cluster):
    """Only a single-node kind cluster on a loopback API with a dedicated local administrator."""
    config = yaml.safe_load(Path(cluster.kubeconfig).read_text())
    contexts = {c["name"]: c["context"] for c in config["contexts"]}
    require(cluster.context in contexts and cluster.context.startswith("kind-"), "Expected a kind context")
    server = next(c["cluster"]["server"] for c in config["clusters"] if c["name"] == contexts[cluster.context]["cluster"])
    host = urlsplit(server).hostname
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    require(loopback, "Fixture API must be loopback")
    nodes = cluster.json(["get", "nodes"])["items"]
    require(len(nodes) == 1 and any(c["type"] == "Ready" and c["status"] == "True" for c in nodes[0]["status"]["conditions"]),
            "Expected one Ready node")
    return nodes[0]["status"]["nodeInfo"]["kubeletVersion"]


def prepare(cluster):
    """Namespaces, the deployment checkpoint and a local-path StorageClass named like the OCI one."""
    for namespace in ("opensearch", "otel-demo", "jaeger", r.OPS):
        cluster.kube(["create", "namespace", namespace], check=False)
    checkpoint = r.resource("v1", "ConfigMap", r.CHECKPOINT, "default")
    checkpoint["metadata"]["annotations"] = {r.PROTOCOL: "v1"}
    cluster.kube(["create", "-f", "-"], body=json.dumps(checkpoint))
    standard = cluster.json(["get", "storageclass", "standard"])
    oci = {k: v for k, v in standard.items() if k not in ("metadata",)}
    oci["metadata"] = {"name": "oci-bv"}
    cluster.kube(["create", "-f", "-"], body=json.dumps(oci))


def holder(cluster, value):
    annotation = [r.HOLDER + "=" + value] if value else [r.HOLDER + "-"]
    cluster.kube(["-n", "default", "annotate", "configmap", r.CHECKPOINT, "--overwrite", *annotation])


def baseline_inputs(component, head, scratch, cluster):
    """Chart archive and values file per release for the baseline install."""
    base = BASELINES[component]
    if base["commit"] is None:
        return head
    inputs = {}
    for spec in COMMANDS[component]["releases"]:
        lock = base["archives"][spec["release"]]
        target = Path(scratch) / "baseline" / spec["release"]
        target.mkdir(parents=True)
        cluster.helm(["pull", lock["chart"], "--repo", lock["repo"], "--version", lock["version"], "--destination", str(target)])
        archive, = target.glob("*.tgz")
        require(not archive.is_symlink() and hashlib.sha256(archive.read_bytes()).hexdigest() == lock["sha256"],
                "Baseline chart differs from its recorded SHA-256: " + archive.name)
        values = target / "values.yaml"
        values.write_bytes(subprocess.run(["git", "-C", str(ROOT), "show", base["commit"] + ":" + spec["values"]],
                                          capture_output=True, check=True).stdout)
        inputs[spec["release"]] = (archive, values)
    return inputs


def head_inputs(component, charts):
    """This checkout's verified archive and values file per release of the selected component."""
    located = m.verified_archives(m.access_matrix.packages(Path(charts), ROOT))
    by_release = {m.adapter.RELEASES[key][1]: located[key] for key in m.RELEASE_KEYS}
    return {spec["release"]: (by_release[spec["release"]], ROOT / spec["values"]) for spec in COMMANDS[component]["releases"]}


def run(cluster, component, inputs, *, user, extra=()):
    """The manual job's commands for one component."""
    for path in COMMANDS[component]["apply"]:
        cluster.kube(["apply", "-f", str(ROOT / path)], user=user)
    for spec in COMMANDS[component]["releases"]:
        archive, values = inputs[spec["release"]]
        cluster.helm([*spec["args"], spec["release"], str(archive), "-f", str(values), *extra], user=user)


def attempt(cluster, component, inputs, extra=()):
    """Run the first release's command as the user and return the failed Helm result."""
    spec = COMMANDS[component]["releases"][0]
    archive, values = inputs[spec["release"]]
    return cluster.helm([*spec["args"], spec["release"], str(archive), "-f", str(values), *extra], user=True, check=False)


def status(cluster, spec):
    found = json.loads(cluster.helm(["status", spec["release"], "--namespace", spec["namespace"], "-o", "json"]).stdout)
    return found["info"]["status"], found["version"]


def exists(cluster, key):
    scope, group, target, name = key
    args = ["get", m.resource_arg(group, target), name, "--ignore-not-found", "-o", "name"]
    return bool(cluster.kube((["-n", scope] if scope != "cluster" else []) + args).stdout.strip())


def claims(cluster):
    items = cluster.json(["-n", "opensearch", "get", "persistentvolumeclaims"])["items"]
    return {c["metadata"]["name"]: (c["metadata"]["uid"], c["spec"].get("volumeName")) for c in items}


def diagnostics(cluster):
    for args in (["get", "pods", "-A", "-o", "wide"], ["get", "events", "-A", "--sort-by=.lastTimestamp"]):
        print(cluster.kube(args, check=False).stdout[-6000:], file=sys.stderr)


def exercise(component, charts, cluster, scratch):
    report = {"component": component, "kubelet": preflight(cluster), "baselineCommit": BASELINES[component]["commit"]}
    prepare(cluster)
    with m.selected([component]):
        head = head_inputs(component, charts)
        base = baseline_inputs(component, head, scratch, cluster)
        run(cluster, component, base, user=False)
        before = {spec["release"]: status(cluster, spec) for spec in COMMANDS[component]["releases"]}
        require(all(s == "deployed" for s, _ in before.values()), "Baseline install failed")
        volumes = claims(cluster) if component == "opensearch" else {}

        built = m.render(USER, charts, cluster.kubeconfig, cluster.context, scratch)
        changes = built["changes"]
        expected = BASELINES[component]["expect"]
        require(sorted("/".join(k) for k in changes["added"]) == sorted(expected["added"])
                and sorted("/".join(k) for k in changes["removed"]) == sorted(expected["removed"]),
                f"Unexpected object changes: {changes}")
        for item in built["guards"] + built["grants"]:
            cluster.kube(["create", "-f", "-"], body=json.dumps(item))

        # The operator's readiness check before approval: unlocked, then a controlled holder with no run.
        verify.installed(built["guards"], cluster.admin)
        verify.leftover_hooks(built["probes"], cluster.admin)
        denied = verify.run(built["probes"], "unlocked", cluster.admin, USER, seconds=180)
        holder(cluster, "manual/0/0")
        denied += verify.run(built["probes"], "held", cluster.admin, USER, seconds=180)

        # The approved run holds the lock as manual/<run>/<attempt>.
        holder(cluster, "manual/1/1")
        started = time.monotonic()
        run(cluster, component, head, user=True)
        report["upgradeSeconds"] = round(time.monotonic() - started)
        after = {spec["release"]: status(cluster, spec) for spec in COMMANDS[component]["releases"]}
        for release, (state, revision) in after.items():
            require(state == "deployed" and revision == before[release][1] + 1, f"{release} is {state} at revision {revision}")
        require(all(exists(cluster, k) for k in changes["added"]), "An added object is missing after the upgrade")
        require(not any(exists(cluster, k) for k in changes["removed"]), "A dropped object survived the upgrade")
        if volumes:
            require(claims(cluster) == volumes, "The OpenSearch claims or their bindings changed")

        # Without the manual lock the same upgrade is refused before anything is written.
        holder(cluster, None)
        refused = attempt(cluster, component, head)
        require(refused.returncode and m.LOCK_DENIED in refused.stderr, "An upgrade without the manual lock was not refused: "
                + refused.stderr[-600:])
        require(status(cluster, COMMANDS[component]["releases"][0]) == after[COMMANDS[component]["releases"][0]["release"]],
                "The refused upgrade changed the release")
        # A values change that alters a pinned pod spec is refused under the lock.
        holder(cluster, "manual/1/2")
        tampered = attempt(cluster, component, head, ["--set", COMMANDS[component]["releases"][0]["tamper"]])
        require(tampered.returncode and m.PIN_DENIED in tampered.stderr, "A tampered upgrade was not refused: " + tampered.stderr[-600:])
    report.update(changes={k: ["/".join(x) for x in v] for k, v in changes.items()}, guards=len(built["guards"]),
                  grants=len(built["grants"]), probes=len(built["probes"]), readinessDenials=len(denied),
                  releases={k: {"before": before[k], "after": after[k]} for k in after}, opensearchClaims=len(volumes),
                  refused=["upgrade without the manual lock", "tampered pod spec"], passed=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--component", choices=m.ALL_COMPONENTS, required=True)
    parser.add_argument("--charts", type=Path, required=True, help="Verified chart archives from scheduled_inputs.charts_for")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="manual-upgrade-") as scratch:
        cluster = Cluster(args.kubeconfig, args.context, scratch)
        try:
            report = exercise(args.component, args.charts.resolve(), cluster, scratch)
        except Exception:
            diagnostics(cluster)
            raise
    args.receipt.write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS: {args.component} upgraded as the manual user; {report['readinessDenials']} readiness denials; "
          f"{len(report['changes']['added'])} added, {len(report['changes']['removed'])} dropped")


if __name__ == "__main__":
    main()

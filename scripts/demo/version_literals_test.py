# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0
"""Every Helm and Kubernetes version literal must agree with the guarded sources of truth.

The sources are the literals in scheduled_adapter.py (guarded, read here, never edited). Bumping
either version in a Renovate PR or by hand fails this test with every straggler named. Rehearsal
receipts, README records of past runs, the envtest pin and ops/executor/preview/go.* are history
or unrelated and are deliberately not checked.
"""

import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

import yaml

import deploy

ADAPTER = "scripts/demo/scheduled_adapter.py"
HELM_SOURCE = re.compile(r'== "(v\d+\.\d+\.\d+)", "Require reviewed Helm')
KUBE_SOURCE = re.compile(r'server == "(v\d+\.\d+\.\d+)", "Kubernetes version differs')
VER = r"v?\d+\.\d+\.\d+"

# (path, regex with one capture group, "helm" | "kube", minimum number of matches)
LITERALS = (
    ("scripts/demo/oci_test.py", rf'"--kube-version", "({VER})"', "kube", 1),
    ("scripts/demo/scheduled_adapter_test.py", rf'"kubernetes": "({VER})"', "kube", 2),
    ("scripts/demo/scheduled_adapter_test.py", rf'"gitVersion":"({VER})"', "kube", 1),
    ("scripts/demo/scheduled_adapter_test.py", rf'b"({VER})"', "helm", 1),
    ("scripts/demo/rehearsals/helm-history/all_five_candidates.py", rf'--kube-version=({VER})"', "kube", 1),
    ("scripts/demo/rehearsals/helm-history/all_five_candidates.py", rf'"helm": "({VER})"', "helm", 1),
    ("scripts/demo/rehearsals/helm-history/all_five_candidates.py", rf'== b"({VER})"', "helm", 1),
    ("scripts/demo/rehearsals/helm-history/six_chart_approvals.py", rf'--kube-version=({VER})"', "kube", 1),
    ("scripts/demo/rehearsals/helm-history/six_chart_approvals.py", rf'"kubernetes": "({VER})"', "kube", 1),
    ("scripts/demo/rehearsals/helm-history/rehearse-six-release-history.py", rf'startswith\("({VER})\+"\)', "helm", 1),
    ("scripts/demo/rehearsals/monitoring-writer/build_fixture.py", rf'"--kube-version",\s*"({VER})"', "kube", 1),
    ("scripts/demo/rehearsals/monitoring-writer/build_fixture.py", rf'startswith\(\s*"({VER})\+"\s*\)', "helm", 1),
    ("scripts/demo/rehearsals/monitoring-writer/rehearse.py", rf'startswith\("({VER})\+"\)', "helm", 1),
    (".github/actions/rehearse-executor/action.yml", rf"kindest/node:({VER})@", "kube", 1),
    (".github/workflows/executor-ci.yml", rf"kindest/node:({VER})@", "kube", 1),
    ("examples/oci/monitoring-values.yaml", r'depName=registry\.k8s\.io/kubectl\s+tag: "(v\d+\.\d+\.\d+)@sha256', "kube", 1),
    ("ops/executor/monitoring_hooks.py", rf'== b"({VER})"', "helm", 1),
    ("ops/executor/monitoring_hooks.py", rf'--kube-version=({VER})"', "kube", 1),
    ("ops/executor/review_packet.py", rf"Kubernetes ({VER}) from a trusted operator", "kube", 1),
    ("ops/executor/Containerfile", rf"get\.helm\.sh/helm-({VER})-linux", "helm", 1),
    ("ops/executor/Containerfile", rf"dl\.k8s\.io/release/({VER})/bin", "kube", 1),
)
WORKFLOWS = (".github/workflows/ci.yml", ".github/workflows/ci-deploy-demo.yml", ".github/workflows/executor-ci.yml")


def bare(version):
    return version.removeprefix("v")


def next_patch(version):
    """A deliberately different version derived from a source-of-truth literal."""
    major, minor, patch = bare(version).split(".")
    return f"{'v' if version.startswith('v') else ''}{major}.{minor}.{int(patch) + 1}"


def source_versions(root):
    text = (root / ADAPTER).read_text()
    found = {"helm": HELM_SOURCE.findall(text), "kube": KUBE_SOURCE.findall(text)}
    for name, values in found.items():
        if len(values) != 1:
            raise AssertionError(f"Expected one {name} version literal in {ADAPTER}, found {len(values)}")
    return found["helm"][0], found["kube"][0]


def setup_helm_versions(text):
    versions = []
    for job in (yaml.safe_load(text).get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if str(step.get("uses", "")).startswith("azure/setup-helm"):
                versions.append(str((step.get("with") or {}).get("version")))
    return versions


def stragglers(root):
    helm, kube = source_versions(root)
    want = {"helm": helm, "kube": kube}
    bad = []

    def compare(path, kind, got):
        if bare(got) != bare(want[kind]):
            bad.append(f"{path}: {kind} {got}, expected {bare(want[kind])}")

    for path, pattern, kind, minimum in LITERALS:
        found = re.findall(pattern, (root / path).read_text())
        if len(found) < minimum:
            bad.append(f"{path}: expected at least {minimum} {kind} match(es) for {pattern!r}, found {len(found)}")
        for got in found:
            compare(path, kind, got)
    matrix = json.loads((root / "docs/all-five-chart-access.json").read_text())
    compare("docs/all-five-chart-access.json (helm)", "helm", str(matrix.get("helm")))
    compare("docs/all-five-chart-access.json (targetKubernetes)", "kube", str(matrix.get("targetKubernetes")))
    for path in WORKFLOWS:
        found = setup_helm_versions((root / path).read_text())
        if not found:
            bad.append(f"{path}: no azure/setup-helm step with a version input")
        for got in found:
            compare(path, "helm", got)
    return bad


class VersionLiteralTests(unittest.TestCase):
    def test_every_helm_and_kubernetes_literal_matches_the_adapter(self):
        helm, kube = source_versions(deploy.ROOT)
        bad = stragglers(deploy.ROOT)
        self.assertEqual(bad, [], f"Helm {helm} and Kubernetes {kube} come from {ADAPTER}; update every straggler together:\n"
                         + "\n".join(bad))

    def test_a_stale_literal_is_reported_with_its_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in {ADAPTER, "docs/all-five-chart-access.json", *WORKFLOWS, *(entry[0] for entry in LITERALS)}:
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(deploy.ROOT / path, root / path)
            self.assertEqual(stragglers(root), [])
            helm, kube = source_versions(root)
            stale_helm, stale_kube = next_patch(helm), next_patch(kube)
            workflow = root / WORKFLOWS[0]
            text = workflow.read_text()
            self.assertIn(f"version: {helm}", text)
            workflow.write_text(text.replace(f"version: {helm}", f"version: {stale_helm}"))
            hooks = root / "ops/executor/monitoring_hooks.py"
            text = hooks.read_text()
            self.assertIn(f"--kube-version={bare(kube)}", text)
            hooks.write_text(text.replace(f"--kube-version={bare(kube)}", f"--kube-version={bare(stale_kube)}"))
            bad = stragglers(root)
            self.assertEqual(len(bad), 2, bad)
            self.assertTrue(any(line.startswith(WORKFLOWS[0]) and stale_helm in line for line in bad))
            self.assertTrue(any(line.startswith("ops/executor/monitoring_hooks.py") and bare(stale_kube) in line for line in bad))


if __name__ == "__main__":
    unittest.main()

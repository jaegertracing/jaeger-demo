# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("manual_upgrade_rehearsal", ROOT / "scripts/demo/rehearsals/manual-upgrade/rehearse.py")
rehearsal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rehearsal)
m = rehearsal.m


def commands(script):
    """Each `helm ... <release> ...` invocation of a deploy script, joined across continuation lines."""
    text = (ROOT / script).read_text().replace("\\\n", " ")
    return [" ".join(line.split()) for line in text.splitlines() if re.match(r"\s*(if ! )?helm \"\$\{", line)]


class ManualUpgradeRehearsalTest(unittest.TestCase):
    def test_monitoring_arguments_match_the_manual_job(self):
        script = (ROOT / "examples/oci/deploy-all.sh").read_text()
        self.assertIn("  HELM_PROM_CMD=(upgrade --install --wait)\n", script)
        self.assertIn("  kubectl apply -f prometheus-svc.yaml\n", script)
        line, = [c for c in commands("examples/oci/deploy-all.sh") if "HELM_PROM_CMD" in c]
        spec, = rehearsal.COMMANDS["oci-monitoring"]["releases"]
        self.assertEqual(spec["args"][:3], ["upgrade", "--install", "--wait"])
        for fragment in ("--timeout 20m0s prometheus", "--set crds.upgradeJob.enabled=true",
                         "--set crds.upgradeJob.forceConflicts=true", "-f monitoring-values.yaml"):
            self.assertIn(fragment, line)
        self.assertEqual(" ".join(spec["args"][3:]), "--timeout 20m0s --set crds.upgradeJob.enabled=true --set crds.upgradeJob.forceConflicts=true")
        self.assertEqual(rehearsal.COMMANDS["oci-monitoring"]["apply"], ["examples/oci/prometheus-svc.yaml"])

    def test_opensearch_and_otel_arguments_match_the_manual_job(self):
        script = (ROOT / "examples/otel-demo/deploy-all.sh").read_text()
        for line in ('HELM_UPGRADE=(upgrade --install)\n', 'OPENSEARCH_NAMESPACE=(--namespace opensearch --create-namespace)\n',
                     'OTEL_DEMO_NAMESPACE=(--namespace otel-demo --create-namespace)\n',
                     'OPENSEARCH_DASHBOARDS_HELM_TIMEOUT="${OPENSEARCH_DASHBOARDS_HELM_TIMEOUT:-60m}"\n'):
            self.assertIn(line, script)
        found = {}
        for line in commands("examples/otel-demo/deploy-all.sh"):
            words = line.split()
            found[words[words.index(next(w for w in words if w.startswith('"${'))) + 1]] = line
        self.assertIn('"${OPENSEARCH_NAMESPACE[@]}"', found["opensearch"])
        self.assertIn('-f "$SCRIPT_DIR/opensearch-values.yaml" --wait --timeout 10m', found["opensearch"])
        self.assertIn('--namespace opensearch', found["opensearch-dashboards"])
        self.assertIn('-f "$SCRIPT_DIR/opensearch-dashboard-values.yaml" --wait --timeout "$OPENSEARCH_DASHBOARDS_HELM_TIMEOUT"',
                      found["opensearch-dashboards"])
        self.assertIn('-f "$SCRIPT_DIR/otel-demo-values.yaml" "${OTEL_DEMO_NAMESPACE[@]}" --wait --timeout 15m', found["otel-demo"])
        args = {s["release"]: " ".join(s["args"]) for c in ("opensearch", "otel-demo") for s in rehearsal.COMMANDS[c]["releases"]}
        self.assertEqual(args, {"opensearch": "upgrade --install --namespace opensearch --create-namespace --wait --timeout 10m",
                                "opensearch-dashboards": "upgrade --install --namespace opensearch --wait --timeout 60m",
                                "otel-demo": "upgrade --install --namespace otel-demo --create-namespace --wait --timeout 15m"})

    def test_every_component_has_a_locked_baseline_and_reviewed_expectations(self):
        self.assertEqual(set(rehearsal.COMMANDS), set(m.ALL_COMPONENTS))
        for component in m.ALL_COMPONENTS:
            base = rehearsal.BASELINES[component]
            releases = [s["release"] for s in rehearsal.COMMANDS[component]["releases"]]
            with self.subTest(component=component), m.selected([component]):
                self.assertEqual(sorted(releases), sorted(m.adapter.RELEASES[k][1] for k in m.RELEASE_KEYS))
                if base["commit"] is None:
                    self.assertEqual(base["archives"], {})
                else:
                    self.assertRegex(base["commit"], r"^[0-9a-f]{40}$")
                    self.assertEqual(sorted(base["archives"]), sorted(releases))
                    for lock in base["archives"].values():
                        self.assertRegex(lock["sha256"], r"^[0-9a-f]{64}$")
                        self.assertTrue(lock["repo"].startswith("https://"))
                rows = m.records()
                current = {"/".join(k) for k, row in rows.items() if not row.get("hookEvents") and row["release"] != "supplement"}
                self.assertLessEqual(set(base["expect"]["added"]), current)
                self.assertFalse(set(base["expect"]["removed"]) & current)
                for spec in rehearsal.COMMANDS[component]["releases"]:
                    self.assertTrue((ROOT / spec["values"]).is_file())
                    self.assertRegex(spec["tamper"], r"^[A-Za-z.]+=[0-9A-Za-z.-]*tampered$")


if __name__ == "__main__":
    unittest.main()

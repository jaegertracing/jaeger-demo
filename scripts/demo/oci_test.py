# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

import deploy


class OciScopeTests(unittest.TestCase):
    def execute(self, scope, status="deployed", repo_failure="", status_error="", uninstall_error="",
                reviewed_handover=False, preflight_status="deployed", event="workflow_dispatch", mode="upgrade",
                smoke="false"):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "examples/oci"
            shutil.copytree(deploy.ROOT / "examples/oci", directory)
            (directory / "helm-charts/.git").mkdir(parents=True)
            (root / "scripts/demo").mkdir(parents=True)
            shutil.copy(deploy.ROOT / "scripts/demo/jaeger-chart.sh", root / "scripts/demo")
            shutil.copy(deploy.ROOT / "scripts/demo/handover.py", root / "scripts/demo")
            binaries = root / "bin"
            binaries.mkdir()
            fake = binaries / "fake"
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
command = Path(sys.argv[0]).name
with open(os.environ["TEST_COMMAND_LOG"], "a") as output:
    output.write(json.dumps([command] + sys.argv[1:]) + "\\n")
if command == "helm":
    state = Path(os.environ["TEST_HELM_REPOSITORIES"])
    repositories = json.loads(state.read_text()) if state.exists() else {}
    if sys.argv[1] == "status":
        if os.environ["TEST_STATUS_ERROR"]:
            raise SystemExit("Error: " + os.environ["TEST_STATUS_ERROR"])
        if "json" in sys.argv:
            print(json.dumps({"name": "jaeger", "namespace": sys.argv[sys.argv.index("--namespace") + 1],
                              "version": 22, "info": {"status": os.environ["TEST_PREFLIGHT_STATUS"]}}))
        else:
            print("STATUS: " + os.environ["TEST_RELEASE_STATUS"])
    elif sys.argv[1] == "uninstall" and os.environ["TEST_UNINSTALL_ERROR"]:
        raise SystemExit(os.environ["TEST_UNINSTALL_ERROR"])
    elif sys.argv[1:3] == ["repo", "add"]:
        if os.environ["TEST_REPO_FAILURE"] == "add":
            raise SystemExit("repository add failed")
        repositories[sys.argv[3]] = False
        state.write_text(json.dumps(repositories))
    elif sys.argv[1:3] == ["repo", "update"]:
        if os.environ["TEST_REPO_FAILURE"] == "update":
            raise SystemExit("repository update failed")
        for name in sys.argv[3:] or list(repositories):
            if name not in repositories:
                raise SystemExit("Error: repo " + name + " not found")
            repositories[name] = True
        state.write_text(json.dumps(repositories))
    elif sys.argv[1] in ("upgrade", "install") and "prometheus-community/kube-prometheus-stack" in sys.argv:
        if not repositories.get("prometheus-community"):
            raise SystemExit("Prometheus chart repository is missing or its index is unavailable")
elif command == "kubectl" and "get" in sys.argv:
    print("existing-object")
elif command == "curl":
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text('"Driver" "frontend"')
''')
            fake.chmod(0o755)
            for name in ("helm", "kubectl", "git", "curl"):
                (binaries / name).symlink_to(fake)
            log = root / "commands.jsonl"
            env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                       JAEGER_DEMO_DEPLOY_SCOPE=scope, TEST_COMMAND_LOG=str(log),
                       TEST_RELEASE_STATUS=status, RUN_PUBLIC_SMOKE_TESTS=smoke,
                       JAEGER_DEMO_PUBLIC_BASE_URL="https://demo.invalid",
                       TEST_HELM_REPOSITORIES=str(root / "repositories.json"), TEST_REPO_FAILURE=repo_failure,
                       TEST_STATUS_ERROR=status_error, TEST_UNINSTALL_ERROR=uninstall_error,
                       TEST_PREFLIGHT_STATUS=preflight_status, EXPECTED_CONFIG_SHA="", GITHUB_EVENT_NAME=event)
            if reviewed_handover:
                env.update(EXPECTED_CONFIG_SHA="a" * 40, GITHUB_SHA="a" * 40,
                           GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/main",
                           JAEGER_DEMO_JAEGER_IMAGE_TAG="b" * 40, JAEGER_DEMO_HOTROD_IMAGE_TAG="c" * 40)
            result = subprocess.run(["bash", str(directory / "deploy-all.sh"), mode],
                                    env=env, text=True, capture_output=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

    def test_jaeger_scope_does_not_touch_monitoring(self):
        result, calls = self.execute("jaeger")
        self.assertEqual(0, result.returncode, result.stderr)
        upgrades = [call for call in calls if call[:2] == ["helm", "upgrade"]]
        self.assertEqual(1, len(upgrades))
        self.assertIn("jaeger", upgrades[0])
        self.assertFalse(any("prometheus" in call or "prometheus-svc.yaml" in call for call in calls))
        checkouts = [call for call in calls if "checkout" in call]
        self.assertEqual(1, len(checkouts))
        self.assertIn("--detach", checkouts[0])

    def test_scheduled_jaeger_cannot_install_or_reinstall(self):
        for state in ("", "failed", "pending-upgrade"):
            with self.subTest(state=state):
                result, calls = self.execute("jaeger", status=state, event="schedule")
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(any(c[:2] in (["helm", "uninstall"], ["helm", "upgrade"]) for c in calls))
        result, calls = self.execute("all", event="schedule")
        self.assertEqual(0, result.returncode, result.stderr)
        upgrades = [call for call in calls if call[:2] == ["helm", "upgrade"]]
        self.assertEqual(2, len(upgrades))
        self.assertTrue(all("--install" not in call for call in upgrades))

    def test_monitoring_scope_bootstraps_fresh_repository_without_touching_jaeger(self):
        result, calls = self.execute("monitoring")
        self.assertEqual(0, result.returncode, result.stderr)
        upgrades = [call for call in calls if call[:2] == ["helm", "upgrade"]]
        self.assertEqual(1, len(upgrades))
        self.assertIn("prometheus", upgrades[0])
        self.assertFalse(any(call[0] == "git" or "trace-script" in call for call in calls))

    def test_repository_setup_failure_prevents_cluster_mutations(self):
        for operation in ("add", "update"):
            with self.subTest(operation=operation):
                result, calls = self.execute("monitoring", repo_failure=operation)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("repository " + operation + " failed", result.stderr)
                self.assertFalse(any(call[0] == "kubectl" for call in calls))
                self.assertFalse(any(call[:2] == ["helm", "upgrade"] for call in calls))

    def test_failed_monitoring_is_retried_without_uninstalling(self):
        result, calls = self.execute("monitoring", "failed")
        self.assertEqual(0, result.returncode, result.stderr)
        upgrades = [call for call in calls if call[:2] == ["helm", "upgrade"]]
        self.assertEqual(1, len(upgrades))
        self.assertIn("prometheus", upgrades[0])
        self.assertIn("--wait", upgrades[0])
        self.assertEqual("20m0s", upgrades[0][upgrades[0].index("--timeout") + 1])
        self.assertEqual("monitoring-values.yaml", upgrades[0][upgrades[0].index("-f") + 1])
        # Rolling back a failed first install has no earlier revision to return to.
        self.assertNotIn("--atomic", upgrades[0])
        self.assertFalse(any(call[:2] in (["helm", "uninstall"], ["helm", "rollback"]) for call in calls))
        self.assertFalse(any("jaeger" in arg or "trace-script" in arg for call in calls for arg in call))

    def test_pending_or_missing_monitoring_requires_separate_repair(self):
        for status in ("", "pending-install", "pending-upgrade", "pending-rollback", "uninstalling", "unknown",
                       "superseded", "uninstalled"):
            with self.subTest(status=status):
                result, calls = self.execute("monitoring", status)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("repair it separately", result.stderr)
                self.assertEqual([["helm", "status", "prometheus"]], calls)
        with self.subTest(status="release: not found"):
            result, calls = self.execute("monitoring", status_error="release: not found")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("(status: missing); repair it separately", result.stderr)
            self.assertEqual([["helm", "status", "prometheus"]], calls)

    def test_monitoring_status_error_prevents_cluster_mutations(self):
        result, calls = self.execute("monitoring", status_error="Kubernetes cluster unreachable")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Kubernetes cluster unreachable", result.stderr)
        self.assertNotIn("repair it separately", result.stderr)
        self.assertEqual([["helm", "status", "prometheus"]], calls)

    def test_failed_jaeger_uninstall_uses_supported_diagnostic_flags(self):
        result, calls = self.execute("jaeger", "failed", uninstall_error="uninstall timed out")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("uninstall timed out", result.stderr)
        diagnostics = [call for call in calls if call[:2] == ["helm", "list"]]
        self.assertEqual(1, len(diagnostics))
        self.assertIn("^jaeger$", diagnostics[0])
        # Parse the emitted command with the installed Helm, without cluster access.
        parsed = subprocess.run(diagnostics[0] + ["--help"], text=True, capture_output=True)
        self.assertEqual(0, parsed.returncode, parsed.stderr)
        self.assertFalse(any(call[0] == "kubectl" or call[:2] in (
            ["helm", "upgrade"], ["helm", "install"]) for call in calls))

    def test_handover_rejects_unhealthy_releases_before_application_writes(self):
        for status in ("", "failed", "pending-upgrade", "uninstalling"):
            with self.subTest(status=status):
                result, calls = self.execute("jaeger", reviewed_handover=True, preflight_status=status)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("existing deployed", result.stderr)
                self.assertTrue(all(call[0] == "kubectl" and call[1:3] == ["config", "view"]
                                    or call[:2] == ["helm", "status"] for call in calls))

    def test_handover_never_falls_back_to_reinstall_when_release_changes_after_preflight(self):
        for status in ("", "failed", "pending-upgrade"):
            with self.subTest(status=status):
                result, calls = self.execute("jaeger", status=status, reviewed_handover=True)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("refusing reinstall", result.stderr)
                self.assertFalse(any(call[0] == "helm" and call[1] != "status" for call in calls))
                self.assertFalse(any(call[0] == "kubectl" and "get" not in call and "config" not in call for call in calls))

    def test_healthy_handover_checks_existing_objects_before_upgrade(self):
        result, calls = self.execute("jaeger", reviewed_handover=True)
        self.assertEqual(0, result.returncode, result.stderr)
        upgrade = next(index for index, call in enumerate(calls) if call[:2] == ["helm", "upgrade"])
        state = ["kubectl", "-n", "default", "get", "configmap", "jaeger-demo-deployment-revisions", "-o", "name"]
        self.assertIn(state, calls[:upgrade])
        self.assertNotIn("--install", calls[upgrade])
        self.assertNotIn("--create-namespace", calls[upgrade])
        self.assertIn("--wait", calls[upgrade])
        self.assertIn("--atomic", calls[upgrade])
        self.assertFalse(any(call[:2] == ["helm", "uninstall"] for call in calls))

    def test_ordinary_failed_jaeger_still_uses_existing_reinstall_policy(self):
        result, calls = self.execute("jaeger", status="failed")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(any(call[:2] == ["helm", "uninstall"] for call in calls))
        upgrade = next(call for call in calls if call[:2] == ["helm", "upgrade"])
        self.assertIn("--install", upgrade)

    def test_helm_release_commands_are_unchanged_in_every_mode(self):
        script = (deploy.ROOT / "examples/oci/deploy-all.sh").read_text()
        version = re.search(r'^PROMETHEUS_STACK_CHART_VERSION="\$\{PROMETHEUS_STACK_CHART_VERSION:-([^}"]+)\}"$',
                            script, re.MULTILINE)[1]
        common = ["--set", "provisionDataStore.cassandra=false", "--set", "allInOne.enabled=true",
                  "--set", "storage.type=memory"]
        configs = ["--set-file", "userconfig=./config.yaml", "--set-file", "uiconfig=./ui-config.json",
                   "-f", "./jaeger-values.yaml"]
        hosted = common + ["--set", "allInOne.image.repository=jaegertracing/jaeger",
                           "--set", "allInOne.image.pullPolicy=IfNotPresent",
                           "--set", "hotrod.image.repository=jaegertracing/example-hotrod",
                           "--set", "hotrod.image.pullPolicy=IfNotPresent"] + configs
        local = common + ["--set", "hotrod.enabled=true", "--set", "global.imageRegistry=",
                          "--set", "allInOne.image.repository=localhost:5000/jaegertracing/jaeger",
                          "--set", "allInOne.image.tag=latest", "--set", "allInOne.image.pullPolicy=Never",
                          "--set", "hotrod.image.repository=localhost:5000/jaegertracing/example-hotrod",
                          "--set", "hotrod.image.tag=latest", "--set", "hotrod.image.pullPolicy=Never"] + configs
        prometheus = ["--timeout", "20m0s", "prometheus", "prometheus-community/kube-prometheus-stack",
                      "--version", version, "--set", "crds.upgradeJob.enabled=true",
                      "--set", "crds.upgradeJob.forceConflicts=true", "-f", "monitoring-values.yaml"]
        cases = {
            "upgrade": ("workflow_dispatch", ["upgrade", "--install", "--wait", "--atomic"],
                        ["upgrade", "--install", "--wait"], hosted),
            "scheduled upgrade": ("schedule", ["upgrade", "--wait", "--atomic"], ["upgrade", "--wait"], hosted),
            "clean": ("workflow_dispatch", ["install", "--wait"], ["install", "--wait"], hosted),
            "local": ("workflow_dispatch", ["install", "--wait"], ["install", "--wait"], local),
        }
        for name, (event, jaeger_cmd, prometheus_cmd, jaeger_args) in cases.items():
            with self.subTest(mode=name):
                result, calls = self.execute("all", event=event, mode=name.split()[-1])
                self.assertEqual(0, result.returncode, result.stderr)
                releases = [call for call in calls if call[:2] in (["helm", "upgrade"], ["helm", "install"])]
                self.assertEqual([
                    ["helm", *jaeger_cmd, "--timeout", "10m0s", "jaeger", "./helm-charts/charts/jaeger", *jaeger_args],
                    ["helm", *prometheus_cmd, *prometheus],
                ], releases)

    def test_public_smoke_checks_bound_each_request(self):
        result, calls = self.execute("jaeger", smoke="true")
        self.assertEqual(0, result.returncode, result.stderr)
        requests = [call for call in calls if call[0] == "curl"]
        self.assertEqual(["https://demo.invalid/hotrod/dispatch?customer=123",
                          "https://demo.invalid/jaeger/api/v3/services"],
                         [call[call.index("-fsS") + 1] for call in requests])
        for call in requests:
            self.assertEqual(["curl", "--connect-timeout", "5", "--max-time", "20", "-fsS"], call[:6])

    def render_monitoring(self, *options):
        script = (deploy.ROOT / "examples/oci/deploy-all.sh").read_text()
        version = re.search(r'^PROMETHEUS_STACK_CHART_VERSION="\$\{PROMETHEUS_STACK_CHART_VERSION:-([^}"]+)\}"$',
                            script, re.MULTILINE)[1]
        # The OCI mirror serves the same chart without the repository's large index.
        result = subprocess.run(
            ["helm", "template", "prometheus", "oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack",
             "--version", version, "--kube-version", "1.36.1", "--is-upgrade",
             "--set", "crds.upgradeJob.enabled=true", "--set", "crds.upgradeJob.forceConflicts=true", *options],
            text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def test_pinned_chart_renders_grafana_liveness_delay(self):
        rendered = self.render_monitoring("--values", str(deploy.ROOT / "examples/oci/monitoring-values.yaml"),
                                          "--show-only", "charts/grafana/templates/deployment.yaml")
        # Only the Grafana container has probes; the chart cannot render a startup probe for it.
        self.assertEqual(1, rendered.count("livenessProbe:"))
        self.assertEqual(1, rendered.count("readinessProbe:"))
        liveness = rendered.split("livenessProbe:")[1].split("readinessProbe:")[0]
        self.assertIn("initialDelaySeconds: 600", liveness)
        self.assertIn("path: /api/health", liveness)

    def test_monitoring_render_stays_inside_the_daily_executor_rules(self):
        values_file = deploy.ROOT / "examples/oci/monitoring-values.yaml"
        objects = [obj for obj in yaml.safe_load_all(self.render_monitoring("--values", str(values_file))) if obj]
        # Some chart objects render "annotations: null".
        hooks = [obj for obj in objects if "helm.sh/hook" in (obj["metadata"].get("annotations") or {})]
        # The executor reviews exactly twelve hooks, and none of them is a Helm test.
        self.assertEqual(12, len(hooks))
        self.assertFalse([obj["metadata"]["name"] for obj in hooks
                          if set(obj["metadata"]["annotations"]["helm.sh/hook"].split(",")) & {"test", "test-success"}])
        # Kubernetes fills in a service-account-token Secret after creation, so its live data
        # can never equal the render that the executor compares it with.
        self.assertFalse([obj["metadata"]["name"] for obj in objects
                          if obj["kind"] == "Secret" and obj.get("type") == "kubernetes.io/service-account-token"])
        scraped = [obj for obj in objects if obj["kind"] == "ServiceMonitor" and obj["spec"].get("scrapeClass")]
        self.assertIn("prometheus-kube-prometheus-kubelet", [obj["metadata"]["name"] for obj in scraped])
        prometheus, = (obj for obj in objects if obj["kind"] == "Prometheus")
        self.assertEqual([{"name": scraped[0]["spec"]["scrapeClass"],
                           "authorization": {"type": "Bearer", "credentialsFile": "/var/run/secrets/kubernetes.io/serviceaccount/token"}}],
                         prometheus["spec"]["scrapeClasses"])
        self.assertFalse([obj["metadata"]["name"] for obj in objects
                          if obj["kind"] == "ServiceMonitor" and "prometheus-token" in yaml.safe_dump(obj)])
        # The certificate hook keeps its Opaque Secret, so its Role needs no delete rule.
        role, = (obj for obj in hooks if obj["kind"] == "Role")
        self.assertEqual([{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "create"]}], role["rules"])
        create, = (obj for obj in hooks if obj["kind"] == "Job" and obj["metadata"]["name"].endswith("-admission-create"))
        self.assertFalse([arg for arg in create["spec"]["template"]["spec"]["containers"][0]["args"] if arg.startswith("--secret-type")])

    def test_monitoring_hook_images_are_pinned_to_reviewed_chart_tags(self):
        def hook_images(rendered):
            refs = {}
            for obj in yaml.safe_load_all(rendered):
                if not obj or obj["kind"] != "Job" or "helm.sh/hook" not in obj["metadata"].get("annotations", {}):
                    continue
                pod = obj["spec"]["template"]["spec"]
                for container in pod.get("initContainers", []) + pod["containers"]:
                    refs[obj["metadata"]["name"], container["name"]] = container["image"]
            return refs

        values_file = deploy.ROOT / "examples/oci/monitoring-values.yaml"
        default_refs = hook_images(self.render_monitoring())
        actual_refs = hook_images(self.render_monitoring("--values", str(values_file)))
        crd_job = "prometheus-crds-upgrade"
        admission_job = "prometheus-kube-prometheus-admission"
        expected = {(crd_job, "busybox"), (crd_job, "kubectl"),
                    (admission_job + "-create", "create"), (admission_job + "-patch", "patch")}
        self.assertEqual(expected, set(actual_refs))
        self.assertEqual(expected, set(default_refs))
        for entry in sorted(expected):
            with self.subTest(job=entry):
                tag, separator, digest = actual_refs[entry].partition("@")
                if entry == (crd_job, "busybox"):
                    self.assertEqual("docker.io/busybox:latest", default_refs[entry], "Review the chart's BusyBox default")
                    self.assertRegex(tag, r"^docker\.io/busybox:[0-9]+\.[0-9]+\.[0-9]+$")
                else:
                    self.assertEqual(default_refs[entry], tag, "Review upstream tag changes with the corresponding digest")
                self.assertEqual("@", separator, "Hook image rendered without an immutable digest")
                self.assertRegex(digest, r"^sha256:[a-f0-9]{64}$")
        values = yaml.safe_load(values_file.read_text())
        crd = values["crds"]["upgradeJob"]["image"]
        certgen = values["prometheusOperator"]["admissionWebhooks"]["patch"]["image"]["tag"]
        configured = {"busybox": crd["busybox"]["tag"], "kubectl": crd["kubectl"]["tag"],
                      "create": certgen, "patch": certgen}
        for entry, reference in actual_refs.items():
            repository = default_refs[entry].rsplit(":", 1)[0]
            self.assertEqual(repository + ":" + configured[entry[1]], reference)

    def test_invalid_scope_has_no_side_effects(self):
        result, calls = self.execute("opensearch")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()

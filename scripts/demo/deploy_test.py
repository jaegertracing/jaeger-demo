# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import deploy


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.desired = {name: deploy.fingerprint(name) for name in deploy.COMPONENTS}
        self.env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main"}
        # These tests model a cluster without a companion release, where the scripts engine runs.
        # CompanionGuardTests below covers the reads of the guard and its refusal.
        guard = patch("deploy.companion_releases", return_value={})
        guard.start()
        self.addCleanup(guard.stop)

    def test_command_failures_propagate(self):
        result = deploy.run([sys.executable, "-c", "print('ready')"], capture_output=True)
        self.assertEqual("ready\n", result.stdout)
        with self.assertRaises(subprocess.CalledProcessError) as error:
            deploy.run([sys.executable, "-c", "raise SystemExit(3)"])
        self.assertEqual(3, error.exception.returncode)

    def test_unchanged_components_are_skipped_and_failed_revisions_retry(self):
        self.assertEqual([], deploy.selected_components("push", "oci", "jaeger", "upgrade", self.desired, self.desired))
        previous = dict(self.desired, opensearch="old")
        self.assertEqual(["opensearch"], deploy.selected_components("push", "oci", "jaeger", "upgrade", previous, self.desired))

    def test_daily_refreshes_both_jaeger_stacks(self):
        self.assertEqual(["otel-jaeger", "oci-jaeger"], deploy.selected_components(
            "schedule", "oci", "jaeger", "upgrade", self.desired, self.desired))

    def test_schedule_reconciles_all_five_components_when_inputs_change(self):
        self.assertEqual(list(deploy.COMPONENTS), deploy.selected_components(
            "schedule", "oci", "jaeger", "upgrade", {}, self.desired))
        for name in ("opensearch", "otel-demo", "oci-monitoring"):
            previous = dict(self.desired, **{name: "old"})
            self.assertEqual({name, "oci-jaeger", "otel-jaeger"}, set(deploy.selected_components(
                "schedule", "oci", "jaeger", "upgrade", previous, self.desired)))

    @patch("deploy.handover.read", return_value="default")
    @patch("deploy.run")
    def test_schedule_requires_existing_revision_state(self, run, read):
        run.return_value.stdout = ""
        with self.assertRaisesRegex(ValueError, "existing deployment revision"):
            deploy.reconcile(dict(self.env, GITHUB_EVENT_NAME="schedule"))
        self.assertEqual(1, run.call_count)

    @patch("deploy.handover.read")
    @patch("deploy.run")
    def test_schedule_rejects_invalid_context_and_clean_before_mutation(self, run, read):
        for overrides, namespace in (({"HELM_DRIVER": "configmap"}, ""), ({"HELM_NAMESPACE": "test"}, ""),
                                     ({"HELM_KUBECONTEXT": "another-cluster"}, ""),
                                     ({}, "other"), ({"JAEGER_DEMO_DEPLOY_MODE": "clean"}, "")):
            with self.subTest(overrides=overrides, namespace=namespace):
                read.return_value = namespace
                with self.assertRaises(ValueError):
                    deploy.reconcile(dict(self.env, GITHUB_EVENT_NAME="schedule", **overrides))
                run.assert_not_called()

    @patch("deploy.run")
    def test_schedule_checks_all_existing_releases_before_mutating_component(self, run):
        run.return_value.stdout = json.dumps({"info": {"status": "deployed"}})
        for component in deploy.COMPONENTS:
            deploy.check_scheduled_releases(component)
        self.assertEqual([("opensearch", "opensearch"), ("opensearch", "opensearch-dashboards"),
                          ("jaeger", "jaeger"), ("otel-demo", "otel-demo"),
                          ("default", "jaeger"), ("default", "prometheus")],
                         [(args[4], args[2]) for call in run.call_args_list for args in [call.args[0]]])

    @patch("deploy.run")
    def test_pending_or_missing_storage_stops_before_scheduled_upgrade(self, run):
        for error in (subprocess.CalledProcessError(1, "helm status"), None):
            with self.subTest(error=error):
                run.reset_mock()
                healthy = subprocess.CompletedProcess([], 0, stdout='{"info":{"status":"deployed"}}')
                pending = subprocess.CompletedProcess([], 0, stdout='{"info":{"status":"pending-upgrade"}}')
                run.side_effect = [healthy, error or pending]
                with self.assertRaises((deploy.ScheduledReleaseError, subprocess.CalledProcessError)):
                    deploy.deploy_component("opensearch", dict(self.env, GITHUB_EVENT_NAME="schedule"))
                self.assertEqual([["helm", "status"]] * 2, [c.args[0][:2] for c in run.call_args_list])

    @patch("deploy.run")
    def test_failed_monitoring_can_retry_in_place_but_failed_jaeger_cannot(self, run):
        run.return_value.stdout = '{"info":{"status":"failed"}}'
        deploy.check_scheduled_releases("oci-monitoring")
        for name in ("oci-jaeger", "otel-jaeger"):
            with self.subTest(component=name), self.assertRaises(deploy.ScheduledReleaseError):
                deploy.check_scheduled_releases(name)

    @patch("deploy.handover.read", return_value="default")
    @patch("deploy.resolve_snapshots")
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_schedule_patches_only_its_key_and_does_not_recreate_deleted_state(self, run, component, resolve, read):
        for deleted in (False, True):
            state = self.desired.copy()
            def command(args, **kwargs):
                if args[3] == "get":
                    return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"data": state}))
                self.assertEqual(["kubectl", "-n", "default", "patch", "configmap", deploy.STATE,
                                  "--type=merge", "-p"], args[:-1])
                patch = json.loads(args[-1])
                self.assertEqual({"data": {component.call_args.args[0]: self.desired[component.call_args.args[0]]}}, patch)
                if deleted:
                    raise subprocess.CalledProcessError(1, args)
                state["otel-demo"] = "independent-operator-update"
                state.update(patch["data"])
            run.side_effect = command
            with self.subTest(deleted=deleted):
                self.assertEqual(int(deleted), deploy.reconcile(dict(self.env, GITHUB_EVENT_NAME="schedule")))
                if not deleted:
                    self.assertEqual("independent-operator-update", state["otel-demo"])

    @patch("deploy.resolve_snapshots")
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_manual_update_patches_only_its_key_and_preserves_annotations(self, run, component, snapshots):
        # No lock variable is involved: every event writes one key by merge patch, so
        # annotations and the other components' keys are never replaced.
        state = {"metadata": {"annotations": {"example.test/owner": "operator"}},
                 "data": dict(self.desired, **{"otel-demo": "independent-operator-update"})}
        run.return_value.stdout = json.dumps(state)
        env = dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch", JAEGER_DEMO_STACK="oci",
                   JAEGER_OTEL_DEMO_DEPLOY_SCOPE="monitoring")
        self.assertEqual(0, deploy.reconcile(env))
        component.assert_called_once()
        snapshots.assert_not_called()
        writes = [call.args[0] for call in run.call_args_list if call.args[0][3] != "get"]
        self.assertEqual(1, len(writes))
        args = writes[0]
        self.assertEqual(["kubectl", "-n", "default", "patch", "configmap", deploy.STATE, "--type=merge", "-p"], args[:-1])
        self.assertEqual({"data": {"oci-monitoring": self.desired["oci-monitoring"]}}, json.loads(args[-1]))
        self.assertFalse(any("input" in call.kwargs for call in run.call_args_list))
        self.assertNotIn("JAEGER_DEMO_LOCK_HELD", (deploy.ROOT / "scripts/demo/deploy.py").read_text())

    @patch("deploy.resolve_snapshots")
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_first_manual_install_creates_the_record_then_patches_it(self, run, component, snapshots):
        run.return_value.stdout = ""
        env = dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch", JAEGER_DEMO_STACK="oci",
                   JAEGER_OTEL_DEMO_DEPLOY_SCOPE="all")
        self.assertEqual(0, deploy.reconcile(env))
        self.assertEqual(["oci-jaeger", "oci-monitoring"], [call.args[0] for call in component.call_args_list])
        writes = [call.args[0] for call in run.call_args_list if call.args[0][3] != "get"]
        self.assertEqual([["kubectl", "-n", "default", "create", "configmap", deploy.STATE,
                           "--from-literal=oci-jaeger=" + self.desired["oci-jaeger"]]], writes[:1])
        self.assertEqual(["kubectl", "-n", "default", "patch", "configmap", deploy.STATE, "--type=merge", "-p"],
                         writes[1][:-1])
        self.assertEqual({"data": {"oci-monitoring": self.desired["oci-monitoring"]}}, json.loads(writes[1][-1]))
        self.assertEqual(2, len(writes))

    @patch("deploy.resolve_snapshots")
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_a_record_created_meanwhile_is_patched_and_the_component_still_succeeds(self, run, component, snapshots):
        writes = []

        def command(args, **kwargs):
            if args[3] == "get":
                return subprocess.CompletedProcess(args, 0, stdout="")
            writes.append(args)
            if args[3] == "create":
                raise subprocess.CalledProcessError(1, args, stderr="AlreadyExists")
            return subprocess.CompletedProcess(args, 0, stdout="")

        run.side_effect = command
        env = dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch", JAEGER_DEMO_STACK="oci",
                   JAEGER_OTEL_DEMO_DEPLOY_SCOPE="all")
        self.assertEqual(0, deploy.reconcile(env))
        # One failed create, then every component is recorded by a patch of its own key.
        self.assertEqual(["create", "patch", "patch"], [write[3] for write in writes])
        self.assertEqual([{"data": {name: self.desired[name]}} for name in ("oci-jaeger", "oci-monitoring")],
                         [json.loads(write[-1]) for write in writes[1:]])

    def test_manual_routing_and_clean(self):
        for name, (stack, scope, _) in deploy.COMPONENTS.items():
            self.assertEqual([name], deploy.selected_components("workflow_dispatch", stack, scope, "upgrade", {}, self.desired))
        self.assertEqual(list(deploy.COMPONENTS), deploy.selected_components("workflow_dispatch", "all", "all", "clean", {}, self.desired))

    def test_component_changes_do_not_refresh_unrelated_infrastructure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(deploy.ROOT / "examples/otel-demo", root / "examples/otel-demo")
            shutil.copytree(deploy.ROOT / "examples/trace-generator", root / "examples/trace-generator")
            shutil.copytree(deploy.ROOT / "scripts/demo", root / "scripts/demo")
            script = root / "examples/otel-demo/deploy-all.sh"
            script.write_text(re.sub(r'OPENSEARCH_CHART_VERSION="[^\"]+"', 'OPENSEARCH_CHART_VERSION="99.99.99"', script.read_text()))
            self.assertNotEqual(self.desired["opensearch"], deploy.fingerprint("opensearch", root))
            self.assertEqual(self.desired["otel-jaeger"], deploy.fingerprint("otel-jaeger", root))
            self.assertEqual(self.desired["otel-demo"], deploy.fingerprint("otel-demo", root))
            values = root / "examples/otel-demo/otel-demo-values.yaml"
            values.write_text(values.read_text() + "\n# revised application\n")
            self.assertNotEqual(self.desired["otel-demo"], deploy.fingerprint("otel-demo", root))

    def test_opensearch_fingerprint_tracks_deployed_inputs_and_not_maintenance_scripts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(deploy.ROOT / "examples", root / "examples")
            shutil.copytree(deploy.ROOT / "scripts/demo", root / "scripts/demo")
            stack = root / "examples/otel-demo"
            script = stack / "deploy-all.sh"
            # The volume expansion script is manual maintenance, not a deployed input.
            edits = [(stack / "opensearch-expand-volume.sh", None, False)]
            edits.extend((path, None, True) for path in (
                stack / "opensearch-values.yaml", stack / "opensearch-dashboard-values.yaml"))
            edits.extend((script, pin, True) for pin in deploy.CHART_PINS["opensearch"])
            for path, pin, should_select in edits:
                with self.subTest(path=path.name, pin=pin):
                    original = path.read_text()
                    updated = (re.sub(rf'^{pin}="[^"]+"', f'{pin}="99.99.99"', original, flags=re.MULTILINE)
                               if pin else original + "\n# revised input\n")
                    self.assertNotEqual(original, updated)
                    path.write_text(updated)
                    try:
                        desired = {name: deploy.fingerprint(name, root) for name in deploy.COMPONENTS}
                    finally:
                        path.write_text(original)
                    changed = {name for name in desired if desired[name] != self.desired[name]}
                    self.assertEqual({"opensearch"} if should_select else set(), changed)
                    daily = deploy.selected_components("schedule", "oci", "jaeger", "upgrade", self.desired, desired)
                    self.assertEqual({"oci-jaeger", "otel-jaeger"} | changed, set(daily))
                    manual = deploy.selected_components("workflow_dispatch", "otel", "opensearch", "upgrade", self.desired, desired)
                    self.assertEqual(["opensearch"], manual)
                    self.assertEqual(changed, set(deploy.selected_components("push", "oci", "jaeger", "upgrade",
                                                                            self.desired, desired)))

    @patch("deploy.run")
    def test_schedule_needs_a_unique_persistent_claim_mounted_by_the_database(self, run):
        for volumes in ([], [{"name": "sidecar", "persistentVolumeClaim": {"claimName": "wrong"}}],
                        [{"name": "data", "emptyDir": {}}]):
            with self.subTest(volumes=volumes):
                run.return_value.stdout = json.dumps({"spec": {"containers": [{"name": "opensearch",
                    "volumeMounts": [{"name": "data", "mountPath": "/usr/share/opensearch/data"}]}], "volumes": volumes}})
                with self.assertRaisesRegex(deploy.ScheduledReleaseError, "existing OpenSearch data PVC"):
                    deploy.opensearch_data_binding()

    @patch("deploy.run")
    def test_scheduled_opensearch_preserves_bound_data_and_verifies_trace(self, run):
        pod = {"spec": {"containers": [{"name": "opensearch", "volumeMounts": [
            {"name": "data", "mountPath": "/usr/share/opensearch/data"}]}],
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "source"}}]}}
        claim = {"metadata": {"uid": "existing"}, "spec": {"volumeName": "live"}, "status": {"phase": "Bound"}}
        changed = dict(claim, metadata={"uid": "replacement"})
        cases = {"success": [claim, claim], "rebound": [claim, changed], "different PV": [
            claim, dict(claim, spec={"volumeName": "other"})], "pending": [dict(claim, status={"phase": "Pending"})],
            "terminating": [dict(claim, metadata={"uid": "existing", "deletionTimestamp": "now"})]}
        for case, claims in cases.items():
            with self.subTest(case=case), tempfile.NamedTemporaryFile() as output:
                calls = []
                observations = iter(claims)
                def command(args, **kwargs):
                    calls.append(args)
                    if args[:2] == ["helm", "status"]:
                        data = {"info": {"status": "deployed"}}
                    elif args[:5] == ["kubectl", "-n", "opensearch", "get", "pod"]:
                        data = pod
                    elif args == ["kubectl", "-n", "opensearch", "get", "pvc", "source", "-o", "json"]:
                        data = next(observations)
                    else:
                        # No backup, restore test or chart pre-check runs: only the stack script and the verifier.
                        self.assertIn(Path(args[1]).name, ("deploy-all.sh", "verify_trace.py"))
                        data = {}
                    return subprocess.CompletedProcess(args, 0, stdout=json.dumps(data))
                run.side_effect = command
                env = dict(self.env, GITHUB_EVENT_NAME="schedule", GITHUB_OUTPUT=output.name)
                if case == "success":
                    deploy.deploy_component("opensearch", env)
                    self.assertEqual("verify_trace.py", Path(calls[-1][1]).name)
                else:
                    with self.assertRaises(deploy.ScheduledReleaseError):
                        deploy.deploy_component("opensearch", env)
                    self.assertFalse(any(Path(args[1]).name == "verify_trace.py" for args in calls))
                self.assertEqual("", Path(output.name).read_text())
                self.assertEqual(case in ("success", "rebound", "different PV"),
                                 any(Path(args[1]).name == "deploy-all.sh" for args in calls))

    @patch("deploy.run")
    def test_manual_opensearch_upgrade_runs_the_stack_script_then_verifies_a_fresh_trace(self, run):
        # No recovery point is taken or required, and a leftover value of the removed input changes nothing.
        for leftover in ({}, {"JAEGER_OTEL_DEMO_OPENSEARCH_RECOVERY": "required"},
                         {"JAEGER_OTEL_DEMO_OPENSEARCH_RECOVERY": "waive"}):
            with self.subTest(leftover=leftover), tempfile.NamedTemporaryFile() as output:
                run.reset_mock()
                deploy.deploy_component("opensearch", dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch",
                                                          GITHUB_OUTPUT=output.name, **leftover))
                pins = dict(deploy.PIN_LINE.findall((deploy.ROOT / "examples/otel-demo/deploy-all.sh").read_text()))
                first, second = [call.args[0] for call in run.call_args_list]
                self.assertEqual(["bash", str(deploy.ROOT / "examples/otel-demo/deploy-all.sh"), "upgrade"], first)
                self.assertEqual(["python3", str(deploy.ROOT / "scripts/demo/verify_trace.py"),
                                  pins["OPENSEARCH_CHART_VERSION"], pins["OPENSEARCH_DASHBOARDS_CHART_VERSION"]], second)
                self.assertEqual("opensearch", run.call_args_list[0].kwargs["env"]["JAEGER_DEMO_DEPLOY_SCOPE"])
                # The job has no cleanup step to signal any more.
                self.assertEqual("", Path(output.name).read_text())

    @patch("deploy.run")
    def test_every_component_starts_with_its_stack_script(self, run):
        # deploy-all.sh verifies the charts against the lock before it changes anything.
        for component in deploy.COMPONENTS:
            with self.subTest(component=component):
                run.reset_mock()
                deploy.deploy_component(component, dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch"))
                self.assertTrue(run.call_args_list[0].args[0][1].endswith("deploy-all.sh"))

    @patch("deploy.run")
    def test_verifier_receives_each_chart_pin_in_order(self, run):
        # The shipped pins can be equal, so use a copy of the stack with distinct pins.
        script = (deploy.ROOT / "examples/otel-demo/deploy-all.sh").read_text()
        script = re.sub(r'^OPENSEARCH_CHART_VERSION="[^"]+"$', 'OPENSEARCH_CHART_VERSION="9.1.0"', script, flags=re.M)
        script = re.sub(r'^OPENSEARCH_DASHBOARDS_CHART_VERSION="[^"]+"$', 'OPENSEARCH_DASHBOARDS_CHART_VERSION="9.2.0"',
                        script, flags=re.M)
        self.assertEqual({"OPENSEARCH_CHART_VERSION": "9.1.0", "OPENSEARCH_DASHBOARDS_CHART_VERSION": "9.2.0"},
                         {k: v for k, v in deploy.PIN_LINE.findall(script) if k.startswith("OPENSEARCH")})
        with tempfile.TemporaryDirectory() as temp:
            stack = Path(temp) / "examples/otel-demo"
            stack.mkdir(parents=True)
            (stack / "deploy-all.sh").write_text(script)
            with patch.object(deploy, "ROOT", Path(temp)):
                deploy.deploy_component("opensearch", dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch"))
        self.assertEqual(2, run.call_count)
        verifier = run.call_args_list[1].args[0]
        self.assertTrue(verifier[1].endswith("verify_trace.py"))
        self.assertEqual(["9.1.0", "9.2.0"], verifier[2:])
        self.assertEqual("opensearch", run.call_args_list[0].kwargs["env"]["JAEGER_DEMO_DEPLOY_SCOPE"])

    @patch("deploy.run")
    def test_a_failed_opensearch_upgrade_is_not_verified(self, run):
        def command(args, **kwargs):
            if str(args[1]).endswith("deploy-all.sh"):
                raise subprocess.CalledProcessError(1, args)
        run.side_effect = command
        with self.assertRaises(subprocess.CalledProcessError):
            deploy.deploy_component("opensearch", dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch"))
        self.assertEqual(1, run.call_count)

    @patch("deploy.run")
    def test_snapshot_exports_are_loaded_and_push_can_use_published_fallback(self, run):
        def export(*args, **kwargs):
            Path(kwargs["env"]["GITHUB_ENV"]).write_text("JAEGER_DEMO_JAEGER_IMAGE_TAG=abc\nIGNORED=x\n")
        run.side_effect = export
        deploy.resolve_snapshots(self.env)
        self.assertEqual("abc", self.env["JAEGER_DEMO_JAEGER_IMAGE_TAG"])
        self.assertNotIn("IGNORED", self.env)
        self.assertEqual("schedule", run.call_args.kwargs["env"]["GITHUB_EVENT_NAME"])

    @patch("deploy.resolve_snapshots")
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_failed_component_is_not_recorded_and_independent_components_continue(self, run, component, resolve):
        run.return_value.stdout = ""
        component.side_effect = [subprocess.CalledProcessError(1, "opensearch"), None, None]
        self.assertEqual(1, deploy.reconcile(self.env))
        self.assertEqual(["opensearch", "oci-jaeger", "oci-monitoring"], [call.args[0] for call in component.call_args_list])
        writes = [call.args[0] for call in run.call_args_list if call.args[0][3] != "get"]
        # The record did not exist: the first success creates it, the next patches its own key.
        recorded = [writes[0][-1].removeprefix("--from-literal=").split("=", 1)[0]]
        recorded += [key for write in writes[1:] for key in json.loads(write[-1])["data"]]
        self.assertEqual(["create", "patch"], [write[3] for write in writes])
        self.assertNotIn("opensearch", recorded)
        self.assertEqual(["oci-jaeger", "oci-monitoring"], recorded)

    @patch("deploy.resolve_snapshots")
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_only_successful_component_revisions_are_written(self, run, component, resolve):
        previous = dict(self.desired, **{"otel-demo": "old"})
        run.return_value.stdout = json.dumps({"data": previous})
        with tempfile.NamedTemporaryFile() as summary:
            self.env["GITHUB_STEP_SUMMARY"] = summary.name
            self.assertEqual(0, deploy.reconcile(self.env))
            self.assertIn("otel-demo: deployed", Path(summary.name).read_text())
        component.assert_called_once_with("otel-demo", self.env)
        resolve.assert_not_called()
        writes = [call.args[0] for call in run.call_args_list if call.args[0][3] != "get"]
        self.assertEqual(1, len(writes))
        self.assertEqual({"data": {"otel-demo": self.desired["otel-demo"]}}, json.loads(writes[0][-1]))

    def test_deploy_workflow_ends_with_reconcile_and_the_notification(self):
        workflows = deploy.ROOT / ".github/workflows"
        workflow = (workflows / "ci-deploy-demo.yml").read_text()
        self.assertEqual(1, len(re.findall(r"^  [a-z-]+:$", workflow.split("\njobs:\n", 1)[1], re.MULTILINE)))
        job, *steps = workflow.split("\n    - name: ")
        names = [step.split("\n", 1)[0] for step in steps]
        # Nothing runs between the reconciliation and the notification but the upload of the Helmfile
        # engine's record: no restore test to clean up after.
        self.assertEqual(["Reconcile demo components", "Upload the deployment record",
                          "Send detailed Slack notification on failure"], names[-3:])
        self.assertIn("\n      uses: actions/upload-artifact@", steps[-2])
        self.assertNotIn("\n      run:", steps[-2])
        self.assertFalse([name for name in names if "lock" in name.lower()])
        self.assertNotIn("continue-on-error", workflow)
        limit = re.search(r"^    timeout-minutes: (\d+)$", job, re.MULTILINE)
        self.assertIsNotNone(limit, "the deploy job has no timeout-minutes")
        self.assertLess(int(limit[1]), 360)
        self.assertIn("\n      if: failure() || cancelled()\n", steps[-1])

    @patch("deploy.resolve_snapshots")
    @patch("deploy.run")
    def test_administrator_clean_is_dispatched_once_per_stack(self, run, resolve):
        # An administrator's shell, not GitHub Actions: GITHUB_ACTIONS is absent.
        env = dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch", JAEGER_DEMO_STACK="all", JAEGER_DEMO_DEPLOY_MODE="clean")
        self.assertNotIn("GITHUB_ACTIONS", env)
        self.assertEqual(0, deploy.reconcile(env))
        self.assertEqual(2, run.call_count)
        self.assertTrue(all(call.args[0][-1] == "clean" for call in run.call_args_list))

    @patch("deploy.resolve_snapshots")
    @patch("deploy.run")
    def test_clean_is_refused_in_github_actions_before_any_command(self, run, resolve):
        # The workflow identities cannot create or delete namespaces, so a clean run
        # there would uninstall what they can delete and fail to reinstall.
        for stack in ("oci", "otel", "all"):
            with self.subTest(stack=stack):
                env = dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_ACTIONS="true",
                           JAEGER_DEMO_STACK=stack, JAEGER_DEMO_DEPLOY_MODE="clean")
                with self.assertRaisesRegex(ValueError, "GitHub Actions"):
                    deploy.reconcile(env)
        run.assert_not_called()
        resolve.assert_not_called()

    @patch("deploy.resolve_snapshots")
    @patch("deploy.run")
    def test_actions_still_run_upgrades_and_only_actions_refuse_clean(self, run, resolve):
        env = dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_ACTIONS="true",
                   JAEGER_DEMO_STACK="oci", JAEGER_DEMO_DEPLOY_MODE="upgrade")
        run.return_value.stdout = json.dumps({"data": self.desired})
        self.assertEqual(0, deploy.reconcile(env))
        # An administrator's shell does not set GITHUB_ACTIONS to true.
        run.reset_mock()
        env = dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_ACTIONS="false",
                   JAEGER_DEMO_STACK="oci", JAEGER_DEMO_DEPLOY_MODE="clean")
        self.assertEqual(0, deploy.reconcile(env))
        self.assertEqual(["clean"], [call.args[0][-1] for call in run.call_args_list])

    def test_rejects_invalid_or_automatic_unsafe_routes(self):
        cases = [{"GITHUB_REF": "refs/heads/other"}, {"JAEGER_DEMO_STACK": "invalid"},
                 {"JAEGER_DEMO_DEPLOY_MODE": "invalid"}, {"JAEGER_DEMO_DEPLOY_MODE": "clean"},
                 {"GITHUB_EVENT_NAME": "pull_request"}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                deploy.reconcile(dict(self.env, **case))

    @patch("deploy.run")
    def test_manual_invalid_scope_fails(self, run):
        run.return_value.stdout = json.dumps({"data": self.desired})
        with self.assertRaises(ValueError):
            deploy.reconcile(dict(self.env, GITHUB_EVENT_NAME="workflow_dispatch", JAEGER_OTEL_DEMO_DEPLOY_SCOPE="invalid"))

    def handover_env(self, **overrides):
        return dict(self.env, **dict({
            "GITHUB_EVENT_NAME": "workflow_dispatch", "EXPECTED_CONFIG_SHA": "a" * 40, "GITHUB_SHA": "a" * 40,
            "JAEGER_DEMO_STACK": "oci", "JAEGER_DEMO_DEPLOY_MODE": "upgrade", "JAEGER_OTEL_DEMO_DEPLOY_SCOPE": "jaeger",
            "JAEGER_DEMO_JAEGER_IMAGE_TAG": "b" * 40, "JAEGER_DEMO_HOTROD_IMAGE_TAG": "c" * 40,
        }, **overrides))

    @patch("deploy.run")
    def test_unsafe_handover_routes_fail_before_any_command(self, run):
        for overrides in ({"GITHUB_EVENT_NAME": "schedule"}, {"JAEGER_DEMO_STACK": "all"},
                          {"JAEGER_DEMO_DEPLOY_MODE": "clean"}, {"JAEGER_OTEL_DEMO_DEPLOY_SCOPE": "monitoring"},
                          {"JAEGER_DEMO_JAEGER_IMAGE_TAG": "latest"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                deploy.reconcile(self.handover_env(**overrides))
        run.assert_not_called()

    @patch("deploy.handover.preflight")
    @patch("deploy.resolve_snapshots")
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_handover_preflight_precedes_deployment_and_state_write(self, run, component, resolve, preflight):
        run.return_value.stdout = json.dumps({"data": self.desired})
        calls = []
        preflight.side_effect = lambda *args: calls.append("preflight")
        component.side_effect = lambda *args: calls.append("deploy")
        def command(*args, **kwargs):
            if args[0][3] == "patch":
                calls.append("state")
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"data": self.desired}))
        run.side_effect = command
        env = self.handover_env()
        self.assertEqual(0, deploy.reconcile(env))
        self.assertEqual(["preflight", "deploy", "state"], calls)
        component.assert_called_once_with("oci-jaeger", env)
        preflight.assert_called_once_with(env, "oci", "upgrade", "jaeger")

    @patch("deploy.handover.preflight", side_effect=subprocess.CalledProcessError(1, "kubectl", stderr="NotFound"))
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_missing_handover_object_prevents_component_and_state_mutations(self, run, component, preflight):
        run.return_value.stdout = ""
        with self.assertRaises(subprocess.CalledProcessError):
            deploy.reconcile(self.handover_env())
        component.assert_not_called()
        self.assertEqual(1, run.call_count)
        self.assertEqual("get", run.call_args.args[0][3])

    @patch("deploy.selected_components", return_value=["oci-jaeger", "oci-monitoring"])
    @patch("deploy.handover.preflight")
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_unexpected_handover_component_selection_fails_closed(self, run, component, preflight, selected):
        run.return_value.stdout = ""
        with self.assertRaisesRegex(ValueError, "selected components"):
            deploy.reconcile(self.handover_env())
        component.assert_not_called()
        preflight.assert_not_called()
        self.assertEqual(1, run.call_count)

    @patch("deploy.handover.preflight")
    @patch("deploy.resolve_snapshots")
    @patch("deploy.deploy_component")
    @patch("deploy.run")
    def test_changed_handover_image_resolution_prevents_all_writes(self, run, component, resolve, preflight):
        run.return_value.stdout = ""
        resolve.side_effect = lambda env: env.update(JAEGER_DEMO_JAEGER_IMAGE_TAG="d" * 40)
        with self.assertRaisesRegex(ValueError, "image resolution changed"):
            deploy.reconcile(self.handover_env())
        component.assert_not_called()
        self.assertEqual(1, run.call_count)


class CompanionGuardTests(unittest.TestCase):
    """The scripts engine refuses to run while Helm holds a demo-resources companion release (#158)."""

    NAMESPACES = ("default", "jaeger", "opensearch", "otel-demo")
    APPLICATIONS = {"default": ("jaeger", "prometheus"), "jaeger": ("jaeger",),
                    "opensearch": ("opensearch", "opensearch-dashboards"), "otel-demo": ("otel-demo",)}
    # Every route of reconcile that passes input validation, with what it would go on to write.
    ROUTES = {
        "scheduled": {"GITHUB_EVENT_NAME": "schedule"},
        "push": {"GITHUB_EVENT_NAME": "push"},
        "manual with the default inputs": {"GITHUB_EVENT_NAME": "workflow_dispatch"},
        "manual, every component": {"GITHUB_EVENT_NAME": "workflow_dispatch", "JAEGER_DEMO_STACK": "all",
                                    "JAEGER_OTEL_DEMO_DEPLOY_SCOPE": "all"},
        "handover": {"GITHUB_EVENT_NAME": "workflow_dispatch", "EXPECTED_CONFIG_SHA": "a" * 40, "GITHUB_SHA": "a" * 40,
                     "JAEGER_DEMO_STACK": "otel", "JAEGER_OTEL_DEMO_DEPLOY_SCOPE": "jaeger",
                     "JAEGER_DEMO_JAEGER_IMAGE_TAG": "b" * 40, "JAEGER_DEMO_HOTROD_IMAGE_TAG": "c" * 40},
        "clean from an administrator's shell": {"GITHUB_EVENT_NAME": "workflow_dispatch", "JAEGER_DEMO_STACK": "all",
                                                "JAEGER_DEMO_DEPLOY_MODE": "clean"},
    }

    def setUp(self):
        self.desired = {name: deploy.fingerprint(name) for name in deploy.COMPONENTS}
        self.calls = []
        for name in ("deploy_component", "resolve_snapshots", "handover.preflight"):
            patcher = patch("deploy." + name)
            setattr(self, name.split(".")[-1], patcher.start())
            self.addCleanup(patcher.stop)
        # The scheduled route reads the kubeconfig's namespace before the guard.
        patcher = patch("deploy.handover.read", return_value="default")
        patcher.start()
        self.addCleanup(patcher.stop)

    def cluster(self, companions=None, unlisted=None, denied=()):
        """A fake `deploy.run` for a cluster whose namespaces hold these companion statuses.

        companions maps a namespace to the status `helm list` shows. unlisted maps a namespace to
        a status that only the named read shows. denied names namespaces whose named read fails.
        """
        companions, unlisted = companions or {}, unlisted or {}

        def run(args, **kwargs):
            self.calls.append((list(args), kwargs))
            if args[:2] == ["helm", "list"]:
                namespace = args[args.index("-n") + 1]
                names = self.APPLICATIONS[namespace] + (("demo-resources",) if namespace in companions else ())
                listing = [{"name": name, "namespace": namespace, "revision": "1",
                            "status": companions.get(namespace, "deployed") if name == "demo-resources" else "deployed"}
                           for name in names]
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(listing))
            if args[:2] == ["helm", "history"]:
                namespace = args[args.index("-n") + 1]
                if namespace in denied:
                    raise subprocess.CalledProcessError(1, args, stderr="Error: secrets is forbidden")
                if namespace in unlisted:
                    return subprocess.CompletedProcess(args, 0, stdout=json.dumps(
                        [{"revision": 1, "status": unlisted[namespace]}]))
                raise subprocess.CalledProcessError(1, args, stderr="Error: release: not found")
            if args[:1] == ["kubectl"] and "get" in args:
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"data": self.desired}))
            return subprocess.CompletedProcess(args, 0, stdout="")

        return patch("deploy.run", side_effect=run)

    def commands(self):
        return [args for args, _ in self.calls]

    def env(self, route):
        return dict({"GITHUB_REF": "refs/heads/main"}, **self.ROUTES[route])

    def assert_only_release_reads(self):
        # Nothing but the per-namespace listing and the named history read: no kubectl at all,
        # no helm upgrade, no stack script, no snapshot resolution and no handover preflight.
        self.assertTrue(self.commands())
        for args in self.commands():
            self.assertEqual("helm", args[0], args)
            self.assertIn(args[1], ("list", "history"), args)
            self.assertNotIn("-A", args)
            self.assertNotIn("--all-namespaces", args)
        self.deploy_component.assert_not_called()
        self.resolve_snapshots.assert_not_called()
        self.preflight.assert_not_called()

    def test_a_companion_release_refuses_every_route_before_any_write(self):
        for route in self.ROUTES:
            for namespace in self.NAMESPACES:
                for status in ("deployed", "failed", "pending-install", "superseded", "uninstalling"):
                    with self.subTest(route=route, namespace=namespace, status=status):
                        self.calls.clear()
                        with self.cluster({namespace: status}), self.assertRaises(deploy.CompanionReleaseError) as error:
                            deploy.reconcile(self.env(route))
                        message = str(error.exception)
                        self.assertIn(f"demo-resources companion release in {namespace} ({status}).", message)
                        self.assertIn("The Helmfile engine is required", message)
                        self.assertIn("de-adoption", message)
                        self.assert_only_release_reads()

    def test_the_refusal_names_every_namespace_that_holds_a_companion(self):
        adopted = {"default": "deployed", "jaeger": "deployed", "opensearch": "failed", "otel-demo": "deployed"}
        with self.cluster(adopted), self.assertRaises(deploy.CompanionReleaseError) as error:
            deploy.reconcile(self.env("manual with the default inputs"))
        self.assertIn("in default (deployed), jaeger (deployed), opensearch (failed), otel-demo (deployed).",
                      str(error.exception))
        self.assert_only_release_reads()
        with self.cluster({"jaeger": "deployed"}), self.assertRaises(deploy.CompanionReleaseError) as error:
            deploy.reconcile(self.env("manual with the default inputs"))
        for namespace in ("default", "opensearch", "otel-demo"):
            self.assertNotIn(namespace + " (", str(error.exception))

    def test_the_refusal_is_a_workflow_error_annotation(self):
        with self.cluster({"otel-demo": "deployed"}), patch("builtins.print") as printed:
            with self.assertRaises(deploy.CompanionReleaseError) as error:
                deploy.reconcile(self.env("scheduled"))
        self.assertEqual([(("::error::" + str(error.exception),), {"flush": True})],
                         [(call.args, call.kwargs) for call in printed.call_args_list])

    def test_each_namespace_is_listed_on_its_own_with_the_helmfile_path_command(self):
        with self.cluster():
            self.assertEqual({}, deploy.companion_releases({"PATH": "/bin"}))
        lists = [args for args in self.commands() if args[1] == "list"]
        self.assertEqual([["helm", *deploy.release_phases.list_args(namespace)] for namespace in self.NAMESPACES], lists)
        self.assertEqual([["helm", "list", "-n", namespace, "--max", "0", "-o", "json"] for namespace in self.NAMESPACES],
                         lists)
        # A listing that omits the companion proves nothing, so each is then read by name.
        self.assertEqual([["helm", "history", "demo-resources", "-n", namespace, "--max", "1", "-o", "json"]
                          for namespace in self.NAMESPACES], self.commands()[len(lists):])

    def test_a_companion_that_the_listing_omits_is_found_by_name(self):
        # Helm's default listing omits a release whose status is unknown.
        with self.cluster(unlisted={"opensearch": "unknown"}), self.assertRaises(deploy.CompanionReleaseError) as error:
            deploy.reconcile(self.env("manual with the default inputs"))
        self.assertIn("in opensearch (unknown).", str(error.exception))
        self.assert_only_release_reads()

    def test_a_read_that_fails_stops_the_run_and_is_not_an_absent_release(self):
        for route in self.ROUTES:
            with self.subTest(route=route):
                self.calls.clear()
                with self.cluster(denied=("jaeger",)), patch("builtins.print") as printed:
                    with self.assertRaises(subprocess.CalledProcessError):
                        deploy.reconcile(self.env(route))
                self.assert_only_release_reads()
                self.assertEqual([("Error: secrets is forbidden",)], [call.args for call in printed.call_args_list])

        def refused_listing(args, **kwargs):
            self.calls.append((list(args), kwargs))
            raise subprocess.CalledProcessError(1, args, stderr="Error: release: not found")

        # Only the named read may report "not found". A listing that fails stops the run whatever it says.
        self.calls.clear()
        with patch("deploy.run", side_effect=refused_listing), patch("builtins.print"):
            with self.assertRaises(subprocess.CalledProcessError):
                deploy.reconcile(self.env("manual with the default inputs"))
        self.assertEqual([["helm", "list", "-n", "default", "--max", "0", "-o", "json"]], self.commands())

    def test_the_reads_use_the_cluster_and_storage_that_kubectl_and_the_records_use(self):
        # Helm's own settings could point the check at another cluster, identity or storage driver,
        # where it would find no companion. The stack scripts' kubectl ignores all of them.
        overrides = {"HELM_DRIVER": "memory", "HELM_NAMESPACE": "other", "HELM_KUBECONTEXT": "another-cluster",
                     "HELM_KUBEAPISERVER": "https://example.test", "HELM_KUBETOKEN": "token",
                     "HELM_KUBEASUSER": "someone", "HELM_KUBECAFILE": "/tmp/ca"}
        kept = {"PATH": "/bin", "KUBECONFIG": "/tmp/kubeconfig", "HELM_CACHE_HOME": "/tmp/cache"}
        with self.cluster({"default": "deployed"}), self.assertRaises(deploy.CompanionReleaseError):
            deploy.reconcile(dict(self.env("manual with the default inputs"), **overrides, **kept))
        self.assertTrue(self.calls)
        for args, kwargs in self.calls:
            self.assertTrue(kwargs["capture_output"])
            self.assertEqual(kept, {key: value for key, value in kwargs["env"].items() if key in kept})
            self.assertEqual([], [key for key in kwargs["env"] if key in overrides])

    def test_invalid_inputs_are_still_refused_without_reading_the_cluster(self):
        for overrides in ({"GITHUB_REF": "refs/heads/other", "GITHUB_EVENT_NAME": "schedule"},
                          {"JAEGER_DEMO_STACK": "invalid"}, {"GITHUB_EVENT_NAME": "pull_request"},
                          {"JAEGER_DEMO_DEPLOY_MODE": "clean", "GITHUB_ACTIONS": "true"}):
            with self.subTest(overrides=overrides), self.cluster({"default": "deployed"}):
                with self.assertRaises(ValueError) as error:
                    deploy.reconcile(dict(self.env("manual with the default inputs"), **overrides))
                self.assertNotIsInstance(error.exception, deploy.CompanionReleaseError)
                self.assertEqual([], self.calls)

    def test_without_a_companion_release_the_engine_runs_unchanged(self):
        # The cluster before adoption and after de-adoption, which removes the release records.
        expected = {
            "scheduled": ["otel-jaeger", "oci-jaeger"],
            "push": [],
            "manual with the default inputs": ["oci-jaeger"],
            "manual, every component": list(deploy.COMPONENTS),
            "handover": ["otel-jaeger"],
        }
        for route, components in expected.items():
            with self.subTest(route=route):
                self.calls.clear()
                self.deploy_component.reset_mock()
                with self.cluster():
                    self.assertEqual(0, deploy.reconcile(self.env(route)))
                self.assertEqual(components, [call.args[0] for call in self.deploy_component.call_args_list])
                commands = self.commands()
                # The eight release reads come first, then exactly what the engine ran before the guard.
                self.assertEqual([("helm", "list")] * 4 + [("helm", "history")] * 4,
                                 [tuple(args[:2]) for args in commands[:8]])
                rest = commands[8:]
                self.assertEqual(["kubectl", "-n", "default", "get", "configmap", deploy.STATE,
                                  "--ignore-not-found", "-o", "json"], rest[0])
                self.assertEqual([["kubectl", "-n", "default", "patch", "configmap", deploy.STATE, "--type=merge", "-p",
                                   json.dumps({"data": {name: self.desired[name]}})] for name in components], rest[1:])
        # The administrator's clean reinstall runs each stack script once, as before.
        self.calls.clear()
        with self.cluster():
            self.assertEqual(0, deploy.reconcile(self.env("clean from an administrator's shell")))
        self.assertEqual([["bash", str(deploy.ROOT / "examples" / directory / "deploy-all.sh"), "clean"]
                          for directory in ("oci", "otel-demo")], self.commands()[8:])


if __name__ == "__main__":
    unittest.main()

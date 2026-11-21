# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

"""The access matrix check must agree with the executor and name what it finds."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

import access_matrix as m
import scheduled_adapter as a


RECORDS = json.loads((a.ROOT / m.MATRIX).read_bytes())["records"]
CLUSTER_KINDS = {kind.lower() + "s": kind for kind in a.CLUSTER}
KINDS = {plural: kind for kind, plural in m.PLURALS.items()}
CI = yaml.safe_load((a.ROOT / ".github/workflows/executor-ci.yml").read_text())["jobs"]["chart-admission"]


def release_key(row):
    """The release a reviewed record belongs to."""
    keys = [key for key in a.RELEASES if a.component(key) == row["component"]
            and row["release"] in ("supplement", a.RELEASES[key][1])]
    assert keys, row
    return keys[0]


def synthetic(row, name=None):
    """A minimal object with the identity that a reviewed record describes."""
    resource = row["resource"]
    kind = CLUSTER_KINDS.get(resource) or KINDS.get(resource) or resource[:-1].capitalize()
    metadata = {"name": name or row["name"]}
    if row["scope"] != "cluster":
        metadata["namespace"] = row["scope"]
    if row.get("hookEvents"):
        metadata["annotations"] = {a.HELM_HOOK: row["hookEvents"]}
    return {"apiVersion": row["apiGroup"] + "/v1" if row["apiGroup"] else "v1", "kind": kind, "metadata": metadata}


def renders_from_matrix():
    """Renders that reproduce the reviewed records exactly."""
    renders = {key: ([], []) for key in a.RELEASES}
    for row in m.chart_rows(RECORDS).values():
        renders[release_key(row)][row["release"] == "supplement"].append(synthetic(row))
    return renders


class AccessMatrixTests(unittest.TestCase):
    def run_main(self, renders, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as charts, patch.object(m, "gather", return_value=renders), \
                patch("sys.stderr", err):
            status = m.main(["--charts", charts, *arguments], out=out)
        return status, out.getvalue(), err.getvalue()

    def test_only_helm_history_and_the_checkpoint_are_outside_the_comparison(self):
        compared = m.chart_rows(RECORDS)
        self.assertEqual({"history", "shared"}, {row["component"] for row in RECORDS} - {row["component"] for row in compared.values()})
        self.assertEqual(len(compared), sum(row["component"] not in ("history", "shared") for row in RECORDS))

    def test_every_reviewed_record_maps_the_way_the_executor_matches_it(self):
        for row in m.chart_rows(RECORDS).values():
            key, supplemental = release_key(row), row["release"] == "supplement"
            namespace = a.RELEASES[key][0]
            with self.subTest(record=m.describe(tuple(row[f] for f in m.FIELDS))):
                obj = synthetic(row)
                self.assertEqual(tuple(row[f] for f in m.FIELDS), m.row_key(key, a.identity(obj, namespace), supplemental))
                # The executor accepts exactly this object and rejects a renamed one.
                a.inventory(key, [] if supplemental else [obj], [obj] if supplemental else [])
                other = synthetic(row, name=row["name"] + "-unreviewed")
                with self.assertRaisesRegex(a.AdapterError, "outside reviewed access inventory"):
                    a.inventory(key, [] if supplemental else [other], [other] if supplemental else [])
                found = m.rendered_rows(key, [] if supplemental else [other], [other] if supplemental else [])
                self.assertEqual(list(found), m.compare(found, RECORDS)[0])

    def test_renders_equal_to_the_matrix_pass(self):
        status, out, err = self.run_main(renders_from_matrix())
        self.assertEqual((0, ""), (status, err))
        self.assertIn("Every rendered object has a reviewed record.", out)
        self.assertIn("  oci-monitoring: 128 chart objects, 2 supplemental", out)
        self.assertNotIn("no reviewed record", out)
        self.assertNotIn("match no rendered object", out)

    def test_an_unreviewed_object_fails_and_is_named_with_the_fix(self):
        renders = renders_from_matrix()
        renders["otel-demo"][0].append({"apiVersion": "apps/v1", "kind": "Deployment",
                                        "metadata": {"name": "new-service", "namespace": "otel-demo"}})
        renders["opensearch-dashboards"][0].append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
                                                    "metadata": {"name": "extra", "namespace": "opensearch"}})
        status, out, err = self.run_main(renders)
        self.assertEqual(1, status)
        self.assertIn("2 rendered object(s) have no reviewed record:", out)
        self.assertIn("  - otel-demo / otel-demo: apps/deployments otel-demo/new-service\n", out)
        self.assertIn("  - opensearch / opensearch-dashboards: rbac.authorization.k8s.io/roles opensearch/extra (PROTECTED kind", out)
        self.assertIn("python3 scripts/demo/access_matrix.py --propose", err)
        self.assertIn(m.PROCEDURE, err)
        self.assertIn("until a manual upgrade has established the new release", err)
        self.assertNotIn("Draft records", out)
        self.assertTrue((a.ROOT / "docs/releases.md").read_text().count("\n## Merging a change to guarded files\n"))

    def test_propose_drafts_records_in_the_matrix_shape_without_editing_it(self):
        renders = renders_from_matrix()
        renders["otel-demo"][0].append({"apiVersion": "apps/v1", "kind": "Deployment",
                                        "metadata": {"name": "new-service", "namespace": "otel-demo"}})
        renders["oci-monitoring"][0].append({"apiVersion": "batch/v1", "kind": "Job", "metadata": {
            "name": "new-hook", "namespace": "default", "annotations": {a.HELM_HOOK: "pre-upgrade"}}})
        renders["otel-jaeger"][1].append({"apiVersion": "v1", "kind": "Service",
                                         "metadata": {"name": "new-supplement", "namespace": "jaeger"}})
        before = (a.ROOT / m.MATRIX).read_bytes()
        status, out, _ = self.run_main(renders, "--propose")
        self.assertEqual(1, status)
        self.assertEqual(before, (a.ROOT / m.MATRIX).read_bytes())
        drafts = json.loads(out.split("confirm each one:\n", 1)[1])
        self.assertEqual([
            {"component": "oci-monitoring", "release": "prometheus", "scope": "default", "apiGroup": "batch",
             "resource": "jobs", "name": "new-hook", "verbs": ["get", "create", "delete"],
             "source": "pinned Helm upgrade render", "hookEvents": "pre-upgrade"},
            {"component": "otel-demo", "release": "otel-demo", "scope": "otel-demo", "apiGroup": "apps",
             "resource": "deployments", "name": "new-service", "verbs": ["get", "patch"],
             "source": "pinned Helm upgrade render"},
            {"component": "otel-jaeger", "release": "supplement", "scope": "jaeger", "apiGroup": "",
             "resource": "services", "name": "new-supplement", "verbs": ["get", "patch"], "source": "kubectl apply"},
        ], drafts)
        # A draft has the keys, in order, that reviewed records of its kind already use.
        ordinary = next(row for row in RECORDS if row["component"] == "otel-demo")
        self.assertEqual(list(ordinary), list(drafts[1]))
        hook = next(row for row in RECORDS if row.get("hookEvents"))
        self.assertEqual((list(hook), hook["verbs"]), (list(drafts[0]), drafts[0]["verbs"]))

    def test_a_record_without_a_rendered_object_is_listed_but_does_not_fail(self):
        renders = renders_from_matrix()
        removed = renders["opensearch"][0].pop()
        status, out, err = self.run_main(renders)
        self.assertEqual((0, ""), (status, err))
        self.assertIn("1 reviewed record(s) match no rendered object. A scheduled run refuses the removal", out)
        self.assertIn("opensearch/" + removed["metadata"]["name"] + "\n", out)

    def test_changed_hook_events_fail_and_test_hooks_are_ignored(self):
        renders = renders_from_matrix()
        hooked = next(obj for obj in renders["oci-monitoring"][0] if a.HELM_HOOK in obj["metadata"].get("annotations", {}))
        reviewed = hooked["metadata"]["annotations"][a.HELM_HOOK]
        hooked["metadata"]["annotations"][a.HELM_HOOK] = ",".join(reversed(reviewed.split(",")))
        renders["otel-demo"][0].append({"apiVersion": "v1", "kind": "Pod", "metadata": {
            "name": "connection-test", "namespace": "otel-demo", "annotations": {a.HELM_HOOK: "test"}}})
        self.assertEqual(0, self.run_main(renders)[0])
        hooked["metadata"]["annotations"][a.HELM_HOOK] = reviewed + ",post-delete"
        status, out, _ = self.run_main(renders)
        self.assertEqual(1, status)
        self.assertIn("1 object(s) have Helm hook events that differ from their record:", out)
        self.assertIn(hooked["metadata"]["name"] + ": rendered '" + reviewed + ",post-delete'", out)
        del hooked["metadata"]["annotations"]
        self.assertEqual(1, self.run_main(renders)[0])

    def test_a_reviewed_hook_that_becomes_a_test_hook_fails(self):
        for events in ("test", "test-success", "test,test-success"):
            with self.subTest(events=events):
                renders = renders_from_matrix()
                hooked = next(obj for obj in renders["oci-monitoring"][0] if a.HELM_HOOK in obj["metadata"].get("annotations", {}))
                hooked["metadata"]["annotations"][a.HELM_HOOK] = events
                status, out, _ = self.run_main(renders)
                self.assertEqual(1, status)
                self.assertIn("1 object(s) have Helm hook events that differ from their record:", out)
                self.assertIn(hooked["metadata"]["name"] + ": rendered '" + events + "'", out)
                self.assertNotIn("match no rendered object", out)
        # The executor itself skips the object once it is test-only, so only this check sees the change.
        a.inventory("oci-monitoring", [dict(hooked, metadata=dict(hooked["metadata"], name="unreviewed"))], [])

    def test_output_never_includes_manifest_content(self):
        renders = renders_from_matrix()
        renders["otel-demo"][0].append({"apiVersion": "v1", "kind": "Secret", "data": {"password": "c2VjcmV0LXZhbHVl"},
                                        "metadata": {"name": "new-secret", "namespace": "otel-demo"}})
        status, out, err = self.run_main(renders, "--propose")
        self.assertEqual(1, status)
        self.assertIn("core/secrets otel-demo/new-secret (PROTECTED kind", out)
        self.assertNotIn("c2VjcmV0LXZhbHVl", out + err)
        self.assertNotIn("password", out + err)

    def test_archives_are_located_by_the_reviewed_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            charts = Path(temp)
            with self.assertRaisesRegex(ValueError, r"Missing chart archive [a-z-]+-[0-9.]+\.tgz"):
                m.packages(charts, a.ROOT)
            pinned = [charts / f"{name}-{m.scheduled_inputs.pin(a.ROOT, variable)}.tgz"
                      for _, variable, name, _ in m.scheduled_inputs.CHARTS]
            for path in pinned:
                path.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "Expected one packaged Jaeger chart in .*, found 0"):
                m.packages(charts, a.ROOT)
            (charts / "jaeger-4.0.0.tgz").write_bytes(b"")
            located = m.packages(charts, a.ROOT)
            self.assertEqual(set(a.RELEASES), set(located))
            self.assertEqual(located["oci-jaeger"], located["otel-jaeger"])
            self.assertEqual({a.CHARTS[key] for key in a.RELEASES},
                             {path.name.rsplit("-", 1)[0] for path in located.values()})
            # An archive of another version does not stand in for the pinned one.
            pinned[0].rename(pinned[0].with_name(pinned[0].name.replace(".tgz", "9.tgz")))
            with self.assertRaisesRegex(ValueError, "Missing chart archive " + pinned[0].name):
                m.packages(charts, a.ROOT)
            (charts / "jaeger-4.0.1.tgz").write_bytes(b"")
            pinned[0].write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "found 2"):
                m.packages(charts, a.ROOT)

    def test_render_is_an_offline_upgrade_at_the_reviewed_kubernetes_version(self):
        manifest = b"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: one\n"
        env = {"PATH": "/usr/bin", "KUBECONFIG": "/dev/null", "HOME": "/tmp/x"}
        with patch.object(a, "command", return_value=manifest) as command:
            objects = m.render("oci-monitoring", Path("/charts/kube-prometheus-stack-1.0.0.tgz"), a.ROOT, "1.36.1", env)
        self.assertEqual([{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "one"}}], objects)
        args, kwargs = command.call_args
        self.assertEqual(["helm", "template", "prometheus", "/charts/kube-prometheus-stack-1.0.0.tgz", "-n", "default",
                          "--kube-version=1.36.1", "--is-upgrade", "--values", "-"], args[0])
        self.assertEqual(env, kwargs["env"])
        values = json.loads(kwargs["body"])
        self.assertEqual("synthetic-local-fixture", values["grafana"]["adminPassword"])
        # A failed render names the release, archive and values file. Helm's own output may hold Secrets.
        with patch.object(a, "command", side_effect=a.AdapterError("Scheduled command failed")):
            with self.assertRaisesRegex(ValueError, r"helm template failed for otel-demo with opentelemetry-demo-9\.9\.9\.tgz\. "
                                                    r"The values in examples/otel-demo/otel-demo-values\.yaml"):
                m.render("otel-demo", Path("/charts/opentelemetry-demo-9.9.9.tgz"), a.ROOT, "1.36.1", env)
        with patch.object(m, "check", return_value=({}, [], [])), patch.object(m, "gather", return_value={}) as gather:
            with tempfile.TemporaryDirectory() as charts:
                m.main(["--charts", charts], out=io.StringIO())
        kube_version, helm_env = gather.call_args.args[2:]
        self.assertEqual(json.loads((a.ROOT / m.MATRIX).read_bytes())["targetKubernetes"], kube_version)
        self.assertEqual("/dev/null", helm_env["KUBECONFIG"])

    def test_ci_runs_the_check_on_the_verified_archives_before_the_admission_rehearsal(self):
        names = [step.get("name", "") for step in CI["steps"]]
        check = names.index("Compare the pinned chart renders with the reviewed access matrix")
        self.assertLess(names.index("Fetch the reviewed chart sources and verify their digests"), check)
        self.assertLess(check, names.index("Stage and verify all six real charts under native admission"))
        step = CI["steps"][check]
        self.assertEqual('python3 scripts/demo/access_matrix.py --charts "$RUNNER_TEMP/chart-sources/charts"', step["run"].strip())
        self.assertNotIn("continue-on-error", step)
        self.assertNotIn("if", step)


if __name__ == "__main__":
    unittest.main()

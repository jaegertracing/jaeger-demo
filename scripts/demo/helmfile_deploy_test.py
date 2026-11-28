# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import yaml

import companion_ownership
import companion_values
import deploy
import helmfile_deploy as hd
import release_phases
import stack_health

ROOT = Path(__file__).resolve().parents[2]
HEAD = "c" * 40
CORE = "d" * 40
DIGEST = "sha256:" + "e" * 64
PLATFORM = "sha256:" + "f" * 64
COMPANIONS = [f"{ns}/demo-resources" for ns in release_phases.NAMESPACES]
APPLICATIONS = ["default/prometheus", "default/jaeger", "opensearch/opensearch", "opensearch/opensearch-dashboards",
                "jaeger/jaeger", "otel-demo/otel-demo"]
ORDER = ["default/prometheus", "default/jaeger", "default/demo-resources", "opensearch/opensearch",
         "opensearch/opensearch-dashboards", "jaeger/jaeger", "otel-demo/otel-demo", "jaeger/demo-resources",
         "opensearch/demo-resources", "otel-demo/demo-resources"]
# Words that would mean a value, a Secret or a diff reached the record.
FORBIDDEN_KEYS = {"values", "data", "secret", "secrets", "password", "diff", "manifest", "objects", "stringData"}


def done(args, stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


ALLOWLIST = yaml.safe_load((ROOT / "charts/demo-resources/allowlist.yaml").read_text())
RESOURCES = {resource: kind for kind, resource in hd.KINDS.items()}


def companion_object(ns, kind, name, owned=True):
    meta = {"namespace": ns, "name": name, "uid": "uid-" + name, "resourceVersion": "7"}
    if owned:
        meta["labels"] = {"app.kubernetes.io/managed-by": "Helm"}
        meta["annotations"] = {"meta.helm.sh/release-name": "demo-resources", "meta.helm.sh/release-namespace": ns,
                               "helm.sh/resource-policy": "keep"}
    return {"apiVersion": companion_ownership.API[kind], "kind": kind, "metadata": meta}


class Cluster:
    """A fake runner holding Helm releases. helmfile apply changes only the releases in `changes`."""

    def __init__(self, statuses=None, changes=(), state=True):
        self.releases = {key: {"revision": 4, "status": "deployed", "chart": "c-1"} for key in APPLICATIONS + COMPANIONS}
        for key, status in (statuses or {}).items():
            if status is None:
                del self.releases[key]
            else:
                self.releases[key]["status"] = status
        self.changes, self.state, self.calls = set(changes), state, []
        # The allowlisted companion objects, adopted by the reviewed ownership patches.
        self.objects = {(ns, kind, name): companion_object(ns, kind, name)
                        for ns, kinds in ALLOWLIST.items() for kind, names in kinds.items() for name in names}
        self.stuck, self.broken_history, self.failing = set(), set(), set()
        self.written = set()
        # The revision a listing reports, when it differs from the release's history.
        self.listed_revision = {}

    def __call__(self, args, check=True, capture=False, env=None, cwd=None):
        args = [str(a) for a in args]
        self.calls.append(args)
        if args[:2] == ["git", "-C"]:
            return done(args, HEAD + "\n")
        if args[:2] == ["helm", "list"]:
            ns = args[3]
            listed = [{"name": key.split("/")[1], "namespace": ns, "status": rel["status"],
                       "revision": str(self.listed_revision.get(key, rel["revision"]))}
                      for key, rel in self.releases.items() if key.startswith(ns + "/") and rel["status"] != "unknown"]
            return done(args, json.dumps(listed))
        if args[:2] == ["helm", "history"]:
            key = args[4] + "/" + args[2]
            if key in self.broken_history and key in self.written:
                return done(args, "", 1, "Error: connection to https://10.0.0.1 refused, token=SECRET-TOKEN")
            if key not in self.releases:
                return done(args, "", 1, "Error: release: not found")
            rel = self.releases[key]
            return done(args, json.dumps([{"revision": rel["revision"], "status": rel["status"], "chart": rel["chart"],
                                           "app_version": "1"}]))
        if args[:2] == ["helm", "install"]:
            self.releases[args[args.index("--namespace") + 1] + "/demo-resources"] = {
                "revision": 1, "status": "deployed", "chart": "demo-resources-0.1.0"}
            return done(args)
        if args[0] == "helmfile":
            selector = dict(part.split("=") for part in args[args.index("-l") + 1].split(","))
            key = selector["namespace"] + "/" + selector["name"]
            method = args[args.index("--skip-deps") + 1]
            if method == "diff":
                # Helmfile reports a release that is not deployed as changed.
                rel = self.releases.get(key)
                return done(args, returncode=2 if key in self.changes or not rel or rel["status"] != "deployed" else 0)
            self.written.add(key)
            if key in self.failing:
                raise subprocess.CalledProcessError(1, args, stderr="password=SECRET-TOKEN at https://10.0.0.1")
            rel = self.releases[key]
            if method == "sync" or key in self.changes or rel["status"] != "deployed":
                rel.update(revision=rel["revision"] + 1, status="failed" if key in self.stuck else "deployed")
            return done(args)
        if args[:6] == ["kubectl", "-n", "default", "get", "configmap", deploy.STATE]:
            return done(args, json.dumps({"data": {}}) if self.state else "")
        if args[0] == "kubectl" and args[3] == "get" and args[4] in RESOURCES:
            live = self.objects.get((args[2], RESOURCES[args[4]], args[5]))
            return done(args, json.dumps(live) if live else "")
        return done(args)

    def writes(self):
        return [c for c in self.calls if (c[0] == "helmfile" and "diff" not in c) or c[:2] == ["helm", "install"]
                or (c[0] == "kubectl" and ("patch" in c or "restart" in c))]

    def cluster_calls(self):
        return [c for c in self.calls if c[0] in ("kubectl", "helm") or (c[0] == "helmfile" and "template" not in c)]


class Checks:
    def __init__(self, fail=(), log=None):
        self.fail, self.calls, self.log, self.images, self.since = set(fail), [], log, None, {}

    def prepare(self, stacks, images=None):
        self.calls.append(("prepare", tuple(sorted(stacks))))
        self.images = images

    def verify(self, stack, since=None):
        self.calls.append(("verify", stack))
        self.since[stack] = since
        if self.log is not None:
            self.log.append(["verify", stack])
        if stack in self.fail:
            raise RuntimeError(f"{stack} health failed")
        return {"health": {"stack": stack}}


API_RESOURCES = {"v1": [("ConfigMap", "configmaps"), ("Service", "services"), ("Pod", "pods")],
                 "apps/v1": [("Deployment", "deployments"), ("ReplicaSet", "replicasets")],
                 "networking.k8s.io/v1": [("Ingress", "ingresses")]}


@contextlib.contextmanager
def fake_api(workloads=None, objects=None):
    """A GET-only API like live_observation.reader's. objects serves named objects of the jaeger namespace by
    (kind, name); workloads serves ready Deployments by (namespace, name): (image reference, running digest)."""
    kinds = {plural: kind for group in API_RESOURCES.values() for kind, plural in group}

    def deployment(ns, name, reference):
        return {"apiVersion": "apps/v1", "kind": "Deployment",
                "metadata": {"name": name, "namespace": ns, "uid": "dep-" + name, "generation": 1},
                "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}}, "template": {"spec": {
                    "containers": [{"name": name, "image": reference, "imagePullPolicy": "Always"}]}}},
                "status": {"observedGeneration": 1, "replicas": 1, "readyReplicas": 1, "updatedReplicas": 1,
                           "availableReplicas": 1}}

    def get(url, accept="application/json"):
        path, _, query = url.partition("?")
        if path == "/api":
            return {"kind": "APIVersions", "versions": ["v1"]}
        if path == "/apis":
            return {"kind": "APIGroupList", "groups": [{"versions": [{"groupVersion": g}]} for g in API_RESOURCES if g != "v1"]}
        if path == "/version":
            return {"gitVersion": "v1.37.0"}
        for group, entries in API_RESOURCES.items():
            if path == ("/api/" if group == "v1" else "/apis/") + group:
                return {"kind": "APIResourceList", "groupVersion": group, "resources": [
                    {"kind": kind, "name": plural, "namespaced": True, "verbs": ["get", "list"]} for kind, plural in entries]}
        parts = path.split("/")
        at = parts.index("namespaces")
        ns, plural, name = parts[at + 1], parts[at + 2], (parts[at + 3:] or [None])[0]
        if objects is not None:
            return objects.get((kinds[plural], name))
        if plural == "deployments":
            return deployment(ns, name, workloads[(ns, name)][0])
        if plural == "replicasets":
            owner = name.removesuffix("-rs")
            return {"apiVersion": "apps/v1", "kind": "ReplicaSet", "metadata": {
                "name": name, "namespace": ns, "uid": "rs-" + owner, "ownerReferences": [
                    {"controller": True, "kind": "Deployment", "apiVersion": "apps/v1", "name": owner, "uid": "dep-" + owner}]}}
        owner = parse_qs(query)["labelSelector"][0].split("=")[1]
        reference, running = workloads[(ns, owner)]
        pod = {"metadata": {"namespace": ns, "name": owner + "-pod", "uid": "pod-" + owner, "labels": {"app": owner},
                            "ownerReferences": [{"controller": True, "kind": "ReplicaSet", "apiVersion": "apps/v1",
                                                 "name": owner + "-rs", "uid": "rs-" + owner}]},
               "spec": {"containers": [{"name": owner, "image": reference}]},
               "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                          "containerStatuses": [{"name": owner, "ready": True, "imageID": "docker.io/x@" + running}]}}
        return {"apiVersion": "v1", "kind": "PodList", "metadata": {"resourceVersion": "1"}, "items": [pod]}

    yield get


def live_observed():
    images = {}
    for stack in ("oci", "otel"):
        images[stack] = {name: {"reference": f"docker.io/{repo}:{'1' * 40}", "sourceCommit": "1" * 40, "digest": DIGEST}
                         for name, repo in (("jaeger", "jaegertracing/jaeger-snapshot"),
                                            ("hotrod", "jaegertracing/example-hotrod-snapshot"))}
    policies = {stack: {"jaeger": "Always", "hotrod": "Always"} for stack in ("oci", "otel")}
    return images, policies


class DriverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("examples", "scripts", "charts", "helmfile.yaml", "helmfile-resources.yaml.gotmpl"):
            (self.root / name).symlink_to(ROOT / name)
        (self.root / "helmfile").mkdir()
        self.mode("live")
        self.summary = self.root / "summary.md"
        self.env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": HEAD,
                    "EXPECTED_CONFIG_SHA": HEAD, "DEPLOY_ENGINE": "helmfile", "HELMFILE_OPERATION": "apply",
                    "COMPANION_NAMESPACE": "none", "GITHUB_STEP_SUMMARY": str(self.summary), "MAIN_SHA": CORE}

    def tearDown(self):
        self.temp.cleanup()

    def mode(self, mode):
        (self.root / "helmfile/images.yaml").write_text(f"mode: {mode}\n")

    def reconcile(self, cluster, checks=None, companion_gate=None, **env):
        self.checks = checks or Checks()
        self.gates = []

        def gate(run, root, namespace, open_reader):
            self.gates.append(namespace)
            return companion_gate(run, root, namespace, open_reader) if companion_gate else None

        return hd.reconcile(dict(self.env, **env), root=self.root, run=cluster, checks=self.checks,
                            live_images=lambda run: live_observed(), verify_charts=lambda root: {"charts": {}},
                            open_reader=lambda: self.fail("the test reads no live object"), companion_gate=gate)

    def record(self):
        return json.loads((self.root / hd.RECORD).read_text())

    def digest_inputs(self, receipt_sha=None):
        work = hd.workdir(self.root)
        sources = {name: {"repository": repo, "tag": "1" * 40, "sourceCommit": "1" * 40, "digest": DIGEST,
                          "platformManifest": PLATFORM, "reference": f"docker.io/{repo}:{'1' * 40}@{DIGEST}"}
                   for name, repo in (("jaeger", "jaegertracing/jaeger-snapshot"),
                                      ("hotrod", "jaegertracing/example-hotrod-snapshot"))}
        selection = {"mode": "promotion", "configCommit": HEAD, "coreMain": CORE, "platform": "linux/amd64",
                     "sources": sources, "images": {}}
        data = json.dumps(selection).encode()
        (work / hd.SELECTION).write_bytes(data)
        receipt = {"configCommit": HEAD, "selectionSHA256": receipt_sha or hashlib.sha256(data).hexdigest(),
                   "checks": {"oci": {"result": "passed"}, "otel": {"result": "passed"}}}
        (work / hd.RECEIPT).write_text(json.dumps(receipt))

    # ------------------------------------------------------------ commands

    def test_each_release_runs_alone_with_the_direct_flags_and_no_global_rollback(self):
        application = hd.helmfile_command(self.root, {"id": "default/jaeger", "kind": "application"}, "apply",
                                          "/v/images.json", "/v/companions.json")
        self.assertEqual(["helmfile", "-f", self.root / ".helmfile-locked.yaml", "--state-values-file", "/v/images.json",
                          "-l", "namespace=default,name=jaeger", "--skip-deps", "apply", "--concurrency", "1",
                          "--reset-values", "--sync-args=--install=false --create-namespace=false",
                          "--diff-args=--allow-unreleased=false", "--suppress-secrets", "--suppress-diff"], application)
        companion = hd.helmfile_command(self.root, {"id": "jaeger/demo-resources", "kind": "companion"}, "sync",
                                        "/v/images.json", "/v/companions.json")
        self.assertEqual(self.root / "helmfile-resources.yaml.gotmpl", companion[2])
        self.assertEqual("/v/companions.json", companion[4])
        self.assertIn("sync", companion)
        for command in (application, companion):
            text = " ".join(map(str, command))
            self.assertNotIn("rollback-on-failure", text)
            self.assertNotIn("--atomic", text)
            self.assertNotIn("take-ownership", text)
            self.assertNotIn("helmfile.yaml ", text)

    def test_all_ten_releases_run_oci_first_with_companions_after_their_stack(self):
        self.assertEqual(ORDER, [row["id"] for row in hd.release_rows(ROOT)])
        cluster = Cluster()
        self.mode("digest")
        self.digest_inputs()
        self.reconcile(cluster, GITHUB_EVENT_NAME="schedule", EXPECTED_CONFIG_SHA="")
        ran = [c[c.index("-l") + 1] for c in cluster.calls if c[0] == "helmfile"]
        self.assertEqual([f"namespace={k.split('/')[0]},name={k.split('/')[1]}" for k in ORDER], ran)
        self.assertEqual({str(self.root / hd.WORK / hd.SELECTION)}, {c[4] for c in cluster.calls
                                                                if c[0] == "helmfile" and "demo-resources" not in c[6]})

    # ------------------------------------------------------------ guards

    def assert_refused(self, message, cluster=None, **env):
        cluster = cluster or Cluster()
        with self.assertRaisesRegex(ValueError, message):
            self.reconcile(cluster, **env)
        self.assertEqual([], cluster.cluster_calls())
        self.assertEqual("refused", self.record()["outcome"])
        return cluster

    def test_guards_refuse_before_any_cluster_call(self):
        cases = [
            ("schedule or a manual dispatch", {"GITHUB_EVENT_NAME": "push"}),
            ("main only", {"GITHUB_REF": "refs/heads/topic"}),
            ("does not select", {"DEPLOY_ENGINE": "scripts"}),
            ("differs from the workflow revision", {"GITHUB_SHA": "a" * 40}),
            ("requires expected_config_sha", {"EXPECTED_CONFIG_SHA": ""}),
            ("requires expected_config_sha", {"EXPECTED_CONFIG_SHA": "a" * 40}),
            ("Unknown Helmfile operation", {"HELMFILE_OPERATION": "sync"}),
            ("only to install-companion", {"COMPANION_NAMESPACE": "default"}),
            ("needs one companion_namespace", {"HELMFILE_OPERATION": "install-companion"}),
            ("needs one companion_namespace", {"HELMFILE_OPERATION": "install-companion", "COMPANION_NAMESPACE": "kube-system"}),
            ("Only a manual dispatch installs", {"HELMFILE_OPERATION": "install-companion", "COMPANION_NAMESPACE": "default",
                                                 "GITHUB_EVENT_NAME": "schedule"}),
            ("scheduled Helmfile run resolves its images", {"GITHUB_EVENT_NAME": "schedule"}),
            ("remove the image tag inputs", {"JAEGER_DEMO_JAEGER_IMAGE_TAG": "abc"}),
        ]
        for message, env in cases:
            with self.subTest(message=message, env=env):
                self.assert_refused(message, **env)

    def test_a_bad_image_mode_is_refused(self):
        (self.root / "helmfile/images.yaml").write_text("mode: tags\n")
        self.assert_refused("must set mode")

    def test_a_missing_state_configmap_refuses_before_any_write(self):
        cluster = Cluster(state=False)
        with self.assertRaisesRegex(ValueError, "existing jaeger-demo-deployment-revisions"):
            self.reconcile(cluster)
        self.assertEqual([], cluster.writes())

    # ------------------------------------------------------------ images

    def test_failed_validation_leaves_no_receipt_and_reconcile_makes_no_cluster_call(self):
        self.mode("digest")
        renders = []

        def template(args, **kwargs):
            renders.append((args, kwargs.get("env", {}).get("KUBECONFIG")))
            return done(args, "kind: Deployment\n")

        def incompatible(*args):
            raise ValueError("Jaeger rejected the oci configuration")

        reference = f"docker.io/jaegertracing/jaeger-snapshot:{'1' * 40}@{DIGEST}"
        selection = {"configCommit": HEAD, "coreMain": CORE, "sources": {
            "jaeger": {"reference": reference, "digest": DIGEST, "sourceCommit": "1" * 40}}}
        output = io.StringIO()
        with self.assertRaisesRegex(ValueError, "rejected"), contextlib.redirect_stdout(output):
            hd.images(dict(self.env), root=self.root, run=template,
                      resolve=lambda event, overrides, core_main=None: selection, validate=incompatible)
        self.assertEqual(2, len(renders))
        for args, kubeconfig in renders:
            self.assertEqual(hd.os.devnull, kubeconfig)
            self.assertIn("template", args)
            self.assertEqual("helmfile", args[0])
        self.assertFalse((self.root / hd.WORK / hd.RECEIPT).exists())
        # The candidate is named before the gate runs, and its rejection is kept, without rendered values.
        log = output.getvalue()
        self.assertLess(log.index("Validating jaeger " + reference), log.index("rejected the candidate for stack oci"))
        gate = {"operation": "validate", "rejectedStack": "oci", "configCommit": HEAD, "coreMain": CORE,
                "type": "ValueError",
                "images": {"jaeger": {"reference": reference, "digest": DIGEST, "sourceCommit": "1" * 40}}}
        self.assertEqual(gate, json.loads((self.root / hd.WORK / hd.GATE).read_text()))
        early = self.record()
        self.assertEqual(("refused", gate, "images"), (early["outcome"], early["imageGate"], early["error"]["operation"]))
        self.assertNotIn("kind: Deployment", (self.root / hd.RECORD).read_text())
        # The early record names the configuration commit that rejected the images.
        self.assertEqual(HEAD, early["configCommit"])
        self.assertIn(f"Configuration commit: {HEAD}; upstream Jaeger main: {CORE}", self.summary.read_text())
        self.assertIn("Image gate rejected stack oci (ValueError): jaeger " + reference, self.summary.read_text())
        # Reconcile, should it run, refuses before any cluster call and folds the gate into its record.
        cluster = self.assert_refused("gate rejected the candidate images")
        self.assertEqual([["git", "-C", str(self.root), "rev-parse", "HEAD"]], cluster.calls)
        self.assertEqual(gate, self.record()["imageGate"])

    def test_a_companion_install_selects_and_validates_no_image(self):
        self.mode("digest")
        never = lambda *args, **kwargs: self.fail("a companion install resolved or rendered an image")  # noqa: E731
        env = dict(self.env, HELMFILE_OPERATION="install-companion", COMPANION_NAMESPACE="default")
        self.assertIsNone(hd.images(env, root=self.root, run=never, resolve=never, validate=never))
        with self.assertRaisesRegex(ValueError, "Only a manual dispatch installs"):
            hd.images(dict(env, GITHUB_EVENT_NAME="schedule"), root=self.root, run=never, resolve=never, validate=never)

    def test_images_passes_overrides_and_renders_both_jaeger_releases_offline(self):
        self.mode("digest")
        seen = {}

        def resolve(event, overrides, core_main=None):
            seen.update(event=event, overrides=overrides, core=core_main)
            return {"coreMain": core_main, "sources": {"jaeger": {"reference": "r", "sourceCommit": "s"}}}

        def validate(data, digest, renders, runtime):
            seen.update(sha=digest == hashlib.sha256(data).hexdigest(), stacks=sorted(renders), runtime=runtime)
            return {"selectionSHA256": digest}

        calls = []
        hd.images(dict(self.env, JAEGER_DEMO_HOTROD_IMAGE_TAG="abc"), root=self.root,
                  run=lambda args, **kw: calls.append(args) or done(args, "x"), resolve=resolve, validate=validate)
        # The selection is made for the upstream commit the workflow resolved once, MAIN_SHA.
        self.assertEqual({"event": "workflow_dispatch", "overrides": {"jaeger": None, "hotrod": "abc"}, "sha": True,
                          "stacks": ["oci", "otel"], "runtime": "docker", "core": CORE}, seen)
        self.assertEqual(["stack=oci,component=jaeger", "stack=otel,component=jaeger"],
                         [c[c.index("-l") + 1] for c in calls])
        self.assertTrue((self.root / hd.WORK / hd.RECEIPT).is_file())

    def test_images_selects_for_the_workflows_upstream_commit_only(self):
        self.mode("digest")

        def other(event, overrides, core_main=None):
            return {"coreMain": "9" * 40, "sources": {}}

        never = lambda *args, **kwargs: self.fail("rendered for another commit")  # noqa: E731
        with self.assertRaisesRegex(ValueError, "another upstream main commit"):
            hd.images(dict(self.env), root=self.root, run=never, resolve=other, validate=never)
        env = dict(self.env, GITHUB_ACTIONS="true")
        del env["MAIN_SHA"]
        with self.assertRaisesRegex(ValueError, "MAIN_SHA is required"):
            hd.images(env, root=self.root, run=never, resolve=other, validate=never)
        with self.assertRaisesRegex(ValueError, "not a full commit"):
            hd.images(dict(self.env, MAIN_SHA="main"), root=self.root, run=never, resolve=other, validate=never)

    def test_images_in_live_mode_does_nothing_but_refuses_a_schedule(self):
        hd.images(dict(self.env), root=self.root, run=None, resolve=None, validate=None)
        with self.assertRaisesRegex(ValueError, "resolves its images"):
            hd.images(dict(self.env, GITHUB_EVENT_NAME="schedule"), root=self.root)

    def test_a_receipt_for_another_selection_is_refused(self):
        self.mode("digest")
        self.digest_inputs(receipt_sha="0" * 64)
        self.assert_refused("receipt is for another selection")

    # ------------------------------------------------------------ live mode

    def test_live_mode_reuses_tag_references_without_digest_syntax(self):
        observed, policies = live_observed()
        values, record, expected = hd.live_image_values(observed, policies)
        for stack in ("oci", "otel"):
            for name in ("jaeger", "hotrod"):
                image = values["images"][stack][name]
                self.assertNotIn("@", image["tag"])
                self.assertEqual(observed[stack][name]["reference"], f"docker.io/{image['repository']}:{image['tag']}")
                self.assertEqual(DIGEST, record[stack][name]["digest"])
                # Afterwards each workload must still run exactly the live reference.
                self.assertEqual((observed[stack][name]["reference"], "1" * 40, DIGEST),
                                 (expected[stack][name]["reference"], expected[stack][name]["tag"],
                                  expected[stack][name]["digest"]))
        cluster = Cluster()
        self.reconcile(cluster)
        self.assertEqual(expected, self.checks.images)
        written = (self.root / hd.WORK / hd.LIVE_VALUES).read_text()
        self.assertNotIn("sha256", written)
        self.assertEqual(hd.NOT_REVALIDATED, self.record()["compatibility"])
        self.assertEqual(CORE, self.record()["coreMain"])
        for ref in (f"docker.io/jaegertracing/jaeger-snapshot:{'1' * 40}@{DIGEST}",
                    f"jaegertracing/jaeger-snapshot:{'1' * 40}"):
            observed["oci"]["jaeger"]["reference"] = ref
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, "not a tag in the form"):
                hd.live_image_values(observed, policies)

    # ------------------------------------------------------------ phases

    def test_an_empty_diff_still_verifies_both_stacks_and_creates_no_revision(self):
        cluster = Cluster()
        record = self.reconcile(cluster)
        self.assertEqual([("prepare", ("oci", "otel")), ("verify", "oci"), ("verify", "otel")], self.checks.calls)
        self.assertEqual(["oci", "otel"], record["verifiedStacks"])
        self.assertTrue(all(r["revisionBefore"] == r["revisionAfter"] == 4 and not r["changed"] and r["method"] == "apply"
                            for r in record["releases"]))
        self.assertEqual("succeeded", self.record()["outcome"])

    def digest(self):
        """A routine deployment after adoption: digest mode with a validated selection."""
        self.mode("digest")
        self.digest_inputs()

    def test_a_failed_release_with_an_empty_diff_is_synced(self):
        self.digest()
        cluster = Cluster({"default/prometheus": "failed"})
        record = self.reconcile(cluster)
        prometheus, = [c for c in cluster.calls if c[0] == "helmfile" and "namespace=default,name=prometheus" in c]
        self.assertEqual("sync", prometheus[prometheus.index("--skip-deps") + 1])
        entry = record["releases"][0]
        self.assertEqual(("default/prometheus", "failed", 4, "sync", 5, True),
                         (entry["id"], entry["statusBefore"], entry["revisionBefore"], entry["method"],
                          entry["revisionAfter"], entry["changed"]))

    def test_a_pending_or_unknown_release_refuses_before_any_write(self):
        for status in ("pending-upgrade", "pending-rollback", "pending-install", "unknown", None):
            with self.subTest(status=status):
                cluster = Cluster({"otel-demo/otel-demo": status})
                with self.assertRaisesRegex(ValueError, "operator review"):
                    self.reconcile(cluster)
                self.assertEqual([], cluster.writes())
                self.assertEqual(["prepare"], [c[0] for c in self.checks.calls])
                self.assertEqual("refused", self.record()["outcome"])

    def test_failed_oci_verification_stops_before_any_otel_call(self):
        self.digest()
        cluster = Cluster(changes={"default/jaeger", "jaeger/jaeger"})
        with self.assertRaisesRegex(RuntimeError, "oci health failed"):
            self.reconcile(cluster, Checks(fail={"oci"}))
        otel = [c for c in cluster.calls if c[0] == "helmfile" and not c[c.index("-l") + 1].startswith("namespace=default,")]
        self.assertEqual([], otel)
        # Only the OCI generator restart that precedes the OCI checks: no OTel restart, no fingerprint.
        self.assertEqual([["kubectl", "-n", "default", "rollout", "restart", "deployment/trace-generator"]],
                         [c for c in cluster.writes() if c[0] == "kubectl"])
        self.assertEqual([("prepare", ("oci", "otel")), ("verify", "oci")], self.checks.calls)
        record = self.record()
        self.assertEqual("failed", record["outcome"])
        self.assertEqual([], record["verifiedStacks"])
        # The failed stack keeps the bound its checks ran against, which is the one they were given;
        # OTel never started, so it has none.
        self.assertEqual(["oci"], list(record["evidenceSince"]))
        self.assertEqual(self.checks.since["oci"].isoformat(), record["evidenceSince"]["oci"])
        self.assertEqual({}, record["checks"])
        self.assertIn(f"- Health evidence newer than: oci {record['evidenceSince']['oci']}", hd.summary(record))
        self.assertEqual([(5, "apply"), (None, None)], [(r["revisionAfter"], r["method"]) for r in record["releases"]
                                                        if r["id"] in ("default/jaeger", "jaeger/jaeger")])

    def test_fingerprints_are_patched_one_key_at_a_time_per_verified_stack(self):
        cluster = Cluster()
        with self.assertRaises(RuntimeError):
            self.reconcile(cluster, Checks(fail={"otel"}))
        patches = [json.loads(c[-1]) for c in cluster.calls if c[:4] == ["kubectl", "-n", "default", "patch"]]
        self.assertEqual([{"data": {name: deploy.fingerprint(name, self.root)}} for name in ("oci-jaeger", "oci-monitoring")],
                         patches)
        self.assertEqual(["oci-jaeger", "oci-monitoring"], self.record()["fingerprints"])
        # Each stack's bound is taken when its own checks start, OCI before OTel, and is kept on failure.
        bounds = self.record()["evidenceSince"]
        self.assertEqual(["oci", "otel"], list(bounds))
        self.assertEqual({stack: at.isoformat() for stack, at in self.checks.since.items()}, bounds)
        self.assertLessEqual(self.checks.since["oci"], self.checks.since["otel"])
        cluster = Cluster()
        self.reconcile(cluster)
        patched = [list(json.loads(c[-1])["data"]) for c in cluster.calls if c[:4] == ["kubectl", "-n", "default", "patch"]]
        self.assertEqual([[name] for name in ("oci-jaeger", "oci-monitoring", "opensearch", "otel-jaeger", "otel-demo")],
                         patched)

    def test_the_generator_restarts_only_on_a_digest_apply_and_before_the_stack_checks(self):
        self.mode("digest")
        self.digest_inputs()
        cluster = Cluster()
        record = self.reconcile(cluster, Checks(log=cluster.calls), GITHUB_EVENT_NAME="schedule", EXPECTED_CONFIG_SHA="")
        steps = [c[:2] if c[0] == "verify" else ("restart", c[2]) for c in cluster.calls
                 if c[0] == "verify" or "restart" in c]
        self.assertEqual([("restart", "default"), ["verify", "oci"], ("restart", "jaeger"), ["verify", "otel"]], steps)
        self.assertEqual(["oci", "otel"], record["generatorRestarts"])
        # Each restart waits for its rollout before the checks.
        restart = [i for i, c in enumerate(cluster.calls) if "restart" in c][0]
        self.assertEqual(["kubectl", "-n", "default", "rollout", "status", "deployment/trace-generator", "--timeout=5m"],
                         cluster.calls[restart + 1])
        # Live mode is adoption: no pod template may change, so nothing restarts.
        self.mode("live")
        cluster = Cluster()
        record = self.reconcile(cluster)
        self.assertEqual([], [c for c in cluster.calls if "restart" in c])
        self.assertEqual([], record["generatorRestarts"])

    def test_install_companion_installs_only_an_absent_named_companion_without_atomic_or_ownership(self):
        env = {"HELMFILE_OPERATION": "install-companion", "COMPANION_NAMESPACE": "jaeger"}
        cluster = Cluster({"jaeger/demo-resources": None})

        def no_image_read(run):
            raise AssertionError("A companion install read the live images")

        self.checks = Checks()
        gates = []
        record = hd.reconcile(dict(self.env, **env), root=self.root, run=cluster, checks=self.checks,
                              live_images=no_image_read, verify_charts=lambda root: {"charts": {}},
                              companion_gate=lambda run, root, ns, reader: gates.append(ns))
        # The drift gate ran for that namespace before the install, and no image is checked afterwards.
        self.assertEqual((["jaeger"], None), (gates, self.checks.images))
        install_index = [i for i, c in enumerate(cluster.calls) if c[:2] == ["helm", "install"]][0]
        self.assertIn(["helm", "history", "demo-resources", "-n", "jaeger", "--max", "1", "-o", "json"],
                      cluster.calls[:install_index])
        # Adoption changes no pod template: a companion install never restarts a generator.
        self.assertEqual([], [c for c in cluster.calls if "restart" in c])
        self.assertEqual((None, hd.NOT_USED), (record["images"], record["compatibility"]))
        # Every allowlisted object of the namespace was read before the install.
        reads = [c for c in cluster.calls if c[:4] == ["kubectl", "-n", "jaeger", "get"]]
        self.assertEqual({(RESOURCES[c[4]], c[5]) for c in reads},
                         {(kind, name) for kind, names in ALLOWLIST["jaeger"].items() for name in names})
        install, = [c for c in cluster.calls if c[:2] == ["helm", "install"]]
        self.assertEqual(["helm", *release_phases.install_args("jaeger", self.root / "charts/demo-resources",
                                                               self.root / hd.WORK / "companion-jaeger.json")], install)
        self.assertNotIn("--take-ownership", install)
        self.assertIn("--rollback-on-failure=false", install)
        self.assertEqual([], [c for c in cluster.calls if c[0] == "helmfile"])
        self.assertEqual(["jaeger/demo-resources"], [r["id"] for r in record["releases"]])
        self.assertEqual(("absent", None, 1, True), (record["releases"][0]["statusBefore"], record["releases"][0]["revisionBefore"],
                                                     record["releases"][0]["revisionAfter"], record["releases"][0]["changed"]))
        self.assertEqual(["oci", "otel"], record["verifiedStacks"])
        self.assertEqual([], record["fingerprints"])
        values = json.loads((self.root / hd.WORK / "companion-jaeger.json").read_text())
        self.assertEqual({"trace-script", "trace-generator", "jaeger-demo-ingress", "jaeger-query-clusterip"},
                         {o["metadata"]["name"] for o in values["objects"]})
        # A present companion is never reinstalled.
        cluster = Cluster()
        with self.assertRaisesRegex(ValueError, "not absent"):
            self.reconcile(cluster, **env)
        self.assertEqual([], cluster.writes())

    def test_a_companion_install_refuses_a_missing_or_unadopted_object(self):
        env = {"HELMFILE_OPERATION": "install-companion", "COMPANION_NAMESPACE": "default"}
        service = ("default", "Service", "prometheus")
        for case, change, message in (
                ("missing", lambda c: c.objects.pop(service), "default/Service/prometheus is missing"),
                ("unadopted", lambda c: c.objects.update({service: companion_object(*service, owned=False)}),
                 "lacks the reviewed demo-resources ownership"),
                ("foreign", lambda c: c.objects[service]["metadata"]["annotations"].update(
                    {"meta.helm.sh/release-name": "other"}), "foreign or conflicting ownership")):
            with self.subTest(case=case):
                cluster = Cluster({"default/demo-resources": None})
                change(cluster)
                with self.assertRaisesRegex(ValueError, message):
                    self.reconcile(cluster, **env)
                self.assertEqual([], cluster.writes())
                self.assertEqual("refused", self.record()["outcome"])

    def test_a_companion_install_refuses_while_any_release_is_pending(self):
        cluster = Cluster({"otel-demo/demo-resources": None, "opensearch/opensearch": "pending-upgrade"})
        with self.assertRaisesRegex(ValueError, "no release is pending: opensearch/opensearch"):
            self.reconcile(cluster, HELMFILE_OPERATION="install-companion", COMPANION_NAMESPACE="otel-demo")
        self.assertEqual([], cluster.writes())

    def test_a_listing_that_disagrees_with_the_named_history_refuses(self):
        cluster = Cluster()
        cluster.listed_revision["jaeger/jaeger"] = 3
        with self.assertRaisesRegex(ValueError, "jaeger/jaeger changed between its listing and its history"):
            self.reconcile(cluster)
        self.assertEqual([], cluster.writes())

    def test_a_release_that_is_not_deployed_after_its_write_fails_the_run(self):
        self.digest()
        cluster = Cluster({"default/prometheus": "failed"})
        cluster.stuck.add("default/prometheus")
        with self.assertRaisesRegex(RuntimeError, "default/prometheus is failed after sync, not deployed"):
            self.reconcile(cluster)
        record = self.record()
        self.assertEqual(("failed", "reconcile", "default/prometheus", "Failed"),
                         (record["outcome"], record["error"]["operation"], record["error"]["release"],
                          record["error"]["type"]))
        self.assertEqual([], self.checks.calls[1:])

    def test_helm_context_overrides_are_refused(self):
        for name, value in (("HELM_DRIVER", "configmap"), ("HELM_KUBECONTEXT", "prod"), ("HELM_NAMESPACE", "jaeger"),
                            ("HELMFILE_KUBE_CONTEXT", "prod"), ("HELMFILE_NAMESPACE", "jaeger"),
                            ("HELMFILE_ENVIRONMENT", "other"), ("HELMFILE_HELM_BINARY", "/tmp/helm"),
                            ("HELM_KUBEAPISERVER", "https://203.0.113.7:6443"), ("HELM_KUBETOKEN", "t0ken"),
                            ("HELM_KUBEASUSER", "admin"), ("HELM_KUBEASGROUPS", "system:masters"),
                            ("HELM_KUBECAFILE", "/tmp/ca.crt"), ("HELM_KUBETLS_SERVER_NAME", "other.test"),
                            ("HELM_KUBEINSECURE_SKIP_TLS_VERIFY", "true"), ("HELM_MAX_HISTORY", "0")):
            with self.subTest(name=name):
                self.assert_refused(name if name != "HELM_DRIVER" else "unset HELM_DRIVER", **{name: value})
                with self.assertRaisesRegex(ValueError, "HELM"):
                    hd.diff(dict(self.env, **{name: value}), root=self.root, run=Cluster())
        hd.check_helm_context({"HELM_DRIVER": "secret"})
        # Every connection setting Helm reads from the environment is refused.
        self.assertTrue({"HELM_KUBEAPISERVER", "HELM_KUBETOKEN", "HELM_KUBEASUSER", "HELM_KUBEASGROUPS",
                         "HELM_KUBECAFILE", "HELM_KUBETLS_SERVER_NAME",
                         "HELM_KUBEINSECURE_SKIP_TLS_VERIFY"} <= set(hd.HELM_OVERRIDES))

    def test_each_verified_stack_is_recorded_as_its_checks_pass(self):
        cluster = Cluster()
        with self.assertRaises(RuntimeError):
            self.reconcile(cluster, Checks(fail={"otel"}))
        record = self.record()
        self.assertEqual((["oci"], "verify", "otel"), (record["verifiedStacks"], record["error"]["operation"],
                                                      record["error"]["stack"]))

    def test_errors_are_recorded_without_command_output_or_command_lines(self):
        cluster = Cluster()
        cluster.failing.add("default/jaeger")
        cluster.broken_history.add("default/jaeger")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(subprocess.CalledProcessError):
            self.reconcile(cluster)
        # The failed history read neither replaced the command's error nor left the log group open.
        self.assertEqual(output.getvalue().count("::group::"), output.getvalue().count("::endgroup::"))
        record = self.record()
        self.assertEqual({"operation": "reconcile", "release": "default/jaeger", "stack": None,
                          "type": "CalledProcessError", "message": hd.FAILURES["reconcile"]}, record["error"])
        for text in ((self.root / hd.RECORD).read_text(), self.summary.read_text()):
            for leaked in ("SECRET-TOKEN", "10.0.0.1", "helmfile -f", "--state-values-file"):
                self.assertNotIn(leaked, text)

    def test_main_prints_no_traceback_or_exception_data(self):
        stderr = io.StringIO()
        with patch.object(hd, "pinned_tools", return_value=True), \
                patch.object(hd, "reconcile", side_effect=RuntimeError("token=SECRET-TOKEN https://10.0.0.1")), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(1, hd.main(["reconcile"]))
        self.assertNotIn("SECRET-TOKEN", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertIn("reconcile failed (RuntimeError)", stderr.getvalue())
        with patch.object(hd, "pinned_tools", return_value=True), \
                patch.object(hd, "reconcile", side_effect=hd.Refused("A manual Helmfile run requires expected_config_sha")), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(1, hd.main(["reconcile"]))
        self.assertIn("requires expected_config_sha", stderr.getvalue())

    def test_an_absent_companion_blocks_apply(self):
        cluster = Cluster({"opensearch/demo-resources": None})
        with self.assertRaisesRegex(ValueError, "explicit first install"):
            self.reconcile(cluster)
        self.assertEqual([], cluster.writes())

    # ------------------------------------------------------------ record

    def test_the_record_holds_provenance_but_no_value_secret_or_diff(self):
        self.mode("digest")
        self.digest_inputs()
        cluster = Cluster(changes={"default/jaeger"})
        self.reconcile(cluster, GITHUB_EVENT_NAME="schedule", EXPECTED_CONFIG_SHA="")
        record = self.record()

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for value in node:
                    yield from keys(value)

        self.assertEqual(set(), FORBIDDEN_KEYS & set(keys(record)))
        text = (self.root / hd.RECORD).read_text()
        generator = (ROOT / "examples/trace-generator/generate_traces.py").read_text()
        self.assertNotIn(generator[:200], text)
        self.assertEqual((HEAD, CORE, "digest", "succeeded"),
                         (record["configCommit"], record["coreMain"], record["imageMode"], record["outcome"]))
        self.assertEqual({"oci", "otel"}, set(record["images"]))
        self.assertEqual({"reference", "sourceCommit", "digest"}, set(record["images"]["oci"]["jaeger"]))
        self.assertEqual({"helm", "helmfile", "helm-diff", "kubeconform"}, set(record["tools"]))
        self.assertEqual("v4.3.2", record["tools"]["helm"]["version"])
        self.assertEqual(["default/jaeger"], [r["id"] for r in record["releases"] if r["changed"]])
        self.assertIn("passed for selection", self.summary.read_text())
        self.assertIn("| default/jaeger | deployed | 4 | apply | 5 | yes |", self.summary.read_text())

    # ------------------------------------------------------------ locked charts

    def test_locked_charts_must_be_current_and_match_the_lock(self):
        root = self.root
        (root / "scripts").unlink()
        (root / "scripts/demo").mkdir(parents=True)
        lock = {"charts": {"a-1.0.0.tgz": hashlib.sha256(b"a").hexdigest()}, "jaegerDependencies": {}}
        (root / "scripts/demo/chart-archives.lock.json").write_text(json.dumps(lock))
        (root / ".tools/chart-archives").mkdir(parents=True)
        (root / ".tools/chart-archives/a-1.0.0.tgz").write_bytes(b"a")
        (root / ".tools/helm-charts/charts/jaeger/charts").mkdir(parents=True)

        def state(source_sha=None):
            source = source_sha or hashlib.sha256((root / "helmfile.yaml").read_bytes()).hexdigest()
            locked = hashlib.sha256((root / "scripts/demo/chart-archives.lock.json").read_bytes()).hexdigest()
            (root / ".helmfile-locked.yaml").write_text(
                f"# Generated\n# helmfile.yaml sha256: {source}\n# scripts/demo/chart-archives.lock.json sha256: {locked}\n"
                "releases:\n  - {name: a, namespace: default, chart: ./.tools/chart-archives/a-1.0.0.tgz}\n"
                "  - {name: jaeger, namespace: default, chart: ./.tools/helm-charts/charts/jaeger}\n")

        pinned = hd.jaeger_chart_commit()

        def at(commit):
            return lambda path: commit if path == root / ".tools/helm-charts" else None

        state()
        verified = hd.verify_locked_charts(root, head=at(pinned))
        self.assertEqual(({"a-1.0.0.tgz": lock["charts"]["a-1.0.0.tgz"]}, pinned),
                         (verified["charts"], verified["jaegerChartCommit"]))
        for moved in ("0" * 40, None):
            with self.subTest(head=moved), self.assertRaisesRegex(ValueError, "not at the pinned commit"):
                hd.verify_locked_charts(root, head=at(moved))
        state("0" * 64)
        with self.assertRaisesRegex(ValueError, "stale"):
            hd.verify_locked_charts(root, head=at(pinned))
        state()
        (root / ".tools/chart-archives/a-1.0.0.tgz").write_bytes(b"b")
        with self.assertRaisesRegex(ValueError, "differs from its reviewed SHA-256"):
            hd.verify_locked_charts(root, head=at(pinned))
        (root / ".helmfile-locked.yaml").unlink()
        with self.assertRaisesRegex(ValueError, "run helmfile_deploy.py prepare"):
            hd.verify_locked_charts(root, head=at(pinned))

    def test_prepare_fails_fast_pins_the_chart_commit_and_pulls_without_a_cluster(self):
        calls, pulls = [], []

        def run(args, **kwargs):
            calls.append(([str(a) for a in args], kwargs.get("env", {}).get("KUBECONFIG")))
            return done(args)

        def locked_state(root, dest, run=None):
            # Stand in for chart_archives.locked_state: record the environment its helm pull gets.
            env = {}
            with patch.object(hd.subprocess, "run", side_effect=lambda args, **kw: env.update(kw.get("env", {}))):
                run(["helm", "pull", "x"])
            pulls.append(env.get("KUBECONFIG"))
            return root / ".helmfile-locked.yaml"

        with patch.object(hd.chart_archives, "dependencies"), \
                patch.object(hd, "verify_locked_charts", return_value={"charts": {}}):
            hd.prepare(self.root, run=run, locked_state=locked_state, head=lambda path: hd.jaeger_chart_commit())
        (command, kubeconfig), = calls
        self.assertEqual(["bash", "-c", 'set -euo pipefail; source "$1"; prepare_jaeger_chart "$2"'], command[:3])
        self.assertEqual((hd.os.devnull, [hd.os.devnull]), (kubeconfig, pulls))
        with patch.object(hd.chart_archives, "dependencies") as dependencies, \
                self.assertRaisesRegex(ValueError, "not at the pinned commit"):
            hd.prepare(self.root, run=run, locked_state=locked_state, head=lambda path: "0" * 40)
        dependencies.assert_not_called()

    # ------------------------------------------------------------ production checks

    def test_production_checks_compare_the_data_binding_and_require_ready_certificates(self):
        calls = []
        bindings = iter([("claim", "uid", "pv"), ("claim", "uid", "pv")])
        expected = {"jaeger": "jaeger-demo-tls", "opensearch": "opensearch-demo-tls", "otel-demo": "otel-demo-tls"}
        # Derived from the issuer-annotated Ingresses of the companion sources, not typed in.
        self.assertEqual(sorted(expected.items()), hd.expected_certificates(ROOT))
        certificates = {ns: {"items": [{"metadata": {"name": name}, "status": {"conditions": [
            {"type": "Ready", "status": "True"}]}}]} for ns, name in expected.items()}

        def run(args, **kwargs):
            calls.append([str(a) for a in args])
            return done(args, json.dumps(certificates.get(args[2], {"items": []})))

        checks = hd.ProductionChecks(ROOT, run, health=lambda stack, since=None: {"stack": stack},
                                     trace=lambda *versions: calls.append(["trace", *versions]),
                                     binding=lambda: next(bindings), sleep=lambda s: None)
        checks.prepare({"oci", "otel"})
        evidence = checks.verify("otel")
        self.assertEqual([f"{ns}/{name}" for ns, name in sorted(expected.items())], evidence["readyCertificates"])
        self.assertIn(["trace", "3.10.0", "3.10.0"], calls)
        self.assertEqual(len(hd.OTEL_ROLLOUTS), len([c for c in calls if "rollout" in c]))
        self.assertTrue(evidence["openSearchDataBindingUnchanged"])
        bindings = iter([("claim", "uid", "pv"), ("claim", "uid", "other-pv")])
        checks = hd.ProductionChecks(ROOT, run, health=lambda stack, since=None: {}, trace=lambda *v: None,
                                     binding=lambda: next(bindings), sleep=lambda s: None)
        checks.prepare({"otel"})
        with self.assertRaisesRegex(RuntimeError, "data claim or its bound volume changed"):
            checks.verify("otel")
        certificates["opensearch"]["items"][0]["status"]["conditions"][0]["status"] = "False"
        checks = hd.ProductionChecks(ROOT, run, health=lambda stack, since=None: {}, trace=lambda *v: None,
                                     binding=lambda: ("c", "u", "p"), sleep=lambda s: None)
        checks.prepare({"otel"})
        with self.assertRaisesRegex(RuntimeError, "opensearch/opensearch-demo-tls is not Ready"):
            checks.verify("otel")
        # A missing expected Certificate fails even when every listed one is Ready.
        certificates["opensearch"]["items"][0]["status"]["conditions"][0]["status"] = "True"
        certificates["otel-demo"]["items"][0]["metadata"]["name"] = "another-tls"
        with self.assertRaisesRegex(RuntimeError, "otel-demo/otel-demo-tls is missing"):
            checks.certificates()
        oci = hd.ProductionChecks(ROOT, run, health=lambda stack, since=None: {"stack": stack}, sleep=lambda s: None)
        calls.clear()
        self.assertEqual({"stack": "oci"}, oci.verify("oci")["health"])
        self.assertEqual([["kubectl", "-n", "default", "rollout", "status", f"deployment/{name}", "--timeout=5m"]
                          for name in ("jaeger", "jaeger-hotrod", "trace-generator", "prometheus-grafana")], calls)

    def test_health_must_be_newer_than_the_start_of_the_stack_checks(self):
        # Issue #160: a bucket from before the stack's writes must not pass its verification.
        utc = timezone.utc
        started = datetime(2026, 11, 25, 16, 37, 10, tzinfo=utc)
        now, slept, asked = [started], [], []

        def sleep(seconds):
            slept.append(seconds)
            now[0] += timedelta(seconds=seconds)

        def health(stack, since=None):
            asked.append((stack, since, now[0]))
            if now[0] < datetime(2026, 11, 25, 16, 42, tzinfo=utc):
                raise stack_health.TrafficPending("No recent completed SPM bucket with positive calls")
            return {"stack": stack}

        def run(args, **kwargs):
            # The rollouts take time too; the wait covers only what is left.
            now[0] += timedelta(seconds=5)
            return done(args)

        checks = hd.ProductionChecks(ROOT, run, health=health, sleep=sleep, clock=lambda: now[0])
        self.assertEqual({"stack": "oci"}, checks.verify("oci")["health"])
        # One wait up to the first bucket that can lie wholly after the start, then the retries.
        ready = stack_health.evidence_ready_at(started)
        self.assertEqual(datetime(2026, 11, 25, 16, 41, tzinfo=utc), ready)
        self.assertEqual([(ready - started).total_seconds() - 5 * len(hd.OCI_ROLLOUTS), 30, 30], slept)
        self.assertEqual([("oci", started, ready), ("oci", started, ready + timedelta(seconds=30)),
                          ("oci", started, ready + timedelta(seconds=60))], asked)

        # No orders after the change: the run fails after the bounded retries, never on older buckets.
        slept.clear()
        asked.clear()
        now[0] = started

        def stalled(stack, since=None):
            asked.append(since)
            raise stack_health.TrafficPending("No recent completed SPM bucket with positive calls")

        checks = hd.ProductionChecks(ROOT, run, health=stalled, sleep=sleep, clock=lambda: now[0])
        with self.assertRaises(stack_health.TrafficPending):
            checks.verify("oci")
        self.assertEqual([started] * hd.HEALTH_ATTEMPTS, asked)
        self.assertEqual(1 + (hd.HEALTH_ATTEMPTS - 1), len(slept))
        self.assertLessEqual(sum(slept), 4 * 60 + (hd.HEALTH_ATTEMPTS - 1) * hd.HEALTH_DELAY)

        # Checks that already took longer than the wait add none.
        slept.clear()
        now[0] = started

        def slow(args, **kwargs):
            now[0] += timedelta(minutes=2)
            return done(args)

        checks = hd.ProductionChecks(ROOT, slow, health=lambda stack, since=None: {"since": since}, sleep=sleep,
                                     clock=lambda: now[0])
        self.assertEqual({"since": started}, checks.verify("oci")["health"])
        self.assertEqual([], slept)
        # reconcile passes the bound it recorded; the checks use that one, not their own clock.
        recorded = started - timedelta(minutes=10)
        self.assertEqual({"since": recorded}, checks.verify("oci", recorded)["health"])

    # ------------------------------------------------------------ second review

    def test_running_images_must_match_the_expected_references_and_digests(self):
        reference = f"docker.io/jaegertracing/jaeger-snapshot:{'1' * 40}@{DIGEST}"
        hotrod = f"docker.io/jaegertracing/example-hotrod-snapshot:{'1' * 40}@{DIGEST}"
        expected = {stack: {"jaeger": hd.expected_image(reference, "1" * 40, {"sourceCommit": "1" * 40, "digest": DIGEST,
                                                                             "platformManifest": PLATFORM}),
                            "hotrod": hd.expected_image(hotrod, "1" * 40, {"sourceCommit": "1" * 40, "digest": DIGEST,
                                                                           "platformManifest": PLATFORM})}
                    for stack in ("oci", "otel")}
        running = {("default", "jaeger"): (reference, PLATFORM), ("default", "jaeger-hotrod"): (hotrod, DIGEST)}

        def check(workloads):
            checks = hd.ProductionChecks(ROOT, lambda *a, **k: done(a), open_reader=lambda: fake_api(workloads))
            checks.prepare({"oci"}, images=expected)
            return checks.running_images("oci")

        self.assertEqual({"jaeger": reference, "hotrod": hotrod}, check(running))
        other = "sha256:" + "0" * 64
        for case, changed in (("pod digest", {("default", "jaeger"): (reference, other)}),
                              ("reference", {("default", "jaeger-hotrod"): (hotrod.replace("1" * 40, "2" * 40), DIGEST)})):
            with self.subTest(case=case), self.assertRaisesRegex(RuntimeError, "image differs from the expected one"):
                check({**running, **changed})
        # A companion install expects no image, and reads none.
        checks = hd.ProductionChecks(ROOT, None, open_reader=lambda: self.fail("read images"))
        checks.prepare({"oci"})
        self.assertIsNone(checks.running_images("oci"))

    def test_a_live_mode_apply_that_would_change_a_release_is_refused_before_any_write(self):
        cluster = Cluster(changes={"otel-demo/otel-demo"})
        with self.assertRaisesRegex(ValueError, "Adoption would change otel-demo/otel-demo .*live-diff"):
            self.reconcile(cluster)
        self.assertEqual([], cluster.writes())
        record = self.record()
        self.assertEqual(("refused", {"gate": "live-diff", "release": "otel-demo/otel-demo", "result": "changes"}),
                         (record["outcome"], record["adoptionGate"]))
        self.assertIn("Adoption gate live-diff refused otel-demo/otel-demo: changes", self.summary.read_text())
        diffs = [c for c in cluster.calls if c[0] == "helmfile"]
        self.assertTrue(diffs and all("diff" in c and "--detailed-exitcode" in c and "--reset-values" in c for c in diffs))
        self.assertEqual(str(self.root / hd.WORK / hd.LIVE_VALUES), diffs[0][4])
        # A digest-mode apply is a routine deployment, not adoption: changes are its purpose.
        self.digest()
        cluster = Cluster(changes={"otel-demo/otel-demo"})
        self.reconcile(cluster)
        self.assertEqual([], [c for c in cluster.calls if c[0] == "helmfile" and "diff" in c])

    def test_the_companion_drift_gate_refuses_anything_but_ownership(self):
        sources = companion_values.source_values(ROOT)["companions"]["jaeger"]["objects"]
        rendered = [dict(obj, metadata=dict(obj["metadata"], namespace="jaeger", annotations=dict(
            obj["metadata"].get("annotations") or {}, **{"helm.sh/resource-policy": "keep"}))) for obj in sources]

        def live(obj, owned=True):
            obj = json.loads(json.dumps(obj))
            meta = obj["metadata"]
            meta.update(uid="uid-" + meta["name"], resourceVersion="3")
            if owned:
                meta["labels"] = dict(meta.get("labels") or {}, **{"app.kubernetes.io/managed-by": "Helm"})
                meta["annotations"].update({"meta.helm.sh/release-name": "demo-resources",
                                            "meta.helm.sh/release-namespace": "jaeger"})
            else:
                meta["annotations"].pop("helm.sh/resource-policy")
                if not meta["annotations"]:
                    del meta["annotations"]  # the API server stores no empty map
            return obj

        def gate(objects):
            def run(args, **kwargs):
                self.assertEqual((["helm", "template", "demo-resources"], hd.os.devnull),
                                 ([str(a) for a in args[:3]], kwargs["env"]["KUBECONFIG"]))
                return done(args, yaml.safe_dump_all(rendered))
            hd.workdir(self.root)
            return hd.companion_drift_gate(run, self.root, "jaeger", lambda: fake_api(objects=objects))

        adopted = {(o["kind"], o["metadata"]["name"]): live(o) for o in rendered}
        self.assertIsNone(gate(adopted))
        generator = ("Deployment", "trace-generator")
        changed = copy.deepcopy(adopted)
        changed[generator]["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": "FIXTURE", "value": "1"}]
        unadopted = {**adopted, generator: live(rendered[[o["kind"] for o in rendered].index("Deployment")], owned=False)}
        missing = {k: v for k, v in adopted.items() if k != generator}
        for case, objects, kind in (("pod template", changed, "unexplained"), ("unadopted", unadopted, "explained"),
                                    ("missing", missing, "missing")):
            with self.subTest(case=case):
                result = gate(objects)
                self.assertEqual(("companion-drift", "jaeger/demo-resources"), (result["gate"], result["release"]))
                self.assertEqual([{"identity": "apps/v1:Deployment:jaeger:trace-generator", "class": kind}],
                                 result["objects"])

    def test_a_refusing_companion_gate_stops_the_install_and_is_recorded(self):
        cluster = Cluster({"jaeger/demo-resources": None})
        refusal = {"gate": "companion-drift", "release": "jaeger/demo-resources",
                   "objects": [{"identity": "apps/v1:Deployment:jaeger:trace-generator", "class": "unexplained"}]}
        with self.assertRaisesRegex(ValueError, "Adoption would change jaeger/demo-resources"):
            self.reconcile(cluster, companion_gate=lambda *args: refusal, HELMFILE_OPERATION="install-companion",
                           COMPANION_NAMESPACE="jaeger")
        self.assertEqual([], cluster.writes())
        self.assertEqual(refusal, self.record()["adoptionGate"])
        self.assertIn("apps/v1:Deployment:jaeger:trace-generator (unexplained)", self.summary.read_text())

    def test_a_companion_install_refuses_an_unlisted_release_that_is_not_deployed(self):
        env = {"HELMFILE_OPERATION": "install-companion", "COMPANION_NAMESPACE": "jaeger"}
        # Helm's listing omits an unknown release; the named read finds it.
        cluster = Cluster({"jaeger/demo-resources": None, "default/prometheus": "unknown"})
        with self.assertRaisesRegex(ValueError, "every present release is deployed: default/prometheus is unknown"):
            self.reconcile(cluster, **env)
        self.assertEqual([], cluster.writes())
        # Other companions that are not installed yet are absent, not a reason to refuse.
        cluster = Cluster({"jaeger/demo-resources": None, "otel-demo/demo-resources": None,
                           "opensearch/demo-resources": None})
        self.assertEqual("succeeded", self.reconcile(cluster, **env)["outcome"])

    def test_diff_checks_the_locked_charts_first(self):
        cluster = Cluster()

        def stale(root):
            raise hd.Refused(".helmfile-locked.yaml is stale: helmfile.yaml changed after prepare")

        with self.assertRaisesRegex(ValueError, "is stale"):
            hd.diff(dict(self.env), root=self.root, run=cluster, verify_charts=stale)
        self.assertEqual([], cluster.calls)
        verified = []
        hd.diff(dict(self.env), root=self.root, run=cluster, live_images=lambda run: live_observed(),
                verify_charts=lambda root: verified.append(root))
        self.assertEqual([self.root], verified)

    def test_an_unreadable_history_after_a_write_records_unknown_not_changed(self):
        cluster = Cluster()
        cluster.broken_history.add("default/prometheus")
        with self.assertRaisesRegex(RuntimeError, "default/prometheus is unreadable after apply"):
            self.reconcile(cluster)
        entry = self.record()["releases"][0]
        self.assertEqual(("default/prometheus", None, None, None),
                         (entry["id"], entry["revisionAfter"], entry["statusAfter"], entry["changed"]))
        self.assertIn("| default/prometheus | deployed | 4 | apply | unknown | unknown |", self.summary.read_text())
        self.assertIn("| default/jaeger | deployed | 4 | not run | - | no |", self.summary.read_text())

    def test_health_is_retried_until_it_passes_or_the_attempts_run_out(self):
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise ValueError("No recent matching spans")
            return "ok"

        self.assertEqual("ok", hd.retry(flaky, attempts=5, sleep=lambda s: None))
        attempts.clear()
        with self.assertRaisesRegex(ValueError, "No recent"):
            hd.retry(flaky, attempts=2, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()

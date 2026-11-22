# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops/executor"))
import manual_upgrade as m  # noqa: E402
import manual_upgrade_verify as verify  # noqa: E402
import monitoring_hooks as hooks  # noqa: E402
import permissions as p  # noqa: E402

USER = "ocid1.user.oc1..fixture"
UID = "11111111-2222-3333-4444-555555555555"
HOOK_KINDS = {gvr: kind for kind, gvr in hooks.KINDS.items()}
WRITE_VERBS = {"create", "update", "patch", "delete", "escalate", "bind", "deletecollection", "impersonate", "*"}


# Executor package digests rendered by main at f9b54fe, before the manual package generalized the shared
# builders. A deliberate change to an executor stage updates the digest in the same pull request.
EXECUTOR_DIGESTS = {
    "daily-grants": "d0044c7a4205d0d8c630ed274843b41248df0a47b85ecaceb032860dc2aa91d5",
    "daily-guards": "562a45ad51523dbbcb78e3f4e86f7dee334c38427a6b312873aa9d67f13c1c34",
    "history-grants": "810333a152fef969d3a29334a68af41fb04a3818094ed9d8cea659ab4bc0b6ff",
    "history-guard": "7d9176d36eb9cf9a56b59ddc3e28b29bfede4da5c35ae80f0672a76c7cc4e76c",
    "hook-account": "b4c586c09df985b837189e5d5b63fd67ca7436cbbe0e8e0544d7fddba4927ea8",
    "hook-grants": "5bd7d2f5901c3557a4a364e05b5f4fa301f6d2922e25ae63af60f31828ce0ebd",
    "hook-guards": "5488345c6d479167a4a38bf6ba720c9de877631e17b62277bf0e13b53531a7a0",
    "reads": "cb9c056f0101a835588ea49d0cc0879552492006336a7cbec60bcc822db00245",
    "routine-grants": "d0044c7a4205d0d8c630ed274843b41248df0a47b85ecaceb032860dc2aa91d5",
    "routine-guards": "e34ca643c3b9324d9bc9f317215d2aa2f568d2e2665acf3771d2ed2078a98189"
}
EXECUTOR_HOOKS = [
    {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "prometheus-crds-upgrade", "namespace": "default"}},
    {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "prometheus-crds-upgrade", "namespace": "default"},
     "binaryData": {"crds.bz2": "YQ=="}},
    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": "prometheus-crds-upgrade"},
     "rules": [{"apiGroups": ["x"], "resources": ["y"], "verbs": ["get"]}]},
    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "prometheus-crds-upgrade"},
     "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "prometheus-crds-upgrade"},
     "subjects": [{"kind": "ServiceAccount", "name": "prometheus-crds-upgrade", "namespace": "default"}]},
    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
     "metadata": {"name": "prometheus-kube-prometheus-admission", "namespace": "default"}, "rules": []},
    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
     "metadata": {"name": "prometheus-kube-prometheus-admission", "namespace": "default"},
     "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "prometheus-kube-prometheus-admission"}, "subjects": []},
    {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "prometheus-crds-upgrade", "namespace": "default", "uid": UID},
     "spec": {"selector": {"matchLabels": {"batch.kubernetes.io/controller-uid": UID}},
              "template": {"spec": {"containers": [{"name": "c", "image": "i@sha256:" + "a" * 64}]}}}},
]


def digest(items):
    return hashlib.sha256(json.dumps(list(items), sort_keys=True).encode()).hexdigest()


def fixture_object(scope, group, target, name, kind):
    obj = {"apiVersion": group + "/v1" if group else "v1", "kind": kind, "metadata": {"name": name}}
    if scope != "cluster":
        obj["metadata"]["namespace"] = scope
    if kind in ("Role", "ClusterRole"):
        obj["rules"] = [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get"]}]
    elif kind.endswith("Binding"):
        obj["roleRef"] = {"apiGroup": m.RBAC_GROUP, "kind": kind.removesuffix("Binding"), "name": name}
        obj["subjects"] = [{"kind": "ServiceAccount", "name": name, "namespace": "default"}]
    elif kind in ("Deployment", "DaemonSet", "StatefulSet"):
        obj["spec"] = {"template": {"spec": {"containers": [{"name": "c", "image": "registry.example/c@sha256:" + "a" * 64}]}}}
    elif kind in ("Prometheus", "Alertmanager"):
        obj["spec"] = {"replicas": 1, "serviceAccountName": name}
    elif kind == "Service":
        obj["spec"] = {"type": "ClusterIP", "ports": [{"port": 80}]}
    elif kind in m.WEBHOOKS:
        obj["webhooks"] = [{"name": "fixture.example.com", "admissionReviewVersions": ["v1"], "sideEffects": "None",
                            "clientConfig": {"service": {"name": "svc", "namespace": "default"}, "caBundle": "Zm9v"}}]
    elif kind == "Job":
        obj["metadata"]["uid"] = UID
        obj["spec"] = {"selector": {"matchLabels": {"batch.kubernetes.io/controller-uid": UID}},
                       "template": {"spec": {"containers": [{"name": "c", "image": "registry.example/c@sha256:" + "b" * 64}]}}}
    elif kind == "ConfigMap":
        obj["binaryData"] = {"crds.bz2": "YQ=="}
    return obj


def fixtures():
    pinned, hook_objects = [], []
    for (scope, group, target, name), row in sorted(m.records().items()):
        if row.get("hookEvents"):
            hook_objects.append(fixture_object(scope, group, target, name, HOOK_KINDS[group, target]))
        elif (group, target) in m.PINNED:
            pinned.append(fixture_object(scope, group, target, name, m.PINNED[group, target]))
    return pinned, hook_objects


def rules_of(grants):
    for obj in grants:
        if obj["kind"] in ("Role", "ClusterRole"):
            for rule in obj["rules"]:
                yield obj["metadata"].get("namespace", "cluster"), rule


def policy_targets(guards):
    """(group, resource) -> list of (operations, names condition, validations) for each policy."""
    found = {}
    for obj in guards:
        if obj["kind"] != "ValidatingAdmissionPolicy":
            continue
        rule, = obj["spec"]["matchConstraints"]["resourceRules"]
        conditions = {c["name"]: c["expression"] for c in obj["spec"]["matchConditions"]}
        found.setdefault((rule["apiGroups"][0], rule["resources"][0]), []).append(
            (set(rule["operations"]), conditions.get("reviewed-target", ""), obj))
    return found


class ManualUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pinned, cls.hooks = fixtures()
        cls.guards, cls.grants = m.package(USER, cls.pinned, cls.hooks)

    def test_every_policy_matches_only_the_manual_user_and_fails_closed(self):
        policies = [g for g in self.guards if g["kind"] == "ValidatingAdmissionPolicy"]
        bindings = {g["metadata"]["name"]: g for g in self.guards if g["kind"] == "ValidatingAdmissionPolicyBinding"}
        self.assertGreater(len(policies), 100)
        for policy in policies:
            spec = policy["spec"]
            self.assertEqual(spec["failurePolicy"], "Fail")
            self.assertEqual(spec["matchConditions"][0], {"name": "manual-deployer", "expression": 'request.userInfo.username == "' + USER + '"'})
            binding = bindings[policy["metadata"]["name"]]["spec"]
            self.assertEqual(binding["validationActions"], ["Deny"])
            self.assertEqual(binding["paramRef"]["parameterNotFoundAction"], "Deny")
            self.assertTrue(policy["metadata"]["name"].startswith(m.PREFIX + "-"))

    def test_every_policy_requires_the_manual_lock_except_the_archive_comparison(self):
        archive_bound = []
        for policy in (g for g in self.guards if g["kind"] == "ValidatingAdmissionPolicy"):
            first = policy["spec"]["validations"][0]["expression"]
            if m.HOLDER_PATTERN in first:
                continue
            archive_bound.append(policy["metadata"]["name"])
        # The CRD archive ConfigMap's content check is parameterized by the archive. The hook
        # inventory policy on ConfigMaps still requires the lock for the same writes.
        self.assertEqual(len(archive_bound), 1)
        inventory = [g for g in self.guards if g["metadata"]["name"] == m.PREFIX + "-hook-inventory-configmaps"
                     and g["kind"] == "ValidatingAdmissionPolicy"]
        self.assertIn(m.HOLDER_PATTERN, inventory[0]["spec"]["validations"][0]["expression"])

    def test_grants_name_only_the_manual_user_and_hold_no_wildcards(self):
        subjects = [s for o in self.grants if "subjects" in o for s in o["subjects"]]
        self.assertTrue(subjects)
        self.assertTrue(all(s == {"kind": "User", "apiGroup": m.RBAC_GROUP, "name": USER} for s in subjects))
        for _, rule in rules_of(self.grants):
            self.assertNotIn("*", rule["verbs"] + rule["resources"] + rule["apiGroups"])
            self.assertFalse({"impersonate", "deletecollection"} & set(rule["verbs"]))
            self.assertNotIn("customresourcedefinitions", rule["resources"])
            self.assertNotIn("namespaces", rule["resources"])
            self.assertFalse(any("/" in resource for resource in rule["resources"]), "No subresources such as pods/exec")

    def test_every_write_grant_has_an_admission_guard_on_the_same_target(self):
        targets = policy_targets(self.guards)
        for scope, rule in rules_of(self.grants):
            writes = set(rule["verbs"]) & WRITE_VERBS
            if not writes:
                continue
            key = (rule["apiGroups"][0], rule["resources"][0])
            names = rule.get("resourceNames", [])
            with self.subTest(scope=scope, target=key, verbs=sorted(writes)):
                guarded = targets.get(key, [])
                if "patch" in writes:
                    for name in names:
                        # Lock gates name "namespace/name"; pins compare request.name directly.
                        self.assertTrue(any("UPDATE" in ops and ('/' + name + '"' in cond or '"' + name + '"' in cond)
                                            for ops, cond, _ in guarded), name)
                if "escalate" in writes and names != [""]:
                    # Escalation on an existing role is only safe with a rules pin for that exact role.
                    pinned = [obj for ops, cond, obj in guarded if obj["metadata"]["name"].startswith(m.PREFIX + "-pin-")]
                    for name in names:
                        self.assertTrue(any('request.name == "' + name + '"' in obj["spec"]["matchConditions"][1]["expression"]
                                            for obj in pinned), name)
                if key == ("", "secrets") and writes & {"create", "update", "delete"}:
                    history, = [obj for _, _, obj in guarded if obj["metadata"]["name"] == m.PREFIX + "-history"]
                    # Exactly the six releases: the policy also matches the user's Stage 1 Jaeger history writes.
                    self.assertIn(json.dumps(list(p.HISTORY)), history["spec"]["validations"][1]["expression"])
                elif names == [""] or "create" in writes:
                    # Collection create and create-time escalation: only the twelve reviewed hook names.
                    self.assertTrue(any("CREATE" in ops and obj["metadata"]["name"].startswith(m.PREFIX + "-hook-inventory-")
                                        for ops, _, obj in guarded))
                if "bind" in writes:
                    # A binding referencing these roles may be patched only with pinned subjects.
                    for binding in (o for o in self.pinned if o["kind"].endswith("Binding") and o["roleRef"]["name"] in names):
                        _, group, target, name = m.ident(binding)
                        self.assertTrue(any('request.name == "' + name + '"' in obj["spec"]["matchConditions"][1]["expression"]
                                            for _, _, obj in targets[group, target]
                                            if obj["metadata"]["name"].startswith(m.PREFIX + "-pin-")))

    def test_privileged_surfaces_are_pinned_per_object(self):
        pins = [g for g in self.guards if g["kind"] == "ValidatingAdmissionPolicy" and g["metadata"]["name"].startswith(m.PREFIX + "-pin-")]
        self.assertEqual(len(pins), len(self.pinned))
        # Two kinds can share a namespace and name (the Grafana Role and Deployment), so keep a list.
        by_name = [(pin["spec"]["matchConditions"][1]["expression"], pin["spec"]["validations"][1]["expression"]) for pin in pins]
        operator = next(o for o in self.pinned if o["kind"] == "Deployment")
        expression = next(v for k, v in by_name if '"' + operator["metadata"]["name"] + '"' in k and "template" in v)
        self.assertIn('dyn(dyn(object)["spec"]["template"]["spec"]) == dyn(dyn(oldObject)["spec"]["template"]["spec"])', expression)
        self.assertIn(p.literal(operator["spec"]["template"]["spec"]), expression)
        webhook = next(v for k, v in by_name if "prometheus-kube-prometheus-admission" in k
                       and '["caBundle"]' in v)
        self.assertIn('["clientConfig"]', webhook)
        service = next(o for o in self.pinned if o["kind"] == "Service")
        expression = next(v for k, v in by_name if '"' + service["metadata"]["name"] + '"' in k and "externalIPs" in v)
        self.assertIn('"ClusterIP"', expression)

    def test_render_outside_or_short_of_the_matrix_is_refused(self):
        policies = m.policy_builder(USER)
        extra = copy.deepcopy(self.pinned) + [fixture_object("default", m.RBAC_GROUP, "clusterroles", "unreviewed", "ClusterRole")]
        extra[-1]["metadata"].pop("namespace", None)
        with self.assertRaisesRegex(ValueError, "outside the reviewed access matrix"):
            m.pins(extra, policies)
        with self.assertRaisesRegex(ValueError, "missing from the render"):
            m.pins(self.pinned[1:], policies)
        with self.assertRaisesRegex(ValueError, "outside the reviewed access matrix"):
            m.pins(self.pinned + self.pinned[:1], policies)

    def test_server_defaults_keep_objects_the_chart_places_outside_the_release_namespace(self):
        service = next(o for o in self.pinned if o["kind"] == "Service" and o["metadata"]["namespace"] == "kube-system")
        role = next(o for o in self.pinned if o["kind"] == "ClusterRole")
        bare = copy.deepcopy(next(o for o in self.pinned if o["kind"] == "Deployment"))
        release_namespace = bare["metadata"].pop("namespace")
        trials = []

        def admit(argv, *, input, **_):
            trial = json.loads(input)
            trials.append(trial)
            trial["metadata"]["uid"] = UID
            return subprocess.CompletedProcess(argv, 0, json.dumps(trial).encode(), b"")

        with mock.patch.object(m.subprocess, "run", side_effect=admit):
            admitted = m.server_defaults([(service, "default"), (role, "default"), (bare, release_namespace)], ["kubectl"])
        self.assertEqual([m.ident(o) for o in admitted], [m.ident(service), m.ident(role), m.ident(bare, release_namespace)])
        self.assertEqual(admitted[0]["metadata"]["namespace"], "kube-system")
        self.assertNotIn("namespace", admitted[1]["metadata"])
        self.assertTrue(all(t["metadata"]["name"].startswith("review-") for t in trials))
        with mock.patch.object(m.subprocess, "run", side_effect=admit):
            # The CI failure: every monitoring object arrives paired with its release namespace.
            everything = m.server_defaults([(o, "default") for o in self.pinned], ["kubectl"])
        self.assertTrue(m.pins(everything, m.policy_builder(USER)))

        def moved(argv, *, input, **_):
            trial = json.loads(input)
            trial["metadata"]["namespace"] = "default"
            return subprocess.CompletedProcess(argv, 0, json.dumps(trial).encode(), b"")

        with mock.patch.object(m.subprocess, "run", side_effect=moved), self.assertRaisesRegex(ValueError, "changed namespace"):
            m.server_defaults([(service, "default")], ["kubectl"])

    def policy(self, guards, name):
        found, = [g for g in guards if g["kind"] == "ValidatingAdmissionPolicy" and g["metadata"]["name"] == name]
        return found["spec"]

    def test_statefulset_storage_is_preserved_whatever_the_pod_spec_does(self):
        stateful = next(o for o in self.pinned if o["kind"] == "StatefulSet")
        expression = m.surface_expression(stateful)
        storage, transition = expression.split(") && ((", 1)
        for field in ("volumeClaimTemplates", "persistentVolumeClaimRetentionPolicy", "selector", "serviceName", "replicas"):
            self.assertIn('"' + field + '"', storage)
            self.assertNotIn('"' + field + '"', transition)
        self.assertIn("oldObject", storage)
        self.assertNotIn("persistentVolumeClaimRetentionPolicy", m.surface_expression(next(o for o in self.pinned if o["kind"] == "Deployment")))

    def test_history_admits_canonical_records_of_all_six_releases(self):
        spec = self.policy(self.guards, m.PREFIX + "-history")
        for release in p.HISTORY:
            self.assertIn('"' + release + '"', spec["validations"][1]["expression"])
        self.assertIn("default/jaeger", p.HISTORY)
        self.assertIn("jaeger/jaeger", p.HISTORY)

    def test_manual_hook_inventory_and_preservation_cover_only_the_hooks(self):
        def target(spec):
            return [c["expression"] for c in spec["matchConditions"] if c["name"] == "reviewed-target"]
        self.assertEqual(target(self.policy(self.guards, m.PREFIX + "-hook-inventory-configmaps")), ["request.namespace == 'default'"])
        self.assertEqual(target(self.policy(self.guards, m.PREFIX + "-hook-inventory-clusterroles")), [])
        for kind in ("roles", "clusterroles"):
            scoped, = target(self.policy(self.guards, m.PREFIX + "-hook-preserve-" + kind))
            self.assertIn("request.name in", scoped)
        executor, _ = hooks.controls(self.hooks)
        for name in ("jaeger-demo-hook-inventory-configmaps", "jaeger-demo-hook-preserve-roles", "jaeger-demo-hook-preserve-clusterroles"):
            self.assertEqual(target(self.policy(executor, name)), [])

    def test_caller_supplied_chart_archives_must_match_the_checksum_lock(self):
        locks = json.loads((m.ROOT / m.scheduled_inputs.LOCK).read_bytes())["charts"]
        with tempfile.TemporaryDirectory() as scratch:
            located = {key: Path(scratch) / name for key, name in zip(m.RELEASE_KEYS, sorted(locks))}
            for path in located.values():
                path.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "differs from the reviewed checksum"):
                m.verified_archives(located)
            located["otel-demo"] = Path(scratch) / "opentelemetry-demo-0.0.0.tgz"
            located["otel-demo"].write_bytes(b"unlocked")
            with self.assertRaisesRegex(ValueError, "differs from the reviewed checksum"):
                m.verified_archives(located)

    def test_cluster_role_labels_are_pinned_against_aggregation(self):
        cluster_role = next(o for o in self.pinned if o["kind"] == "ClusterRole")
        role = next(o for o in self.pinned if o["kind"] == "Role")
        self.assertIn('["metadata"]["labels"]', m.surface_expression(cluster_role))
        self.assertNotIn('["labels"]', m.surface_expression(role))

    def test_every_binding_leaves_the_checkpoint_recoverable(self):
        bindings = [g for g in self.guards if g["kind"] == "ValidatingAdmissionPolicyBinding"]
        self.assertTrue(bindings)
        for binding in bindings:
            self.assertEqual(binding["spec"]["paramRef"]["parameterNotFoundAction"], "Deny")
            self.assertEqual(binding["spec"]["matchResources"]["excludeResourceRules"], [m.CHECKPOINT_RULE])
        self.assertEqual(m.CHECKPOINT_RULE["resourceNames"], [p.CHECKPOINT])

    def test_hooks_are_defaulted_under_renamed_copies(self):
        trials = []

        def admit(argv, *, input, **_):
            trial = json.loads(input)
            trials.append(trial["metadata"]["name"])
            if trial["kind"] == "Job":
                # The Job API derives template labels from the name; the uid stays the fixture's.
                trial["spec"]["template"].setdefault("metadata", {}).setdefault("labels", {}).update(
                    {"job-name": trial["metadata"]["name"], "batch.kubernetes.io/job-name": trial["metadata"]["name"]})
            return subprocess.CompletedProcess(argv, 0, json.dumps(trial).encode(), b"")

        with mock.patch.object(hooks.subprocess, "run", side_effect=admit):
            admitted = m.dry_admitted_hooks(self.hooks, ["kubectl"])
        originals = [h["metadata"]["name"] for h in self.hooks]
        self.assertFalse(set(trials) & set(originals))
        self.assertEqual([h["metadata"]["name"] for h in admitted], originals)
        job = next(h for h in admitted if h["kind"] == "Job")
        self.assertEqual(job["spec"]["template"]["metadata"]["labels"]["job-name"], job["metadata"]["name"])
        self.assertNotIn("review-", json.dumps(admitted))
        with mock.patch.object(hooks.subprocess, "run", side_effect=admit):
            direct = hooks.dry_admitted(copy.deepcopy(self.hooks), ["kubectl"])
        self.assertEqual(admitted, direct)

        def controls(objects):
            return hooks.controls(objects, policies=m.policy_builder(USER), lock=None, archive_name=m.ARCHIVE,
                                  prefix=m.PREFIX, account_guards=False, scoped=True)
        self.assertEqual(controls(admitted), controls(direct))

    def test_readiness_probes_cover_every_guard_with_its_own_message(self):
        probes = m.readiness_probes(self.pinned, self.hooks)
        held = [x for x in probes if x["phase"] == "held"]
        pins = [x for x in held if x["expect"] == m.PIN_DENIED]
        self.assertEqual(sorted((x["namespace"] or "cluster", x["resource"], x["name"]) for x in pins),
                         sorted((s_, m.resource_arg(g, t), n) for s_, g, t, n in map(m.ident, self.pinned)))
        self.assertEqual(len([x for x in held if x["expect"] == m.HOOK_DENIED]), len(self.hooks))
        self.assertEqual(len([x for x in held if x["expect"] == m.INVENTORY_DENIED]), len({hooks.KINDS[h["kind"]] for h in self.hooks}))
        self.assertEqual(sorted(x["body"]["metadata"]["namespace"] for x in held if x["expect"] == m.HISTORY_DENIED), sorted(m.NAMESPACES))
        locks = [x for x in probes if x["phase"] == "unlocked"]
        self.assertEqual(len(locks), len(m.ordinary(m.records())))
        self.assertTrue(all(x["expect"] == m.LOCK_DENIED for x in locks))
        # Every message the probes expect is one the package actually emits.
        messages = {v["message"] for g in self.guards if g["kind"] == "ValidatingAdmissionPolicy" for v in g["spec"]["validations"]}
        self.assertLessEqual({x["expect"] for x in probes}, messages)

    def test_pin_probes_change_a_pinned_field(self):
        def merge(target, change):
            if not isinstance(change, dict) or not isinstance(target, dict):
                return change
            return {**target, **{k: merge(target.get(k), v) for k, v in change.items()}}
        for obj in self.pinned:
            kind_of_patch, change = m.pin_probe(obj)
            patched = copy.deepcopy(obj)
            if kind_of_patch == "merge":
                patched = merge(patched, change)
            else:
                op, = change
                patched["webhooks"][0]["timeoutSeconds"] = op["value"]
            surface = m.SURFACE.get(obj["kind"], [(("webhooks",), [])])
            with self.subTest(kind=obj["kind"], name=obj["metadata"]["name"]):
                self.assertTrue(any(m.at(patched, path, d) != m.at(obj, path, d) for path, d in surface))

    def test_verifier_requires_the_phase_holder_and_the_expected_denial(self):
        probe = {"phase": "held", "label": "pin", "verb": "patch", "resource": "deployments.apps", "namespace": "default",
                 "name": "x", "type": "merge", "patch": {}, "expect": m.PIN_DENIED}
        calls = []

        def cluster(holder_value, answers):
            def fake(argv, **_):
                if "configmap" in argv and p.CHECKPOINT in argv:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"metadata": {"annotations": {p.HOLDER: holder_value}}}).encode(), b"")
                calls.append(argv)
                code, err = answers.pop(0) if len(answers) > 1 else answers[0]
                return subprocess.CompletedProcess(argv, code, "", err)
            return fake

        with mock.patch.object(verify.subprocess, "run", side_effect=cluster("manual/7/1", [(1, "lock"), (1, m.PIN_DENIED)])), \
                mock.patch.object(verify.time, "sleep"):
            self.assertEqual(verify.run([probe], "held", ["kubectl"], USER), ["pin"])
        self.assertIn("--dry-run=server", calls[-1])
        self.assertEqual(calls[-1][1:5], ["--as", USER, "--as-group", "system:authenticated"])
        with mock.patch.object(verify.subprocess, "run", side_effect=cluster("daily/2026-11-22T13:00:00Z/x", [(1, m.PIN_DENIED)])), \
                self.assertRaisesRegex(ValueError, "holder does not match"):
            verify.run([probe], "held", ["kubectl"], USER)
        with mock.patch.object(verify.subprocess, "run", side_effect=cluster("manual/7/1", [(0, "")])), \
                mock.patch.object(verify.time, "sleep"), mock.patch.object(verify.time, "monotonic", side_effect=[0, 0, 999]), \
                self.assertRaisesRegex(ValueError, "Not enforced: pin"):
            verify.run([probe], "held", ["kubectl"], USER)

    def test_verifier_refuses_hooks_left_by_a_failed_run(self):
        probes = m.readiness_probes(self.pinned, self.hooks)
        job = next(h for h in self.hooks if h["kind"] == "Job")
        asked = []

        def cluster(present):
            def fake(argv, **_):
                asked.append(argv)
                hit = present in argv
                return subprocess.CompletedProcess(argv, 0, "job.batch/" + present + "\n" if hit else "", "")
            return fake

        with mock.patch.object(verify.subprocess, "run", side_effect=cluster(job["metadata"]["name"])), \
                self.assertRaisesRegex(ValueError, "Delete leftover hooks.*" + job["metadata"]["name"]):
            verify.leftover_hooks(probes, ["kubectl"])
        self.assertEqual(len(asked), len(self.hooks))
        self.assertTrue(all("--ignore-not-found" in a for a in asked))
        with mock.patch.object(verify.subprocess, "run", side_effect=cluster("absent")):
            self.assertEqual(verify.leftover_hooks(probes, ["kubectl"]), 0)

    def test_installed_guards_must_match_guards_json(self):
        policy = next(g for g in self.guards if g["kind"] == "ValidatingAdmissionPolicy")
        served = copy.deepcopy(policy)
        served["spec"]["matchConstraints"]["matchPolicy"] = "Equivalent"
        self.assertTrue(verify.contains(policy["spec"], served["spec"]))
        served["spec"]["validations"][1]["expression"] = "true"
        self.assertFalse(verify.contains(policy["spec"], served["spec"]))
        served["spec"] = copy.deepcopy(policy["spec"])
        served["spec"]["validations"].pop()
        self.assertFalse(verify.contains(policy["spec"], served["spec"]))

    def changed(self):
        """An upgrade that adds a Deployment, a ConfigMap, a hook-kind ClusterRole and a Secret, and drops two objects."""
        rows = m.records()
        steady = sorted(k for k, row in rows.items() if not row.get("hookEvents") and row["release"] != "supplement")
        pick = {}
        for key in steady:
            for want in (("otel-demo", "apps", "deployments"), ("otel-demo", "", "configmaps"), ("cluster", m.RBAC_GROUP, "clusterroles"),
                         ("default", "", "secrets")):
                if key[:3] == want and want not in pick:
                    pick[want] = key
        added = sorted(pick.values())
        removed = [("otel-demo", "", "configmaps", "fixture-retired"), ("cluster", m.RBAC_GROUP, "clusterrolebindings", "fixture-retired"),
                   ("default", "", "secrets", "fixture-retired")]
        changes = m.release_changes(set(steady) - set(added) | set(removed))
        self.assertEqual(changes, {"added": added, "removed": sorted(removed), "retained": []})
        kinds = {(k[1], k[2]): HOOK_KINDS.get((k[1], k[2])) or m.PINNED.get((k[1], k[2])) for k in added}
        kinds.update({("", "configmaps"): "ConfigMap", ("", "secrets"): "Secret"})
        bodies = {k: fixture_object(*k[:3], k[3], kinds[k[1], k[2]]) for k in added}
        return changes, bodies

    def test_release_changes_refuse_deleting_unreviewed_kinds(self):
        steady = {k for k, row in m.records().items() if not row.get("hookEvents") and row["release"] != "supplement"}
        self.assertEqual(m.release_changes(steady), m.NO_CHANGES)
        for stray in (("cluster", "apiextensions.k8s.io", "customresourcedefinitions", "x"), ("jaeger", "apps", "deployments", "x")):
            with self.assertRaisesRegex(ValueError, "needs an operator"):
                m.release_changes(steady | {stray})

    def test_an_upgrade_that_changes_objects_grants_only_named_deletes_and_gated_creates(self):
        changes, _ = self.changed()
        guards, grants = m.package(USER, self.pinned, self.hooks, changes)
        self.assertEqual(m.package(USER, self.pinned, self.hooks, m.NO_CHANGES), (self.guards, self.grants))
        rules = list(rules_of(grants))
        for scope, group, target, name in changes["added"]:
            self.assertTrue(any(s == scope and rule["resources"] == [target] and "create" in rule["verbs"]
                                and "resourceNames" not in rule for s, rule in rules), target)
        for scope, group, target, name in changes["removed"]:
            self.assertTrue(any(s == scope and rule["resources"] == [target] and rule["verbs"] == ["delete", "get"]
                                and rule.get("resourceNames") == [name] for s, rule in rules), target)
        self.assertFalse(any("delete" in rule["verbs"] and not rule.get("resourceNames") for s, rule in rules if rule["resources"] != ["secrets"]))
        policies = {g["metadata"]["name"]: g["spec"] for g in guards if g["kind"] == "ValidatingAdmissionPolicy"}

        def conditions(spec):
            return " ".join(c["expression"] for c in spec["matchConditions"])
        deployment = next(k for k in changes["added"] if k[2] == "deployments")
        pin = next(spec for spec in policies.values() if spec["validations"][-1]["message"] == m.PIN_DENIED
                   and '"' + deployment[3] + '"' in conditions(spec) and '"otel-demo"' in conditions(spec))
        self.assertEqual(pin["matchConstraints"]["resourceRules"][0]["operations"], ["CREATE", "UPDATE"])
        self.assertTrue(pin["validations"][-1]["expression"].startswith('request.operation == "CREATE" ? ('))
        gates = [spec for spec in policies.values() if spec["validations"][-1]["message"] == m.CREATE_DENIED]
        # Only the otel-demo ConfigMap and Deployment need a gate: the hook inventory and history cover the others.
        self.assertEqual(sorted(g["matchConstraints"]["resourceRules"][0]["resources"][0] for g in gates), ["configmaps", "deployments"])
        cluster_role = next(k for k in changes["added"] if k[2] == "clusterroles")
        inventory = policies[m.PREFIX + "-hook-inventory-clusterroles"]["validations"][-1]["expression"]
        self.assertIn('"' + cluster_role[3] + '"', inventory)
        # A dropped binding may be deleted but never recreated: no pin covers its content.
        bindings = policies[m.PREFIX + "-hook-inventory-clusterrolebindings"]["validations"][-1]["expression"]
        create_side, delete_side = bindings.split(" : ", 1)
        self.assertTrue(create_side.startswith('(request.operation == "CREATE" ? '))
        self.assertNotIn('"fixture-retired"', create_side)
        self.assertIn('"fixture-retired"', delete_side)
        self.assertNotIn('"' + cluster_role[3] + '"', inventory.split(" : ", 1)[1])
        # Chart Secrets: create the added one and delete the dropped one inside the lock-bearing history policy;
        # updates to either stay canonical-history only, and the condition still matches them.
        secret = next(k for k in changes["added"] if k[2] == "secrets")
        history = policies[m.PREFIX + "-history"]
        self.assertEqual([c["expression"] for c in history["matchConditions"]][1:],
                         [p.CHART_SECRET_UPDATE + json.dumps(p.MONITORING_SECRETS) + ")"])
        self.assertIn('(request.operation == "CREATE" && (request.namespace + "/" + request.name) in ["default/' + secret[3] + '"])',
                      history["validations"][-1]["expression"])
        self.assertIn('(request.operation == "DELETE" && (request.namespace + "/" + request.name) in ["default/fixture-retired"])',
                      history["validations"][-1]["expression"])
        self.assertEqual(history["validations"][0]["message"], m.LOCK_DENIED)
        retire = [spec for name, spec in policies.items() if name.startswith(m.PREFIX + "-retire-")]
        self.assertEqual(len(retire), 3)
        self.assertTrue(all(spec["matchConstraints"]["resourceRules"][0]["operations"] == ["DELETE"] for spec in retire))
        # Unchanged executor output: the exempt names never reach the executor's own hook controls.
        self.assertEqual(hooks.controls(self.hooks), hooks.controls(self.hooks, exempt_create=None, exempt_delete=None))

    def test_readiness_probes_create_added_objects_and_lock_their_gates(self):
        changes, bodies = self.changed()
        probes = m.readiness_probes(self.pinned, self.hooks, changes, bodies)
        deployment = next(k for k in changes["added"] if k[2] == "deployments")
        on_create = [x for x in probes if x["label"].startswith("pin on create")]
        self.assertTrue(any(x["body"]["metadata"]["name"] == deployment[3] and x["expect"] == m.PIN_DENIED
                            and x["body"]["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 7919 for x in on_create))
        self.assertFalse(any(x["verb"] == "patch" and x.get("name") == deployment[3] and x.get("namespace") == "otel-demo" for x in probes))
        gates = sorted(x["label"] for x in probes if x["expect"] == m.CREATE_DENIED)
        self.assertEqual(gates, ["create gate configmaps otel-demo", "create gate deployments otel-demo"])
        self.assertTrue(all(x["body"]["metadata"]["name"] == m.PROBE for x in probes if x["expect"] == m.CREATE_DENIED))
        retire = [x for x in probes if x["verb"] == "delete"]
        self.assertEqual(sorted((x["namespace"] or "cluster", x["name"]) for x in retire),
                         [("cluster", "fixture-retired"), ("default", "fixture-retired"), ("otel-demo", "fixture-retired")])
        self.assertTrue(all(x["phase"] == "unlocked" and x["expect"] == m.LOCK_DENIED for x in retire))

    def test_a_single_component_package_grants_nothing_on_the_others(self):
        for component, namespace in m.COMPONENT_NAMESPACES.items():
            with self.subTest(component=component), m.selected([component]):
                pinned, hook_objects = fixtures()
                guards, grants = m.package(USER, pinned, hook_objects)
                rows = m.records()
                self.assertEqual({row["component"] for row in rows.values()}, {component})
                scopes = {s for s, _ in rules_of(grants)}
                self.assertEqual({s for s, rule in rules_of(grants) if rule["resources"] == ["secrets"] and "create" in rule["verbs"]},
                                 {namespace})
                self.assertLessEqual(scopes, {namespace, "cluster", "kube-system", p.OPS} if component == "oci-monitoring" else {namespace, "cluster"})
                hook_policies = [g for g in guards if g["metadata"]["name"].startswith(m.PREFIX + "-hook")]
                self.assertEqual(bool(hook_policies), component == "oci-monitoring")
                self.assertEqual(m.RELEASE_KEYS, m.COMPONENT_RELEASES[component])
        self.assertEqual(m.COMPONENTS, m.ALL_COMPONENTS)
        self.assertEqual(m.NAMESPACES, ("default", "opensearch", "otel-demo"))
        with self.assertRaisesRegex(ValueError, "Unknown or empty"), m.selected(["jaeger"]):
            pass
        with m.selected(["opensearch"]), self.assertRaisesRegex(ValueError, "hooks outside"):
            m.package(USER, [], self.hooks)

    def test_records_kept_only_as_rollback_targets_get_no_rights_and_are_deleted(self):
        """access_matrix.py keeps records of dropped objects while a rollback target needs them."""
        steady = {k for k, row in m.records().items() if not row.get("hookEvents") and row["release"] != "supplement"}
        role = next(k for k in sorted(steady) if k[2] == "clusterroles")
        deployment = next(k for k in sorted(steady) if k[2] == "deployments")
        retained = {role, deployment}
        changes = m.release_changes(steady, steady - retained)
        self.assertEqual(changes, {"added": [], "removed": sorted(retained), "retained": sorted(retained)})
        pinned = [o for o in self.pinned if m.ident(o) not in retained]
        guards, grants = m.package(USER, pinned, self.hooks, changes)
        for scope, group, target, name in retained:
            named = [rule for s, rule in rules_of(grants) if s == scope and rule["resources"] == [target] and name in rule.get("resourceNames", [])]
            verbs = {verb for rule in named for verb in rule["verbs"]}
            # Deleted by Helm; never patched or escalated. A rendered binding may still bind it under its own pin.
            self.assertLessEqual({"delete", "get"}, verbs, name)
            self.assertFalse(verbs & {"patch", "escalate", "create", "update"}, name)
        probes = m.readiness_probes(pinned, self.hooks, changes, {})
        gone = {(None if k[0] == "cluster" else k[0], m.resource_arg(k[1], k[2]), k[3]) for k in retained}
        self.assertFalse([x for x in probes if x["verb"] == "patch" and (x["namespace"], x["resource"], x["name"]) in gone])
        with self.assertRaisesRegex(ValueError, "without a reviewed record"):
            m.release_changes(steady, steady | {("otel-demo", "", "configmaps", "fixture-unreviewed")})
        # Without the narrowing, a retained privileged record would be granted patch and escalate with no pin.
        with self.assertRaisesRegex(ValueError, "missing from the render"):
            m.package(USER, pinned, self.hooks)

    def test_a_dropped_chart_secret_keeps_the_history_check(self):
        steady = {k for k, row in m.records().items() if not row.get("hookEvents") and row["release"] != "supplement"}
        grafana = ("default", "", "secrets", "prometheus-grafana")
        self.assertIn("prometheus-grafana", p.MONITORING_SECRETS)
        self.assertEqual(m.chart_secrets(), p.MONITORING_SECRETS)
        for changes in (m.release_changes(steady, steady - {grafana}), m.release_changes(steady - {grafana}, steady - {grafana})):
            with self.subTest(deployed=grafana in changes["removed"]):
                self.assertIn(grafana, changes["retained"])
                guards, _ = m.package(USER, self.pinned, self.hooks, changes)
                policies = {g["metadata"]["name"]: g["spec"] for g in guards if g["kind"] == "ValidatingAdmissionPolicy"}
                condition = [c["expression"] for c in policies[m.PREFIX + "-history"]["matchConditions"]][1]
                self.assertEqual(condition, p.CHART_SECRET_UPDATE + json.dumps([n for n in p.MONITORING_SECRETS if n != "prometheus-grafana"]) + ")")
                gates = [spec for name, spec in policies.items() if name.startswith(m.PREFIX + "-lock-")
                         and spec["matchConstraints"]["resourceRules"][0]["resources"] == ["secrets"]]
                self.assertFalse(any('"default/prometheus-grafana"' in c["expression"] for g in gates for c in g["matchConditions"]))

    def test_server_side_applied_hooks_need_an_exact_update_too(self):
        hook_policies = [g["spec"] for g in self.guards if g["kind"] == "ValidatingAdmissionPolicy"
                         and g["spec"]["validations"][-1]["message"] == m.HOOK_DENIED]
        self.assertEqual(len(hook_policies), len(self.hooks))
        self.assertTrue(all(spec["matchConstraints"]["resourceRules"][0]["operations"] == ["CREATE", "UPDATE", "DELETE"]
                            for spec in hook_policies))
        names = {h["metadata"]["name"] for h in self.hooks}
        patched = {n for _, rule in rules_of(self.grants) if "patch" in rule["verbs"] and set(rule.get("resourceNames", [])) <= names
                   for n in rule.get("resourceNames", [])}
        self.assertEqual(patched, names)
        executor_guards, executor_grants = hooks.controls(self.hooks)
        self.assertFalse(any("patch" in rule["verbs"] for o in executor_grants if "rules" in o for rule in o["rules"]))

    def test_pins_also_accept_the_server_side_applied_form(self):
        deployment = next(o for o in self.pinned if o["kind"] == "Deployment")
        reordered = copy.deepcopy(deployment)
        reordered["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": "B", "value": "2"}, {"name": "A", "value": "1"}]
        plain = m.surface_expression(deployment)
        self.assertEqual(m.surface_expression(deployment, applied=copy.deepcopy(deployment)), plain)
        both = m.surface_expression(deployment, applied=reordered)
        self.assertIn('"name":dyn("B")', both.replace(" ", ""))
        self.assertIn(m.take_expression(deployment), both)
        self.assertIn(m.take_expression(reordered), both)
        guards = m.pins(self.pinned, m.policy_builder(USER), applied={m.ident(deployment): reordered})
        self.assertTrue(any(m.take_expression(reordered) in g["spec"]["validations"][1]["expression"]
                            for g in guards if g["kind"] == "ValidatingAdmissionPolicy"))

    def test_server_side_apply_dry_runs_only_objects_a_manager_applied(self):
        live = {"helm": {"metadata": {"managedFields": [{"manager": "helm", "operation": "Apply"},
                                                        {"manager": "kube-controller-manager", "operation": "Update"}]}},
                "client": {"metadata": {"managedFields": [{"manager": "helm", "operation": "Update"}]}}}
        self.assertEqual(m.apply_manager(live["helm"]), "helm")
        self.assertIsNone(m.apply_manager(live["client"]))
        self.assertEqual(m.apply_manager({"metadata": {"managedFields": [{"manager": "other", "operation": "Apply"}]}}), "other")
        objects = [o for o in self.pinned if o["kind"] == "Deployment"][:3]
        modes = dict(zip((o["metadata"]["name"] for o in objects), ("helm", "client", None)))
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            if "get" in argv:
                mode = modes[argv[argv.index("get") + 2]]
                return subprocess.CompletedProcess(argv, 0, json.dumps(live[mode]).encode() if mode else b"", b"")
            body = json.loads(kw["input"])
            body["metadata"]["uid"] = UID
            return subprocess.CompletedProcess(argv, 0, json.dumps(body).encode(), b"")

        with mock.patch.object(m.subprocess, "run", side_effect=fake):
            applied = m.server_applied([(o, o["metadata"]["namespace"]) for o in objects], ["kubectl"])
        self.assertEqual(list(applied), [m.ident(objects[0])])
        apply_calls = [c for c in calls if "apply" in c]
        self.assertEqual(len(apply_calls), 1)
        for flag in ("--server-side", "--dry-run=server", "--field-manager=helm"):
            self.assertIn(flag, apply_calls[0])

    def test_added_objects_can_be_created_by_server_side_apply(self):
        changes, _ = self.changed()
        _, grants = m.package(USER, self.pinned, self.hooks, changes)
        deployment = next(k for k in changes["added"] if k[2] == "deployments")
        self.assertIn({"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["patch"], "resourceNames": [deployment[3]]},
                      [rule for scope, rule in rules_of(grants) if scope == "otel-demo"])

    def test_executor_output_is_byte_identical_to_main(self):
        found = {"reads": digest(p.read_access()), "history-guard": digest(p.history_guard()), "history-grants": digest(p.history_grants())}
        found["routine-guards"], found["routine-grants"] = map(digest, p.regular())
        found["daily-guards"], found["daily-grants"] = map(digest, p.regular(daily=True))
        found["hook-guards"], found["hook-grants"] = map(digest, hooks.controls(copy.deepcopy(EXECUTOR_HOOKS)))
        found["hook-account"] = digest(hooks.hook_account_guards())
        self.assertEqual(found, EXECUTOR_DIGESTS)

    def test_a_monitoring_upgrade_shaped_like_59_adds_and_drops_hook_kinds_in_default(self):
        """#59 adds a dashboard ConfigMap and drops the Grafana Role and RoleBinding, all in default."""
        steady = {k for k, row in m.records().items() if not row.get("hookEvents") and row["release"] != "supplement"}
        dashboard = next(k for k in sorted(steady) if k[:3] == ("default", "", "configmaps"))
        dropped = {("default", m.RBAC_GROUP, "roles", "fixture-grafana"), ("default", m.RBAC_GROUP, "rolebindings", "fixture-grafana")}
        changes = m.release_changes(steady - {dashboard} | dropped)
        self.assertEqual(changes, {"added": [dashboard], "removed": sorted(dropped), "retained": []})
        guards, grants = m.package(USER, self.pinned, self.hooks, changes)
        policies = {g["metadata"]["name"]: g["spec"] for g in guards if g["kind"] == "ValidatingAdmissionPolicy"}
        create_side, delete_side = policies[m.PREFIX + "-hook-inventory-configmaps"]["validations"][-1]["expression"].split(" : ", 1)
        self.assertIn('"' + dashboard[3] + '"', create_side)
        self.assertNotIn('"' + dashboard[3] + '"', delete_side)
        for target in ("roles", "rolebindings"):
            create_side, delete_side = policies[m.PREFIX + "-hook-inventory-" + target]["validations"][-1]["expression"].split(" : ", 1)
            self.assertNotIn('"fixture-grafana"', create_side)
            self.assertIn('"fixture-grafana"', delete_side)
        self.assertFalse([n for n in policies if n.startswith(m.PREFIX + "-create-")])
        rules = [rule for scope, rule in rules_of(grants) if scope == "default"]
        for target in ("roles", "rolebindings"):
            self.assertIn({"apiGroups": [m.RBAC_GROUP], "resources": [target], "verbs": ["delete", "get"],
                           "resourceNames": ["fixture-grafana"]}, rules)
        self.assertTrue(any(rule["resources"] == ["configmaps"] and rule["verbs"] == ["create"] for rule in rules))

    def test_bind_is_granted_only_where_each_binding_lives(self):
        scoped = {}
        for scope, rule in rules_of(self.grants):
            if "bind" in rule["verbs"] and rule.get("resourceNames") != [""]:
                scoped.setdefault(scope, set()).update(rule["resourceNames"])
        cluster_refs = {o["roleRef"]["name"] for o in self.pinned if o["kind"] == "ClusterRoleBinding"}
        hook_roles = {h["metadata"]["name"] for h in self.hooks if h["kind"] == "ClusterRole"}
        self.assertEqual(scoped.get("cluster", set()), cluster_refs | hook_roles)
        for binding in (o for o in self.pinned if o["kind"] == "RoleBinding"):
            self.assertIn(binding["roleRef"]["name"], scoped[binding["metadata"]["namespace"]])
        # A RoleBinding to a ClusterRole needs bind in its own namespace only, never at cluster scope.
        pinned = copy.deepcopy(self.pinned)
        binding = next(o for o in pinned if o["kind"] == "RoleBinding")
        binding["roleRef"] = {"apiGroup": m.RBAC_GROUP, "kind": "ClusterRole", "name": "fixture-namespaced-reference"}
        _, grants = m.package(USER, pinned, self.hooks)
        where = {scope for scope, rule in rules_of(grants) if "bind" in rule["verbs"]
                 and "fixture-namespaced-reference" in rule.get("resourceNames", []) and rule["resources"] == ["clusterroles"]}
        self.assertEqual(where, {binding["metadata"]["namespace"]})

    def test_user_name_must_be_a_plain_user(self):
        for bad in ("", " padded", "system:serviceaccount:jaeger-demo-operations:jaeger-demo-executor", "a\nb", "x" * 257):
            with self.assertRaises(ValueError):
                m.subjects_for(bad)

    def test_manual_names_never_collide_with_the_executor_package(self):
        executor = {o["metadata"]["name"] for o in p.history_guard()}
        guards, roles = p.regular(daily=True)
        executor |= {o["metadata"]["name"] for o in guards + roles + list(p.read_access())}
        hook_guards, hook_grants = hooks.controls(self.hooks)
        executor |= {o["metadata"]["name"] for o in hook_guards + hook_grants}
        manual = {o["metadata"]["name"] for o in self.guards + self.grants}
        self.assertFalse(manual & executor)
        self.assertTrue(all(o["metadata"]["name"].startswith("jaeger-demo-hook") or o["kind"] == "ConfigMap"
                            or not o["metadata"]["name"].startswith(m.PREFIX) for o in hook_guards))

    def test_executor_hook_defaults_still_target_the_executor(self):
        guards, grants = hooks.controls(self.hooks)
        users = {c["expression"] for g in guards if g["kind"] == "ValidatingAdmissionPolicy"
                 for c in g["spec"]["matchConditions"] if c["name"] == "trusted-executor"}
        self.assertEqual(users, {'request.userInfo.username == "' + p.USER + '"'})
        self.assertEqual({s["name"] for o in grants if "subjects" in o for s in o["subjects"]}, {p.EXECUTOR})
        archive, = [g for g in guards if g["kind"] == "ConfigMap"]
        self.assertEqual(archive["metadata"]["name"], hooks.ARCHIVE)


if __name__ == "__main__":
    unittest.main()

# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops/executor"))
import manual_upgrade as m  # noqa: E402
import monitoring_hooks as hooks  # noqa: E402
import permissions as p  # noqa: E402

USER = "ocid1.user.oc1..fixture"
UID = "11111111-2222-3333-4444-555555555555"
HOOK_KINDS = {gvr: kind for kind, gvr in hooks.KINDS.items()}
WRITE_VERBS = {"create", "update", "patch", "delete", "escalate", "bind", "deletecollection", "impersonate", "*"}


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

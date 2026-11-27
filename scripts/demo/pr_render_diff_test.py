# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

import base64
import contextlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import pr_render_diff as diff


def records(*resources):
    data = {}
    diff.index_objects(data, "default", "demo-resources", resources)
    return data


class RenderDiffTest(unittest.TestCase):
    def test_changes_report_only_resource_identities(self):
        def resource(kind, name, value):
            return {"apiVersion": "v1", "kind": kind, "metadata": {"name": name, "namespace": "default"},
                    "data": {"credential": base64.b64encode(value.encode()).decode()}}
        old = resource("Secret", "protected", "old-private-value")
        new = resource("Secret", "protected", "new-private-value")
        added = resource("ConfigMap", "created", "private-annotation")
        removed = resource("Secret", "retired", "old-private-value")
        output = diff.summary(records(old, removed), records(new, added), "a" * 40, "b" * 40, True)
        self.assertIn("1 added, 1 modified, 1 removed", output)
        for action, obj in (("Modified", new), ("Added", added), ("Removed", removed)):
            self.assertIn(f"| {action} | `default/demo-resources` | `v1` | `{obj['kind']}` | `default` | `{obj['metadata']['name']}`", output)
            self.assertNotIn(obj["data"]["credential"], output)
        self.assertNotIn("private", output)
        self.assertIn("script-era", output)

    def test_reordered_manifest_keys_and_documents_are_a_noop(self):
        left = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "names"}, "data": {"a": "b", "b": "a"}}
        right = dict(reversed(left.items()))
        right["data"] = dict(reversed(right["data"].items()))
        other = dict(left, metadata={"name": "other"})
        output = diff.summary(records(left, other), records(other, right), "a" * 40, "b" * 40, False)
        self.assertIn("0 added, 0 modified, 0 removed", output)
        self.assertNotIn("script-era", output)
        self.assertNotIn("Change | Release", output)

    def test_dirty_and_untracked_render_inputs_cannot_claim_a_commit_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(["git", "-C", tmp, *args], stderr=subprocess.DEVNULL)
            git("init", "-q")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "Fixture")
            self.assertEqual(diff.revision(root), git("rev-parse", "HEAD").decode().strip())
            (root / "extra.yaml").write_text("unreviewed")
            with self.assertRaisesRegex(ValueError, "clean"):
                diff.revision(root)

    def test_failed_child_diagnostics_stay_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as raised:
                diff.child(Path(tmp), {}, sys.executable, "-c",
                           'import sys; print("private-payload"); sys.exit("private-payload")')
        self.assertNotIn("private-payload", str(raised.exception))

    def test_duplicate_and_unsafe_names_cannot_enter_summary(self):
        obj = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "ok"}}
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            records(obj, obj)
        for invalid in ("new\n## fake", "name|", "left`right", "<img>"):
            with self.assertRaisesRegex(ValueError, "Invalid Kubernetes identity"):
                records(dict(obj, metadata={"name": invalid}))

    def test_summary_names_which_side_was_verified(self):
        empty = records()
        verified = diff.summary(empty, empty, "a" * 40, "b" * 40, False, base_verified=True)
        self.assertIn("Head charts: verified against the chart archive lock. Base charts: verified against the lock.", verified)
        unverified = diff.summary(empty, empty, "a" * 40, "b" * 40, False)
        self.assertIn("Base charts: NOT verified, rendered from the chart publishers.", unverified)

    def state_script(self, root, body):
        script = root / "scripts/demo/chart_archives.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(body)

    def test_a_state_failure_is_a_verification_error_without_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.state_script(root, 'import sys; print("private-payload"); sys.exit("private-payload")')
            with self.assertRaises(diff.VerificationError) as raised:
                with diff.locked_state(root, root / "temp"):
                    pass
            self.assertNotIn("private-payload", str(raised.exception))

    def test_locked_state_restores_a_previous_state_and_removes_a_new_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.state_script(root, 'import pathlib; '
                                    '(pathlib.Path(__file__).resolve().parents[2] / ".helmfile-locked.yaml").write_text("locked")')
            target = root / ".helmfile-locked.yaml"
            target.write_text("previous")
            with diff.locked_state(root, root / "temp") as state:
                self.assertEqual(state, ".helmfile-locked.yaml")
                self.assertEqual(target.read_text(), "locked")
            self.assertEqual(target.read_text(), "previous")
            target.unlink()
            with diff.locked_state(root, root / "temp2"):
                self.assertEqual(target.read_text(), "locked")
            self.assertFalse(target.exists())

    def run_main(self, *, legacy, state_error=None):
        calls = []
        @contextlib.contextmanager
        def fake_state(root, temp):
            calls.append(("state", root.name))
            if state_error == root.name:
                raise diff.VerificationError("bad")
            yield ".helmfile-locked.yaml"
        def fake_render(root, temp, legacy_flag, **kwargs):
            calls.append(("render", root.name, kwargs.get("state")))
            return {}
        with tempfile.TemporaryDirectory() as tmp:
            base, head = Path(tmp) / "base", Path(tmp) / "head"
            for path in (base, head):
                path.mkdir()
            (head / "helmfile.yaml").write_text("")
            if not legacy:
                (base / "helmfile.yaml").write_text("")
            with mock.patch.object(diff, "locked_state", fake_state), \
                 mock.patch.object(diff, "render", fake_render), \
                 mock.patch.object(diff, "revision", lambda root: "a" * 40), \
                 mock.patch.object(sys, "argv", ["pr_render_diff.py", "--base", str(base), "--head", str(head)]), \
                 mock.patch("sys.stdout") as out, mock.patch("sys.stderr") as err:
                try:
                    diff.main()
                    code = 0
                except SystemExit as exit_:
                    code = exit_.code
                text = "".join(call.args[0] for call in out.write.call_args_list)
                errors = "".join(call.args[0] for call in err.write.call_args_list)
        return code, calls, text, errors

    def test_head_and_a_helmfile_base_both_render_from_locked_state(self):
        code, calls, text, _ = self.run_main(legacy=False)
        self.assertEqual(code, 0)
        self.assertEqual([c for c in calls if c[0] == "render"],
                         [("render", "base", ".helmfile-locked.yaml"), ("render", "head", ".helmfile-locked.yaml")])
        self.assertIn("Base charts: verified against the lock.", text)

    def test_a_script_era_base_is_labeled_unverified(self):
        code, calls, text, _ = self.run_main(legacy=True)
        self.assertEqual(code, 0)
        self.assertNotIn(("state", "base"), calls)
        self.assertIn("Base charts: NOT verified", text)

    def test_a_head_that_does_not_verify_fails_before_any_render(self):
        code, calls, text, errors = self.run_main(legacy=False, state_error="head")
        self.assertEqual(code, 1)
        self.assertEqual([c for c in calls if c[0] == "render"], [])
        self.assertEqual(text, "")
        self.assertIn("Could not verify the pinned chart archives against the lock.", errors)

    def test_a_helmfile_base_that_does_not_verify_fails_instead_of_falling_back(self):
        code, calls, text, errors = self.run_main(legacy=False, state_error="base")
        self.assertEqual(code, 1)
        self.assertEqual([c for c in calls if c[0] == "render"], [])
        self.assertEqual(text, "")
        self.assertIn("Could not verify the pinned chart archives against the lock.", errors)

    def test_a_locked_state_render_never_pulls_from_the_publishers(self):
        calls = []
        def fake_child(root, env, *args):
            calls.append(args)
            if args[:1] == ("helmfile",) and "list" in args:
                return b"[]"
            return b"{}"
        with tempfile.TemporaryDirectory() as tmp:
            root, temp = Path(tmp) / "root", Path(tmp) / "temp"
            root.mkdir()
            (root / ".helmfile-locked.yaml").write_text("releases: []\n")
            with mock.patch.object(diff, "child", fake_child):
                self.assertEqual(diff.render(root, temp, False, state=".helmfile-locked.yaml"), {})
        self.assertFalse(any("repos" in args for args in calls))
        self.assertTrue(any(args[:2] == ("helmfile", "-f") and args[2] == ".helmfile-locked.yaml" for args in calls))


if __name__ == "__main__":
    unittest.main()

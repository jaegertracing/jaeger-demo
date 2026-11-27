#!/usr/bin/env python3
# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

"""Compare offline PR base and head renders. Print only validated Kubernetes identities."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import yaml

from chart_archives import LOCKED_STATE
from companion_values import source_values

# The parity rehearsal owns the script-era chart arguments and fixture images.
sys.path.insert(0, str(Path(__file__).parent / "rehearsals/helmfile-applications"))
from equivalence import IMAGES, TARGET, ManifestLoader, legacy_inputs, stable_values


DNS = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*")
KIND = re.compile(r"[a-zA-Z][a-zA-Z0-9]*")


def identity_field(value, pattern=DNS):
    if not isinstance(value, str) or len(value) > 253 or not pattern.fullmatch(value):
        raise ValueError("Invalid Kubernetes identity in rendered fixture")
    return value


def index_objects(records, ns, release, docs):
    owner = identity_field(ns) + "/" + identity_field(release)
    for obj in docs:
        if obj is None:
            continue
        group = obj["apiVersion"].split("/")
        if len(group) not in (1, 2):
            raise ValueError("Invalid Kubernetes API version")
        api = "/".join(identity_field(part) for part in group)
        meta = obj["metadata"]
        key = (owner, api, identity_field(obj["kind"], KIND),
               identity_field(meta["namespace"]) if "namespace" in meta else "", identity_field(meta["name"]))
        if key in records:
            raise ValueError("Duplicate rendered identity")
        canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        records[key] = hashlib.sha256(canonical).digest()


def summary(base, head, before, after, legacy, base_verified=False):
    for commit in (before, after):
        if not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise ValueError("Invalid revision identity")
    changed = []
    counts = {"Added": 0, "Modified": 0, "Removed": 0}
    for key in sorted(base.keys() | head.keys()):
        if base.get(key) == head.get(key):
            continue
        state = "Added" if key not in base else "Removed" if key not in head else "Modified"
        counts[state] += 1
        changed.append((state, key))
    output = ["## Rendered Kubernetes changes", "", f"Base `{before}` and head `{after}`.", ""]
    output += ["Head charts: verified against the chart archive lock. "
               f"Base charts: {'verified against the lock' if base_verified else 'NOT verified, rendered from the chart publishers'}.", ""]
    if legacy:
        output += ["The base uses the script-era chart arguments and supplemental manifests. The head uses Helmfile.", ""]
    output += [f"{counts['Added']} added, {counts['Modified']} modified, {counts['Removed']} removed. "
               f"Compared {len(base)} base and {len(head)} head resources.", ""]
    if changed:
        output += ["| Change | Release | API version | Kind | Rendered namespace | Resource |",
                   "| --- | --- | --- | --- | --- | --- |"]
        for action, (release, api, kind, namespace, name) in changed[:400]:
            output.append(f"| {action} | `{release}` | `{api}` | `{kind}` | `{namespace or '(omitted)'}` | `{name}` |")
        if len(changed) > 400:
            output += ["", f"Showing the first 400 of {len(changed)} changed identities."]
    return "\n".join(output) + "\n"


def child(root, env, *args):
    proc = subprocess.run(args, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=360, check=False)
    if proc.returncode:
        raise ValueError("Public fixture preparation or rendering failed")
    return proc.stdout


def revision(root):
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                                    stderr=subprocess.DEVNULL)
    if dirty:
        raise ValueError("Use clean base and head checkouts")
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
                                   text=True, stderr=subprocess.DEVNULL).strip()


def tool_env(temp, home):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "KUBECONFIG": os.devnull,
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
           "HELM_CACHE_HOME": str(temp / "cache"), "HELM_CONFIG_HOME": str(temp / "config"),
           "HELM_DATA_HOME": str(temp / "data")}
    if "HELM_PLUGINS" in os.environ:
        env["HELM_PLUGINS"] = os.environ["HELM_PLUGINS"]
    return env


class VerificationError(ValueError):
    """A chart archive did not verify against its lock."""


@contextlib.contextmanager
def locked_state(root, temp):
    """Write root's lock-verified Helmfile state and yield its path relative to root.

    Any refusal, including a digest mismatch or a failed pull, is an error. Nothing falls back
    to the publishers' charts. On exit the checkout's previous state file is restored, or the
    written state removed when there was none, so a local comparison never leaves a state whose
    archive paths point into a deleted temporary directory.
    """
    home = temp / "home"
    home.mkdir(mode=0o700, parents=True)
    child_env = tool_env(temp, home)
    archives = temp / "archives"
    archives.mkdir(mode=0o700)
    target = root / LOCKED_STATE
    previous = target.read_bytes() if target.is_file() else None
    try:
        try:
            child(root, child_env, sys.executable, "scripts/demo/chart_archives.py", "state", str(archives))
        except ValueError as error:
            raise VerificationError("Chart archives did not verify") from error
        yield LOCKED_STATE
    finally:
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous)


def render(root, temp, legacy, target=TARGET, index=index_objects, images=IMAGES, stable=True, state="helmfile.yaml"):
    """Render the six releases. `target`, `index`, `images`, `stable` and `state` let another check reuse this render.

    `images` applies to the Helmfile render only; the script-era projection uses the fixture images.
    `stable` adds the public fixture Grafana password; without it the checkout's own values apply.
    `state` is the application Helmfile, relative to root. Another check passes the lock-verified
    state that scripts/demo/chart_archives.py writes, which has local archives and no repositories,
    or None to render the companion releases alone.
    """
    def fixture_values(name):
        return stable_values(name) if stable else []

    temp.mkdir(mode=0o700)
    home = temp / "home"
    home.mkdir(mode=0o700)
    env = tool_env(temp, home)
    tools = root / ".tools"
    tools.mkdir(exist_ok=True)
    child(root, env, "bash", "-eu", "-o", "pipefail", "-c",
          'source scripts/demo/jaeger-chart.sh; prepare_jaeger_chart "$1"', "pr-diff", str(tools / "helm-charts"))
    records = {}
    if legacy:
        for name, url in (("opensearch", "https://opensearch-project.github.io/helm-charts"),
                          ("open-telemetry", "https://open-telemetry.github.io/opentelemetry-helm-charts")):
            child(root, env, "helm", "repo", "add", name, url)
        for ns, name, chart, flags in legacy_inputs(root):
            raw = child(root, env, "helm", "template", name, chart, "--namespace", ns, *target, *flags, *fixture_values(name))
            index(records, ns, name, yaml.load_all(raw, Loader=ManifestLoader))
        for ns, companions in source_values(root)["companions"].items():
            for obj in companions["objects"]:
                # The script applied each object with --namespace, including the generated ConfigMap.
                obj["metadata"].setdefault("namespace", ns)
            index(records, ns, "demo-resources", companions["objects"])
    else:
        if state == "helmfile.yaml":
            child(root, env, "helmfile", "-f", "helmfile.yaml", "repos")
        for path in (state, "helmfile-resources.yaml.gotmpl"):
            if path is None:
                continue
            if not (root / path).is_file():
                if path == state:
                    raise ValueError("Missing head Helmfile")
                continue
            fixture = temp / (Path(path).name + ".json")
            if path == state:
                fixture.write_text(json.dumps({"images": images}))
            else:
                # Use this revision's loader and its own supplemental source mapping.
                fixture.write_bytes(child(root, env, sys.executable, "scripts/demo/companion_values.py"))
            args = ["helmfile", "-f", path, "--state-values-file", str(fixture)]
            releases = json.loads(child(root, env, *args, "list", "--output", "json"))
            for item in releases:
                ns, name = identity_field(item["namespace"]), identity_field(item["name"])
                if not item["enabled"] or not item["installed"]:
                    continue
                raw = child(root, env, *args, "--skip-deps", "-l", f"namespace={ns},name={name}",
                            "template", "--skip-needs", *target, *fixture_values(name))
                index(records, ns, name, yaml.load_all(raw, Loader=ManifestLoader))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    args = parser.parse_args()
    base, head = args.base.resolve(), args.head.resolve()
    try:
        if base == head or not (head / "helmfile.yaml").is_file():
            raise ValueError("Expected separate worktrees and a head Helmfile")
        before, after = revision(base), revision(head)
        legacy = not (base / "helmfile.yaml").is_file()
        with tempfile.TemporaryDirectory(prefix="demo-pr-renders-") as staging:
            root = Path(staging)
            # The head must verify. Every Helmfile base can verify too, so only a script-era
            # base keeps the publisher render; a Helmfile base that does not verify fails the job.
            with contextlib.ExitStack() as states:
                head_state = states.enter_context(locked_state(head, root / "head-archives"))
                base_verified = not legacy
                base_state = states.enter_context(locked_state(base, root / "base-archives")) if base_verified else "helmfile.yaml"
                baseline = render(base, root / "base", legacy, state=base_state)
                candidate = render(head, root / "head", False, state=head_state)
            if (before, after) != (revision(base), revision(head)):
                raise ValueError("Checkout changed while rendering")
            print(summary(baseline, candidate, before, after, legacy, base_verified), end="")
    except VerificationError:
        parser.exit(1, "Could not verify the pinned chart archives against the lock.\n")
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError, yaml.YAMLError):
        # Child tools, YAML parser excerpts and exception values can include an
        # entire Secret. The public job gets only this fixed failure message.
        parser.exit(1, "Could not compare both offline renders. Verify the pinned charts and fixture inputs.\n")


if __name__ == "__main__":
    main()

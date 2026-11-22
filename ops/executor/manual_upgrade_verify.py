# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0
"""Prove an installed manual upgrade package enforces before a demo-apply run is approved.

Admission policies load asynchronously, and lock-only gates return the same lock
message as the content pins, so lock denials alone cannot show that a pin is loaded.
This checks that every guard in guards.json is installed unchanged and that no hook
from a failed run is left behind, then runs the
server dry-run probes from probes.json as the manual user:

  --phase unlocked  no manual holder: every lock gate must deny.
  --phase held      an operator-set manual holder and no run: every pin, hook,
                    hook inventory and history policy must deny with its own message.

Run both phases after installing the package and before approving a run. Nothing is
stored: every probe is a server dry-run.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manual_upgrade as m  # noqa: E402
import permissions as r  # noqa: E402

PHASES = ("unlocked", "held")


def contains(expected, actual):
    """Every field the package set is present unchanged; the API server may add defaults."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(map(contains, expected, actual))
    return expected == actual


def installed(guards, kube):
    """Every policy, binding and the archive ConfigMap of guards.json is present as rendered."""
    listed = subprocess.run([*kube, "get", "validatingadmissionpolicies,validatingadmissionpolicybindings", "-o", "json"],
                            capture_output=True, timeout=120, check=True)
    live = {(o["kind"], o["metadata"]["name"]): o for o in json.loads(listed.stdout)["items"]}
    for item in guards:
        key = (item["kind"], item["metadata"]["name"])
        if item["kind"] == "ConfigMap":
            got = subprocess.run([*kube, "-n", item["metadata"]["namespace"], "get", "configmap", key[1], "-o", "json"],
                                 capture_output=True, timeout=60, check=True)
            found = json.loads(got.stdout)
            r.require(found.get("binaryData") == item["binaryData"] and found.get("immutable") is True, "Hook archive differs: " + key[1])
            continue
        r.require(key in live, "Guard not installed: " + "/".join(key))
        r.require(contains(item["spec"], live[key]["spec"]), "Installed guard differs from guards.json: " + "/".join(key))
    return len(guards)


def leftover_hooks(probes, kube):
    """Hooks exist only during a hook run; the chart deletes them on success. A failed run leaves them, and
    Helm's before-hook-creation delete is checked against this commit's hook, so an older leftover would
    block the retry. An administrator removes leftovers before a run is approved."""
    found = []
    for probe in probes:
        if probe["expect"] != m.HOOK_DENIED:
            continue
        meta = probe["body"]["metadata"]
        namespace = ["-n", meta["namespace"]] if meta.get("namespace") else []
        got = subprocess.run([*kube, *namespace, "get", m.resource_arg(*m.hooks.KINDS[probe["body"]["kind"]]), meta["name"],
                              "--ignore-not-found", "-o", "name"], capture_output=True, text=True, timeout=60, check=True)
        if got.stdout.strip():
            found.append(got.stdout.strip())
    r.require(not found, "Delete leftover hooks before approving a run: " + ", ".join(found))
    return 0


def holder(kube):
    got = subprocess.run([*kube, "-n", "default", "get", "configmap", r.CHECKPOINT, "-o", "json"],
                         capture_output=True, timeout=60, check=True)
    return (json.loads(got.stdout)["metadata"].get("annotations") or {}).get(r.HOLDER, "")


def attempt(probe, kube, user):
    base = [*kube, "--as", user, "--as-group", "system:authenticated"]
    namespace = ["-n", probe["namespace"]] if probe.get("namespace") else []
    body = None
    if probe["verb"] == "patch":
        args = [*base, *namespace, "patch", probe["resource"], probe["name"], "--type=" + probe["type"],
                "-p", json.dumps(probe["patch"]), "--dry-run=server"]
    elif probe["verb"] == "delete":
        args = [*base, *namespace, "delete", probe["resource"], probe["name"], "--dry-run=server"]
    else:
        args = [*base, *namespace, "create", "-f", "-", "--dry-run=server"]
        body = json.dumps(probe["body"])
    return subprocess.run(args, input=body, text=True, capture_output=True, timeout=60)


def run(probes, phase, kube, user, *, seconds=120):
    """Each probe must be denied with its expected message; retry while policies load."""
    r.require(phase in PHASES, "Unknown phase")
    held = re.fullmatch(m.HOLDER_PATTERN.strip("^$"), holder(kube)) is not None
    r.require(held == (phase == "held"), "The checkpoint holder does not match the " + phase + " phase")
    selected = [p for p in probes if p["phase"] == phase]
    r.require(selected, "No probes for the " + phase + " phase")
    deadline = time.monotonic() + seconds
    for probe in selected:
        while True:
            result = attempt(probe, kube, user)
            if result.returncode and probe["expect"] in result.stderr:
                break
            if time.monotonic() > deadline:
                raise ValueError(f"Not enforced: {probe['label']}: {(result.stderr or 'admitted')[:400]}")
            time.sleep(1)
    return [p["label"] for p in selected]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--package", type=Path, required=True, help="The rendered package directory")
    parser.add_argument("--user", required=True)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--phase", choices=PHASES, required=True)
    args = parser.parse_args()
    m.subjects_for(args.user)
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.context, "--request-timeout=30s"]
    guards = json.loads((args.package / "guards.json").read_bytes())["items"]
    probes = json.loads((args.package / "probes.json").read_bytes())
    report = {"phase": args.phase, "guards": installed(guards, kube), "leftoverHooks": leftover_hooks(probes, kube),
              "denied": run(probes, args.phase, kube, args.user)}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

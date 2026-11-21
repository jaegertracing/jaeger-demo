# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

"""Build the local hook fixture from the reviewed public chart and repository values."""

import argparse
import base64
import bz2
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

import yaml

CHART = "e0c601a95d333616fd30961ec9374893fba2b6852ce7df87f97f9efdd2cb9b41"
VALUES = "2852cb6b1e50687218aef41acf9666106c164d503e3846dc591206c9fe8b2473"
SEED = "533412f8fa328a3d772901f1c7b17d95ceeb671e9c3b428dd3cd44613132222d"
ARTIFACT = "monitoring-hook-fixture"
MARKER = "jaegertracing.io/local-rehearsal-marker"
REVIEWED = "e82c0f9b61666adcccb20cea759f90478f726bc7932a3eca42e274ed39e75dd2"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def content_digest(pkg):
    """Bind every Helm input; ignore archive time, ownership and compression headers."""
    expected = {f"{ARTIFACT}/Chart.yaml"}
    expected.update(
        f"{ARTIFACT}/{part}/{i:02}.yaml"
        for i in range(17)
        for part in ("artifacts", "templates")
    )
    with tarfile.open(pkg, "r:gz") as chart:
        members = chart.getmembers()
        if (
            len(members) != len(expected)
            or {m.name for m in members} != expected
            or not all(m.isfile() and 0 < m.size <= 1024 * 1024 for m in members)
        ):
            raise ValueError("Unexpected fixture package members")
        hashes = {m.name: sha(chart.extractfile(m).read()) for m in members}
    return sha(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())


def build(args):
    archive = args.archive.resolve()
    # The receipt records chart 82.10.4 with the values reviewed for it. The repository's
    # values have moved on with later charts, so the reviewed copy is kept beside the fixture.
    values = Path(__file__).with_name("reviewed-monitoring-values.yaml")
    if sha(archive.read_bytes()) != CHART or sha(values.read_bytes()) != VALUES:
        raise ValueError("Pinned public chart or values differ from reviewed inputs")
    out = args.output.resolve()
    out.mkdir(mode=0o700)
    chart = out / ARTIFACT
    (chart / "templates").mkdir(parents=True)
    (chart / "artifacts").mkdir()
    pkgdir = out / "package"
    pkgdir.mkdir()
    (chart / "Chart.yaml").write_text(
        "apiVersion: v2\nname: monitoring-hook-fixture\nversion: 0.0.1\n"
    )
    with tempfile.TemporaryDirectory() as temp:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": temp,
            "KUBECONFIG": temp + "/empty",
            "HELM_NO_PLUGINS": "1",
            "HELM_CONFIG_HOME": temp,
            "HELM_CACHE_HOME": temp,
            "HELM_DATA_HOME": temp,
        }
        if (
            not subprocess.check_output([args.helm, "version", "--short"], env=env)
            .decode()
            .startswith("v4.3.2+")
        ):
            raise ValueError("Helm 4.3.2 required")
        render = [
            args.helm,
            "template",
            "prometheus",
            str(archive),
            "--namespace",
            "default",
            "--kube-version",
            "1.36.1",
            "--dry-run=client",
            "--is-upgrade",
            "-f",
            str(values),
            "--set",
            "crds.upgradeJob.enabled=true",
            "--set",
            "crds.upgradeJob.forceConflicts=true",
        ]
        docs = [
            o for o in yaml.safe_load_all(subprocess.check_output(render, env=env)) if o
        ]
        selected = [
            o
            for o in docs
            if "helm.sh/hook" in (o["metadata"].get("annotations") or {})
            or o["kind"]
            in (
                "Secret",
                "ValidatingWebhookConfiguration",
                "MutatingWebhookConfiguration",
            )
        ]
        if len(docs) != 129 or len(selected) != 17:
            raise ValueError("Unexpected chart resource inventory")
        selected.sort(
            key=lambda o: (
                o["kind"],
                o["metadata"].get("namespace", ""),
                o["metadata"]["name"],
            )
        )
        for i, obj in enumerate(selected):
            file = f"{i:02}.yaml"
            template = '{{ .Files.Get "artifacts/' + file + '" }}\n'
            if (
                obj["kind"] == "Secret"
                and obj["metadata"]["name"] == "prometheus-grafana"
            ):
                obj["data"]["admin-password"] = base64.b64encode(
                    b"local-fixture-only"
                ).decode()
                template = (
                    '{{- $obj := .Files.Get "artifacts/' + file + '" | fromJson -}}\n'
                    "{{- $ann := default (dict) $obj.metadata.annotations -}}\n"
                    '{{- $_ := set $ann "'
                    + MARKER
                    + '" (default "baseline" .Values.fixtureMarker) -}}\n'
                    '{{- $_ := set $obj.metadata "annotations" $ann -}}\n{{ $obj | toJson }}\n'
                )
            (chart / "artifacts" / file).write_text(
                json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"
            )
            (chart / "templates" / file).write_text(template)
        subprocess.run(
            [args.helm, "package", str(chart), "--destination", str(pkgdir)],
            env=env,
            check=True,
        )
    pkg = pkgdir / (ARTIFACT + "-0.0.1.tgz")
    if content_digest(pkg) != REVIEWED:
        raise ValueError("Packaged templates or public fixture resources changed")
    config = next(o for o in selected if o["kind"] == "ConfigMap")
    seed = bz2.decompress(
        base64.b64decode(config["binaryData"]["crds.bz2"], validate=True)
    )
    if sha(seed) != SEED:
        raise ValueError("Unexpected CRD payload")
    (out / "seed-monitoring-crds.yaml").write_bytes(seed)
    images = {
        c["image"]
        for o in selected
        if o["kind"] == "Job"
        for group in ("initContainers", "containers")
        for c in o["spec"]["template"]["spec"].get(group, [])
    }
    crds = [
        o["metadata"]["name"] for o in yaml.load_all(seed, Loader=yaml.BaseLoader) if o
    ]
    receipt = {
        "helmPackagedFixtureSHA256": sha(pkg.read_bytes()),
        "crdPayloadSHA256": SEED,
        "hookImages": sorted(images),
        "crds": sorted(crds),
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--helm", default="helm")
    build(parser.parse_args())

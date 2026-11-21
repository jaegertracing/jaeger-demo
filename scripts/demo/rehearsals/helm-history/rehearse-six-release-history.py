#!/usr/bin/env python3
# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

"""Exercise Helm storage against a private, loopback-only disposable API server.

Supply --assets containing official envtest kube-apiserver, etcd and kubectl.
Keep helm 4.3.2 and openssl on PATH. Python 3 uses the standard library.
The script generates local-only credentials, never reads a user's kubeconfig, and
removes its data directory and processes on exit. The output receipt is public.
"""

import argparse
import contextlib
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
WRITER = "fixture-helm-writer"
SCOPE = (
    ("default", "jaeger"),
    ("default", "prometheus"),
    ("jaeger", "jaeger"),
    ("opensearch", "opensearch"),
    ("opensearch", "opensearch-dashboards"),
    ("otel-demo", "otel-demo"),
)
POLICY = HERE / "helm-history-policy.yaml"
DENIAL = "Only canonical Helm history"


def now():
    return datetime.now(timezone.utc).isoformat()


def free_ports():
    with contextlib.ExitStack() as stack:
        handles = [stack.enter_context(socket.socket()) for _ in range(3)]
        for handle in handles:
            handle.bind(("127.0.0.1", 0))
        return [h.getsockname()[1] for h in handles]


def terminate(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-live-observation", action="store_true", help="Exercise the Helmfile read-only observer")
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--storage-charts", type=Path,
                        help="Also exercise the two pinned storage charts from this archive directory")
    parser.add_argument("--frozen-transaction", action="store_true", help="Exercise the scripts-v1 signed-render Helm transaction")
    parser.add_argument("--monitoring-lookup", type=Path, help="Verify full chart dry-run and real Grafana Secret lookup")
    parser.add_argument("--monitoring-hook-access", type=Path, help="Verify RBAC/admission using the twelve pinned monitoring hooks")
    parser.add_argument("--manual-upgrade-access", type=Path, metavar="CHARTS",
                        help="Verify the manual deploy identity's guarded grants with the pinned charts in this directory")
    parser.add_argument("--jaeger-patch-methods", type=Path, help="Audit real Jaeger chart SSA/CSA upgrade patch permissions")
    parser.add_argument("--typed-approval-rehearsal", action="store_true",
                        help="Use a fresh API exclusively for the per-slot workload admission rehearsal")
    parser.add_argument("--six-chart-approvals", nargs=2, type=Path, metavar=("CHARTS", "JAEGER_CHART_CHECKOUT"),
                        help="Exercise actual public chart renders and broad native approval partitions on a fresh API")
    args = parser.parse_args()
    assets = args.assets.resolve()
    receipt = args.receipt.absolute()
    if receipt.is_symlink():
        parser.error("The evidence output must be a regular file.")
    if receipt.exists():
        try:
            prior = json.loads(receipt.read_text())
            is_fixture = (
                isinstance(prior, dict)
                and prior.get("productionAccess") is False
                and prior.get("identity") == WRITER
                and prior.get("endpoint") == "ephemeral 127.0.0.1"
                and prior.get("passed") is True
            )
        except (OSError, ValueError):
            is_fixture = False
        if not is_fixture:
            parser.error("Choose a new receipt or a previously generated six-release fixture receipt.")
    if not __debug__:
        parser.error("Use the normal Python interpreter so rehearsal assertions stay enabled.")

    def on_termination(_signal, _frame):
        raise SystemExit("Interrupted during local rehearsal")

    signal.signal(signal.SIGTERM, on_termination)
    apiserver, etcd, kubectl = [str(assets / x) for x in ("kube-apiserver", "etcd", "kubectl")]
    helm = shutil.which("helm")
    openssl = shutil.which("openssl")
    assert helm and openssl
    old_umask = os.umask(0o077)
    report = dict(
        startedAt=now(),
        productionAccess=False,
        endpoint="ephemeral 127.0.0.1",
        identity=WRITER,
        authorization="local API server RBAC with user impersonation",
        scope="Synthetic chart with immutable ConfigMap. Local real Secret admission, Helm storage, pruning and atomic rollback.",
        limits=[
            "No application pods, cloud CSI, chart hooks or OCI identity.",
            "History envelope controls Secret identity, not the encoded Helm payload.",
        ],
        releases=[],
        negativeTests=[],
        policySHA256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),
        harnessSHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    try:
        with (
            tempfile.TemporaryDirectory(prefix="six-history-", dir=receipt.parent) as temp,
            contextlib.ExitStack() as stack,
        ):
            root = Path(temp)
            cfg = root / "fixture.kubeconfig"
            env = dict(
                PATH=os.environ["PATH"],
                KUBECONFIG=str(cfg),
                HELM_DRIVER="secret",
                HELM_CONFIG_HOME=str(root / "helm-config"),
                HELM_CACHE_HOME=str(root / "helm-cache"),
                HELM_DATA_HOME=str(root / "helm-data"),
                TMPDIR=str(root),
                NO_PROXY="127.0.0.1,localhost",
            )

            def execute(cmd, body=None, check=True, timeout=35):
                p = subprocess.run(
                    list(map(str, cmd)),
                    input=body,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    cwd=root,
                    timeout=timeout,
                )
                if check and p.returncode:
                    # None of the test commands output credentials. Input is never included.
                    raise RuntimeError(
                        f"{Path(cmd[0]).name} {cmd[1]} failed ({p.returncode}): {p.stderr[-1300:]}"
                    )
                return p

            def kube(cmd, writer=False, **kw):
                as_user = ["--as", WRITER, "--as-group", "system:authenticated"] if writer else []
                return execute([kubectl, "--kubeconfig", cfg, "--context", "fixture", *as_user, *cmd], **kw)

            def release_cmd(cmd, ns, writer=True, **kw):
                as_user = (
                    ["--kube-as-user", WRITER, "--kube-as-group", "system:authenticated"] if writer else []
                )
                return execute(
                    [
                        helm,
                        "--kubeconfig",
                        cfg,
                        "--kube-context",
                        "fixture",
                        "--namespace",
                        ns,
                        *as_user,
                        *cmd,
                    ],
                    **kw,
                )

            def apply(doc):
                return kube(["apply", "-f", "-"], body=json.dumps(doc))

            def run_server(cmd, name):
                stream = stack.enter_context((root / f"{name}.log").open("w"))
                proc = subprocess.Popen(list(map(str, cmd)), env=env, cwd=root, stdout=stream, stderr=stream)
                stack.callback(terminate, proc)
                return proc

            def assert_denied(label, cmd, body=None, admission=True):
                p = kube(cmd, writer=True, check=False, body=body)
                expected = DENIAL if admission else "forbidden"
                if p.returncode == 0 or expected not in p.stderr:
                    raise AssertionError(label + " expected denial: " + p.stderr[-900:])
                report["negativeTests"].append(dict(test=label, through="admission" if admission else "RBAC"))

            def history(ns, name):
                return [
                    (int(r["revision"]), r["status"])
                    for r in json.loads(release_cmd(["history", name, "-o", "json"], ns).stdout)
                ]

            def cm(ns, name):
                doc = json.loads(
                    kube(["get", "cm", name + "-fixture", "-n", ns, "-o", "json"], writer=True).stdout
                )
                assert doc["immutable"] and doc["data"] == {"marker": "stable"}
                return doc["metadata"]["uid"]

            def fixture_secret(ns, name, release="jaeger", v="1", typ="helm.sh/release.v1"):
                return dict(
                    apiVersion="v1",
                    kind="Secret",
                    type=typ,
                    metadata=dict(
                        namespace=ns, name=name, labels=dict(owner="helm", name=release, version=v)
                    ),
                )

            v = execute([helm, "version", "--short"]).stdout.strip()
            assert v.startswith("v4.3.2+"), v
            report.update(
                helm=v,
                apiServer=execute([apiserver, "--version"]).stdout.strip(),
                kubectl=json.loads(execute([kubectl, "version", "--client", "-o", "json"]).stdout)[
                    "clientVersion"
                ]["gitVersion"],
                etcd=execute([etcd, "--version"]).stdout.splitlines()[0],
            )
            assert report["apiServer"] == "Kubernetes v1.36.0"
            assert report["kubectl"] == "v1.36.0"
            assert report["etcd"] == "etcd Version: 3.6.8"
            cert, key, sa_key, tokens = [
                root / p for p in ("tls.pem", "tls.key", "service-account.key", "tokens.csv")
            ]
            execute(
                [
                    openssl,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-sha256",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1,DNS:localhost",
                    "-keyout",
                    key,
                    "-out",
                    cert,
                ]
            )
            execute([openssl, "genrsa", "-out", sa_key, "2048"])
            token = secrets.token_urlsafe(48)
            with tokens.open("w", newline="") as fp:
                csv.writer(fp).writerow((token, "fixture-local-admin", "fixture-admin", "system:masters"))
            port, peer, api = free_ports()
            endpoint = f"http://127.0.0.1:{port}"
            peer_endpoint = f"http://127.0.0.1:{peer}"
            server = f"https://127.0.0.1:{api}"
            etcd_proc = run_server(
                [
                    etcd,
                    "--name",
                    "default",
                    "--data-dir",
                    root / "etcd",
                    "--listen-client-urls",
                    endpoint,
                    "--advertise-client-urls",
                    endpoint,
                    "--listen-peer-urls",
                    peer_endpoint,
                    "--initial-advertise-peer-urls",
                    peer_endpoint,
                    "--initial-cluster",
                    "default=" + peer_endpoint,
                    "--log-level",
                    "error",
                ],
                "etcd",
            )
            audit = []
            if args.storage_charts or args.jaeger_patch_methods:
                audit_policy = root / "audit-policy.json"
                audit_policy.write_text(json.dumps({
                    "apiVersion": "audit.k8s.io/v1", "kind": "Policy",
                    "omitStages": ["RequestReceived", "ResponseStarted"],
                    "rules": [{"level": "Metadata"}],
                }))
                audit = ["--audit-policy-file", audit_policy, "--audit-log-path", root / "audit.log",
                         "--audit-log-mode", "blocking"]
            api_proc = run_server(
                [
                    apiserver,
                    "--etcd-servers",
                    endpoint,
                    "--secure-port",
                    str(api),
                    "--bind-address",
                    "127.0.0.1",
                    "--advertise-address",
                    "127.0.0.1",
                    "--service-cluster-ip-range",
                    "10.226.0.0/24",
                    "--tls-cert-file",
                    cert,
                    "--tls-private-key-file",
                    key,
                    "--cert-dir",
                    root / "certs",
                    "--token-auth-file",
                    tokens,
                    "--authorization-mode=RBAC",
                    "--anonymous-auth=false",
                    "--service-account-signing-key-file",
                    sa_key,
                    "--service-account-key-file",
                    sa_key,
                    "--service-account-issuer",
                    "https://fixture.invalid",
                    "--allow-privileged=false",
                    *audit,
                ],
                "apiserver",
            )
            cfg.write_text(
                json.dumps(
                    dict(
                        apiVersion="v1",
                        kind="Config",
                        clusters=[
                            dict(
                                name="fixture", cluster={"server": server, "certificate-authority": str(cert)}
                            )
                        ],
                        users=[dict(name="admin", user={"token": token})],
                        contexts=[dict(name="fixture", context={"cluster": "fixture", "user": "admin"})],
                        **{"current-context": "fixture"},
                    )
                )
            )
            for _ in range(90):
                if kube(["get", "--raw", "/readyz"], check=False, timeout=6).returncode == 0:
                    break
                if any(p.poll() is not None for p in (api_proc, etcd_proc)):
                    raise RuntimeError(
                        "Local API startup failed: " + (root / "apiserver.log").read_text()[-1800:]
                    )
                time.sleep(0.4)
            else:
                raise RuntimeError("Local API timeout: " + (root / "apiserver.log").read_text()[-1500:])
            if args.six_chart_approvals:
                if any((args.typed_approval_rehearsal, args.storage_charts, args.monitoring_lookup, args.monitoring_hook_access,
                        args.frozen_transaction, args.jaeger_patch_methods)):
                    raise ValueError("The six-chart preparation fixture requires its own empty API")
                from six_chart_approvals import run
                evidence = run(cfg, root, kube, *(p.resolve() for p in args.six_chart_approvals))
                with receipt.open("x") as output:
                    json.dump({"productionAccess": False, "sixChartApprovals": evidence, "passed": True}, output, indent=2)
                    output.write("\n")
                print("PASS: six real chart renders, pre-existing drift, and twelve broad approval partitions")
                return
            if args.typed_approval_rehearsal:
                if any((args.storage_charts, args.monitoring_lookup, args.monitoring_hook_access,
                        args.frozen_transaction, args.jaeger_patch_methods)):
                    raise ValueError("The admission parameter fixture requires its own empty API")
                probe = Path(__file__).resolve().parents[4] / "ops/executor/rehearsals/approvals.py"
                result = execute([sys.executable, probe, "--kubeconfig", cfg, "--context", "fixture"], timeout=480)
                evidence = json.loads(result.stdout)
                assert evidence["exactForwardAndRollback"] and evidence["realHelmSSAAndCSA"]
                assert evidence["rollbackAfterControllerRevisionUpdate"]
                with receipt.open("x") as file:
                    json.dump({"productionAccess": False, "typedApprovals": evidence, "passed": True}, file, indent=2)
                print("PASS: native approval partitions, both Helm apply modes and rollback after a controller revision change")
                return
            for ns in ("jaeger", "opensearch", "otel-demo"):
                kube(["create", "namespace", ns])
            api_group = "rbac.authorization.k8s.io"
            for ns in ("default", "jaeger", "opensearch", "otel-demo"):
                apply(
                    dict(
                        apiVersion=api_group + "/v1",
                        kind="Role",
                        metadata={"namespace": ns, "name": "fixture-history"},
                        rules=[
                            dict(
                                apiGroups=[""],
                                resources=["secrets"],
                                verbs=["get", "list", "create", "update", "delete"],
                            ),
                            dict(
                                apiGroups=[""],
                                resources=["configmaps"],
                                verbs=["get", "list", "watch", "create", "patch", "update"],
                            ),
                            dict(apiGroups=[""], resources=["events"], verbs=["get", "list", "watch"]),
                        ],
                    )
                )
                apply(
                    dict(
                        apiVersion=api_group + "/v1",
                        kind="RoleBinding",
                        metadata={"namespace": ns, "name": "fixture-history"},
                        roleRef=dict(apiGroup=api_group, kind="Role", name="fixture-history"),
                        subjects=[dict(apiGroup=api_group, kind="User", name=WRITER)],
                    )
                )
            kube(["apply", "-f", POLICY])
            # Wait for admission propagation without materializing a forbidden object.
            probe = json.dumps(fixture_secret("default", "wrong-name"))
            for _ in range(60):
                p = kube(["create", "--dry-run=server", "-f", "-"], writer=True, body=probe, check=False)
                if p.returncode and DENIAL in p.stderr:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Policy failed to enforce: " + p.stderr[:500])
            assert_denied("unrelated name on CREATE", ["create", "-f", "-"], probe)
            assert_denied(
                "wrong namespace/release pair",
                ["create", "-f", "-"],
                json.dumps(fixture_secret("jaeger", "sh.helm.release.v1.prometheus.v5", "prometheus", "5")),
            )
            assert_denied(
                "version label mismatch",
                ["create", "-f", "-"],
                json.dumps(fixture_secret("default", "sh.helm.release.v1.jaeger.v88", "jaeger", "89")),
            )
            assert_denied(
                "wrong Secret type",
                ["create", "-f", "-"],
                json.dumps(
                    fixture_secret("default", "sh.helm.release.v1.jaeger.v88", "jaeger", "88", "Opaque")
                ),
            )
            sentinel = fixture_secret("default", "unrelated-application-setting", typ="Opaque")
            kube(["create", "-f", "-"], body=json.dumps(sentinel))
            kube(
                ["get", "secret", "-n", "default", "unrelated-application-setting", "-o", "name"], writer=True
            )
            s = json.loads(
                kube(["get", "secret", "-n", "default", "unrelated-application-setting", "-o", "json"]).stdout
            )
            s["metadata"]["labels"]["owner"] = "attempted"
            assert_denied("unrelated Secret UPDATE", ["replace", "-f", "-"], json.dumps(s))
            assert_denied(
                "unrelated Secret DELETE",
                ["delete", "secret", "unrelated-application-setting", "-n", "default"],
            )
            assert_denied(
                "namespace outside role bindings", ["get", "secrets", "-n", "kube-system"], admission=False
            )
            assert_denied(
                "policy self-modification",
                ["delete", "validatingadmissionpolicy", "demo-history-envelope"],
                admission=False,
            )
            chart = root / "chart"
            (chart / "templates").mkdir(parents=True)
            (chart / "Chart.yaml").write_text("apiVersion: v2\nname: history-fixture\nversion: 0.1.0\n")
            (chart / "templates" / "stable.yaml").write_text(
                "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{ .Release.Name }}-fixture\nimmutable: true\ndata:\n  marker: {{ .Values.marker | quote }}\n"
            )
            for ns, name in SCOPE:
                release_cmd(["install", name, chart, "--set-string", "marker=stable"], ns)
                stable_uid = cm(ns, name)
                for _ in range(3):
                    release_cmd(
                        ["upgrade", name, chart, "--history-max", "3", "--set-string", "marker=stable"], ns
                    )
                initial = history(ns, name)
                assert initial == [(2, "superseded"), (3, "superseded"), (4, "deployed")], initial
                a = kube(
                    ["get", "secret", f"sh.helm.release.v1.{name}.v1", "-n", ns], writer=True, check=False
                )
                assert a.returncode and "NotFound" in a.stderr
                trial = release_cmd(
                    [
                        "upgrade",
                        name,
                        chart,
                        "--history-max",
                        "3",
                        "--atomic",
                        "--wait",
                        "--timeout",
                        "15s",
                        "--set-string",
                        "marker=incompatible",
                    ],
                    ns,
                    check=False,
                    timeout=38,
                )
                if not trial.returncode or "immutable" not in trial.stderr.lower():
                    raise AssertionError(
                        "Expected real immutable ConfigMap rejection: " + trial.stderr[:1200]
                    )
                after = history(ns, name)
                assert after[-2:] == [(5, "failed"), (6, "deployed")], after
                assert cm(ns, name) == stable_uid
                assert set(rev for rev, _ in after) == {
                    int(m["metadata"]["labels"]["version"])
                    for m in json.loads(
                        kube(
                            ["get", "secrets", "-n", ns, "-l", f"owner=helm,name={name}", "-o", "json"],
                            writer=True,
                        ).stdout
                    )["items"]
                    if m["type"] == "helm.sh/release.v1"
                }
                report["releases"].append(
                    dict(
                        namespace=ns,
                        release=name,
                        historyBeforeFailure=initial,
                        historyAfterAtomicRollback=after,
                        prunedRevisionOne=True,
                        immutableDataAndUIDPreserved=True,
                    )
                )
                print(ns + "/" + name + " prune and atomic rollback passed", flush=True)
            # Cross-release identity mutation must be rejected even on canonical existing history.
            d = json.loads(
                kube(
                    ["get", "secret", "sh.helm.release.v1.jaeger.v6", "-n", "default", "-o", "json"],
                    writer=True,
                ).stdout
            )
            d["metadata"]["labels"]["name"] = "prometheus"
            assert_denied("canonical history identity change", ["replace", "-f", "-"], json.dumps(d))
            d = fixture_secret("default", "sh.helm.release.v1.jaeger.v98", "jaeger", "98")
            d["metadata"]["labels"]["owner"] = "foreign"
            kube(["create", "-f", "-"], body=json.dumps(d))
            before = json.loads(
                kube(["get", "secret", d["metadata"]["name"], "-n", "default", "-o", "json"]).stdout
            )
            before["metadata"]["labels"]["owner"] = "helm"
            assert_denied(
                "adopt mismatched OLD record via UPDATE", ["replace", "-f", "-"], json.dumps(before)
            )
            assert_denied(
                "delete foreign record with canonical name",
                ["delete", "secret", d["metadata"]["name"], "-n", "default"],
            )
            if args.verify_live_observation:
                sys.path.insert(0, str(HERE.parents[1]))
                from live_observation_fixture import exercise
                report["liveObservation"] = exercise(root, cfg, kubectl, kube, release_cmd, chart, apply)
            report["unrestrictedSecretReadsInReleaseNamespaces"] = True
            report["historyLimitUsed"] = 3
            if args.monitoring_hook_access:
                from monitoring_hook_access import exercise as exercise_hook_access
                report["monitoringHookAccess"] = exercise_hook_access(args.monitoring_hook_access, kubectl, cfg, kube)
            if args.manual_upgrade_access:
                if args.monitoring_hook_access:
                    raise ValueError("The manual upgrade fixture creates its own checkpoint; run it on a separate API")
                from manual_upgrade_access import exercise as exercise_manual_upgrade
                report["manualUpgradeAccess"] = exercise_manual_upgrade(args.manual_upgrade_access.resolve(), kubectl, cfg, kube)
            if args.frozen_transaction:
                from frozen_transaction import exercise
                report["frozenTransaction"] = exercise(root, cfg, kubectl, chart, WRITER)
            if args.monitoring_lookup:
                from frozen_transaction import monitoring_lookup
                report["monitoringLookup"] = monitoring_lookup(root, cfg, kubectl, kube, apply, args.monitoring_lookup)
            if args.jaeger_patch_methods:
                from jaeger_patch_methods import exercise as exercise_jaeger_patches
                report["jaegerPatchMethods"] = exercise_jaeger_patches(root, cfg, kube, apply, args.jaeger_patch_methods)
            report["kubernetesEnforcedAdmission"] = True
            if args.storage_charts:
                from storage_charts import rehearse

                report["storageChartRehearsal"] = rehearse(
                    args.storage_charts.resolve(), root, kube, release_cmd, WRITER
                )
        report["cleanup"] = (
            "Both local servers stopped and temporary credentials, charts, histories and etcd data removed."
        )
        report["finishedAt"] = now()
        report["passed"] = True
        # Publish a completed receipt atomically and preserve the private umask on retries.
        with tempfile.NamedTemporaryFile("w", dir=receipt.parent, delete=False) as f:
            json.dump(report, f, indent=2)
            f.write("\n")
        os.replace(f.name, receipt)
        print("PASS:", len(report["releases"]), "releases;", len(report["negativeTests"]), "negative checks")
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()

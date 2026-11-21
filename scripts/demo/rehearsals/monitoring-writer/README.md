<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Monitoring update and rollback with a restricted requester

The local rehearsal runs the 12 pinned monitoring hooks, two admission webhook configurations and three chart-owned Secrets from the reviewed kube-prometheus-stack 82.10.4 chart. The operator installs the baseline in a fresh kind cluster. A dedicated Kubernetes ServiceAccount then requests an upgrade and a rollback through two operator-prepared ConfigMaps. The fixture executor runs Helm with separate local administrator credentials. The requester can read and signal only those two ConfigMaps. [Request policy and RBAC](trigger-policy.yaml) · [Executor](tickets.py)

## Observed behavior

Kubernetes 1.36.1 executed seven hook Jobs, three on installation, three on upgrade and one on rollback. The ten monitoring CRDs retained their UIDs and Established state. The three chart-owned Secrets retained their UIDs and payloads. The admission certificate Secret retained its UID and payload, and its CA matched all three webhook entries. Helm recorded revision 3 as deployed after rolling back to the revision 1 fixture. [Execution receipt](receipt.json)

The requester used a short-lived ServiceAccount token and a separate kubeconfig. Ten Kubernetes API requests confirmed its direct access limits, including Helm release and unrelated Secret reads, direct workload operations, RBAC changes and webhook changes. For each transaction the API denied a changed chart digest, an unexpected input and a second signal. A second executor claim against the original resource version failed. [Requester results](receipt.json)

## Request and execution

1. The executor verifies all 35 chart-package members against a digest of their canonical names and bytes. It copies the package into its private working directory and checks the copied bytes. The original public chart, values and ten-CRD seed also have fixed digests.
2. The executor prepares a ConfigMap containing its fixed action, monitoring release, configuration commit, package SHA-256, current Helm revision and stored release digest. A rollback also includes the operator-selected target revision and its stored release digest. The requester can change only the `requested` field from `false` to `true`.
3. The executor checks the object UID, all request fields and both relevant stored release digests. It claims the operation with an atomic JSON Patch that tests the ConfigMap UID, resource version and prior state. Only then does it run its fixed Helm command. The requester supplies no executable, Helm arguments, chart location or cloud credential.
4. The executor checks release status, hook execution timestamps, CRD continuity, Secret continuity and webhook trust. A claimed operation requires operator investigation after an interrupted executor. Repeating the signal cannot start a second Helm transaction.

The single-node fixture has one deployment writer. One trusted test process presents the separate operator and requester kubeconfigs to Kubernetes. It tests the Kubernetes permissions and operation protocol. Isolation of a production runner from the executor process and credentials needs its own rehearsal. Production needs independent executor ownership and permissions, serialization with manual operators, durable outcome and crash recovery handling, validation of the full chart and supplemental resources, and functional checks against the actual workloads. Scheduled activation and its first verified run remain separate. A fixture administrator credential is appropriate only inside this disposable cluster. [Production access plan](../../../../docs/all-five-schedule-access.md)

## Reproduce

Use Python 3 with PyYAML, Helm 4.3.2, kubectl 1.36.x and kind with a working Linux container runtime. The tested local node had four CPUs and 4 GiB RAM. Download the public chart archive and build a new fixture directory. The builder validates the archive and merged values before rendering, substitutes a synthetic Grafana password and verifies the entire package content. The package byte hash can vary with Helm's tar timestamps. Each request records the exact package bytes used in that execution. [Public chart archive](https://github.com/prometheus-community/helm-charts/releases/download/kube-prometheus-stack-82.10.4/kube-prometheus-stack-82.10.4.tgz) · [Fixture builder](build_fixture.py)

The fixture is built from that 82.10.4 archive and from `reviewed-monitoring-values.yaml` in this directory, the values file as it was reviewed with that chart. `examples/oci/monitoring-values.yaml` has since changed with newer chart versions, and the recorded digests no longer match it.

From the repository root, set `chart` to the downloaded archive and run:

```bash
set -eu
work=$(mktemp -d)
tools=scripts/demo/rehearsals/monitoring-writer
python3 "$tools/build_fixture.py" --archive "$chart" --output "$work/inputs"
kind create cluster --name jaeger-hook-rehearsal --image kindest/node:v1.36.1 \
  --kubeconfig "$work/kubeconfig" --wait 5m
python3 "$tools/rehearse.py" --inputs "$work/inputs" --kubeconfig "$work/kubeconfig" \
  --context kind-jaeger-hook-rehearsal --helm "$(command -v helm)" \
  --kubectl "$(command -v kubectl)" --receipt "$work/receipt.json"
kind delete cluster --name jaeger-hook-rehearsal --kubeconfig "$work/kubeconfig"
```

Retain the sanitized receipt and remove the temporary directory and any dedicated runtime afterward. On failure inspect the recorded stage and the local cluster, then use a fresh fixture for a replay. The driver requires the named context, loopback API, a single matching Ready node, an empty release history and unused monitoring hook identities.

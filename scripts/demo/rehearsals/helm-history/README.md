<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Helm history and rollback rehearsal

A fresh, local Kubernetes 1.36.0 API server and etcd 3.6.8 enforced the six-release Secret policy. The rehearsal used Helm 4.3.2. Every release pruned revision 1 after three upgrades with `--history-max 3`. The next upgrade attempted to change an immutable ConfigMap. Kubernetes rejected that change, Helm marked revision 5 failed, then recorded an automatic rollback as deployed revision 6. Each ConfigMap kept its UID and original data. The four retained revisions after rollback were 3, 4, 5 and 6. These fixture revision numbers are independent of production.

| Namespace | Fixture release names |
| --- | --- |
| `default` | `jaeger`, `prometheus` |
| `jaeger` | `jaeger` |
| `opensearch` | `opensearch`, `opensearch-dashboards` |
| `otel-demo` | `otel-demo` |

All six transactions used the restricted fixture identity. Eleven negative tests exercised admission and RBAC. Nine tested real Secret admission denials, including CREATE with a mismatched type, release or revision, UPDATE or DELETE of another application’s Secret, and attempted adoption of an existing record. RBAC denied namespace access outside the four Roles and changes to the admission policy. The fixture writer could read unrelated Secrets in its release namespaces, as expected with Helm Secret storage. [Receipt](six-release-helm-history.json)

## Reproduce

Obtain the `kube-apiserver`, `etcd` and `kubectl` binaries from the official Kubernetes controller-tools envtest release matching your platform. Verify the archive digest published with the release before extracting the binaries into an isolated directory. On the tested Darwin arm64 platform the 1.36.0 archive SHA-256 was `211e620e9f61085ac2e3a176a4f4fc5ebc60d40be1dae9ab5e35895f0c748700`. Install Helm 4.3.2, Python 3 and OpenSSL with support for `req -addext`. [Official envtest release](https://github.com/kubernetes-sigs/controller-tools/releases/tag/envtest-v1.36.0)

For example, to inspect the expected archive SHA-256 recorded by the release publisher, set the operating system and architecture to match the archive you downloaded:

```bash
archive=envtest-v1.36.0-darwin-arm64.tar.gz
gh api repos/kubernetes-sigs/controller-tools/releases/tags/envtest-v1.36.0 |
  jq -er --arg archive "$archive" '.assets[] | select(.name == $archive) | .digest'
shasum -a 256 "$archive"
```

Compare the two digests before extracting. Place the [script](rehearse-six-release-history.py) next to the [policy fixture](helm-history-policy.yaml). Supply the directory containing the three executable binaries.

```bash
python3 rehearse-six-release-history.py \
  --assets /path/to/envtest/bin \
  --receipt ./six-release-helm-history.json
```

The script checks supported binary versions and rejects unrelated receipt outputs. It launches the API server and etcd on ephemeral IPv4 loopback ports and creates a temporary local administrator credential. Every Helm and kubectl operation selects the fixture’s explicit kubeconfig and context. Application operations impersonate the dedicated restricted identity. The policy requires both old and new identities on UPDATE and the stored identity on DELETE. On exit both processes stop and the temporary credentials, etcd database and synthetic release histories are removed. The receipt contains no kubeconfig or Secret values.

## Actual OpenSearch charts

The [storage chart rehearsal](storage-chart-rehearsal.md) extends this isolated API with both pinned charts, reviewed workload changes, content enforcement and a seeded PVC binding. Use the separate `--storage-charts` option to include it.

## Scheduled adapter rehearsal

Render all six application packages with the existing public script inputs.
Place the four packages listed in `docs/all-five-chart-access.json` in one
directory and prepare the pinned Jaeger Git chart checkout, including its
packaged dependencies. Run the following command from the repository root.

```sh
python3 scripts/demo/rehearsals/helm-history/all_five_candidates.py \
  --charts "$CHARTS" --jaeger-checkout "$JAEGER_CHART_CHECKOUT"
```

The fixture compares old tag-only snapshots with new digest references, checks
the exact named resource inventory and compares protected manifests. The six
charts contain 212 Helm transaction objects. The scripts also handle 12
supplemental objects. Monitoring's ten static CRDs are checked separately
against the embedded hook archive by the live adapter.
[Offline six-chart receipt](all-five-candidates.json)

Add `--frozen-transaction` to the private history rehearsal to run the signed
post-renderer with a restricted writer. Add `--monitoring-lookup /path/to/kube-prometheus-stack-93.6.1.tgz`
to seed the chart CRDs and a synthetic Grafana Secret, then exercise its actual
server-side dry-run using a read-only local identity.

The Helm 4.3.2 writer in the loopback fixture upgraded a synthetic existing
release from revision 6 to 7. A tampered render stopped before a revision was
written. An incompatible immutable-field upgrade created failed revision 8,
and the adapter identified revision 7 as the successful recovery target. A
separate reader with no Secret patch access obtained the full 129-object
monitoring upgrade, including 12 hooks and the synthetic Grafana password
from the real Kubernetes Secret. Re-rendering through the transaction guard
preserved all 129 objects and left the monitoring release at revision 6.
The private API had no real controllers, scheduled pods, or cloud volumes.
[Private API outcome](frozen-transaction-receipt.json)

The API fixture also creates all ten ordinary RBAC resources from the real
monitoring chart, then reads them back through Kubernetes storage. The
Grafana Role's empty `rules` array returns as null. The preparer accepts
that representation of zero permissions and compares every populated rule,
binding subject, role reference and aggregation rule against the chart.
An impersonated identity with only named GET and PATCH also reapplies all ten
unchanged RBAC definitions under Helm's field manager. This includes the
nonempty operator ClusterRole and its binding. No ordinary bind or escalate
grant is needed for these no-op updates. Operator-managed changes remain
subject to the separate admission and authorization rules.

The same API fixture exercised both initial Jaeger recovery modes. With automatic rollback disabled, an incompatible upgrade left revision 7 failed. An explicit fixture operator rollback established revision 8. A later incompatible upgrade with automatic rollback enabled recorded failure at 9 and restored the release at 10. Independent adapter tests cover selecting that mode from both stored image references and verifying Ready Pod digests.

Add `--jaeger-patch-methods "$JAEGER_CHART_CHECKOUT"` to install the actual
Jaeger chart four times. The fixture uses the OCI and OTel values, each with
both inherited Helm apply methods. It impersonates the executor and checks
its named PATCH rights with Kubernetes SelfSubjectAccessReviews. It then
attempts each upgrade with Deployment-only and full named permissions,
restoring the prior release between attempts. The API audit records only
request metadata. The receipt lists attempted and denied resource names,
and the paths whose values changed after admission.

In all four upgrades, the only stored fields that changed were the two
workload container images. Comparison covers status and all metadata except
`managedFields`, `resourceVersion` and `generation`. This rehearsal checks
RBAC and actual server-defaulted objects using `--wait=hookOnly`; it has no
Pod controllers, production certificates or external endpoints.
[Two-stack Helm PATCH receipt](jaeger-patch-methods.json)

The same fixture seeds Ready Deployment and ReplicaSet status on both real
chart configurations. With named chart reads and the scheduler's existing
workload access, Helm's watcher was denied namespace-wide collection reads.
The legacy wait passed both ready workloads and timed out after the fixture
withdrew readiness. The separate monitoring fixture checks both actual
Prometheus Operator workload CRs using only named GET, then changes one
status to unavailable. These API rehearsals supply controller status and
leave Pod scheduling, network traffic and the installed operator's live
status for cluster commissioning.

## Six-chart approval rehearsal

Use the same verified public chart packages and pinned Jaeger chart checkout
as the offline six-chart render. Put the locally built Helm 4.3.2 preview
helper on PATH. `TOOLS` is a writable binary directory. `ENVTEST_ASSETS`
contains the local kube-apiserver, etcd and kubectl. Choose a fresh receipt
path. The fixture verifies the four chart archives and the four Jaeger
dependency archives against `ops/executor/chart-archives.json`.

```sh
(cd ops/executor/preview && go build -mod=readonly -o "$TOOLS/jaeger-helm-preview" .)
PATH="$TOOLS:$PATH" python3 scripts/demo/rehearsals/helm-history/rehearse-six-release-history.py \
  --assets "$ENVTEST_ASSETS" --receipt "$RECEIPT" \
  --six-chart-approvals "$CHARTS" "$JAEGER_CHART_CHECKOUT"
```

The fixture installs native policy, all twelve private approval objects and
the named RBAC in a fresh loopback Kubernetes API. It seeds all ordinary
eligible objects from actual chart renders and previews changes with
short-lived scoped credentials. It compares SSA and CSA across the six
releases, verifies that prior drift prevents a new publication, then stages
three representative variants for every eligible identity. An executor
update and rollback on a large Grafana dashboard exercise the native CEL
checks with those parameters present.

The Helm records, private Secret, cluster identity, PVC binding and previous
Ready Pod evidence are synthetic. No controller schedules workloads in this
API fixture. Production commissioning covers its actual cloud volume,
controller status, health endpoints and installed policy. The receiver and
bundle-custody tests separately exercise signing, encryption and decryption.
[Six-chart native receipt](six-chart-approvals-receipt.json)

## Production boundary

All six fixture releases used the same synthetic chart. The frozen production scripts use automatic rollback for the two Jaeger releases and require operator inspection for the four other releases. The local test proves the six release identities can write, prune and roll back Helm history through this envelope. It checks neither the content of encoded Helm payloads nor full production chart operation. Monitoring application Secrets and hooks, real workloads, kube-system Services, the live PVC binding, the OKE identity, public health and the unattended GitHub job still require their own verification. A separately authorized manual OpenSearch backup/restore needs its own access and capacity assessment. Scope a production policy to the actual scheduled writer and include the separately reviewed chart-owned Secret handling. The fixture policy uses an inert test principal.

Add `--verify-live-observation` when running from a checkout with PyYAML and
cryptography installed to exercise the Helmfile observer. The harness grants a
second fixture user read access, then uses the fixture administrator to change
status, configuration and synthetic Helm history. A successful receipt records
the observed changes under `liveObservation`. The read-only observer also checks
a synthetic image digest through real Deployment, ReplicaSet and Pod objects.
[Recorded local result](live-observation.json)

The private API fixture has no
workload controller or public endpoints, so its health callback checks the
selected stack. [Observer command and access scope](../../../../docs/helmfile-bundles.md#acquiring-the-live-baseline)

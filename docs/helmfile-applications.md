<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Helmfile application releases

`helmfile.yaml` describes the six existing application releases. The deploy workflow uses the extracted scripts unless its [Helmfile engine](releases.md#helmfile-engine) is selected, which applies this state through `.helmfile-locked.yaml`. The commands below render public configuration for review.

| Stack and component | Helm release | Prerequisites for the component |
| --- | --- | --- |
| OCI monitoring | `default/prometheus` | None |
| OCI Jaeger | `default/jaeger` | `default/prometheus` supplies the SPM query backend |
| OTel OpenSearch | `opensearch/opensearch`, `opensearch/opensearch-dashboards` | Dashboards follows OpenSearch |
| OTel Jaeger | `jaeger/jaeger` | `opensearch/opensearch` stores traces |
| OTel application | `otel-demo/otel-demo` | `opensearch/opensearch` and `jaeger/jaeger` supply storage and ingestion |

The chart pins match the extracted deployment scripts. Helmfile installs only archives whose SHA-256 equals `scripts/demo/chart-archives.lock.json`: `python3 scripts/demo/chart_archives.py state .tools/chart-archives` derives the ignored `.helmfile-locked.yaml` from `helmfile.yaml`, replacing only each repository `chart:` with the verified local archive and dropping `repositories:`. Use `-f .helmfile-locked.yaml` to render or apply the locked charts; an archive with another digest is refused and deleted. Both Jaeger releases use the Git revision declared in `scripts/demo/jaeger-chart.sh`, plus its chart's locked dependencies. The state reads the existing Jaeger config and UI files with Helm's `--set-file` handling. Both Jaeger releases retain atomic upgrades. OpenSearch and Dashboards retain their non-atomic upgrade behavior and existing namespace and storage settings.

## Render a component

Run the following from the repository root with the pinned tools on `PATH`, a private Helm cache and an empty kubeconfig. Preparing charts requires access to public chart repositories.

```bash
export KUBECONFIG=/dev/null
mkdir -p .tools
source scripts/demo/jaeger-chart.sh
prepare_jaeger_chart "$PWD/.tools/helm-charts"
mkdir -p .tools/chart-archives
python3 scripts/demo/chart_archives.py state .tools/chart-archives
```

Pass the exact image values from the reviewed inputs. The following example illustrates the OTel stack with synthetic tags suitable for rendering.

```yaml
images:
  otel:
    jaeger:
      repository: jaegertracing/jaeger-snapshot
      tag: "0123456"
      pullPolicy: Always
    hotrod:
      repository: jaegertracing/example-hotrod-snapshot
      tag: "abcdef0"
      pullPolicy: Always
```

Save the example as `.tools/images.yaml`. For an OCI or all-stack render, supply the same two image records under `images.oci`. During initial adoption, preserve each live repository, tag and pull policy. A subsequent reviewed promotion can use `tag@sha256:<digest>` in the tag field. All three fields are required for each selected Jaeger or HotROD image.

```bash
python3 scripts/demo/helmfile_selection.py --stack otel --component otel-demo
helmfile --file .helmfile-locked.yaml --state-values-file .tools/images.yaml \
  --selector stack=otel,component=otel-demo --skip-deps template \
  --include-transitive-needs --include-crds --kube-version 1.37.0
```

The first command prints all three release identities in dependency order. Both commands use the same stack and component. Always specify the stack when selecting a component because both Jaeger releases are named `jaeger`. Render OCI and OTel separately when evaluating an all-stack change. The production runner must verify OCI's functional checks before beginning OTel mutations.

## Verify script equivalence

```bash
helmfile -f helmfile.yaml repos
python3 scripts/demo/rehearsals/helmfile-applications/equivalence.py
```

The first command registers the repository aliases that the script-era reference renders use; the Helmfile render still uses only the locked state.

This credential-free check uses the extracted scripts' chart versions and equivalent Helm arguments as its reference. It renders each release using Helm and Helmfile, compares full Kubernetes objects by identity including CRDs and hooks, checks both application's exact image references and pull policies, and compares the OTel application selection with Helmfile's native transitive rendering. A synthetic Grafana password makes its two offline renders deterministic. Production planning must compare against the current named Grafana Secret and preserve its UID and payload. Live equivalence, supplemental resource adoption, and rollout verification are separate operator checks. [Adoption contract](MIGRATION.md)

## Deployer role coverage

`python3 scripts/demo/chart_objects.py --check` renders the Helmfile path from `.helmfile-locked.yaml` and `helmfile-resources.yaml.gotmpl` beside the script path, fails when they differ in an object identity or hook event, renders the companions again as their first `helm install`, and requires an inventory record for every object and Helm history records for each of the ten releases. `scripts/demo/deployer_access.py` adds the release-record access of each Helmfile path command. [Deployer access](deployer-access.md#helmfile-path) lists the commands, and that no rule beyond the script path's is needed.

## Read live fingerprints and images

```bash
python3 scripts/demo/live_observation.py --kubeconfig "$KUBECONFIG" --context "$CONTEXT" \
  --object apps/v1:Deployment:default:jaeger --stack oci --stack otel
```

The command makes only `GET` requests, through a local `kubectl proxy` that rejects every other method. For each `--object` identity (`apiVersion:Kind:namespace:name`, with `-` as the namespace of a cluster-scoped object) it resolves the resource through server discovery and prints the object's UID and a SHA-256 of its content without `status` and transient metadata, or `null` when the object is absent. For each `--stack` it prints the Jaeger and HotROD image reference, source commit and registry digest, after checking that the Deployment is fully rolled out and each of its Pods is ready on that registry digest. On any failure it prints nothing on standard output and only the error type on standard error.

## Live drift report

`scripts/demo/live_drift.py` compares every object the Helmfile render would own with the live object of the same identity. The render is the six application releases of `helmfile.yaml` with `--include-crds` (the monitoring chart's ten CRDs and its twelve hooks included) and the four `demo-resources` companion releases of `helmfile-resources.yaml.gotmpl`: 220 application objects and 12 supplemental objects at the current pins. It renders with the image repository, tag and pull policy that the Jaeger and HotROD Deployments of both stacks run, read with the `--stack` checks above, because adoption must not change them. Every other render input comes from the checkout, so run it from a clean checkout of the commit under review. It refuses a checkout with any untracked file, so write the report outside it. The render pulls the pinned charts from their public repositories, not from the lock-verified archives that `make validate` renders since [#146](https://github.com/jaegertracing/jaeger-demo/pull/146).

Run it in Cloud Shell, first with the administrator kubeconfig. Once `ops/rbac/deployer.yaml` from the same commit is applied ([deployer access](deployer-access.md)), run it again as the deployer identity with that identity's kubeconfig and context. The deployer role reads every rendered identity: the objects in the four release namespaces, the named cluster-scoped objects, and the ten monitoring CRDs by name. Python 3 with PyYAML, `kubectl`, and public network access to the chart repositories and Docker Hub are required. The render runs its helper scripts with a reduced environment that keeps only `PATH` and `HOME`, so `PYTHONPATH` does not reach them: PyYAML must be installed for the interpreter itself. Unset `PYTHONPATH` first, or the virtual environment's `pip` can find another copy there and install nothing. Cloud Shell has no PyYAML, so the commands below install the repository's pin into a private virtual environment and run both scripts with its interpreter. Without it the report fails with only `ValueError` on standard error.

```bash
git clone https://github.com/jaegertracing/jaeger-demo.git && cd jaeger-demo
git checkout "$REVIEWED_COMMIT"
unset PYTHONPATH
python3 -m venv "$HOME/live-drift-venv"
"$HOME/live-drift-venv/bin/pip" install -c scripts/demo/oci-cli-constraints.txt PyYAML
py="$HOME/live-drift-venv/bin/python3"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}" CONTEXT="$(kubectl config current-context)"
"$py" scripts/demo/helmfile_tools.py run -- "$py" scripts/demo/live_drift.py \
  --kubeconfig "$KUBECONFIG" --context "$CONTEXT" --output "$HOME/live-drift-report.json"
echo "exit status $?"
```

`--output` refuses a path inside the checkout; without it the report goes to standard output. The live read makes only named `GET` requests through the read-only proxy of `live_observation.py`; it lists no collection except the Pods of the two Jaeger stacks that the image check follows. A rendered custom resource whose CRD the cluster does not serve is reported as missing without a read, with the scope its rendered CRD declares; ambiguous discovery or a denied read stops the report. The render runs with an empty kubeconfig. The JSON report has a `summary` with the count of each class, `renderInputs` (the checkout revision and a SHA-256 of the image inputs), and one entry per identity:

- `class`: `identical` when the normalized live object equals the normalized render, `explained` when every difference has a reason code below, `unexplained` for any other difference, `missing` when the object is rendered but absent.
- `reasons`, and for an unexplained entry `unexplainedFields`: the field paths that differ, without values. For a Secret or ConfigMap a path under `data`, `binaryData` or `stringData` stops there, and a map key that only the live object has is printed as `<live-only>`. In a CRD, every live-only key inside `spec.versions[*].schema` counts as a map key, including property names and keys of object defaults.
- `uidSHA256` (a SHA-256 of the UID), `renderSHA256` (of the normalized render), `liveSHA256` (the `live_observation.py --object` hash of the live object).
- For a Service, `serviceAllocationSHA256`: a SHA-256 over the UID, `clusterIP` and `clusterIPs`. Comparing it between the reports taken before and after adoption shows that adoption kept each Service and its addresses without publishing them.

The report never contains a field value, Secret or ConfigMap data, a live-only map key, a raw UID or an API error body. It does reveal one fact about a Secret: an entry without a `data` difference shows that the live data equals the rendered data. Exit status 0 means nothing is unexplained or missing, 1 means something is, and 2 means the report could not be produced; then nothing is printed on standard output and only the error type on standard error.

Normalization drops `status`, the server-managed metadata (`uid`, `resourceVersion`, `generation`, `creationTimestamp`, `managedFields`) and null values, sets each namespaced object's namespace to its release namespace, drops the namespace of a cluster-scoped object, and stores a Secret's `stringData` as base64 `data`. For a built-in kind and for object metadata, a rendered empty map or list equals an absent live field, and a rendered `false`, `0` or `""` does so only in a fixed list of non-pointer fields that the API server omits at their zero value, such as `hostNetwork` or a volume mount's `readOnly`. An entry of a map such as `data`, `labels`, `annotations` or `nodeSelector` is never omitted. A custom resource keeps empty values, which can be meaningful there. A kind is built in when its API group is core, has no dot, or ends in `.k8s.io`. Only these reason codes explain a difference:

| Code | Explains |
| --- | --- |
| `server-default` | A Kubernetes default, with exactly the default value, for a field the render leaves unset, from a fixed list per kind: workload, pod template, container, probe, volume, Service, Secret, binding subject and webhook defaults, the deprecated `serviceAccount` mirror of `serviceAccountName`, a Service's `clusterIPs` mirror of a rendered `clusterIP`, and the CRD conversion strategy `None`. A rendered zero counts as unset only for a non-pointer field the API server defaults when it is zero, such as a Service `targetPort: 0` or a probe's `timeoutSeconds: 0`; an explicit `replicas: 0` or `terminationGracePeriodSeconds: 0` must match |
| `server-quantity` | A resource quantity in a built-in quantity field (container and pod resources, `emptyDir.sizeLimit`, claim template and claim resources) in the API server's canonical form, equal in value to the rendered one, such as `1000m` read back as `1` |
| `crd-schema-default` | A custom resource field the render leaves unset, with the default declared at that path in the rendered CRD schema, under a parent that the render sets or that is itself defaulted |
| `helm-ownership` | The complete Helm ownership metadata (`app.kubernetes.io/managed-by`, `meta.helm.sh/release-name`, `meta.helm.sh/release-namespace`) of the expected release: the application release, or `demo-resources` for an allowlisted companion |
| `companion-unadopted` | An allowlisted companion with no Helm ownership metadata and no `helm.sh/resource-policy`, which the [ownership patch](helmfile-companions.md) adds at adoption |
| `kubectl-last-applied` | The `kubectl.kubernetes.io/last-applied-configuration` annotation on an allowlisted companion, which the deploy scripts create with `kubectl apply` |
| `service-allocation` | A Service's allocated `spec.clusterIP`, `spec.clusterIPs`, `spec.ipFamilies` or `spec.ipFamilyPolicy` that the render leaves unset. A rendered value, such as `clusterIP: None` on a headless Service, must match exactly, and a live `None` is never an allocation |
| `webhook-ca-bundle` | `caBundle` in the two `prometheus-kube-prometheus-admission` webhook configurations, which the admission patch hook writes |
| `deployment-revision` | The `deployment.kubernetes.io/revision` annotation that the Deployment controller writes |
| `daemonset-generation` | The `deprecated.daemonset.template.generation` annotation that the DaemonSet controller writes |
| `prometheus-operator-validated` | The `prometheus-operator-validated: "true"` annotation on a PrometheusRule, which the monitoring chart's `prometheusrulemutate.monitoring.coreos.com` admission webhook adds to every rule it admits. Any other value, or the annotation on another kind, is unexplained |
| `rollout-restart` | The `kubectl.kubernetes.io/restartedAt` pod template annotation on the two `trace-generator` Deployments, which the deploy scripts restart, and on `jaeger/jaeger`, which `opensearch_reset.py` restarts |
| `grafana-admin-password` | The `admin-password` of `default/prometheus-grafana`, which the chart keeps from the live Secret by `lookup` while the offline render uses the public fixture password, and the Grafana pod template's `checksum/secret` when it equals the chart's checksum of the live password. It applies only when the checkout's values for `default/prometheus` leave the password to that lookup: no `adminPassword`, `admin.existingSecret`, admin password environment variable, `set`, or templated values file. Only then does the render use the fixture password; otherwise it renders the checkout's own values |
| `hook-deleted` | A rendered Helm hook with the `hook-succeeded` delete policy that is absent, as Helm leaves it after a successful run |

Anything else is unexplained, including foreign, partial or missing Helm ownership, an unknown field, a selector, a finalizer or owner reference, a terminating object, and any ConfigMap or Secret content. A new reason needs a reviewed change to `REASONS` and its tests.

`scripts/demo/live_drift_test.py` runs the comparison without credentials against the sanitized fixture in `scripts/demo/testdata/live-drift`: `rendered.yaml` holds rendered objects by release, `live.yaml` holds live objects by identity (`null` for a 404) and `expected.yaml` holds each identity's class, reasons and unexplained fields. Every UID, address and secret in it is fake.

## Public health checks

Run `python3 scripts/demo/stack_health.py --stack oci` or `--stack otel` to check the Jaeger UI and its configured analytics ID, the stack's HTTPS routes, current frontend or checkout traces, and finite p95 latency in a completed SPM bucket with positive calls. Calls and latency use the same UTC end time and must have matching sample timestamps. The OCI check also inspects Grafana database health. The OTel check verifies Dashboards status and the storefront. The output contains service names, freshness and aggregate metrics. A controlled write/read and cluster rollout checks are additional release gates.

Each check also completes one HotROD dispatch itself, which must name a driver: `https://demo.jaegertracing.io/hotrod/dispatch?customer=123` on the OCI stack and `https://hotrod.demo.jaegertracing.io/dispatch?customer=123` on the OTel stack. Everything else it reads is existing traffic, and on the OCI stack that traffic cannot show that a dispatch works: the kubelet's probes of `/hotrod` are `frontend` server spans, so the span and the bucket stay healthy while dispatches hang, and the HotROD page is the probes' own path. The OTel stack is judged by `checkout`, which says nothing about its HotROD. The output's `functionalRequestCompleted` is `true` when the dispatch completed ([#171](https://github.com/jaegertracing/jaeger-demo/issues/171)).

After a change, add `--since <time>` with the UTC time of the change, for example `--since 2026-11-25T16:37:00Z`. The service must then have a server span without an error status that started after that time, and the SPM bucket must be one whose whole rate window began after it, so a bucket from before the change, a client span of a request that hangs, and a request that failed cannot pass ([#160](https://github.com/jaegertracing/jaeger-demo/issues/160)). The check asks for two-minute rates in place of the routine ten-minute ones, and the first bucket that can qualify is complete three to four minutes after the change; until then the check fails with `TrafficPending`. The Helmfile engine passes the start of each stack's checks and writes it to the deployment record as `evidenceSince` before they run. The output's `evidenceSinceUTC` is that time, or `null` for a routine check.

## Pull request render summary

The **Rendered resource changes** pull request job renders the exact base and head
commits using the head’s pinned toolchain and the public image and Grafana
fixtures from the equivalence rehearsal. Each checkout supplies its own Jaeger
chart pin, chart versions, values, templates and supplemental objects. For the
initial migration, the base projection uses the six script-era release arguments
and the twelve supplemental resources in their existing namespaces. The summary
identifies that fallback.

The head is rendered from the chart archives that `scripts/demo/chart_archives.py
state` verified against `scripts/demo/chart-archives.lock.json`, and the job fails
when they do not verify. A base with a Helmfile is verified and rendered the same
way, and the job fails when it does not verify. Only a script-era base keeps the
publisher charts, and the summary marks its charts as not verified. A matching
digest shows the archive matches the lock in that checkout. It does not approve a
pull request that changes the lock or the verifying and comparing scripts,
`scripts/demo/chart_archives.py` and `scripts/demo/pr_render_diff.py`, so review
those changes.

The GitHub Actions job summary lists added, modified and removed resource
identities. Comparison includes Secret contents in process memory. The job
publishes only counts and resource identities. Its children have an isolated
home and Helm configuration, an empty kubeconfig, no cloud credentials and no
access to a GitHub write token. Both checkouts disable credential persistence.

To reproduce, supply separate clean checkouts for the old and new commits. Run in
the candidate checkout with PyYAML installed. The pinned chart repositories need
public network access.

```sh
python3 scripts/demo/helmfile_tools.py run -- python3 scripts/demo/pr_render_diff.py \
  --base /path/to/base --head "$PWD"
```

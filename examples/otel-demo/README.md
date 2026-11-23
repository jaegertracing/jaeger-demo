# OpenTelemetry Demo app + HotRODapp + Jaeger + OpenSearch

> Hosted migration: follow the [handover](../../docs/cutover.md) and
> [release](../../docs/releases.md) runbooks for the existing installation. The
> setup examples below are retained for reference. Do not use `clean` for handover
> or reinstall the shared ingress controller, cert-manager or certificate issuers.

This example provides a one-command deployment of a complete observability stack on Kubernetes:
- Jaeger (all-in-one) for tracing
- OpenSearch and OpenSearch Dashboards
- OpenTelemetry Demo application (multi-service web store)
- HotRod application

It is driven by `deploy-all.sh`, which supports both clean installs and upgrades.

## Prerequisites
- Kubernetes cluster reachable via `kubectl`
- Install `bash`, `git`, `curl`, `kubectl`, `helm`, `python3` and `jq`. The deployment script uses Python for recovery policy selection and to verify each chart archive against `scripts/demo/chart-archives.lock.json` before the first cluster change, and the manual recovery and structured diagnostics use jq.
- Network access to Helm repositories

## Quick start
- Upgrade (default). Without a scope it deploys the `jaeger` scope only (Jaeger
  and HotROD) and expects the OpenSearch service to exist already. It does not
  install OpenSearch or the OpenTelemetry Demo, but it does reapply the Ingress
  objects in the `opensearch` and `otel-demo` namespaces along with Jaeger's:
```bash path=null start=null
./deploy-all.sh
# or explicitly
./deploy-all.sh upgrade
```
- Specify Jaeger all-in-one image tag:
```bash path=null start=null
./deploy-all.sh upgrade <image-tag>
# Example
./deploy-all.sh upgrade latest
```
- Full-stack upgrade of an existing installation (OpenSearch, Jaeger and the
  OpenTelemetry Demo). Direct script use applies the `required` recovery policy,
  so the OpenSearch step first verifies a recent tested recovery point. See
  [Manual recovery option](#manual-recovery-option). This is not fresh-cluster
  setup:
```bash path=null start=null
JAEGER_DEMO_DEPLOY_SCOPE=all ./deploy-all.sh upgrade
```
- Clean install (removes previous releases/namespaces, then installs everything):

> **Warning: `clean` is destructive. Do not run it against the hosted demo cluster.** The deploy workflow does not offer it, and its identities cannot create or delete namespaces; only an administrator runs it from a shell ([deployer access](../../docs/deployer-access.md)).
> `./deploy-all.sh clean` uninstalls the `opensearch`, `opensearch-dashboards`, `jaeger` and `otel-demo` Helm releases, deletes all ingress resources in the `jaeger`, `opensearch` and `otel-demo` namespaces, then deletes those three namespaces. Deleting the `opensearch` namespace removes the OpenSearch data PersistentVolumeClaim. It also removes any VolumeSnapshot and restored recovery PersistentVolumeClaim in that namespace. VolumeSnapshotContent objects with `deletionPolicy: Retain`, and the OCI backups behind them, survive, while content with `Delete` is removed together with its backup; see [OpenSearch recovery](./OPENSEARCH_RECOVERY.md#restore-after-namespace-or-workload-replacement). The script takes no backup first, and whether the underlying volume survives depends on the reclaim policy of the bound PersistentVolume. A destructive reset of the hosted installation requires explicit maintainer approval. See [Operations and releases](../../README.md#operations-and-releases) and [Failure handling](../../docs/releases.md#failure-handling).

```bash path=null start=null
./deploy-all.sh clean
```

Environment variables:
- ROLLOUT_TIMEOUT: rollout wait timeout in seconds (default 600)
- OPENSEARCH_DASHBOARDS_HELM_TIMEOUT: Helm wait timeout for OpenSearch Dashboards (default 60m)

```bash path=null start=null
ROLLOUT_TIMEOUT=900 ./deploy-all.sh upgrade
```

## What gets deployed
- Namespace `opensearch`:
  - OpenSearch (single node) StatefulSet
  - OpenSearch Dashboards Deployment. OpenSearch runs without authentication and
    the hosted Dashboards route is public, so the Dev Tools console and Index
    Management are disabled. Other bundled plugins can still write to the
    cluster; do not treat the hosted Dashboards as read-only.
    Workspaces and Explore (the new Discover, with PPL) are enabled. A
    `workspace-bootstrap` sidecar in the Dashboards pod checks every minute
    (every 15 seconds while waiting) that the Observability workspace and its
    `jaeger-main-jaeger-span-*` index pattern exist and recreates them, because
    any visitor can delete them and a fresh install has no span index until
    Jaeger runs. It does not affect the Dashboards container's readiness. The
    `defaultWorkspace` setting is fixed to Observability, so `/` lands there
    even after a visitor creates another workspace. Read
    its state with
    `kubectl -n opensearch logs deploy/opensearch-dashboards -c workspace-bootstrap`.
    The multiple data source feature stays disabled: it would let visitors
    make the Dashboards pod connect to in-cluster addresses.
- Namespace `jaeger`:
  - Jaeger all-in-one Deployment with OpenSearch-backed trace storage
  - HOTROD application
  - Jaeger Query ClusterIP service (jaeger-query-clusterip)
- Namespace `otel-demo`:
  - OpenTelemetry Demo (frontend, load-generator, and supporting services).
    The load generator runs headless and the flagd-ui sidecar is removed: the
    public `/loadgen/` and `/feature` routes have no backend, and the feature
    flags stay at their reviewed `demo.flagd.json` content.
    The shopping assistant's `agent` service runs for the load generator only.
    It answers from the chart's recorded responses, holds no API key, and its
    model endpoint is the unresolvable `https://llm.invalid`, so a prompt with
    no recorded match fails instead of leaving the cluster. The assistant's MCP
    server and public chat UI are not deployed. Pointing `LLM_BASE_URL` in
    `otel-demo-values.yaml` at a real provider activates a live model and needs
    its own decision.


## Verifying the deployment
- Pods status:
```bash path=null start=null
kubectl get pods -n opensearch
kubectl get pods -n jaeger
kubectl get pods -n otel-demo
```
- Services:
```bash path=null start=null
kubectl get svc -n opensearch
kubectl get svc -n jaeger
kubectl get svc -n otel-demo
```
- Dashboards console and Index Management are disabled. After a Dashboards
  rollout, the hosted Dashboards must load, its menu must show neither Dev
  Tools nor Index Management, and this read-only console probe must return
  `404`:
```bash path=null start=null
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'osd-xsrf: true' \
  'https://opensearch.demo.jaegertracing.io/api/console/proxy?path=/&method=GET'
```
- The Observability workspace exists, and the hosted Dashboards root redirects
  into it. The first read-only check below must print `true` and the second
  must end in `/w/observability/app/workspace_navigation`. Then open **Logs** in the
  workspace: `jaeger-main-jaeger-span-*` must be selected and show recent spans.
```bash path=null start=null
curl -s 'https://opensearch.demo.jaegertracing.io/api/workspaces/observability' | jq -e .success
curl -s -o /dev/null -w '%{redirect_url}\n' 'https://opensearch.demo.jaegertracing.io/'
```
- The load generator web UI and the feature-flag UI are disabled. After an
  OTel demo rollout, both control routes must return `503` while the shop
  itself keeps loading:
```bash path=null start=null
curl -s -o /dev/null -w '%{http_code}\n' 'https://shop.demo.jaegertracing.io/loadgen/'
curl -s -o /dev/null -w '%{http_code}\n' 'https://shop.demo.jaegertracing.io/feature'
```


## Automatic port-forward using scrpit
 - OpenSearch Dashboards:
```bash path=null start=null
./start-port-forward.sh
```


## Customization
- Helm values provided in this directory:
  - `opensearch-values.yaml`
  - `opensearch-dashboard-values.yaml`
  - `jaeger-values.yaml`
  - `jaeger-config.yaml`
  - `otel-demo-values.yaml`
  - `jaeger-query-service.yaml`

You can adjust these files and re-run `./deploy-all.sh upgrade` to apply changes.

### UI analytics

Both public Jaeger demos use the shared [UI configuration](../oci/ui-config.json)
with GA4 measurement ID `G-S88L6V684T` and JavaScript error tracking enabled.
`deploy-all.sh` passes this file to Helm as `uiconfig`; Jaeger Query reads it
from `/etc/jaeger/ui-config/ui-config.json`.

The measurement ID belongs to **CNCF Projects → jaegertracing.io - GA4**, which
also collects the main website's traffic. To separate the reports while retaining
all collected data, use these saved comparisons under **Admin → Data display →
Comparisons**. Set the dimension to **Hostname**, the match type to **exactly
matches**, and select the hostnames as alternative values in the same condition:

| Comparison | Hostnames |
| --- | --- |
| Jaeger Website | `jaegertracing.io`, `www.jaegertracing.io` |
| Jaeger Demos | `demo.jaegertracing.io`, `jaeger.demo.jaegertracing.io` |

Apply the comparison from the comparison picker at the top of a report.
**All Users** continues to include website and demo traffic. Add **Hostname**
as a report dimension to distinguish the two demos, including pages with the same
path. These comparisons apply to existing data as well as new events.
See [Google's comparison documentation](https://support.google.com/analytics/answer/9269518?hl=en).

After deployment, check that each demo page's HTML contains the measurement ID.
In a browser with analytics requests allowed, verify that the page loads
`www.googletagmanager.com/gtag/js?id=G-S88L6V684T` and sends Google Analytics
collection requests with the same ID and the demo's page URL. Confirm receipt
in GA4 Realtime, then verify the Hostname breakdown after the data is processed.
A hostname may only become selectable for a saved comparison after GA4 has
received events for it.

### Scheduled OpenSearch updates

The 13:00 UTC hosted job treats generated demo traces as disposable. When
OpenSearch inputs change, it upgrades the existing OpenSearch StatefulSet and
Dashboards Deployment in that order. It checks the live source PVC binding
before and after the operation, waits for readiness, verifies the running
database and public Dashboards versions, and sends a fresh trace through Jaeger.
The job creates no snapshot, restore PVC or restore Pod. Routine upgrades
preserve storage. If an incompatible database change makes historical traces
unusable, recovery of those traces is not guaranteed. Pause further updates
and get approval before resetting or replacing a volume.

### Manual recovery option

A manual OpenSearch deployment defaults to `opensearch_recovery=required`. The
reconciler runs a retained OCI CSI backup and an isolated restore test against
the live OpenSearch and Jaeger image digests, then checks that the tested
recovery point matches the live engine before upgrading. The standalone
`Verify OTel Demo OpenSearch Recovery` workflow can also prepare a recovery
point. It uses independently authorized storage access and follows these steps:

1. verifies that the live volume uses the OCI Block Volume CSI driver;
2. flushes OpenSearch and creates a retained `VolumeSnapshot`;
3. restores it to a new PVC;
4. boots the exact live OpenSearch and Jaeger image digests without a Service
   or ingress. The OpenSearch image entrypoint reads `DISABLE_SECURITY_PLUGIN`
   and supplies the setting for the loopback-only database.
5. validates cluster recovery, every pre-snapshot Jaeger index, template, and
   alias mapping, document floors, and representative service and trace queries
   before marking the snapshot tested.

Ingestion remains active, so the flushed provider backup is crash-consistent,
not application-consistent. Always run the isolated restore test when a manual operation requires a recovery point.
The restore pod requests the target OpenSearch memory sizing and must run on a
different worker from the live OpenSearch pod, so it also proves that current
cluster capacity can host the recovery test without colocating both databases.

The snapshot and restored PVC are retained deliberately. The workflow removes
only the exact run-specific disposable restore pod after the test, including on
failure, so it does not expose restored data or reserve capacity needed by the
live rollout. Removing either retained storage object is a separate operational
action. Never use the demo's `clean` mode as a database rollback. Restore the
retained volume into a fresh installation of the prior OpenSearch version
instead. See the
[OpenSearch recovery procedure](./OPENSEARCH_RECOVERY.md) for both intact- and
replaced-namespace recovery.

During a manual dispatch from `main`, a maintainer can explicitly select
`opensearch_recovery=waive` for `demo_stack=otel` and
`otel_deploy_scope=opensearch`; the workflow always runs `upgrade`. Direct script use defaults to required recovery.
The hosted daily job derives its own disposable-trace policy. A failure of a
requested manual restore test blocks that protected upgrade.

Check the existing database workload's memory and disk headroom before either
upgrade path.

### OpenSearch volume expansion

The manual `Expand OTel Demo OpenSearch Volume` workflow expands only the
existing OpenSearch PVC to 100Gi. It verifies the expected StatefulSet, pod,
PVC, StorageClass, and OCI CSI driver, then uses a preconditioned JSON Patch and
waits for both Kubernetes and OpenSearch to report the expanded filesystem. It
does not restart or replace the workload and is idempotent after a successful
expansion.

The live PVC intentionally remains larger than the 10Gi StatefulSet claim
template after this operation. Kubernetes does not permit updating that
template in place, and changing it during the OpenSearch Helm upgrade would
fail on an immutable StatefulSet field. Reconcile the template separately only
after the staged version upgrade and soak. PVC expansion cannot be rolled back
or shrunk.

### Index retention

Jaeger writes one OpenSearch index per day, and OpenSearch has no TTL. The
`esIndexCleaner` block in `jaeger-values.yaml` enables the Jaeger chart's
CronJob, which runs `jaeger-es-index-cleaner` daily and deletes
`jaeger-main-jaeger-{span,service,dependencies,sampling}-<date>` indices older
than 30 days. The `jaeger-archive-*` indices are not touched. The cleaner's
`index-prefix` must be `jaeger-main`, the same `index_prefix` in
`jaeger-config.yaml`. The pinned 2.23.0 cleaner appends a dash and then
matches `<prefix>jaeger-{span,service,dependencies,sampling}-<date>`.
`jaeger-values.test.sh` checks that the two stay in sync.

Without retention the data volume eventually passes the OpenSearch flood-stage
watermark (95% disk), which makes the indices read-only and stops trace
ingestion while every pod still reports `Running`. To size retention, keep
`numberOfDays` times the daily index size well under the volume.

The Jaeger deployment uses `jaeger-config.yaml` to configure the Jaeger v2
`jaeger_storage` extension against the in-cluster OpenSearch service. The Helm
chart storage type is set to `elasticsearch` for compatibility with the chart's
persistent-storage wiring, while the Jaeger runtime config points at OpenSearch.

## Clean-up

> These teardown examples delete workloads and namespaced recovery objects.
> Use them only for an explicitly approved teardown. If the operator needs a
> retained backup, verify its content and private recovery record beforehand.

- Clean uninstall using cleanup.sh :
```bash path=null start=null
./cleanup.sh
```
- Manual teardown:
```bash path=null start=null
helm uninstall opensearch -n opensearch || true
helm uninstall opensearch-dashboards -n opensearch || true
helm uninstall jaeger -n jaeger || true
helm uninstall otel-demo -n otel-demo || true
kubectl delete namespace opensearch jaeger otel-demo --ignore-not-found=true
```

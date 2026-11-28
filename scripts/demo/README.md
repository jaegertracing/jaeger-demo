<!--
Copyright (c) 2026 The Jaeger Authors.
SPDX-License-Identifier: Apache-2.0
-->

# Hosted demo maintenance

Renovate checks the two hosted demo directories daily. Releases must be at least seven days old, and maintainers review
every update. OpenSearch and Dashboards remain a matched chart pair. Other updates
are grouped by deployment component. Charts own their bundled image versions;
direct image overrides are tracked separately. The shared trace generator is
standard-library Python and has no tracked requirements.
OpenSearch stays within its configured major line until a reviewed migration or
compatibility change updates that constraint. The OpenTelemetry Demo chart owns
its PostgreSQL and Valkey images.

Manual deployment runs use the reviewed `demo-apply` environment. The 13:00 UTC
run of the same `deploy` job on main uses `demo-scheduled`, which has no
reviewer, and runs only while the repository variable `DEMO_SCHEDULE_ENABLED` is
`true`. Activation follows the
[handover runbook](../../docs/cutover.md#review-scheduled-scope). A merged change
takes effect at the next scheduled run.

The Kubernetes access both environments' identities need is defined in
`ops/rbac/deployer.yaml` and explained in
[deployer access](../../docs/deployer-access.md). `deployer_access.py bindings`
prints the bindings for an administrator to apply, and `deployer_access.py check`
compares an identity's live access with the committed role. Its tests fail when a
chart or script needs access the role lacks, and when a rule has no chart object
or command behind it. Every writer workflow runs
`deployer_access.py check --self` with its own credential before any cluster
change, so a credential that lacks part of the role stops the run before it
changes anything. Those workflows set the OCI API key only on the steps that
configure cluster access or call the cluster, and install the OCI CLI with
required hashes before the first of them
([where the workflows use the API key](../../docs/deployer-access.md#where-the-workflows-use-the-api-key)).

`deploy.py` fingerprints each component's deployment inputs and records successful
revisions in the `default/jaeger-demo-deployment-revisions` ConfigMap, patching
only that component's key; a first manual install creates the ConfigMap. Failed
revisions are not recorded, so the next reconciliation retries them. The daily
run also refreshes Jaeger and HotROD snapshots in both demos. A missing component key selects that component. Scheduled runs require the
existing revision ConfigMap and existing Helm releases, upgrade in place only,
and refuse a release in a `pending-*` state. A partial failure does
not prevent independent components from deploying, but OpenSearch failure blocks
the dependent OTel Jaeger and application updates in that run. Before any write,
on every event, `deploy.py` lists the releases of each of the four release
namespaces and refuses to run while one holds a `demo-resources` companion
release, whose objects the Helmfile engine owns; the error names the namespace
([the engine switch](../../docs/releases.md#the-engine-switch)).

`helmfile_deploy.py` is the Helmfile engine of the same `deploy` job, off by
default. A scheduled run uses it only while the repository variable
`DEMO_DEPLOY_ENGINE` is `helmfile`, and a manual run only with `engine=helmfile`
and `expected_config_sha`. `prepare` builds the lock-verified
`.helmfile-locked.yaml`, `images` resolves and validates digest-mode images before
any step holds the OCI API key, `diff` reports changes read-only, and `reconcile`
applies all ten releases, OCI before OTel, through `release_phases.py`, verifies
both stacks even when nothing changed, patches the fingerprints above for each
verified stack and writes `deploy-record.json`. Only an apply in `digest` image mode
restarts the trace generators, before each stack's checks; a `live` apply and a
companion install change no pod template. A companion install first requires every
allowlisted object of its namespace to exist with the reviewed ownership and to show
no other difference under `live_drift.py`'s classification, and a `live` apply first
requires an empty read-only `helmfile diff` for every release. After an apply each
Jaeger and HotROD workload must run the expected image.
`helmfile/images.yaml` selects `live` image references or `digest` promotion. The
`operations` CI job runs it on kind as the deployer
([Helmfile engine](../../docs/releases.md#helmfile-engine)).

Deployment, the trace reset and volume expansion share the workflow concurrency
group `jaeger-otel-demo-opensearch-recovery`, so one runs at a time and a running
one is never cancelled. GitHub keeps one waiting run per group: a newer waiting
run cancels an older waiting one, which is accepted
([operator action](../../docs/releases.md#a-waiting-run-replaced-by-a-newer-one)).
When the daily job selects OpenSearch, it
upgrades the live StatefulSet on its existing PVC, waits for readiness, and then
upgrades Dashboards. The job
checks the database and public Dashboards versions against the application
version of each deployed chart release and sends a fresh trace
through the collector and query service before recording success. It reads the
public Dashboards status up to 30 times, two seconds apart, while the route
answers 502, 503 or 504 or the connection fails, and then fails with the last
status. Historical
synthetic traces are disposable. A destructive reset needs explicit approval.

A manual OpenSearch upgrade takes the same path. No workflow or script takes or
restores a backup, and the `opensearch_recovery` input and the recovery policy
are removed ([#76](https://github.com/jaegertracing/jaeger-demo/issues/76)).
[OpenSearch storage maintenance](../../docs/releases.md#opensearch-storage-maintenance)
covers volume expansion and what the removed tooling may have left in the cluster.

`opensearch_reset.py` is the manual
[trace reset](../../docs/releases.md#reset-the-trace-data), run by
**Reset OTel Demo OpenSearch Trace Data** after a `demo-apply` reviewer approves
it. `list` prints what a reset would delete. `reset` needs the exact acknowledgment
phrase, deletes only the daily `jaeger-main-jaeger-*` trace indices through the
OpenSearch API, restarts Jaeger and verifies a fresh trace. It takes no name as an
argument and deletes no Kubernetes object.

The manual `demo_stack`, `otel_deploy_scope` and image override
inputs are preserved. The `deploy_mode` input is removed: the workflow always
runs `upgrade`, and `deploy.py` refuses `clean` inside GitHub Actions. The
historical `otel_deploy_scope` input also accepts `monitoring` for an isolated OCI monitoring upgrade. Invalid stack/scope
pairs fail. Direct script callers can select a scope through
`JAEGER_DEMO_DEPLOY_SCOPE`; the legacy OTel environment variable remains accepted.
Before the first cluster change, both scripts verify every chart they install
against [`scripts/demo/chart-archives.lock.json`](chart-archives.lock.json)
with `chart_archives.py`. A chart version without a locked digest, including a
`PROMETHEUS_STACK_CHART_VERSION` override, fails.

Chart upgrades are separate reviewed changes; do not bundle them with the initial
repository ownership handover. Keep component updates separate. Major database
changes require a tested migration in their reviewed change. Existing missing
OCI monitoring releases and pending Helm operations must be repaired under
[core #10002](https://github.com/jaegertracing/jaeger/issues/10002); an upgrade reports that failure instead of skipping monitoring and
reporting success. A `failed` monitoring release is upgraded in place by the
next reconciliation or `monitoring`-scope run.

Run `make lint` and `SHUNIT2=/path/to/shunit2 make test`; the latter runs the
maintenance tests through `scripts/utils/run-tests.sh`. To inspect discovery
without updating dependencies, run `make renovate-discovery`, which validates
the configuration and checks Renovate's credential-free extraction. Kubernetes
and OCI infrastructure/add-on maintenance is outside this workflow.

## Scheduled smoke check

`.github/workflows/demo-smoke.yml` checks the public demos every 30 minutes and
on `workflow_dispatch`, without cloud credentials. For the `oci` and `otel` stacks,
`scripts/demo/stack_health.py --stack <name>` verifies the public routes, the
Jaeger UI tracking configuration, recent application spans and finite span
metrics, and completes one HotROD dispatch on each stack. The spans and metrics
alone would pass on the OCI stack while dispatches hang, because the kubelet's
probes of HotROD are `frontend` spans too. The smoke check is a routine check of
current traffic. The deployment
tooling binds the same script to a change with `--since`
([public health checks](../../docs/helmfile-applications.md#public-health-checks)).

Retries and alerts:

- Each stack check runs up to three times, 20 seconds apart, and each attempt is
  killed after 150 seconds on the OCI stack and 165 on the OTel stack, whose run
  makes one request more. The step fails only when all three attempts fail, so
  one slow probe does not fail a run. The job limit is 22 minutes, above the
  worst case of about 20 minutes, of which the checks take about 17. The retry
  lives in the workflow, not in `stack_health.py`.
- A final step runs `scripts/demo/smoke_alert.py` with this run's result, the
  event and the conclusions of the last 10 completed scheduled runs. A failure
  posts to Slack through `SLACK_WEBHOOK_URL` only when the previous scheduled
  run with a `success` or `failure` conclusion did not fail. A success posts a
  green recovery message only when that run failed. A four-hour outage posts one
  failure and one recovery. Earlier `cancelled`, `skipped` and other
  inconclusive runs are skipped when looking back.
- If the history cannot be read or holds no usable result, a failure still
  posts and a success stays silent. A run that was rerun, or whose last
  attempt was cancelled, counts by its latest `success` or `failure` attempt,
  read with `gh api .../attempts/N`, and results are ordered by when they were
  observed. A failed run fixed by a rerun posts one recovery, and a rerun of an
  old run cannot override a newer result. If one of those
  lookups fails, the history counts as unreadable, so a failure still posts. The run
  list lookup is capped at 30 seconds and the helper at 60, inside the job limit.
- A manual dispatch always posts a failure and never posts a recovery. A
  cancelled manual run posts a failure. A cancelled scheduled run is silent,
  because the next run checks again within 30 minutes.
- The job has `actions: read` and `contents: read`, and reads history with the
  workflow token.

Scheduled runs are off until the repository variable `DEMO_SMOKE_ENABLED` is
`true`; manual dispatch always works. `SLACK_WEBHOOK_URL` is a repository-level
secret only; no deploy environment holds a copy, and this job has no
environment. Before enabling the schedule, run the workflow once by hand and
confirm that it passes and that the "Decide whether to notify" step shows no
"Could not read earlier runs" warning, which proves the history lookup and the
token scope:

```bash
gh workflow run demo-smoke.yml --repo jaegertracing/jaeger-demo
```

Then enable the schedule:

```bash
gh variable set DEMO_SMOKE_ENABLED --body true --repo jaegertracing/jaeger-demo
```

Known limits:

- The tracking check compares the live Jaeger UI with
  `examples/oci/ui-config.json` on `main`. After a change to its `tracking`
  block merges and until it is deployed, every run fails. Set
  `DEMO_SMOKE_ENABLED` to `false` when merging such a change and restore it
  after deployment.
- The Slack steps continue on error, so a failed post never changes a run's
  result. A failing run whose post fails is therefore not announced again,
  because the next failing run sees a failing predecessor.
- A run that is cancelled or fails before its job starts cannot post from its
  own job. `.github/workflows/demo-run-observer.yml` reports it, for manual and
  scheduled runs alike. A scheduled run that is cancelled after its job started
  stays silent, as described above.
- GitHub disables scheduled workflows in public repositories after 60 days
  without repository activity; re-enable the workflow if that happens.

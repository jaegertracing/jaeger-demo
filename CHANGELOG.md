<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

Notable changes to the hosted-demo configuration, deployment tooling and
operations. This repository has no product versions, so entries are grouped by
the UTC date the pull request merged to `main`, newest first. Within a date,
entries use the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
categories: Added, Changed, Fixed, Removed and Security.

A merge is not a production deployment. Deployment evidence and acceptance
status live in [docs/MIGRATION.md](docs/MIGRATION.md) and the
[release records](docs/releases.md). **Executor image** marks entries up to
2026-11-23 whose merge changed a file the in-cluster executor guarded. New entries
do not carry it: the daily update runs `scripts/demo/deploy.py` directly
([#95](https://github.com/jaegertracing/jaeger-demo/issues/95)).

Jaeger and HotROD product changes are recorded in the
[core changelog](https://github.com/jaegertracing/jaeger/blob/main/CHANGELOG.md).
Earlier hosted-demo history is in core up to the extraction cutoff below.

## 2026-11-27

### Changed

- The two hosted HotROD trace generators are one parameterized standard-library
  script, `examples/trace-generator/generate_traces.py`, so the pods no longer
  install `requests` at startup. Each stack keeps its target URL and request
  interval through its Deployment's `TARGET_URL` and `SLEEP_SECONDS`: the OCI
  stack dispatches to `jaeger-hotrod.default` every 10s, the OTel stack to
  `jaeger-hotrod.jaeger` every 5s
  (refs [#2](https://github.com/jaegertracing/jaeger-demo/issues/2)).

- The [Helmfile adoption](docs/cutover.md#helmfile-adoption) runbook reads for the
  adopted demo: its recovery, rollback, repair and de-adoption steps are the
  operating procedures, `digest` is the committed image mode, a later adoption
  first returns `helmfile/images.yaml` to `live` and the Jaeger and HotROD
  workloads to plain tags in separate reviewed changes, and the read-only checks that
  later steps refer to say what holds after adoption
  ([#172](https://github.com/jaegertracing/jaeger-demo/pull/172),
  refs [#74](https://github.com/jaegertracing/jaeger-demo/issues/74)).

### Fixed

- The 30-minute smoke check no longer passes while HotROD dispatches hang. Every
  run of the public health check now completes one HotROD dispatch on its stack,
  which must name a driver; on the OCI stack the kubelet's probe spans and the
  HotROD page alone used to satisfy the routine check. The OTel stack's HotROD,
  checked only by its page before, gets the same request, also in the Helmfile
  engine's OTel checks. An OTel attempt of the smoke check is limited to 165
  seconds and the job to 22 minutes
  ([#173](https://github.com/jaegertracing/jaeger-demo/pull/173),
  fixes [#171](https://github.com/jaegertracing/jaeger-demo/issues/171)).
- The README's deployment section describes what a scheduled run selects on each
  engine and that `DEMO_DEPLOY_ENGINE` or the manual `engine` input picks it. It
  no longer presents the Helmfile engine as future work, and the diagram drops
  the dashed outline that marked it
  ([#175](https://github.com/jaegertracing/jaeger-demo/pull/175),
  fixes [#174](https://github.com/jaegertracing/jaeger-demo/issues/174)).

## 2026-11-26

### Changed

- The Helmfile engine's verification no longer accepts traffic and SPM evidence
  from before the run's changes. Each stack needs a server span without an error
  and an SPM bucket with positive calls and finite latency that are newer than
  the start of that stack's checks, so a run fails when the application stops
  completing requests after a change, or when every request since has failed. The
  OCI stack's check also completes one HotROD dispatch, which its probes cannot
  stand in for. A healthy run takes three to four minutes longer per stack, and
  `deploy-record.json` records the time as `evidenceSince`, also when the checks
  fail. `stack_health.py --since <time>` applies the same bound by hand; the
  smoke check is unchanged
  ([#170](https://github.com/jaegertracing/jaeger-demo/pull/170),
  refs [#160](https://github.com/jaegertracing/jaeger-demo/issues/160)).
- The scripts engine (`scripts/demo/deploy.py`) refuses to run while Helm holds a
  `demo-resources` companion release in any of the four release namespaces. It
  reads the releases before its first write and names each namespace that holds
  one, so a manual dispatch that leaves `engine` at its default, or a scheduled
  run on the scripts, can no longer re-apply objects the Helmfile engine owns.
  De-adoption removes the release records and re-enables the scripts. The
  `operations` CI job rehearses the refusal and the run after de-adoption on kind
  ([#169](https://github.com/jaegertracing/jaeger-demo/pull/169),
  fixes [#158](https://github.com/jaegertracing/jaeger-demo/issues/158)).
- `docs/MIGRATION.md` records the first digest-mode apply of the Helmfile engine
  and the switch of the daily schedule to that engine
  ([#163](https://github.com/jaegertracing/jaeger-demo/pull/163),
  refs [#1](https://github.com/jaegertracing/jaeger-demo/issues/1)).
- `docs/MIGRATION.md` records the first scheduled run through the Helmfile
  engine ([#165](https://github.com/jaegertracing/jaeger-demo/pull/165),
  refs [#1](https://github.com/jaegertracing/jaeger-demo/issues/1)).
- The daily component updates in `docs/releases.md` say which engine a scheduled
  run takes and describe the Helmfile engine's selection, order, checks and record
  beside the scripts engine's. A first scheduled Helmfile run is accepted on its
  deployment record and all ten releases, and `docs/cutover.md` marks the
  fingerprint selection review as scripts-only
  ([#168](https://github.com/jaegertracing/jaeger-demo/pull/168),
  fixes [#164](https://github.com/jaegertracing/jaeger-demo/issues/164)).

### Fixed

- `otel-demo/flagd` has a readiness probe on `/readyz` of flagd's management
  port, where the chart sets none. A new `flagd` Pod that starts without reading
  its flag file is no longer Ready: it stays out of the Service, the previous Pod
  keeps serving, and the `otel-demo` upgrade fails at Helm's wait instead of
  passing. The next OTel demo upgrade restarts `flagd` once, and no other workload
  ([#170](https://github.com/jaegertracing/jaeger-demo/pull/170),
  refs [#160](https://github.com/jaegertracing/jaeger-demo/issues/160)).
- The Helmfile runbook rehearsal in the `operations` CI job waits for the trace
  generators' old Pods to finish terminating before it plans the de-adoption.
  A plan read while one was terminating went stale and failed the job. The wait
  has its own unit tests
  ([#167](https://github.com/jaegertracing/jaeger-demo/pull/167),
  fixes [#166](https://github.com/jaegertracing/jaeger-demo/issues/166)).

## 2026-11-25

### Added

- A Helmfile engine for **Deploy Jaeger Demo to OKE**, off by default. A scheduled
  run uses it only when the `DEMO_DEPLOY_ENGINE` repository variable is `helmfile`,
  and a manual run only with the new `engine=helmfile` input and
  `expected_config_sha`; otherwise the job runs the scripts exactly as before. It
  reconciles all ten releases from lock-verified charts, OCI before OTel, retries
  failed releases with sync, refuses pending ones before any write, verifies both
  stacks even when nothing changed, and installs a first companion only on a
  one-namespace `helmfile_operation=install-companion` dispatch, and only when every
  allowlisted object there already carries the reviewed ownership. Images come from
  the live references (`helmfile/images.yaml` `mode: live`) or, in `digest` mode,
  are resolved for the run's upstream commit and validated before any step holds
  the OCI API key. Adoption runs (`live` mode and companion installs) restart no
  trace generator and are refused before any write if they would change anything
  but ownership: a live-mode apply when a read-only diff shows a change, a companion
  install when the live drift classification finds a difference. After an apply,
  each Jaeger and HotROD workload must run the expected image. The OTel Certificates
  must be present and Ready, which the scripts only log. Each run uploads
  `deploy-record.json` for 90 days, also when the image gate rejects a candidate. A new
  `operations` CI job rehearses it on kind as the deployer. Setting
  `DEMO_DEPLOY_ENGINE` needs `mode: digest` first, and returning to the scripts
  needs de-adoption first.
  ([#153](https://github.com/jaegertracing/jaeger-demo/pull/153),
  refs [#73](https://github.com/jaegertracing/jaeger-demo/issues/73))

- `scripts/demo/live_drift.py`, a read-only drift report that compares every
  object the Helmfile render would own, rendered with the live Jaeger and HotROD
  images, with the live object, and classifies each as identical, explained by a
  fixed reason code, unexplained or missing. It prints identities, reason codes and
  hashes only, never a live-only map key, and exits non-zero on any unexplained or
  missing object. `--output` writes the report outside the checkout.
  `docs/helmfile-applications.md` has the operator command and the reason codes.
  The deployer role gains a named `get` on the monitoring chart's ten CRDs for it,
  and no list; `chart_objects.py --check` fails when the rendered CRDs differ from
  that grant.
  ([#148](https://github.com/jaegertracing/jaeger-demo/pull/148),
  refs [#70](https://github.com/jaegertracing/jaeger-demo/issues/70))

- A `deployer-access` CI job that proves `ops/rbac/deployer.yaml` on a disposable
  kind cluster without production credentials: the API server accepts the role,
  `deployer_access.py check` finds no difference for either identity and catches
  a removed rule and an extra grant, and the scheduled `oci` / `monitoring`
  upgrade through `examples/oci/deploy-all.sh`, Helm's default `--wait` and hooks
  included, succeeds as a user holding only the role, from the pull request's
  base to its head and with both client-side and server-side apply, with no
  request refused in the API audit log. A role or monitoring change that breaks
  that upgrade now fails its pull request.
  ([#147](https://github.com/jaegertracing/jaeger-demo/pull/147),
  fixes [#100](https://github.com/jaegertracing/jaeger-demo/issues/100))

- The deployer access model covers the Helmfile path. `chart_objects.py --check`
  renders the Helmfile releases from `.helmfile-locked.yaml` and the four
  `demo-resources` companions beside the script path, fails when the two differ in
  an object identity or hook event, renders the companions again as their first
  `helm install` so an install-only object needs a record, and requires Helm
  history records for all ten releases. `deployer_access.py` gains a row for each
  Helmfile path command (`helm list -n`, `helm history`, `helm diff upgrade`,
  `helmfile apply`/`sync`, and the first companion `helm install`, which only the
  manual identity runs) and denies a collection delete of release records.
  Upgrades keep Helm 4's default `--server-side=auto`, and the model covers
  releases stored with either apply method. The offline model finds no difference
  for either identity, so `ops/rbac/deployer.yaml` is unchanged. The
  `deployer-access` CI job also runs the read-only Helmfile path commands, a
  `helmfile diff` of the monitoring release from `.helmfile-locked.yaml` included,
  as the scheduled fixture user with no refused request in the audit log.
  ([#152](https://github.com/jaegertracing/jaeger-demo/pull/152),
  fixes [#142](https://github.com/jaegertracing/jaeger-demo/issues/142))

- An operator runbook for Helmfile adoption, recovery, rollback and de-adoption
  ([Helmfile adoption](docs/cutover.md#helmfile-adoption)), whose commands run as
  the manual deployer identity through `scripts/demo/deployer_kubeconfig.py`. The
  `operations` CI job runs its marked commands verbatim on a second kind cluster,
  and `scripts/demo/runbook_blocks_test.py` keeps them in step with that rehearsal.
  ([#156](https://github.com/jaegertracing/jaeger-demo/pull/156),
  refs [#74](https://github.com/jaegertracing/jaeger-demo/issues/74))

- A `prometheus-operator-validated` reason code for the live drift report. The
  monitoring chart's PrometheusRule mutating webhook adds that annotation, set to
  `"true"`, to every rule it admits, so without the code every PrometheusRule is
  reported as unexplained. Any other value, or the annotation on another kind,
  stays unexplained
  ([#155](https://github.com/jaegertracing/jaeger-demo/pull/155),
  refs [#70](https://github.com/jaegertracing/jaeger-demo/issues/70)).

### Changed

- The owner decided to keep the community ingress-nginx controller in place, with no
  replacement planned in this migration. This is an accepted risk for a public demo
  whose data is disposable and which holds no credentials behind its routes. The
  decision is revisited if an exploitable ingress-nginx CVE is published or when the
  cluster is rebuilt.
  ([#143](https://github.com/jaegertracing/jaeger-demo/pull/143),
  fixes [#41](https://github.com/jaegertracing/jaeger-demo/issues/41))
- `docs/MIGRATION.md` records the node pool initialization script being set and
  verified on a new node.
  ([#150](https://github.com/jaegertracing/jaeger-demo/pull/150),
  fixes [#120](https://github.com/jaegertracing/jaeger-demo/issues/120))

- The release phase gate reads releases with one `helm list -n <namespace>` per
  release namespace instead of a cluster-wide listing, which the deployer role
  cannot do. An absent companion release is no longer installed with
  `helmfile apply`: `release_phases.run` installs it, as a plain `helm install`
  from the shared `install_args()`, only when asked and only as the single
  selected release, and refuses every other absent release. The release phase
  and companion adoption rehearsals run their Helm and Helmfile commands as a
  user bound to the committed deployer roles, and the `adoption` CI job gives the
  release phase rehearsal a fresh kind cluster. Nothing calls the new install
  path yet.
  ([#145](https://github.com/jaegertracing/jaeger-demo/pull/145),
  fixes [#141](https://github.com/jaegertracing/jaeger-demo/issues/141))

- Helmfile now installs only lock-verified chart archives. `chart_archives.py state`
  pulls each repository chart through the existing SHA-256 check, requires its
  version to equal the `deploy-all.sh` pin and lock entry, and writes the ignored
  `.helmfile-locked.yaml` with local archive paths and no repositories;
  `make validate` renders from it. A changed archive is refused and deleted.
  ([#146](https://github.com/jaegertracing/jaeger-demo/pull/146),
  fixes [#140](https://github.com/jaegertracing/jaeger-demo/issues/140))

- `docs/MIGRATION.md` now records the recovery role apply and the leftover cleanup
  from the failed OpenSearch recovery run that the removed backup tooling left
  behind. ([#151](https://github.com/jaegertracing/jaeger-demo/pull/151),
  fixes [#149](https://github.com/jaegertracing/jaeger-demo/issues/149))

- The ownership planning, patch and de-adoption commands in
  `docs/cutover.md` and `docs/helmfile-companions.md` use the deployer kubeconfig
  (`$KUBECONFIG` and `$CONTEXT`) instead of the retired read-only plan identity and
  separate apply identity, and de-adoption deletes the companion's release records
  one by one by name, because the deployer role cannot delete a collection of
  Secrets. Both "Under revision" notes are gone.
  ([#156](https://github.com/jaegertracing/jaeger-demo/pull/156),
  refs [#74](https://github.com/jaegertracing/jaeger-demo/issues/74))

- The OTel demo's busybox init containers set `imagePullPolicy: IfNotPresent`, and
  `checkout` requests its 64Mi memory limit, so the render states both instead of
  leaving them to the API server default and to a manual change. An OTel demo
  upgrade restarts each of these Deployments whose init container ran with
  another pull policy
  ([#155](https://github.com/jaegertracing/jaeger-demo/pull/155),
  refs [#70](https://github.com/jaegertracing/jaeger-demo/issues/70)).

- `docs/MIGRATION.md` records the OTel demo upgrade that applied the drift fixes
  and the second live drift report, which found every rendered object explained
  as the administrator and as the deployer identity
  ([#157](https://github.com/jaegertracing/jaeger-demo/pull/157),
  fixes [#70](https://github.com/jaegertracing/jaeger-demo/issues/70)).

- `docs/MIGRATION.md` records the first Helmfile adoption steps: the paused
  schedule, the read-only baseline, and the adoption of all four namespaces'
  supplemental objects into `demo-resources` releases, the adoption apply and a
  second no-op apply, plus the OTel shop outage
  that a restarted `flagd` caused and its fix
  ([#161](https://github.com/jaegertracing/jaeger-demo/pull/161),
  refs [#74](https://github.com/jaegertracing/jaeger-demo/issues/74)).

- `helmfile/images.yaml` selects `mode: digest`: the Helmfile engine resolves the
  upstream Jaeger and HotROD snapshots, pins them as `tag@sha256` and validates them
  before any step holds the OCI API key. A scheduled run refuses `live` mode, so this
  comes before the engine switch. The first apply in this mode rolls the Jaeger and
  HotROD Deployments of both stacks once
  ([#162](https://github.com/jaegertracing/jaeger-demo/pull/162),
  refs [#1](https://github.com/jaegertracing/jaeger-demo/issues/1)).

### Fixed

- `scripts/demo/deployer_kubeconfig.py` accepts the `kind: ""` that
  `oci ce cluster create-kubeconfig` writes, as kubectl does. It refused the OKE
  administrator kubeconfig as "Not a kubeconfig", so the adoption runbook stopped
  at its first Cloud Shell step
  ([#159](https://github.com/jaegertracing/jaeger-demo/pull/159),
  refs [#74](https://github.com/jaegertracing/jaeger-demo/issues/74)).

- The live drift report instructions install PyYAML into a virtual environment.
  The render's helper scripts do not inherit `PYTHONPATH`, so in Cloud Shell the
  report failed with only `ValueError`. They also no longer say that the render
  waits for [#146](https://github.com/jaegertracing/jaeger-demo/pull/146)
  ([#155](https://github.com/jaegertracing/jaeger-demo/pull/155),
  refs [#70](https://github.com/jaegertracing/jaeger-demo/issues/70)).

- The chart archive list in `ops/rbac/chart-objects.json` still named the
  OpenSearch 3.7.0 archives. `chart_objects.py --write` now copies it from
  `scripts/demo/chart-archives.lock.json`, and `--check` fails while it differs, so
  a chart bump updates it with the rest of the inventory.
  ([#152](https://github.com/jaegertracing/jaeger-demo/pull/152),
  refs [#142](https://github.com/jaegertracing/jaeger-demo/issues/142))

- `docs/releases.md` said that the next Helmfile run syncs a failed release. In
  `live` image mode the next apply is refused instead, because its read-only diff
  reports a release that is not `deployed` as changed; the text now says so and
  links the runbook's recovery steps.
  ([#156](https://github.com/jaegertracing/jaeger-demo/pull/156),
  refs [#74](https://github.com/jaegertracing/jaeger-demo/issues/74))

## 2026-11-24

### Added

- **Reset OTel Demo OpenSearch Trace Data**, a manual workflow for the disposable
  traces of the OTel demo. `list` prints what a reset would delete. `reset` needs
  a typed acknowledgment and a `demo-apply` reviewer, deletes only the daily
  `jaeger-main-jaeger-*` trace indices through the OpenSearch API, restarts Jaeger
  and verifies a fresh trace. It deletes no Kubernetes object, keeps the data
  volume and needs no new access. It does not help when OpenSearch cannot start.
  `docs/releases.md` has the runbook.
  ([#131](https://github.com/jaegertracing/jaeger-demo/pull/131),
  fixes [#76](https://github.com/jaegertracing/jaeger-demo/issues/76))
- A node pool initialization script, `ops/oke/node-pool-init.sh`, that grows the
  root filesystem to the boot volume size on every node the pool creates before
  running the unchanged OKE init script, and the operator runbook
  `docs/node-pool.md` to inspect the pool, set the script without dropping other
  node metadata, and verify the next new node. Nothing applies it automatically;
  setting it on the pool is a separately approved operator action.
  ([#138](https://github.com/jaegertracing/jaeger-demo/pull/138),
  refs [#120](https://github.com/jaegertracing/jaeger-demo/issues/120))
- Document the source ingress-controller dependencies. This is documentation only, with operational decisions still open.
  ([#91](https://github.com/jaegertracing/jaeger-demo/pull/91),
  refs [#41](https://github.com/jaegertracing/jaeger-demo/issues/41))

### Changed

- `docs/MIGRATION.md` and the ingress-controller inventory name the controller's
  owner and record that it is the retired community ingress-nginx, installed from
  its static manifest, without publishing the exact version. The replacement and
  its timing are still open.
  ([#135](https://github.com/jaegertracing/jaeger-demo/pull/135),
  refs [#41](https://github.com/jaegertracing/jaeger-demo/issues/41))

- `AGENTS.md` no longer lists recovery removal as a separate future stage, since
  the recovery tooling is gone. Helmfile and post-adoption refactors remain
  separate stages with explicit prerequisites.
  ([#134](https://github.com/jaegertracing/jaeger-demo/pull/134),
  fixes [#133](https://github.com/jaegertracing/jaeger-demo/issues/133))

- The Helmfile conversion deliverable for removing the backup and restore tooling
  in `docs/MIGRATION.md` now says the repository change is done and that the
  reduced role and cluster cleanup remain operator steps. The stage's exit
  criteria are unchanged.
  ([#137](https://github.com/jaegertracing/jaeger-demo/pull/137),
  fixes [#136](https://github.com/jaegertracing/jaeger-demo/issues/136))

- `docs/releases.md` states that a waiting writer replaced by a newer one in the
  shared concurrency group is accepted behavior, and what an operator does when
  the run observer reports a scheduled run cancelled that way. The workflows are
  unchanged: GitHub's `queue: max` setting would keep every waiting run, but no
  released actionlint accepts the key yet. A workflow test asserts the setting on
  all three writers.
  ([#129](https://github.com/jaegertracing/jaeger-demo/pull/129),
  refs [#102](https://github.com/jaegertracing/jaeger-demo/issues/102))

- A manual **Deploy Jaeger Demo to OKE** run that leaves `image_tag` empty and finds
  no published snapshot for upstream `main` HEAD still fails each selected Jaeger
  component before its release is changed, but the error now says that HEAD has no
  published snapshot and names the fix: dispatch again with `image_tag=latest` or
  an explicit published SHA. A run that also selects a component other than Jaeger
  continues with it and may change the cluster. `docs/releases.md` describes the
  failure. A missing explicit tag keeps its earlier message.
  ([#127](https://github.com/jaegertracing/jaeger-demo/pull/127),
  fixes [#110](https://github.com/jaegertracing/jaeger-demo/issues/110))

- The `deployer_access.py bindings --scheduled-user` help says what omitting the
  flag does instead of telling the operator to omit it, and
  `docs/deployer-access.md` warns that applying the manual-only bindings where
  both identities are bound removes the scheduled identity.
  ([#126](https://github.com/jaegertracing/jaeger-demo/pull/126),
  fixes [#125](https://github.com/jaegertracing/jaeger-demo/issues/125))

- The runbooks no longer read as if the executor retirement that
  `docs/MIGRATION.md` records were still ahead, or ask for the retired read-only
  plan identity: `docs/cutover.md` drops the
  `demo-plan` environment from the Stage 1 access step, `docs/deployer-access.md`
  describes the deployer role without the transition from the earlier grants,
  `docs/executor-retirement.md` points to the evidence in `docs/MIGRATION.md`, and
  `docs/helmfile-companions.md` marks its ownership commands as under revision.
  ([#124](https://github.com/jaegertracing/jaeger-demo/pull/124),
  fixes [#123](https://github.com/jaegertracing/jaeger-demo/issues/123))

- `docs/MIGRATION.md` records the retirement of the leftovers that executor
  retirement step 16 lists (the Stage 1 handover grants, the `demo-plan`
  environment with its identity, and the published executor images) and the
  evidence for the first scheduled run after the removals.
  ([#122](https://github.com/jaegertracing/jaeger-demo/pull/122),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))

### Removed

- The OpenSearch backup and restore tooling and the recovery gate: the **Verify
  OTel Demo OpenSearch Recovery** workflow, `opensearch-recovery.sh`,
  `OPENSEARCH_RECOVERY.md`, the snapshot class manifest, `recovery_policy.py` and
  the `opensearch_recovery` input of **Deploy Jaeger Demo to OKE**, which a
  dispatch can no longer pass. Generated traces are disposable: a manual
  OpenSearch upgrade takes and requires no recovery point, a scheduled one still
  fails if the data claim or its volume changed, and volume expansion is kept. The
  `jaeger-demo-recovery` role keeps its name and loses the access only that
  tooling used. Runs pass before an administrator applies the reduced
  `ops/rbac/deployer.yaml`; until then the administrator's `check` reports the
  removed access. The change alters the fingerprints of `opensearch`,
  `otel-jaeger` and `otel-demo`, so the next scheduled run upgrades those releases
  in place with unchanged charts and values. `docs/releases.md` lists the operator
  steps, including the objects the tooling may have left in the cluster.
  ([#131](https://github.com/jaegertracing/jaeger-demo/pull/131),
  fixes [#76](https://github.com/jaegertracing/jaeger-demo/issues/76))

### Fixed

- An `otel` / `opensearch` deployment no longer fails verification when the
  public Dashboards route answers 503 just after the rollout. The Dashboards
  Pod is now Ready only when its status API answers 200, and `verify_trace.py`
  retries the public status read for a bounded time on 502, 503, 504 and
  connection errors, then fails with the last status. The readiness probe
  changes the rendered Dashboards Deployment, so the next deployment that
  applies it restarts the Dashboards Pod.
  ([#128](https://github.com/jaegertracing/jaeger-demo/pull/128),
  fixes [#111](https://github.com/jaegertracing/jaeger-demo/issues/111))

### Security

- The deploy, OpenSearch recovery and volume-expansion workflows give the OCI API
  key only to the steps that configure cluster access or call the cluster, so the
  Helm setup and Slack notification actions no longer receive it. They install the
  OCI CLI before any of those steps with `pip install --require-hashes` from the
  new `scripts/demo/oci-cli-hashes.txt`, and the kubectl configuration action can
  no longer download packages. The PyYAML source build and the action's `kubectl`
  download stay outside hash checking
  ([deployer access](docs/deployer-access.md#oci-cli-installation)).
  ([#130](https://github.com/jaegertracing/jaeger-demo/pull/130),
  fixes [#103](https://github.com/jaegertracing/jaeger-demo/issues/103))

## 2026-11-23

### Added

- Workspaces and Explore (the new Discover, with PPL) on the public OpenSearch
  Dashboards. Visitors land in an Observability workspace with the Jaeger span
  index pattern selected. A sidecar recreates both within a minute because any
  visitor can delete them. The multiple data source feature stays disabled.
  ([#89](https://github.com/jaegertracing/jaeger-demo/pull/89),
  fixes [#88](https://github.com/jaegertracing/jaeger-demo/issues/88))
- Reviewed Kubernetes roles for the scheduled and manual deployment identities in
  `ops/rbac/deployer.yaml`, covering all six releases, with
  `scripts/demo/deployer_access.py` to print their bindings and to compare an
  identity's live access with the role. Tests fail when a chart or script needs
  access the role lacks or when a rule has nothing behind it. An administrator
  applies the role; see `docs/deployer-access.md`.
  ([#97](https://github.com/jaegertracing/jaeger-demo/pull/97),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95),
  [#87](https://github.com/jaegertracing/jaeger-demo/issues/87))
- `docs/executor-retirement.md`: the ordered operator procedure for moving
  production to the direct deployment path and then removing the executor's
  objects from the cluster.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))

### Changed

- `docs/MIGRATION.md` records the evidence for executor retirement steps 11 to 16
  (removing the executor objects) and lists the leftovers that still need their
  own approval.
  ([#121](https://github.com/jaegertracing/jaeger-demo/pull/121),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- `docs/MIGRATION.md` records the evidence for executor retirement steps 8 to 10,
  including the first accepted scheduled run, and the monitoring chart 93.6.1
  upgrade, and narrows the remaining gates to removing the executor objects.
  ([#119](https://github.com/jaegertracing/jaeger-demo/pull/119),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- `docs/MIGRATION.md` records the evidence for executor retirement steps 2 to 7
  (cluster state, deployer role binding and the manual component runs) and lists
  the gates that remain before the executor objects are removed.
  ([#112](https://github.com/jaegertracing/jaeger-demo/pull/112),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- `docs/MIGRATION.md` records operator acceptance of the manual component
  upgrades (executor retirement step 7) and no longer lists it as an open gate.
  Binding the scheduled identity is the next gate.
  ([#117](https://github.com/jaegertracing/jaeger-demo/pull/117),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- kube-prometheus-stack chart pin moves from 82.10.4 to 93.6.1. The chart no
  longer renders the `prometheus-grafana` Role, so `ops/rbac/deployer.yaml` drops
  its `bind` and `escalate` grant; Helm's delete of the old Role needs only the
  existing `delete` rule. To apply it under review rather than unattended, see
  [When a change alters a release's objects](docs/releases.md#when-a-change-alters-a-releases-objects).
  ([#59](https://github.com/jaegertracing/jaeger-demo/pull/59),
  fixes [#58](https://github.com/jaegertracing/jaeger-demo/issues/58))
- The daily 13:00 UTC update and a manual dispatch are now the same `deploy` job,
  which runs `scripts/demo/deploy.py`. A scheduled run uses `demo-scheduled` and
  no reviewer; a manual run still waits for a `demo-apply` reviewer. The job no
  longer signals the in-cluster executor, and a merged change takes effect at the
  next scheduled run without an executor image.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- The deploy, OpenSearch recovery and volume expansion workflows first check
  their credential against `ops/rbac/deployer.yaml` and stop before any cluster
  change when part of that access is missing. An administrator must apply the
  role before the first run after this change.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- A re-run of a scheduled deployment is refused unless it is at the current `main`
  commit, so an old scheduled run cannot redeploy an old configuration.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- `docs/MIGRATION.md` replaces the plan-and-apply execution contract with direct
  deployment from the workflow and retires Helmfile bundle custody. README,
  release and handover documents describe the new path, and the executor
  documents are marked as a retired design.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- The chart archive lock moved to `scripts/demo/chart-archives.lock.json` and the
  chart object inventory to `ops/rbac/chart-objects.json`. The new
  `scripts/demo/chart_objects.py --check` renders the six pinned releases and fails
  when a chart renders an object with no record, and `--write` adds the records.
  It runs in the `Helmfile render and adoption rehearsal` CI job and replaces
  `access_matrix.py`. The Helm and Kubernetes version test now reads
  `scripts/demo/helmfile-tools.lock.json`.
  ([#107](https://github.com/jaegertracing/jaeger-demo/pull/107),
  fixes [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- The `adoption` CI job's release-phases rehearsal now runs native Helmfile with
  the direct deployment flags and proves a no-op apply, a Secret-only change, an
  empty-diff failed release under `apply`, the history limit and refusal to install
  a missing release. The companion-adoption rehearsal's first install is now a
  plain `helm install` without `--take-ownership`, and neither rehearsal imports
  `adoption_bundle` or `helmfile_transaction`.
  ([#108](https://github.com/jaegertracing/jaeger-demo/pull/108),
  fixes [#105](https://github.com/jaegertracing/jaeger-demo/issues/105))
- `scripts/demo/release_phases.py` takes the selected releases and their current
  Helm statuses, as `helm list` reports them, instead of a sealed bundle plan
  and its approval-time baseline. A release the listing omits counts as absent only
  after `helm history` reports it not found. It still refuses pending and unknown states
  before any write, retries a failed release with `sync`, applies OCI before OTel
  and checks OCI health on every run. `scripts/demo/live_observation.py` now prints
  the fingerprints of named objects (`--object`) and the live image references of
  each stack (`--stack`); its `--scope` and `--render-context` options are gone.
  Neither module imports `adoption_bundle.py` any more.
  The release-phases rehearsal reads those statuses from `helm list` and also checks
  that a missing application release, or a companion in the unknown state that the
  listing omits, stops the run before any write.
  ([#109](https://github.com/jaegertracing/jaeger-demo/pull/109),
  fixes [#106](https://github.com/jaegertracing/jaeger-demo/issues/106))
- The OpenTelemetry Demo values now give the reason the assistant stack is
  disabled as the migration's AI activation constraint, not the retired
  executor's size limit, and note the endpoint override the agent would need.
  No rendered object changes.
  ([#118](https://github.com/jaegertracing/jaeger-demo/pull/118),
  fixes [#55](https://github.com/jaegertracing/jaeger-demo/issues/55))

### Fixed

- `docs/MIGRATION.md` and `docs/executor-retirement.md` no longer say the executor
  code removal must wait for the cluster objects to be removed; it merged first, and
  the removal steps use only `kubectl`.
  ([#113](https://github.com/jaegertracing/jaeger-demo/pull/113),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- The access check asks for Helm release records as a kind, not by the names of
  today's revisions, so a grant that would fail on the next upgrade no longer
  passes. It also reports why `kubectl` failed and bounds each request.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- A manual OpenSearch upgrade verifies its chart archives before the recovery test
  creates a snapshot and a restore volume.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- `deploy.py` no longer reports a deployed component as failed when the
  deployment record was created while it ran.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))

### Removed

- The `deploy_mode` input of **Deploy Jaeger Demo to OKE**. The workflow always
  runs `upgrade`, and `scripts/demo/deploy.py` refuses `clean` inside GitHub
  Actions, because the deployer role cannot create or delete namespaces and a
  clean run would leave the demo partly removed. An administrator can still run
  `clean` from a shell, as [deployer access](docs/deployer-access.md) describes.
  ([#104](https://github.com/jaegertracing/jaeger-demo/pull/104),
  fixes [#98](https://github.com/jaegertracing/jaeger-demo/issues/98))
- The deployment lock on `default/jaeger-demo-deployment-revisions`. The three
  workflows that change the cluster share one concurrency group instead, and
  `deploy.py` records each component by patching only its own key.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- The CI notice for executor-guarded changes and the **Executor image.** marker
  on changelog entries.
  ([#101](https://github.com/jaegertracing/jaeger-demo/pull/101),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- The in-cluster executor from the repository: the preparer and receiver code, the
  container image build and release workflow, the CI rehearsals and admission and
  permission generators, and the documents that described them. The deployment no
  longer uses it. What it installed in the cluster is removed by
  `docs/executor-retirement.md`, which must be recorded before this merges.
  ([#107](https://github.com/jaegertracing/jaeger-demo/pull/107),
  fixes [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))
- The bundle-scope live observer: `live_observation.observe`, `live_reader` and
  the Helm history readers, and `release_phases.observation_scope` and
  `baseline_for`. `adoption_bundle.consume` keeps its per-stack drift check until
  [#99](https://github.com/jaegertracing/jaeger-demo/issues/99) deletes that module.
  ([#109](https://github.com/jaegertracing/jaeger-demo/pull/109),
  fixes [#106](https://github.com/jaegertracing/jaeger-demo/issues/106))
- The frozen-bundle Helmfile tooling of the retired plan-and-apply contract:
  `adoption_bundle.py`, `helmfile_transaction.py`, the `verify-frozen-manifests`
  Helm post-renderer, the `frozen-transaction` and `adoption-bundle` rehearsals,
  their CI step and `docs/helmfile-bundles.md`. `helmfile_tools.py` no longer adds
  the post-renderer to the toolchain. No script imports `cryptography` any more: the
  `adoption` CI job no longer installs it, and local checks need only PyYAML on
  Python 3.9 or later instead of Python 3.11.
  ([#114](https://github.com/jaegertracing/jaeger-demo/pull/114),
  fixes [#99](https://github.com/jaegertracing/jaeger-demo/issues/99))

### Security

- Both `deploy-all.sh` scripts install OpenSearch, Dashboards, the OpenTelemetry
  Demo and kube-prometheus-stack only from archives whose SHA-256 equals
  `ops/executor/chart-archives.json`, and stop before any cluster change when an
  archive or a Jaeger chart dependency differs or a version has no locked digest.
  The `Demo tests` CI job now checks that lock against the publishers' indexes.
  **Executor image.**
  ([#96](https://github.com/jaegertracing/jaeger-demo/pull/96),
  refs [#95](https://github.com/jaegertracing/jaeger-demo/issues/95))

## 2026-11-21

### Added

- `Changelog entry` CI check: a pull request that changes a non-test file fails
  without a new dated entry unless a maintainer applies the `skip-changelog` label.
  It also validates the changelog's date and category headings.
  ([#80](https://github.com/jaegertracing/jaeger-demo/pull/80),
  fixes [#79](https://github.com/jaegertracing/jaeger-demo/issues/79))
- Mermaid diagrams of the public routes, the trace flow and the manual and gated
  daily deployment paths in the README, and of the daily executor sequence in
  `docs/in-cluster-executor.md`.
  ([#68](https://github.com/jaegertracing/jaeger-demo/pull/68),
  fixes [#67](https://github.com/jaegertracing/jaeger-demo/issues/67))
- This changelog, backfilled from the extraction onward. `AGENTS.md`,
  `CONTRIBUTING.md` and the PR template require an entry for notable changes, and
  `AGENTS.md` maps each kind of change to the documents it must update.
  ([#64](https://github.com/jaegertracing/jaeger-demo/pull/64),
  fixes [#63](https://github.com/jaegertracing/jaeger-demo/issues/63))
- Helmfile adoption candidate for the six application releases and twelve
  supplemental objects, with companion releases, a private digest-bound bundle
  and a credential-free validation toolchain (`make validate`). Production still
  runs the extracted script workflow.
  ([#16](https://github.com/jaegertracing/jaeger-demo/pull/16),
  refs [#1](https://github.com/jaegertracing/jaeger-demo/issues/1))
- Scheduled public-demo smoke check every 30 minutes, gated by the
  `DEMO_SMOKE_ENABLED` variable.
  ([#26](https://github.com/jaegertracing/jaeger-demo/pull/26),
  fixes [#25](https://github.com/jaegertracing/jaeger-demo/issues/25))
- Smoke check retries transient misses. Scheduled runs post to Slack only on a
  state change (failed or recovered); manual runs alert on every failure or
  cancellation and never post a recovery.
  ([#48](https://github.com/jaegertracing/jaeger-demo/pull/48),
  fixes [#43](https://github.com/jaegertracing/jaeger-demo/issues/43))
- Run observer that reports deploy and smoke runs that ended before any job
  started, which the in-job alerts cannot see.
  ([#50](https://github.com/jaegertracing/jaeger-demo/pull/50),
  fixes [#37](https://github.com/jaegertracing/jaeger-demo/issues/37))
- CI flags pull requests that change executor-guarded files and therefore need an
  executor image release before the next preparation.
  ([#47](https://github.com/jaegertracing/jaeger-demo/pull/47),
  fixes [#38](https://github.com/jaegertracing/jaeger-demo/issues/38))
- `scripts/utils/update-chart-archives.py` refreshes chart archive digests after a
  chart pin bump, and `version_literals_test.py` fails on any Helm or Kubernetes
  version literal that disagrees with the adapter.
  ([#46](https://github.com/jaegertracing/jaeger-demo/pull/46),
  refs [#39](https://github.com/jaegertracing/jaeger-demo/issues/39))
- `scripts/demo/access_matrix.py` names chart objects missing from the reviewed
  access matrix and proposes draft records; executor CI runs it.
  ([#53](https://github.com/jaegertracing/jaeger-demo/pull/53),
  refs [#39](https://github.com/jaegertracing/jaeger-demo/issues/39))
- `make check` runs lint, tests and Renovate discovery; ruff lints Python; CI jobs
  have time limits.
  ([#34](https://github.com/jaegertracing/jaeger-demo/pull/34),
  fixes [#33](https://github.com/jaegertracing/jaeger-demo/issues/33))

### Changed

- The executor, CI rehearsals and render checks target Kubernetes v1.37.0 (kind
  v0.33.0, kubectl v1.37.0, the 1.37.0 OpenAPI schema and the monitoring CRD hook's
  kubectl image), and the executor's pods keep recursive SELinux relabeling. The
  cluster upgrade and a manual monitoring upgrade go with it. **Executor image.**
  ([#83](https://github.com/jaegertracing/jaeger-demo/pull/83),
  fixes [#82](https://github.com/jaegertracing/jaeger-demo/issues/82))
- `docs/MIGRATION.md` now tracks the daily schedule's first-run evidence and open
  gates, and lists what to inspect (preparer, request record and checkpoint lock
  holder) before the next scheduled slot or a manual write.
  ([#78](https://github.com/jaegertracing/jaeger-demo/pull/78),
  refs [#11](https://github.com/jaegertracing/jaeger-demo/issues/11))
- The README and `CONTRIBUTING.md` require Python 3.11 or later for local checks,
  set by the constrained `pycparser`. The README Development section now lists
  PyYAML, cryptography and ruff and links to `CONTRIBUTING.md` for the commands.
  ([#68](https://github.com/jaegertracing/jaeger-demo/pull/68),
  fixes [#67](https://github.com/jaegertracing/jaeger-demo/issues/67))
- Helmfile adoption toolchain pins Helmfile v1.8.4. Its `apply` now also upgrades
  a release that is not deployed and runs `helm list` once for each release it
  diffs; the frozen-transaction rehearsal covers the new case.
  ([#77](https://github.com/jaegertracing/jaeger-demo/pull/77),
  fixes [#75](https://github.com/jaegertracing/jaeger-demo/issues/75))
- Workload image pins: BusyBox 1.38.0, Valkey 8.1.12-alpine, PostgreSQL 17.12,
  Python 3.11.18 and `jaeger-es-index-cleaner` 2.23.0, each pinned by digest.
  ([#52](https://github.com/jaegertracing/jaeger-demo/pull/52),
  fixes [#51](https://github.com/jaegertracing/jaeger-demo/issues/51))
- OpenSearch and OpenSearch Dashboards chart pins move from 3.7.0 to 3.10.0. A
  scheduled run rejects a Dashboards chart change, so the upgrade is a manual
  `opensearch` deployment.
  ([#45](https://github.com/jaegertracing/jaeger-demo/pull/45),
  fixes [#44](https://github.com/jaegertracing/jaeger-demo/issues/44))
- README leads with the public endpoints table and architecture instead of dated
  handover status.
  ([#28](https://github.com/jaegertracing/jaeger-demo/pull/28),
  fixes [#27](https://github.com/jaegertracing/jaeger-demo/issues/27))
- OpenTelemetry Demo chart pin moves from 0.40.9 to 0.42.7 (application 3.1.0),
  with the assistant stack and OpAMP server disabled and Valkey and PostgreSQL on
  the chart's images. The object set changes, so the release needs a
  [manual upgrade](docs/releases.md#when-a-change-alters-a-releases-objects)
  before a scheduled run accepts it. **Executor image.**
  ([#56](https://github.com/jaegertracing/jaeger-demo/pull/56),
  fixes [#54](https://github.com/jaegertracing/jaeger-demo/issues/54))
- OTel stack README leads with `upgrade` and warns that `clean` deletes the
  OpenSearch volume claim without a backup.
  ([#30](https://github.com/jaegertracing/jaeger-demo/pull/30),
  fixes [#29](https://github.com/jaegertracing/jaeger-demo/issues/29))
- Snapshot tag resolver: an explicit image tag must exist on Docker Hub and use
  Docker tag syntax, and the newest-SHA fallback follows later result pages, up
  to ten. **Executor image.**
  ([#49](https://github.com/jaegertracing/jaeger-demo/pull/49),
  refs [#1](https://github.com/jaegertracing/jaeger-demo/issues/1))

### Fixed

- The OTel demo's PostgreSQL (`astronomy-db`) is pinned by its full registry name
  and digest. The chart's short name `postgres:18.4` is refused by the OKE
  Kubernetes 1.37 node image, which stopped the chart 0.42.7 upgrade.
  **Executor image.**
  ([#86](https://github.com/jaegertracing/jaeger-demo/pull/86),
  fixes [#85](https://github.com/jaegertracing/jaeger-demo/issues/85))
- OpenSearch verification compares live versions with each release's
  `appVersion` rather than the chart pin, so a chart-only upgrade no longer fails
  verification. **Executor image.**
  ([#20](https://github.com/jaegertracing/jaeger-demo/pull/20),
  fixes [#19](https://github.com/jaegertracing/jaeger-demo/issues/19))
- Slack alerts fire for cancelled and timed-out deployments, not only failed ones.
  ([#22](https://github.com/jaegertracing/jaeger-demo/pull/22),
  fixes [#21](https://github.com/jaegertracing/jaeger-demo/issues/21))
- ShellCheck covers the example deploy scripts, and the OCI smoke check has a
  bounded curl timeout. **Executor image.**
  ([#36](https://github.com/jaegertracing/jaeger-demo/pull/36),
  fixes [#35](https://github.com/jaegertracing/jaeger-demo/issues/35))
- The daily request job says why the request record does not match its slot. An
  older record fails with "No preparation was published" and a newer one, which
  a late rerun finds, with "The request record has advanced", both in place of
  an interval error; a still-empty record fails with "No preparation has been
  published" in place of a malformed-request error. `docs/releases.md` lists what
  to check. **Executor image.**
  ([#84](https://github.com/jaegertracing/jaeger-demo/pull/84),
  refs [#81](https://github.com/jaegertracing/jaeger-demo/issues/81))

### Security

- Public OpenSearch Dashboards route disables the Dev Tools console and Index
  Management, which made the unauthenticated cluster writable.
  ([#32](https://github.com/jaegertracing/jaeger-demo/pull/32),
  fixes [#31](https://github.com/jaegertracing/jaeger-demo/issues/31))
- OTel demo values run the load generator headless and drop the `flagd-ui`
  sidecar, which closes the unauthenticated `/loadgen/` and `/feature` controls
  on the public shop route.
  ([#60](https://github.com/jaegertracing/jaeger-demo/pull/60),
  refs [#57](https://github.com/jaegertracing/jaeger-demo/issues/57))
- The executor's `jaeger-helm-preview` tool links the Go module versions that
  Helm 4.3.2 builds with. This clears eight advisories against `golang.org/x/net`
  and `golang.org/x/text` that were not reachable in the tool. **Executor image.**
  ([#66](https://github.com/jaegertracing/jaeger-demo/pull/66),
  fixes [#65](https://github.com/jaegertracing/jaeger-demo/issues/65))
- OpenSearch recovery and volume-expansion workflows install the constrained OCI
  CLI and verify it before taking the deployment lock.
  ([#24](https://github.com/jaegertracing/jaeger-demo/pull/24),
  fixes [#23](https://github.com/jaegertracing/jaeger-demo/issues/23))
- `azure/setup-helm` v5.1.0 with `checksum-policy: required` in all three
  workflows that install Helm.
  ([#62](https://github.com/jaegertracing/jaeger-demo/pull/62),
  fixes [#61](https://github.com/jaegertracing/jaeger-demo/issues/61))

## 2026-11-20

### Fixed

- OpenSearch recovery API requests run inside the selected pod, so the restore
  check queries the restored Jaeger instance.
  ([#13](https://github.com/jaegertracing/jaeger-demo/pull/13),
  fixes [#10](https://github.com/jaegertracing/jaeger-demo/issues/10))
- The OpenSearch restore pod receives its security option once, from the image
  entrypoint, and starts correctly.
  ([#14](https://github.com/jaegertracing/jaeger-demo/pull/14),
  fixes [#12](https://github.com/jaegertracing/jaeger-demo/issues/12))

## 2026-11-19

### Added

- Daily 13:00 UTC workflow covers all five components: both Jaeger/HotROD
  stacks refresh snapshot images daily; OpenSearch with Dashboards, the OTel
  sample application and OCI monitoring deploy when their tracked inputs change.
  Requires the repository activation switch.
  ([#15](https://github.com/jaegertracing/jaeger-demo/pull/15),
  refs [#11](https://github.com/jaegertracing/jaeger-demo/issues/11))
- In-cluster executor for the daily update: GitHub signals a named ConfigMap,
  and separate service accounts prepare and execute a signed, dated bundle. The
  rendered installation manifests start the receiver at zero replicas and the
  preparer CronJob suspended; installation itself is a separate approved step.
  ([#17](https://github.com/jaegertracing/jaeger-demo/pull/17),
  refs [#11](https://github.com/jaegertracing/jaeger-demo/issues/11))

### Fixed

- Executor adapter classifies `Namespace` as cluster-scoped, unblocking the
  production dry run. **Executor image.**
  ([#18](https://github.com/jaegertracing/jaeger-demo/pull/18),
  refs [#11](https://github.com/jaegertracing/jaeger-demo/issues/11))

## 2026-11-18

### Fixed

- Deployment tools are frozen for the handover: Helm v4.3.2 and OCI CLI 3.96.3
  with its dependencies constrained and verified before reconciliation.
  ([#5](https://github.com/jaegertracing/jaeger-demo/pull/5),
  fixes [#4](https://github.com/jaegertracing/jaeger-demo/issues/4))
- A reviewed `expected_config_sha` restricts the handover to a single-stack
  Jaeger upgrade with explicit image tags and checks existing release state before
  any write.
  ([#7](https://github.com/jaegertracing/jaeger-demo/pull/7),
  fixes [#6](https://github.com/jaegertracing/jaeger-demo/issues/6))
- OCI collector keeps the deployed `queue_batch` trace pipeline.
  ([#9](https://github.com/jaegertracing/jaeger-demo/pull/9),
  fixes [#8](https://github.com/jaegertracing/jaeger-demo/issues/8))

## 2026-11-17

### Added

- Hosted-demo operations extracted from `jaegertracing/jaeger` at cutoff
  `bcef01d0c848d5be0416c2eee117d5d306416dd7` with authored history preserved,
  credential-free CI, main-only manual production workflows and contributor
  documentation. See [docs/extraction.md](docs/extraction.md).
  ([#3](https://github.com/jaegertracing/jaeger-demo/pull/3),
  refs [jaeger#10189](https://github.com/jaegertracing/jaeger/issues/10189))

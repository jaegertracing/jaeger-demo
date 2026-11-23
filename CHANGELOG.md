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

- `docs/MIGRATION.md` records the evidence for executor retirement steps 2 to 7
  (cluster state, deployer role binding and the manual component runs) and lists
  the gates that remain before the executor objects are removed.
  ([#112](https://github.com/jaegertracing/jaeger-demo/pull/112),
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
- The OpenTelemetry Demo values enable the `agent` service, so the load
  generator's assistant requests have a service to answer them instead of failing
  as error spans. The agent answers from the chart's recorded responses, holds no
  API key, and its model endpoint is the unresolvable `https://llm.invalid`; the
  MCP server and the public chat UI stay off. The release gains a Deployment, a
  Service and the 828 KB `agent-fixtures` ConfigMap, which the deployer role
  already covers. The OTel stack README adds the post-rollout check that every
  load generator prompt is answered.
  ([#115](https://github.com/jaegertracing/jaeger-demo/pull/115),
  refs [#55](https://github.com/jaegertracing/jaeger-demo/issues/55))

### Fixed

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

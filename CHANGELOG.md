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
[release records](docs/releases.md). **Executor image** marks a merge for which
`scripts/demo/guarded_changes.py` reports a changed file in
`scheduled_inputs.GUARDED`: the daily preparation refuses to run until a matching
executor image is published and installed. The check normalizes chart pins, so a
pin-only bump is not marked and needs no executor release.

Jaeger and HotROD product changes are recorded in the
[core changelog](https://github.com/jaegertracing/jaeger/blob/main/CHANGELOG.md).
Earlier hosted-demo history is in core up to the extraction cutoff below.

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
- README leads with the public endpoints table and architecture instead of dated
  handover status.
  ([#28](https://github.com/jaegertracing/jaeger-demo/pull/28),
  fixes [#27](https://github.com/jaegertracing/jaeger-demo/issues/27))
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

### Security

- Public OpenSearch Dashboards route disables the Dev Tools console and Index
  Management, which made the unauthenticated cluster writable.
  ([#32](https://github.com/jaegertracing/jaeger-demo/pull/32),
  fixes [#31](https://github.com/jaegertracing/jaeger-demo/issues/31))
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

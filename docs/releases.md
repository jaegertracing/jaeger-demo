<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Hosted-demo releases

For the Helmfile candidate, run `make validate` before submitting a configuration change. It compares the six application releases to the script-era inputs and checks both application and supplemental resources against the target Kubernetes and chart schemas. The dependency check pairs script and Helmfile chart updates during the transition. Production continues to run the extracted workflow until operator acceptance. [Application validation](helmfile-applications.md) · [Adoption rehearsal](helmfile-companions.md)

The [private Helmfile bundle contract](helmfile-bundles.md) describes the offline planner and executor handoff of the earlier plan-and-apply design. The [migration plan](MIGRATION.md#execution-contract) replaces that contract, and the document is under revision.

A deployment release is a configuration commit plus the chart and tool versions,
Jaeger and HotROD image references and digests, and verification evidence.
`jaegertracing/jaeger` publishes the product images. This repository deploys them;
it does not create a second Jaeger product version or image-publishing pipeline.

## Current stage

The extraction retains the existing shell scripts and Python reconciler.
Manual deployments use a reviewed GitHub environment. The
[script schedule](cutover.md#review-scheduled-scope) runs the same `deploy` job
and reconciler in its own environment, `demo-scheduled`, behind the
`DEMO_SCHEDULE_ENABLED` switch. The future Helmfile runner and its automatic
image-compatibility gate are not implemented by extraction. Track them in
[Helmfile conversion](https://github.com/jaegertracing/jaeger-demo/issues/1). The
generated bundle and read-only `plan` operation of the earlier design are retired
([migration plan](MIGRATION.md#execution-contract)).

The current workflow supports these inputs:

| Input | Use |
| --- | --- |
| `expected_config_sha` | Full reviewed configuration SHA; activates restricted Stage 1 handover guards |
| `demo_stack` | `oci`, `otel`, or `all` |
| `otel_deploy_scope` | Component selection; despite its name it also selects OCI `jaeger` or `monitoring` |
| `jaeger_image_tag` | Explicit Jaeger snapshot tag |
| `hotrod_image_tag` | Explicit HotROD snapshot tag |
| `image_tag` | Shared fallback when an independent tag is omitted |
| `opensearch_recovery` | Manual dispatch defaults to `required`. An isolated main-branch OTel OpenSearch upgrade can explicitly select `waive`. The daily schedule selects the disposable-trace policy automatically. |

Supply each stack's existing published commit tags for the first handover, using
separate OCI and OTel dispatches when they run different images. Do not use `latest`
or leave image selection implicit for that reviewed run. Default image selection
resolves upstream `jaegertracing/jaeger` main, never this configuration repository's
SHA. Jaeger and HotROD tags can differ because they are published independently.
Record the resolved digests even while the scripts still render tag references.
Do not convert image reference syntax during the ownership transition.

The script reconciler can deploy OTel before OCI and continue independent
components after a failure. The stricter OCI-before-OTel failure gate is a Helmfile
requirement, not an extraction guarantee. Existing public smoke checks also do not
replace the complete health acceptance below.

## Release record

Before a production transition, prepare a private operational record containing:

- The merged configuration commit and source-extraction cutoff.
- The exact workflow inputs and selected components/releases.
- Jaeger chart source commit, other chart versions, resolved dependency archives
  and checksums, tool versions and checksums, and pinned Action revisions.
- Independent Jaeger/HotROD image commits, exact references and resolved digests.
- Prior Helm revisions for each affected release, supplemental resource identities
  and captured specs, PVC bindings/capacities, ingress routes and TLS references.
- Rendered changes, explained live drift, a fresh functional baseline, verification
  steps and the exact rollback scope.
- The human-verification decision, time, and hash of the frozen record.

Freeze configuration and all consequential inputs between verification and
execution. If they change, regenerate the record and obtain verification for the
changed consequential inputs. A moving tag or unpinned tool cannot satisfy this
contract merely because its final value appears in a job log. For Stage 1 this is
an operational gate. The generated bundle and automatic rejection of drift arrive
with Helmfile.

Production and credential-free chart tests pin Helm v4.3.2, the version observed
in the November 17, 2026 core deployment writer. Compare fresh release revisions
and timestamps with that writer evidence before handover; the Cloud Shell client
version is not evidence of the last release writer. Freeze and verify the other
tool versions and checksums in the operational record as well. The OKE setup
action's pip installation is constrained by `scripts/demo/oci-cli-constraints.txt`
to OCI CLI 3.96.3 and the dependency versions observed in the same writer. After
setup, `python scripts/demo/verify_oci_cli.py` checks every pinned distribution and
the CLI executable's version, including when the action's installed marker skips
installation. A rejected tool check cannot start reconciliation or restore-pod
cleanup. Credential-free CI installs and verifies these pins. The OpenSearch recovery and
volume-expansion workflows apply the same constraint and check after checkout and
before they verify the deployment identity's access.
Kubectl follows the live cluster's Kubernetes version; record its exact version
and checksum along with the runner image used for handover. Supply
`expected_config_sha` during the reviewed script handover. The workflow
rejects a different configuration SHA before configuring OKE access. Verify the
dispatched run's `headSha` and retain that evidence. Do not execute a different
configuration revision under an earlier approval.

With a reviewed SHA, only a manual single-stack Jaeger upgrade with explicit
commit tags is permitted. Existing deployed release state, Helm storage/namespace
and required objects are checked before application writes; the Helm commands
cannot fall back to installation. See the [handover permission checks](cutover.md#check-the-restricted-handover-path).
OTel ingress apply or certificate-read failures prevent a successful component
record. Restore-pod cleanup runs only if this run actually started the recovery
backup path, so Jaeger-only handover does not attempt OpenSearch Pod deletion.
Without a reviewed SHA, ordinary deployment and schedule selection behavior is
unchanged. Scheduling still requires its separate scope/access decision.

After the run, append its URL, actual inputs, changed releases, resulting Helm
revisions, image digests, checks and outcomes. Mark it successful only after the
functional checks pass. The existing `default/jaeger-demo-deployment-revisions`
ConfigMap stores component fingerprints; it is not a complete release record or
an authoritative last-known-good image-digest store.

Keep a sanitized release summary with the configuration history. Keep detailed
operational records privately through migration acceptance and while any recorded
revision remains a supported rollback target. Confirm storage location, access and
retention before handover; expiring GitHub logs alone are insufficient. Never
publish Secret contents, credentials, raw Helm values or unreviewed configuration
payloads.

## Change and verification process

1. Open a scoped issue and PR. Run local checks and credential-free CI, update the
   affected documentation, and obtain maintainer review.
2. Merge to main. The extraction must use a history-preserving merge that retains
   both imported authored history and the seed commit. Later PRs follow the
   repository's normal merge policy.
3. Prepare and verify the frozen release record. For the initial transition use
   the checkpoints in [cutover.md](cutover.md). Routine accepted operations do not
   require repeated permission, but access changes, destructive resets and changes
   to the documented invariants need a concrete decision.
4. Deploy only the selected components, using the reviewed inputs. A failed run
   remains failed even if unrelated components succeed.
5. Verify fresh trace write/read, current generated traffic, UI and public routes,
   finite SPM calls and latency under load, Grafana, TLS and the configured analytics
   behavior on both demos. Compare namespace/release names, Service addresses and
   PVC bindings against the pre-run record.
6. Record the result. Diagnose failures before retrying or rolling back; do not
   promote an unverified result to last-known-good state.

## Daily component updates

The scheduled run is the `deploy` job of `ci-deploy-demo.yml` on `main`, in the
`demo-scheduled` environment. It resolves upstream Jaeger and HotROD snapshot tags
independently and runs `scripts/demo/deploy.py`, as a manual run does. Both Jaeger
releases refresh daily. Renovate proposes chart, image and
dependency changes for maintainer review, with a seven-day release-age policy
and manual merges. The next daily reconciliation uses the reviewed configuration
on main. Unchanged storage, monitoring and sample-application inputs retain their
current releases. Each successful component records its fingerprint by
merge-patching only its own key in `default/jaeger-demo-deployment-revisions`; a
first manual install creates the ConfigMap.

| Component | Helm releases | Daily action and checks |
| --- | --- | --- |
| OpenSearch | `opensearch/opensearch`, `opensearch/opensearch-dashboards` | On input changes, upgrade OpenSearch on its existing PVC, wait for the StatefulSet, then upgrade Dashboards and wait for its Deployment. Check the database and Dashboards versions against each chart's application version and write/read a fresh trace before recording success. After a Dashboards rollout, also run the [console and menu check](../examples/otel-demo/README.md#verifying-the-deployment). |
| OTel Jaeger and HotROD | `jaeger/jaeger` | Refresh published snapshots, verify collector and HotROD endpoints, generator rollout, public query and UI. Check Helm ownership and update HotROD in place. An immutable selector change needs a separate reviewed migration. |
| OTel sample application | `otel-demo/otel-demo` | On input changes, upgrade the reviewed chart and values, then verify the collector agent, frontend and load generator. After a rollout, also run the [control-interface and agent checks](../examples/otel-demo/README.md#verifying-the-deployment). |
| OCI Jaeger and HotROD | `default/jaeger` | Refresh published snapshots, verify Jaeger, HotROD and generator rollouts, public HotROD dispatch and the Jaeger services API. Check the UI during post-run verification. |
| OCI monitoring | `default/prometheus` | On input changes, upgrade the reviewed monitoring chart and values, and wait for Grafana rollout. Check the public Grafana health endpoint as part of operator verification. The next scheduled run applies a merged chart or values change unattended; to upgrade under review instead, set `DEMO_SCHEDULE_ENABLED` to `false` before merging and follow [When a change alters a release's objects](#when-a-change-alters-a-releases-objects). |

A scheduled run selects both Jaeger components and every component whose
fingerprint differs from its key in the ConfigMap, including a missing key. It
requires that ConfigMap and the existing releases, and upgrades in place only:
it installs no release and creates no namespace. Both Jaeger releases must have
Helm status `deployed`. Any other release may be `deployed` or `failed`, and a
failed one is upgraded in place. A release in a `pending-*` state is refused until
an operator repairs it. Charts install only from archives whose SHA-256 matches
`scripts/demo/chart-archives.lock.json`, checked before any cluster change. A change
that adds, removes or renames an object is applied like any other once the
deployer role covers it. See
[When a change alters a release's objects](#when-a-change-alters-a-releases-objects).

Generated trace history is disposable. Daily OpenSearch upgrades preserve the
existing PVC and create no backup, restored volume or test Pod. If the stored
format becomes unusable, historical traces may be lost. After an explicitly
approved repair, new traffic can repopulate the demo. Use the
[manual recovery procedure](../examples/otel-demo/OPENSEARCH_RECOVERY.md) when a
separately authorized operation requires a tested backup.

A chart version and its application version can differ: a chart-only release
repackages the same OpenSearch or Dashboards version. Manual and scheduled runs
require each release to be `deployed` at its chart pin and read the
application version with `helm get metadata`, which uses the Helm release
Secret reads the upgrade already needs.

Once the schedule is enabled, a merged change to the OpenSearch and Dashboards
charts is applied in place by the next scheduled run, on the existing PVC and
without a backup. The run compares the data PVC and its bound volume before and
after the upgrade. To start an engine upgrade from a tested recovery point
instead, merge after the 13:00 UTC run or pause the schedule, dispatch the manual
`otel` / `opensearch` upgrade with `opensearch_recovery=required`, and let it
finish. Its successful result records the OpenSearch fingerprint, so the next
scheduled run does not select OpenSearch again.

An OpenSearch failure holds both dependent OTel components. Independent OCI
updates may succeed in the same run. A failed component keeps its preceding
fingerprint. Inspect each component outcome and its live Helm history before a
retry, especially when OpenSearch upgraded before Dashboards failed.

Before activation, review the selection read-only with the schedule disabled.
After activation, use the first actual scheduled run to accept the deployment.
Record its run URL, reconcile the six releases and five component fingerprints,
and verify fresh traces, public health, TLS and storage bindings. The scheduled
failure notification includes the run ID in `#jaeger-operations`.
[Activation and first-run criteria](cutover.md#review-scheduled-scope)

## Failure handling

A manual or scheduled run of the `deploy` job that fails, is cancelled or times
out posts to `#jaeger-operations` with its status. The job is limited to
330 minutes and its reconcile step to 300, which leaves 30 minutes for setup,
failure diagnostics, restore-pod cleanup and the notification. The diagnostics
and cleanup steps have their own 5- and 2-minute limits so that a hung request
cannot use up the cancellation window.

A run that ends before its job starts, for example while it waits for approval
or when a newer pending run replaces it, runs no steps and cannot report itself.
The [run observer](../.github/workflows/demo-run-observer.yml) posts to the same
channel for a deploy or smoke-check run that was cancelled, failed, timed out or
failed at startup while none of its jobs reached a runner. It stays silent when
a job did start, because that job reports itself. The observer runs from the
workflow file on `main` and does not report its own failures. It does not watch
the recovery and volume-expansion workflows.

The deployment, OpenSearch recovery and volume-expansion workflows share the
concurrency group `jaeger-otel-demo-opensearch-recovery` with
`cancel-in-progress: false`, so one of them runs at a time and a running writer is
never cancelled. GitHub keeps one waiting run per group: a newer waiting run
cancels an older waiting one. Do not dispatch a manual writer close to 13:00 UTC,
and do not leave a dispatched run waiting for approval across 13:00 UTC; either
can cost the scheduled run or the manual one.

What a failed run leaves behind:

- A failed component's fingerprint is not recorded, so the next run selects and
  retries it. Components that succeeded in the same run keep their new keys.
- An OpenSearch failure blocks the dependent OTel components in that run.
- A scheduled run refuses a release in a `pending-*` state, and a failed Jaeger
  release, until an operator repairs it.
- A run that stops at **Verify the deployment identity's access** changed
  nothing. The credential lacks part of `ops/rbac/deployer.yaml`; an
  administrator applies the role and reruns `check`
  ([deployer access](deployer-access.md)).
- A re-run of a scheduled run deploys only at the current `main` commit. After a
  later merge, wait for the next scheduled run or dispatch manually.

A failure leaves the schedule switch enabled, so the next scheduled run retries.
Set the repository variable `DEMO_SCHEDULE_ENABLED` to `false` to
pause future scheduled jobs while inspecting a failure. Check queued and running
writers separately and let an active Helm transaction finish before rolling back.
Preserve the failed run's diagnostics and compare actual release state with the
recorded prior revisions. Inspect a pending Helm operation before deciding how
to recover it. [Pause and resume the script schedule](cutover.md#review-scheduled-scope).

For an application release, a previously recorded revision can be restored with
`helm rollback` after verifying that its values, images and external dependencies
remain usable. Use the namespace as well as the release name because two releases
are named `jaeger`. Follow the exact recorded rollback procedure, then repeat the
functional checks. Restore supplemental resources and fingerprint state only from
reviewed pre-run captures and only after verifying the resulting deployment.

OpenSearch downgrade, `clean`, `cleanup.sh`, namespace deletion and PVC deletion
are not generic rollback procedures. Storage expansion cannot be reversed by
shrinking the PVC. Disposable trace data allows a separately approved narrow reset;
it does not authorize routine destructive maintenance.

## Helmfile acceptance

The Helmfile phase must add `helmfile apply` from the deploy workflow in place of
the scripts, with compatibility checks before mutation, failed-release retry with
empty diffs, pending-state blocking and OCI verification before OTel mutation. It
adds no bundle and no second identity. Its first
ownership adoption freezes live chart/config/image references. A verified no-op
apply follows adoption; digest-reference promotion is a separate deployment.

Verified manual adoption unlocks guarded automation. Final cutover acceptance
requires a successful scheduled refresh, a reviewed Renovate chart bump, another
healthy no-op result, and at least 24 hours of observation. Core removal waits for
that acceptance and the separate post-adoption refactors to merge.

No workflow deploys through the in-cluster executor, and its code and documents
are not part of this repository. The cluster-side removal of its objects is
described in [executor retirement](executor-retirement.md).

<a id="merging-a-change-to-guarded-files"></a>

## Merging a change

A merged change takes effect at the next scheduled run, which deploys the
configuration on `main` when it starts. No image has to be published or installed
first. When a change must not go out unattended, set `DEMO_SCHEDULE_ENABLED` to
`false` before merging, deploy it with a manual dispatch, verify it, and then
enable the schedule again.

A chart or script that needs Kubernetes access beyond `ops/rbac/deployer.yaml`
fails `scripts/demo/deployer_access_test.py` in the pull request, so the role
changes in the same pull request. A merge does not change the cluster: an
administrator must apply the changed role and rerun `check` before the next run
([Changing the role](deployer-access.md#changing-the-role)). Until then
**Verify the deployment identity's access** stops each run before any change.
Merge such a change after the 13:00 UTC run, or pause the schedule, until the
role is applied.

### When a change alters a release's objects

A chart change that adds, removes or renames an object, or alters a hook or an
RBAC object, no longer needs a manual upgrade before the schedule accepts it. The
next scheduled run upgrades the release in place like any other change. Before
merging, `python3 scripts/demo/chart_objects.py --check` names the objects a chart adds
and the reviewed records it no longer renders, and the role must cover them.

To apply such a change under review rather than unattended, pause the schedule
first, then:

1. Dispatch **Deploy Jaeger Demo to OKE** from `main` for the affected component
   (the workflow always runs `upgrade`). The OTel sample application is
   `demo_stack=otel` and `otel_deploy_scope=otel-demo`. OCI monitoring is
   `demo_stack=oci` and `otel_deploy_scope=monitoring`.
2. Verify the component as in step 5 of the
   [change and verification process](#change-and-verification-process). For the
   OTel sample application, confirm that <https://shop.demo.jaegertracing.io/>
   loads, that <https://jaeger.demo.jaegertracing.io> shows a fresh `checkout`
   trace, and that `python3 scripts/demo/stack_health.py --stack otel` passes.
3. Enable the schedule again and confirm that the next scheduled run succeeds.
   The change is not accepted until it does.

Both paths need an identity with access to the affected release.
[#87](https://github.com/jaegertracing/jaeger-demo/issues/87) records that the
`demo-apply` grants from the script handover covered the two Jaeger releases only,
and that a monitoring upgrade through the workflow failed part-way.
[Deployer access](deployer-access.md) defines the reviewed role for all six
releases. Dispatch step 1 for those components, or let a scheduled run apply
the change, only after an administrator has applied that role and its `check`
command reports no difference.

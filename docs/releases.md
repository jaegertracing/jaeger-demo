<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Hosted-demo releases

For the Helmfile candidate, run `make validate` before submitting a configuration change. It compares the six application releases to the script-era inputs and checks both application and supplemental resources against the target Kubernetes and chart schemas. The dependency check pairs script and Helmfile chart updates during the transition. Production continues to run the extracted workflow until operator acceptance. [Application validation](helmfile-applications.md) · [Adoption rehearsal](helmfile-companions.md)

The [private Helmfile bundle contract](helmfile-bundles.md) describes the offline planner and executor handoff under development.

A deployment release is a configuration commit plus the chart and tool versions,
Jaeger and HotROD image references and digests, and verification evidence.
`jaegertracing/jaeger` publishes the product images. This repository deploys them;
it does not create a second Jaeger product version or image-publishing pipeline.

## Current stage

The extraction retains the existing shell scripts and Python reconciler.
Manual deployments use a reviewed GitHub environment. The
[script schedule](cutover.md#review-scheduled-scope) has its own environment
and activation switch. The future Helmfile runner, generated immutable
bundle, read-only `plan` operation and automatic image-compatibility gate are not
implemented by extraction. Track them in
[Helmfile conversion](https://github.com/jaegertracing/jaeger-demo/issues/1).

The current workflow supports these inputs:

| Input | Use |
| --- | --- |
| `expected_config_sha` | Full reviewed configuration SHA; activates restricted Stage 1 handover guards |
| `demo_stack` | `oci`, `otel`, or `all` |
| `otel_deploy_scope` | Component selection; despite its name it also selects OCI `jaeger` or `monitoring` |
| `deploy_mode` | Use `upgrade` for routine maintenance and handover |
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
before they acquire the deployment lock.
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

The protected planner resolves upstream Jaeger and HotROD snapshot tags independently
and freezes the exact images in the daily bundle. The GitHub job signals the [protected executor](daily-update-request.md) and waits for its verified result. Both Jaeger releases refresh daily. Renovate proposes chart, image and
dependency changes for maintainer review, with a seven-day release-age policy
and manual merges. The next daily reconciliation uses the reviewed configuration
on main. Unchanged storage, monitoring and sample-application inputs retain their
current releases. Each successful component records its fingerprint in
`default/jaeger-demo-deployment-revisions`.

| Component | Helm releases | Daily action and checks |
| --- | --- | --- |
| OpenSearch | `opensearch/opensearch`, `opensearch/opensearch-dashboards` | On input changes, upgrade OpenSearch on its existing PVC, wait for the StatefulSet, then upgrade Dashboards and wait for its Deployment. Check the database and Dashboards versions against each chart's application version and write/read a fresh trace before recording success. After a Dashboards rollout, also run the [console and menu check](../examples/otel-demo/README.md#verifying-the-deployment). |
| OTel Jaeger and HotROD | `jaeger/jaeger` | Refresh published snapshots, verify collector and HotROD endpoints, generator rollout, public query and UI. Check Helm ownership and update HotROD in place. An immutable selector change needs a separate reviewed migration. |
| OTel sample application | `otel-demo/otel-demo` | On input changes, upgrade the reviewed chart and values, then verify the collector agent, frontend and load generator. |
| OCI Jaeger and HotROD | `default/jaeger` | Refresh published snapshots, verify Jaeger, HotROD and generator rollouts, public HotROD dispatch and the Jaeger services API. Check the UI during post-run verification. |
| OCI monitoring | `default/prometheus` | On input changes, upgrade the reviewed monitoring chart and values, and wait for Grafana rollout. Check the public Grafana health endpoint as part of operator verification. |

Generated trace history is disposable. Daily OpenSearch upgrades preserve the
existing PVC and create no backup, restored volume or test Pod. If the stored
format becomes unusable, historical traces may be lost. After an explicitly
approved repair, new traffic can repopulate the demo. Use the
[manual recovery procedure](../examples/otel-demo/OPENSEARCH_RECOVERY.md) when a
separately authorized operation requires a tested backup.

A chart version and its application version can differ: a chart-only release
repackages the same OpenSearch or Dashboards version. The daily executor takes
each application version from its reviewed chart archive. A manual deployment
requires each release to be `deployed` at its chart pin and reads the
application version with `helm get metadata`, which uses the Helm release
Secret reads the upgrade already needs.

Once the schedule is enabled, a merged change to the OpenSearch chart alone is
applied in place by the next daily run, without a backup. A Dashboards chart
version change is not. That chart writes its version into labels on the
Dashboards ServiceAccount and RoleBinding, and the executor requires those
protected objects to equal the live release. Preparation then fails with
"Privileged manifest differs from the operator-established baseline", and the
daily update stops for every component until a manual upgrade has established
the new release. The two charts are bumped together, so plan every OpenSearch
version upgrade as a manual one: merge after the daily run, dispatch the manual
`otel` / `opensearch` upgrade with `opensearch_recovery=required`, which also
starts the engine upgrade from a tested recovery point, and let it finish
before the next preparation. The preparation window opens at 11:00 UTC for
the 13:00 UTC slot; the CronJob runs at 12:20 UTC, but a one-off preparer Job
can run earlier. Preparation freezes the checkpoint and Helm history for the
slot, and a later retry reuses that frozen plan. An upgrade that completes after
preparation makes the executor reject the changed state and leaves the writer
lock for operator recovery. If the upgrade cannot finish before preparation,
first pause both the GitHub signal and the preparer, and let running preparers
and writers drain. Resuming the same day reuses the frozen plan, so keep the
signal off until the next day's slot is prepared after the upgrade.
[Activation and pause order](in-cluster-executor.md#activation-approval)

An OpenSearch failure holds both dependent OTel components. Independent OCI
updates may succeed in the same run. A failed component keeps its preceding
fingerprint. Inspect each component outcome and its live Helm history before a
retry, especially when OpenSearch upgraded before Dashboards failed.

During commissioning, verify the prepared slot and receipt with the GitHub
signal disabled. After activation, use the first actual scheduled run to accept
the deployment. Record its run URL and receipt, reconcile the six releases
and five component fingerprints, and verify fresh traces, public health, TLS
and storage bindings. The scheduled failure notification includes the run ID in
`#jaeger-operations`. [Commissioning and first-run criteria](daily-production-install-review.md)

## Failure handling

A manual deployment or daily request job that fails, is cancelled or times out
posts to `#jaeger-operations` with its status. The manual job is limited to
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
workflow file on `main` and does not report its own failures.

A failure leaves the schedule switch enabled. The operator must reconcile a failed request and any held lock before the next slot. Set the repository variable `DEMO_SCHEDULE_ENABLED` to `false` to
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

The Helmfile phase must add the generated immutable bundle, read-only plan RBAC,
compatibility checks before mutation, failed-release retry with empty diffs,
pending-state blocking and OCI verification before OTel mutation. Its first
ownership adoption freezes live chart/config/image references. A verified no-op
apply follows adoption; digest-reference promotion is a separate deployment.

Verified manual adoption unlocks guarded automation. Final cutover acceptance
requires a successful scheduled refresh, a reviewed Renovate chart bump, another
healthy no-op result, and at least 24 hours of observation. Core removal waits for
that acceptance and the separate post-adoption refactors to merge.

The in-cluster runtime has its own image release, inactive installation manifest and activation procedure. The release receipt records the image digest separately from the daily configuration SHA and component versions. [Executor image release and installation](in-cluster-executor.md#release-and-installation)

## Merging a change to guarded files

The executor refuses its 12:20 UTC preparation when a file in
`scheduled_inputs.GUARDED` on `main` differs from the copy in its installed image.
Pull request CI lists such files in the job summary and in a notice. A chart pin
bump that only edits the pin lines is not listed. Complete these steps before the
next 12:20 UTC preparation, or pause the schedule first:

1. Merge the pull request.
2. Run **Publish the demo executor image** from the merge commit.
3. Obtain installation approval and install the new image.

The image publication and installation steps are in
[Release and installation](in-cluster-executor.md#release-and-installation). To pause the
schedule, follow [Activation approval](in-cluster-executor.md#activation-approval).

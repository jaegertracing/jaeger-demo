<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Hosted-demo ownership handover

This runbook describes the Stage 1 script handover. It is not evidence that
production has migrated. [MIGRATION.md](MIGRATION.md) tracks the phase exits;
[releases.md](releases.md) defines the deployment record and verification contract.

The agent prepares and executes accepted work. Human verification is required for
the concrete access plan, first script handover, first Helmfile adoption and final
acceptance. Routine implementation, diagnostics and operations within accepted
scope proceed without repeated permission. Review-feedback replies require
individual human approval under the organization AI Usage Policy.

## Preserve the existing installation

| Namespace | Application releases |
| --- | --- |
| `default` | `jaeger`, `prometheus` |
| `jaeger` | `jaeger` |
| `opensearch` | `opensearch`, `opensearch-dashboards` |
| `otel-demo` | `otel-demo` |

Preserve the OKE cluster, public URLs/DNS, namespaces, PVCs, release names and
Service addresses. Certificate issuers remain externally managed. Do not combine
handover with infrastructure provisioning, ingress-controller/cert-manager
upgrades, DNS changes or AI activation.

## Prepare before disabling the old path

- Merge the tested extraction to main with its imported history intact. Verify
  CODEOWNERS, required review/checks and DCO/Renovate coverage. Production workflows
  must still be manual-only.
- Establish a fresh healthy baseline on both demos, including finite SPM calls and
  latency under current generated traffic. Existing defects are separate fixes and
  block handover. A Ready pod or HTTP 200 alone does not establish this baseline.
- Capture the complete affected release/resource state, chart/configuration/image
  inputs, prior revisions and rollback procedure. The preliminary inventory that
  omits embedded configuration is not full equivalence evidence.
- Freeze and hash the release record described in [releases.md](releases.md).
  Review every difference against live state. Preserve exact image reference syntax
  for the ownership transition; tag-to-digest conversion can roll pods even when
  the image bytes match.
- Verify the access/security plan, then provision fresh new-repository OCI access.
  Do not copy the old private key. Prepare separate read-only plan and write-capable
  apply identities in `demo-plan` and `demo-apply`, restricted to main. The extraction
  workflows use the apply environment; a generated read-only plan workflow is later
  Helmfile work. Verify permissions without deployment writes before retiring access.

The current workflows refer to these secrets: `OCI_CLI_USER`, `OCI_CLI_TENANCY`,
`OCI_CLI_FINGERPRINT`, `OCI_CLI_KEY_CONTENT`, `OCI_CLI_REGION`,
`OKE_CLUSTER_OCID`, and the deployment failure notification's `SLACK_WEBHOOK_URL`.
Configure names and access scopes in the reviewed environment plan. Do not copy
credential values into documentation, issues, logs or inventories. The scheduled
apply environment must not require a human reviewer for each routine run after
its initial activation is accepted.

Useful read-only checks, from an authenticated administrative terminal:

```bash
gh api repos/jaegertracing/jaeger-demo/commits/main --jq .sha
helm list --all-namespaces -o json
helm history jaeger --namespace default -o json
helm history jaeger --namespace jaeger -o json
kubectl get pvc --all-namespaces
kubectl get ingress --all-namespaces
```

These commands are partial checks, not a complete inventory collector. Review any
additional captured output before sharing it. Helm 4.3.2 lists releases in every
status by default; `--all` is not supported. Keep status filters unset for the
inventory. The default limit is 256 releases, above the six application releases
listed here.

## Check the restricted handover path

Supplying `expected_config_sha` activates the Stage 1 handover guards. The run must
be a manual dispatch from main, select exactly one of `oci` or `otel`, use
`upgrade` and `jaeger`, and supply explicit full Jaeger and HotROD commit tags.
The reconciler checks its actual component selection and rejects any image-tag
change during resolution. Omitting the reviewed SHA uses the ordinary deployment
path; it is not an alternative for the first ownership transfer.

Before application writes, `scripts/demo/handover.py` requires both `default/jaeger`
and `jaeger/jaeger` to exist in `deployed` state using Helm Secret storage. The
implicit kubectl and Helm namespace must be `default`. It requires every target
chart object, generator, supplemental Service/Ingress and the shared
`default/jaeger-demo-deployment-revisions` ConfigMap to exist and be readable.
A denied read is a failure, not evidence that an object is absent. OCI refuses its
ordinary uninstall/reinstall fallback; OTel repeats the check before HotROD
replacement. Both handover Helm commands omit `--install`, and OTel also omits
`--create-namespace`. OTel ingress apply and certificate-read errors fail the run.
Jaeger-only runs skip restore-pod cleanup because they cannot start recovery.

These guards do not compare full live manifests or authorize a new resource set.
Before dispatch, compare captured current, desired and rollback object sets, chart
hooks, exact image references and prior release revisions in the frozen record.
The only accepted application-object deletion/recreation is OTel's
`jaeger/jaeger-hotrod` Deployment. Missing objects, new hooks, unexplained revision
changes or failed/pending releases require a separate repair and fresh verification.
Drain old writers first and keep the new workflow serialized. Verify cluster
identity, expected certificates and public TLS independently; readable certificate
summaries alone do not prove TLS health.

Check effective access using the actual new CI principal before its first write:

| Capability | Initial handover requirement |
| --- | --- |
| Existing chart and supplemental objects | Named `get,patch`; chart/watcher workload reads include `list,watch` and dependent status resources |
| OTel HotROD replacement | Named Deployment `delete` plus Deployment `create` in `jaeger`; native RBAC cannot name-limit top-level create |
| Helm storage in `default` and `jaeger` | Secret `get,list,create`; `update,delete` restricted to individually listed retained Jaeger revisions and the next upgrade/rollback revisions |
| OTel diagnostics and TLS summaries | Service list in `kube-system`, relevant Namespace reads and Certificate list in `jaeger`, `opensearch`, `otel-demo` |
| Excluded mutations | Other application deletes/updates, OpenSearch Pod deletion, exec/port-forward, namespaces, storage, RBAC, CRDs/webhooks and AI ingress |

Secret list discloses namespace Secret contents, and top-level Secret create cannot
be name-limited with native RBAC. Keep that accepted trusted-writer scope explicit.
Use exact observed revision Secret names, not wildcard `resourceNames`; a retry
beyond the accepted revision range needs renewed verification. Include automatic
rollback and history pruning in the permission rehearsal. For example:

```bash
# Expected yes under the apply principal:
kubectl auth can-i patch deployments.apps/jaeger --namespace default
kubectl auth can-i delete deployments.apps/jaeger-hotrod --namespace jaeger
kubectl auth can-i create deployments.apps --namespace jaeger
kubectl auth can-i list services --namespace kube-system
# Expected no:
kubectl auth can-i delete deployments.apps/jaeger --namespace default
kubectl auth can-i delete pods --namespace opensearch
kubectl auth can-i create pods --subresource=exec --namespace opensearch
kubectl auth can-i create pods --subresource=portforward --namespace jaeger
```

These examples are not the complete permission matrix. Check all required verbs
and positive reads/watches against the verified object set; never test forbidden
writes by attempting them. RBAC grants are additive, so inspect other bindings as
well. Keep an independent administrator recovery route. The restricted writer is
not authorized to rebuild the installation or repair missing unrelated objects.

This table is the Stage 1 Jaeger scope. Manual upgrades of monitoring, OpenSearch
and the OTel demo add a separately installed package of named grants fenced by
admission policies, described in [manual upgrade access](manual-upgrade-access.md).
It keeps storage, namespaces, CRDs, exec and port-forward excluded.

## Transfer workflow ownership

1. Verify the concrete handover record with the human. Confirm tools, chart
   dependencies and exact image tags still match the record. Supply the full
   reviewed configuration SHA to the workflow guard.
2. Disable all three core operational workflows:

   ```bash
   gh workflow disable ci-deploy-demo.yml --repo jaegertracing/jaeger
   gh workflow disable ci-opensearch-recovery.yml --repo jaegertracing/jaeger
   gh workflow disable ci-opensearch-volume-expand.yml --repo jaegertracing/jaeger
   ```

3. Drain both running and pending old runs. Disabling a workflow does not cancel
   an existing run. Inspect each workflow until no non-completed run remains:

   ```bash
   for workflow in ci-deploy-demo.yml ci-opensearch-recovery.yml ci-opensearch-volume-expand.yml; do
     gh api --paginate \
       "repos/jaegertracing/jaeger/actions/workflows/$workflow/runs?per_page=100" \
       --jq '.workflow_runs[] | select(.status != "completed") | {id,status,head_sha,html_url}'
   done
   ```

   Do not cancel an active Helm operation merely to expedite the handover. Inspect
   its state and use a reviewed completion/repair procedure. Matching concurrency
   group names do not coordinate across repositories.
4. After the old runs drain, retire their delivery of signing credentials. During
   this handover the operator removed core's repository Actions secret
   `OCI_CLI_KEY_CONTENT`. The corresponding maintainer OCI signing key remains
   active because other consumers could not be ruled out. Record the credential
   disposition and retain the tested independent recovery route.
5. Enable the new manual deployment path if GitHub has disabled it. Dispatch only
   the reviewed inputs from main. Transfer the OCI demo first, verify it, then
   transfer the OTel demo in a separate run. Use `otel_deploy_scope=jaeger`,
   `deploy_mode=upgrade`, and each stack's existing Jaeger/HotROD commit tags. The
   stacks may run different image versions; one `demo_stack=all` invocation shares
   image inputs and cannot preserve those differences. Supply the full configuration SHA in
   `expected_config_sha`; the workflow fails before OKE setup if main has changed.
   Capture the shared fingerprint ConfigMap immediately before each dispatch, with
   a distinct snapshot for each stack. The pre-OTel capture must include the
   successful OCI result. Keep these with that dispatch's release and supplemental
   rollback captures; a snapshot from before both runs is insufficient for an
   OTel-only rollback.
   Set these variables from the verified record, then dispatch:

   ```bash
   : "${config_sha:?Set the reviewed full configuration SHA}"
   : "${oci_jaeger_tag:?Set the existing OCI Jaeger commit tag}"
   : "${oci_hotrod_tag:?Set the existing OCI HotROD commit tag}"
   gh workflow run ci-deploy-demo.yml --repo jaegertracing/jaeger-demo --ref main \
     -f "expected_config_sha=$config_sha" \
     -f demo_stack=oci -f otel_deploy_scope=jaeger -f deploy_mode=upgrade \
     -f "jaeger_image_tag=$oci_jaeger_tag" -f "hotrod_image_tag=$oci_hotrod_tag" \
     -f opensearch_recovery=required
   ```

   Wait for the OCI run to finish and verify its functional and resource checks.
   Only then dispatch the OTel run from the same reviewed configuration:

   ```bash
   : "${config_sha:?Set the reviewed full configuration SHA}"
   : "${otel_jaeger_tag:?Set the existing OTel Jaeger commit tag}"
   : "${otel_hotrod_tag:?Set the existing OTel HotROD commit tag}"
   gh workflow run ci-deploy-demo.yml --repo jaegertracing/jaeger-demo --ref main \
     -f "expected_config_sha=$config_sha" \
     -f demo_stack=otel -f otel_deploy_scope=jaeger -f deploy_mode=upgrade \
     -f "jaeger_image_tag=$otel_jaeger_tag" -f "hotrod_image_tag=$otel_hotrod_tag" \
     -f opensearch_recovery=required
   ```

   Verify and record each run's `headSha` and inputs. A differing configuration
   revision requires re-verification, not removal of the guard.

   These runs deploy the two Jaeger components. They do not exercise a chart
   upgrade of OpenSearch, monitoring or the OTel application. The full release and
   public-behavior checks still cover the existing six-release installation.
   Keep `opensearch_recovery=required`; the Jaeger-only scope does not invoke the
   OpenSearch recovery or upgrade path.
6. Verify actual changes and all health checks in [releases.md](releases.md). The
   extracted scripts retain unconditional generator restarts and OTel HotROD
   Deployment replacement; review those expected effects rather than claiming this
   script handover preserves all pod or Deployment identities. Unchanged identity
   and pod-template adoption tests belong to the later Helmfile transition.
7. Keep the daily script schedule disabled after the two manual runs. Their success
   verifies only the selected Jaeger components, not the schedule's selection or
   permissions. Obtain the separate scope/access decision described below before
   enabling it, then verify the first scheduled run. Stage 1 operational handover
   remains incomplete until that scheduled-refresh gate passes.

The manual OpenSearch recovery input defaults to `required`. A manual
`main` / `otel` / `upgrade` / `opensearch` dispatch can explicitly waive it.
The 13:00 UTC schedule handles disposable trace history automatically. The
existing PVC and the live version and trace checks remain part of its upgrade.
Manual recovery-tool retirement belongs to the later Helmfile conversion.

## Review scheduled scope

The reconciler fingerprints the whole deployment script for each component in its
stack. The handover guard changes therefore also change the OpenSearch, OTel Demo
and monitoring fingerprints. Two successful manual Jaeger runs update only their
own keys. A schedule selects every changed or missing component fingerprint plus
both Jaeger components; it does not use the manual stack/scope filter. The initial
apply role and its finite Helm revision window do not authorize this broader or
repeated work.

From the root of a clean checkout at the proposed merged configuration SHA,
inspect the actual selection with this read-only command. It uses the same fingerprint and
selection functions as the reconciler and reads only the shared ConfigMap:

```bash
: "${config_sha:?Set the proposed merged configuration SHA}"
python3 - "$config_sha" <<'PY'
import json
import subprocess
import sys

if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != sys.argv[1]:
    raise SystemExit("Checkout does not match the proposed configuration SHA")
if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], text=True).strip():
    raise SystemExit("Checkout has uncommitted changes")
sys.path.insert(0, "scripts/demo")
import deploy

state = json.loads(subprocess.check_output([
    "kubectl", "-n", "default", "get", "configmap", deploy.STATE, "-o", "json",
], text=True))
desired = {name: deploy.fingerprint(name) for name in deploy.COMPONENTS}
selected = deploy.selected_components("schedule", "oci", "jaeger", "upgrade", state.get("data", {}), desired)
print(json.dumps({"configuration": sys.argv[1], "state_resource_version": state["metadata"]["resourceVersion"],
                  "selected_components": selected}, indent=2))
PY
```

Retain the result and review every selected component, its recovery policy,
effective permissions, revision allowance and rollback procedure. Rehearse the
accepted selection with matching permissions before activation, and repeat this
inspection if configuration or stored fingerprints change. Do not overwrite
unapplied fingerprints to hide additional selections. The initial manual access
approval is not approval to broaden permissions or remove the environment reviewer.

The maintainers selected all five components for the 13:00 UTC job. Keep the
repository Actions variable `DEMO_SCHEDULE_ENABLED` set to `false` through
preparation. Confirm the effective value if the organization defines the same
name. A skipped scheduled job receives no deployment environment or runner.
Manual runs continue to use `demo-apply`.

1. Review the pinned configuration and all selected releases. For OpenSearch,
   capture the existing StatefulSet, PVC binding, release revision, running
   image and version. Verify the paired Dashboards version and a fresh trace
   before the change. The scheduled upgrade preserves the live volume and
   repeats the version and trace checks afterward. Existing synthetic trace
   history can be lost. Any destructive reset needs its own approval.
2. Review the [proposed access and chart inventory](all-five-schedule-access.md).
   Verify the main-only `demo-scheduled` environment and the four independently authenticated
   request actors. Follow the [request and lock setup](daily-update-request.md). The requester
   may signal the named request. The protected executor needs the rehearsed six-release
   apply, rollback and verification access. Verify prep-only signing and executor-only decryption keys, enforce dry-run-only workload patching for the preparer, and confirm the requester has only its request role. Verify the durable lock across all manual writers and keep the human reviewer on `demo-apply`.
3. With the signal off, verify receiver readiness and the real dated ready receipt from the preparer. With explicit activation approval, enable the preparer and the repository variable for the intended 13:00 UTC event. To pause future scheduled jobs, set it to `false`. Inspect
   and drain any queued or running writers before changing access or rolling back.

   ```bash
   gh variable set DEMO_SCHEDULE_ENABLED --body true --repo jaegertracing/jaeger-demo
   ```

   To pause:

   ```bash
   gh variable set DEMO_SCHEDULE_ENABLED --body false --repo jaegertracing/jaeger-demo
   ```

4. Find the scheduled run, check its event, configuration SHA, environment and
   component summary, and compare all six live release histories and fingerprint
   keys with the pre-run record. A skipped job does not satisfy the stage exit.
   Verify the [per-component checks](releases.md#daily-component-updates) and
   the public health checks on both demos. Record the run URL, versions, trace
   ID, and PVC binding before closing [core handover](https://github.com/jaegertracing/jaeger/issues/10189).

   ```bash
   gh run list --repo jaegertracing/jaeger-demo --workflow ci-deploy-demo.yml \
     --event schedule --limit 5 --json databaseId,headSha,status,conclusion,url
   ```

The credential-free [public smoke check](../scripts/demo/README.md#scheduled-smoke-check)
has its own switch, `DEMO_SMOKE_ENABLED`. `SLACK_WEBHOOK_URL` is already a
repository secret. The check retries each stack up to three times and posts to
Slack only when the scheduled result changes, one message when it starts
failing and one when it recovers. Enable the switch after one passing manual
dispatch of `demo-smoke.yml` that shows no history lookup warning.

While the old maintenance workflows are disabled and the new grant is limited to
Jaeger, the independent operator owns maintenance and incident recovery through the
separate administrator route. Manual recovery and volume expansion use
`demo-apply` workflows and require separately authorized storage access.
The scheduled writer needs neither snapshot privileges nor restore capacity.
The two manual recovery repairs cover in-pod API requests and OpenSearch startup
in the isolated restore Pod. Verify them before relying on a manually tested
recovery point. [In-pod command fix](https://github.com/jaegertracing/jaeger-demo/pull/13),
[restore startup fix](https://github.com/jaegertracing/jaeger-demo/pull/14).

## If the handover fails

Stop new automation first and retain diagnostics. Do not reactivate old workflows
while the new path can still write. Restore application revisions and supplemental
specs using the recorded scope, then verify health. See
[release failure handling](releases.md#failure-handling).

Restore the failed dispatch's application and supplemental resources first. After
verification, restore only its key in `default/jaeger-demo-deployment-revisions`
from that dispatch's pre-run capture: `oci-jaeger` or `otel-jaeger`. Use a merge
patch containing only that entry under `data`; if the key was originally absent,
use a null value to remove it. Read the current ConfigMap before patching and
verify afterward that every other entry is unchanged. Never apply an entire old
ConfigMap snapshot. In particular, an OTel-only rollback must preserve the earlier
successful OCI fingerprint. An unchanged failed-component key needs no patch.

Keep the retired Actions secret absent from core. If returning operational ownership to core is necessary,
verify a replacement scoped credential and the exact old configuration before
enabling that path. Give exactly one repository write access to production at a time.
Do not remove the legacy fingerprint ConfigMap during handover.

## Later Helmfile adoption

Helmfile conversion starts only after the script handover succeeds and the parallel
core HotROD fixture has merged. Before production adoption:

- Complete render/inventory equivalence and live drift checks, confirm the last
  release-writer Helm major and digest support, and pass the credential-free tests.
- Rehearse adoption and rollback in kind. Obtain a fresh healthy live baseline.
- Review the immutable deployment bundle and exact supplemental allowlist. Freeze
  existing application chart/config/image references during ownership adoption.
- Adopt only allowed ingresses, supplemental Services and generator resources into
  namespace-local companion releases. Reject foreign ownership; keep resource
  identities, selectors, ClusterIPs and pod-template hashes. Do not template PVCs,
  namespaces, snapshots or generated TLS secrets. The first companion installation
  must not use atomic rollback.
- Verify OCI before OTel mutation, then perform a second no-op apply. Promote
  digest-pinned candidate images separately after verified ownership adoption.

The implemented Helmfile PR must supply exact commands for that procedure and its
rollback. Companion rollback stops automation, removes only the recorded companion
release metadata to de-adopt kept objects, and reapplies captured specs. Never use
`helm uninstall` on a companion release as a de-adoption shortcut. Never treat an
OpenSearch downgrade as a generic rollback. The old scripts can be a fallback only
after de-adoption has completed.

The commands for the ownership patches and for the companion rollback follow. The
commands that run an approved bundle belong to the executor integration, which is
not complete. See the [bundle contract](helmfile-bundles.md#verify-and-consume).

### Apply the ownership patches

Run this under the deployment lock, after the bundle is approved and before the
first companion install. The planner reads the twelve allowlisted objects by name
with the read-only plan identity. Each patch adds only the Helm ownership label,
the two release annotations and the keep policy, and tests the UID and resource
version it was planned for.

```bash
umask 077
work=$(mktemp -d)
python3 scripts/demo/companion_ownership.py --kubeconfig "$PLAN_KUBECONFIG" \
  --context "$PLAN_CONTEXT" > "$work/ownership.json"
```

Review the plan. Then apply it with the write identity, which needs `patch` on
the twelve named objects and nothing else for this step. `$RECORD` is a private
directory kept with the deployment record:

```bash
bash scripts/demo/apply-ownership-patches.sh "$work/ownership.json" \
  "$APPLY_KUBECONFIG" "$APPLY_CONTEXT" "$RECORD/adopted.json"
python3 scripts/demo/companion_ownership.py --kubeconfig "$PLAN_KUBECONFIG" \
  --context "$PLAN_CONTEXT" | jq -e 'all(.[]; .patch == [])'
```

The script appends each patch that succeeds to `adopted.json`. That file records
exactly what was added to every object and is the only input that de-adoption
accepts. Never edit it, replace it or write it by hand.

The script refuses a plan that contains anything other than guarded label and
annotation changes on the four allowlisted kinds, and stops at the first failed
patch. A patch fails without a write when its object changed after planning. To
retry, plan again into a new file, review it and apply it with the same
`adopted.json`. A fresh plan has an empty patch for each object that is already
adopted, so its entry in the record stays as first written. The script refuses a
plan that would patch an object the record already holds. If the script is
interrupted, compare the planner output with the record before continuing: an
object with an empty patch that the record does not list, and that was not owned
before the first plan, was patched without being recorded.

The second command must exit 0: an adopted object needs no further patch. Pass
`--namespace default` to both planner commands to limit them to the OCI stack, or
one OTel namespace at a time.

### De-adopt companion releases

Use this to roll back companion ownership, and to recover from a first companion
install that left failed revision 1. Stop automation first, as in
[If the handover fails](#if-the-handover-fails), and hold the deployment lock.
Repeat the steps for each namespace `$ns` to de-adopt. `$RECORD` is the directory
that holds `adopted.json`, written while the ownership patches were applied.

1. Remove only the stored release records. The listing prints names and statuses,
   not Secret contents. The objects of the release are not touched.

   ```bash
   kubectl --kubeconfig "$APPLY_KUBECONFIG" --context "$APPLY_CONTEXT" --namespace "$ns" \
     get secret --selector owner=helm,name=demo-resources \
     --output custom-columns=NAME:.metadata.name,STATUS:.metadata.labels.status
   kubectl --kubeconfig "$APPLY_KUBECONFIG" --context "$APPLY_CONTEXT" --namespace "$ns" \
     delete secret --selector owner=helm,name=demo-resources
   ```

2. Plan the metadata removal read-only and review it. It removes exactly the fields
   that the record says were added. It refuses an object with another UID, an
   ownership value that changed since adoption, and a record that names anything
   outside the allowlist.

   ```bash
   python3 scripts/demo/companion_ownership.py --kubeconfig "$PLAN_KUBECONFIG" \
     --context "$PLAN_CONTEXT" --namespace "$ns" \
     --de-adopt "$RECORD/adopted.json" > "$work/de-adoption-$ns.json"
   ```

3. Apply it and verify. The planner must then print only empty patches, and Helm
   must report that the release is not found.

   ```bash
   bash scripts/demo/apply-ownership-patches.sh "$work/de-adoption-$ns.json" \
     "$APPLY_KUBECONFIG" "$APPLY_CONTEXT"
   python3 scripts/demo/companion_ownership.py --kubeconfig "$PLAN_KUBECONFIG" \
     --context "$PLAN_CONTEXT" --namespace "$ns" \
     --de-adopt "$RECORD/adopted.json" | jq -e 'all(.[]; .patch == [])'
   helm --kubeconfig "$PLAN_KUBECONFIG" --kube-context "$PLAN_CONTEXT" \
     --namespace "$ns" status demo-resources
   ```

4. Compare each object with its source at the recorded configuration commit, from
   a clean checkout of that commit. Adoption changes metadata only, so no
   difference is expected. Apply a source only after reviewing a reported difference.

   ```bash
   python3 scripts/demo/companion_values.py > "$work/sources.json"
   jq -c --arg ns "$ns" '.companions[$ns].objects[]' "$work/sources.json" |
     while IFS= read -r object; do
       kubectl --kubeconfig "$APPLY_KUBECONFIG" --context "$APPLY_CONTEXT" --namespace "$ns" \
         diff --filename - <<<"$object" || echo "Review the difference above"
     done
   ```

   ```bash
   kubectl --kubeconfig "$APPLY_KUBECONFIG" --context "$APPLY_CONTEXT" --namespace "$ns" \
     apply --filename reviewed-object.json
   ```

Step 1 needs `list` and `delete` on Secrets in the namespace. Kubernetes cannot
limit a list to Helm records, so review that access like the observer's. Steps 2
to 4 need named `get` and `patch` on the allowlisted objects. For a failed first
install, stop after step 1 when the ownership metadata should stay for a fresh
install plan.

The [kind rehearsal](helmfile-companions.md#rehearse-in-a-fresh-local-cluster) runs
the planner in both directions, the apply script, the record removal and the source
comparison. It makes one patch fail after others succeeded and retries with a fresh
plan and the same record. It checks that the adoption patches change no UID, spec or payload,
that removing the release records changes no object, and that after de-adoption
every label and annotation equals its value before adoption, the planner proposes
the original adoption again and each source manifest shows no difference. OKE
authentication and the production RBAC for these commands are not rehearsed.

Verified initial adoption unlocks guarded automation. Final acceptance requires
successful scheduled refresh and reviewed Renovate chart update, a healthy no-op
apply, at least 24 hours of observation, and verified documentation/release
procedures. Post-adoption refactors then merge before core assets and legacy
fingerprint state are retired.

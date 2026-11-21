<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Production access for daily hosted demo updates

When enabled, the 13:00 UTC job evaluates five components. Both Jaeger releases refresh their
images daily. OpenSearch and Dashboards, the OTel application and OCI monitoring
apply reviewed input changes. Scheduled OpenSearch upgrades use the existing
PVC and treat generated trace history as disposable. The separate manual recovery workflow can create a tested backup.

## Current read-only access

The dedicated `jaeger-demo-scheduled` OCI identity authenticates with an API
signing key. Its IAM policy permits access to the existing demo cluster only.
Four Kubernetes RoleBindings allow it to inspect workload and readiness objects
in `default`, `jaeger`, `opensearch` and `otel-demo`. A separate ClusterRoleBinding
lets it look up those four namespaces by name. These bindings grant no access to
Secrets, Pod logs or exec, and no workload writes.

On November 18, 2026, maintainers authenticated as the scheduled identity and
verified the expected cluster and four namespace identities. The Kubernetes
authorization API returned 208 expected allows and 180 expected denials. The
identity also completed 18 direct reads of non-Secret objects and two bounded
watches.

The `demo-scheduled` GitHub environment holds the six OCI connection settings.
It is restricted to `main` and has no per-run reviewer. The
`DEMO_SCHEDULE_ENABLED` switch remains off. Manual deployments continue to use
the reviewer-gated `demo-apply` environment.

## Deployment access awaiting approval

The GitHub scheduled credential signals one named request through the [protected daily update protocol](daily-update-request.md). The independent executor owns the application write access. Its review covers candidate and rollback objects, all six Helm histories, functional checks and privileged monitoring hooks. Helm Secret GET/LIST in the four release namespaces can expose unrelated AI and TLS credentials to that executor. The Prometheus operator has cluster-wide Secret access. Protect the executor code, bundle custody, infrastructure, effective RBAC and accepted operations independently before the write grant and schedule activation.

Scheduled API permissions exclude snapshot operations, PVC or PV mutation, restore
Pod/config creation and cleanup, and OCI backup, block-volume, compute and IAM
administration. Existing production workloads still need their normal resources.
This schedule has no need for a second OpenSearch instance, new worker or backup
capacity.

## Tested inventory

The [chart and script matrix](all-five-chart-access.json) contains object identities and static verbs. Four chart archives matched their package index SHA-256 digests. Helm 4.3.2 rendered the configured chart upgrades targeting Kubernetes 1.36.1 at configuration `df261c2e025ac3b102d17fda170b09972fa498f9`. Two matching Jaeger release renders and their supplemental identities are included. The rows include candidate revision Secret examples. Verify actual API operations in rehearsal before preparing a grant. Selection and chart pins come from the [frozen reconciler](https://github.com/jaegertracing/jaeger-demo/blob/df261c2e025ac3b102d17fda170b09972fa498f9/scripts/demo/deploy.py). That commit is the baseline at which all six releases were first rendered. Records derived again later are listed under `rederived` in the matrix, with the chart archive and the SHA-256 of the values file that produced them: the OTel application records now come from `opentelemetry-demo` 0.42.7. `python3 scripts/demo/access_matrix.py` repeats the comparison for the configuration at any commit and runs in CI.

| Component and release | Existing objects and transaction |
| --- | --- |
| OCI Jaeger, `default/jaeger` | 10 chart objects plus trace-generator config/deployment and two ingresses. Named GET/PATCH, rollout reads and generator restart. |
| OTel Jaeger, `jaeger/jaeger` | 12 chart objects. Named GET/PATCH, including the existing Helm-owned HotROD Deployment. Scheduled upgrades preserve that Deployment in place. The script also patches the query Service, trace generator and three ingresses, one each in `jaeger`, `opensearch` and `otel-demo`. |
| OpenSearch and Dashboards, two `opensearch` releases | Five chart objects each. StatefulSet, disruption budget, config and services for OpenSearch. Deployment, config, service, service account and named RoleBinding for Dashboards. Upgrade OpenSearch in place, wait for readiness, then upgrade Dashboards. Read back both versions and verify a newly ingested trace. |
| OTel application, `otel-demo/otel-demo` | 50 chart objects including 21 Deployments, one DaemonSet, 20 Services, four ConfigMaps, two service accounts and the existing `otel-collector` ClusterRole/ClusterRoleBinding. The role and its binding grant approved collector reads to `otel-demo/otel-collector`. |
| OCI monitoring, `default/prometheus` | 129 chart objects including 12 hook objects, six ClusterRoles, six ClusterRoleBindings, both admission webhook configurations, five kube-system Services, operator CRs and three application Secrets. Two additional named Services are applied by the shell script. The CRD upgrade Job manages ten monitoring CRDs and runs on pre-upgrade and pre-rollback. |

Use the actual current, candidate **and rollback** manifests and hooks to authorize their union before each chart change. A client render lacks live discovery, Helm lookup results and the previous chart version. Only compare names, kinds and scope from this matrix for generated Secrets. Preserve live data, webhook CA bundles, Helm owner labels, Service selectors/addresses, release identities, PV and PVC bindings. Apply chart-specific verification to any new, removed or renamed object before granting its lifecycle verbs.

## API operations and limits

| Purpose | Scope and verbs |
| --- | --- |
| Deployment and health reads | GET the four named namespaces, named render objects and Services. GET/LIST/WATCH workload rollout and readiness kinds used by Helm and kubectl, including Pods, Deployments, ReplicaSets, DaemonSets, StatefulSets, Jobs, Services, Endpoints, and the specific CR kinds in the selected bundle. LIST events, Ingresses, Certificates and EndpointSlices where the verifier requires them. `kubectl cluster-info` needs `kube-system` Service LIST. Failure diagnostics read Pod logs via `pods/log` in affected namespaces. Redact before sharing diagnostic output. |
| Steady release updates | GET/PATCH the existing named chart and supplemental objects in the matrix. The five named kube-system Services require a separate namespace Role. Each scheduled success merge-patches only its key in the existing `default/jaeger-demo-deployment-revisions` ConfigMap. Helm hooks need exact CREATE/GET/DELETE lifecycle and their Job wait/list/watch. Add CREATE or DELETE for a chart difference and its rollback only after inspecting the object delta. Deny automatic namespace creation, broad uninstall/reinstall and volume expansion in this job. |
| Helm history | GET/LIST Secrets in the four release namespaces with the standard Helm Secret driver. These reads also expose other Secret data there. Restrict CREATE/UPDATE/DELETE with a fail-closed admission rule to canonical positive revision names and matching Helm type/owner/release/version labels. Release mapping is `default={jaeger,prometheus}`, `jaeger={jaeger}`, `opensearch={opensearch,opensearch-dashboards}`, `otel-demo={otel-demo}`. Validate CREATE on new object, UPDATE on old and new and DELETE on old. Include separately validated chart-owned Secret updates, such as the three named monitoring Secrets. Keep TLS and AI Secrets outside allowed writes. Read-only storage isolation requires a separate design. |
| OpenSearch version and trace checks | GET the live `opensearch/opensearch-cluster-single-0` Pod and its named source PVC to compare the mounted claim name, UID and bound PV name before and after the upgrade. The PV name is read from the PVC. Execute the database version request in that same Pod through `pods/exec`. Check Dashboards version at its public status endpoint. In `jaeger`, read `jaeger-collector` and `jaeger-query` Services and the Pods they select. Port-forward to those Pods through `pods/portforward` to send one OTLP trace and query its ID. Verify the GET or CREATE verbs used by the pinned kubectl transport. Bound the OpenSearch exec target by name and restrict the dynamic Jaeger port-forward targets and ports through the approved enforcement mechanism. Exec can run commands beyond the version probe, and a namespace-wide port-forward can reach unrelated Pods. |
| Chart security objects and hooks | Separate named RBAC, webhook and hook objects from ordinary Helm patch rights. The monitoring hook service accounts use Kubernetes authorization for CRD updates, webhook changes and the hook Secret. Its admission ClusterRole permits changes to all validating/mutating webhook configurations under chart defaults. The regular operator ClusterRole has all verbs on cluster-wide Secrets, ConfigMaps and StatefulSets. Bind existing named roles only to their exact reviewed service accounts. Constrain admission target/CA handling, hook and controller images, commands, volumes and credentials to the approved bundle. Keep the CI actor outside policy administration and arbitrary impersonation. |

Kubernetes RBAC `resourceNames` can select existing objects for named actions. It cannot restrict a top-level CREATE to a name prefix. Role/ClusterRole changes and bindings trigger additional privilege-escalation checks. Some monitoring roles have permissions the deployer should never receive directly. A protected executor can perform those reviewed transactions. If granting named `bind` or `escalate` to do this in-cluster, pair it with independent, fail-closed content and subject enforcement and test rollback to the prior approved definitions. Validate guarded CREATE, PATCH/UPDATE, DELETE and subresource operations with real admission requests. RBAC authorization checks alone cannot demonstrate the guard. [Kubernetes authorization](https://kubernetes.io/docs/reference/access-authn-authz/rbac/), [validating admission policy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/), [OKE controller support](https://docs.oracle.com/en-us/iaas/Content/ContEng/Reference/contengadmissioncontrollers.htm)

## Monitoring rollback target

Helm runs the hooks stored in the rollback target. On November 19 at 01:55 UTC the separately approved operator upgrade established `default/prometheus` revision 7 at configuration `ce3cf3a` as the first verified digest-pinned rollback target. All three hook Jobs succeeded and the ten CRDs, thirteen Service bindings and four monitoring Secret payloads were preserved. Revisions 5 and 6 retain their tag-only pre-rollback CRD Job and require an operator decision before use.

Renovate tracks all three image digests under the seven-day release age and
maintainer review rules. The chart defaults to BusyBox `latest`. At initial
pinning, `latest` and `1.38.0` resolved to the same image index. The numeric
tag lets Renovate propose version and digest updates for BusyBox.

Renovate discovers the selected kubectl and webhook-certgen digests. Its Docker
datasource lacks release timestamps for registry.k8s.io and GHCR, which can
leave digest updates pending. For a manual update, cite the publisher or registry
record that ties the exact image index digest to its publication time in the PR.
Wait seven days from that time. Keep the current pin if the date cannot be verified.

Review tag changes with their source. The chart test fails when its default
BusyBox reference or webhook-certgen tag changes. The chart derives its kubectl
tag from the Kubernetes version. When the cluster version changes, update the
test target and review the kubectl tag and digest together.

## Rehearsal and activation

1. Compare the recorded six Helm revisions to live release and retained manifests. At this checkpoint the revisions were 25, 14, 14, 14, 11 and 6 for OCI Jaeger, OTel Jaeger, OpenSearch, Dashboards, OTel application and monitoring. An upgrade plus rollback could require 26/27, 15/16, 15/16, 15/16, 12/13 and 7/8 respectively. Pruning may delete older retained records. Two Jaeger upgrades use `--atomic`. The other four releases need explicit failure inspection and targeted rollback. A successful OpenSearch engine upgrade followed by a failed Dashboards upgrade needs component-level recovery. Helm rollback does not downgrade migrated storage or restore custom-resource data.
2. Rehearse the proposed rights using isolated Kubernetes credentials and fixtures. Exercise the monitoring pre-upgrade and pre-rollback hook resources and chart-owned Secret handling, not only a Jaeger upgrade. Test a denied unrelated Secret mutation and unexpected privileged Job/RBAC/webhook spec. Verify the actual requester, preparer, executor and recovery operator with their distinct permissions on OKE before activation. Keep recovery/operator access independent and remove broad RBAC before relaxing any admission guard.
3. Verify sufficient headroom for the existing OpenSearch StatefulSet, preserve the live PVC binding, and compare the actual OpenSearch and Dashboards versions and a newly written trace after upgrade. Scheduling omits backups. An incompatible storage change can make old synthetic traces unavailable. If repair requires a destructive storage reset, pause the writers and seek approval for the exact affected resources. Historical trace preservation is available under the separately authorized [manual recovery procedure](../examples/otel-demo/OPENSEARCH_RECOVERY.md).
4. Before the first scheduled write, capture all six releases and supplemental state, check that all five components can run or give a clear reason for each skip, and verify prior/candidate/rollback scope. After the first run inspect each release and component fingerprint plus fresh write/read traces, finite metrics under observed traffic, public UI, TLS, Grafana and Dashboards. On failure drain new writers, preserve successful siblings and restore only the affected release and its fingerprint after inspection.

The [Helm history rehearsal](../scripts/demo/rehearsals/helm-history/README.md) ran Helm 4.3.2 against a disposable Kubernetes API server for all six release identities. Each fixture pruned an old revision, failed an upgrade on an immutable ConfigMap, rolled back to a new deployed revision, and retained the ConfigMap's data and UID. Eleven negative checks covered the Secret admission rules and the writer's RBAC. The [protected monitoring rehearsal](../scripts/demo/rehearsals/monitoring-writer/README.md) exercised the pinned hook upgrade and rollback behind two operator-prepared, one-time requests. The restricted requester had no Helm Secret access. Production executor permissions, full-chart verification and storage binding continuity still require separate evidence. The production write grant, activation and first unattended run remain pending.

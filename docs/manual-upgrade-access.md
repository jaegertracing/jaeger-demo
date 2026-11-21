<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Manual upgrade access for monitoring, OpenSearch and the OTel demo

A change that alters a release's objects needs a manual upgrade through
**Deploy Jaeger Demo to OKE** before a scheduled run accepts it
([releases.md](releases.md#when-a-change-alters-a-releases-objects)). The
`demo-apply` identity used by that job was granted only the two Jaeger releases
during the Stage 1 handover. This page describes the guarded package that also
lets it upgrade `default/prometheus`, `opensearch/opensearch`,
`opensearch/opensearch-dashboards` and `otel-demo/otel-demo`.
[#87](https://github.com/jaegertracing/jaeger-demo/issues/87) records the decision.

## Why the grants need admission guards

A monitoring upgrade runs the chart's hooks. They delete and recreate hook
ClusterRoles and bindings whose rules update CRDs and webhooks. Kubernetes lets a
caller create or change a role only when it holds every permission in it, or has
`escalate`, and lets it bind a role only with `bind`
([RBAC escalation prevention](https://kubernetes.io/docs/reference/access-authn-authz/rbac/#privilege-escalation-prevention-and-bootstrapping)).
Without content checks, those verbs, together with patches to workloads that run
under powerful service accounts, make the credential equivalent to cluster-admin.
For example, the prometheus-operator ClusterRole has every verb on cluster-wide
Secrets, and node-exporter runs with host access.

[`ops/executor/manual_upgrade.py`](../ops/executor/manual_upgrade.py) therefore
renders two lists from the reviewed commit:

| File | Contents |
| --- | --- |
| `guards.json` | ValidatingAdmissionPolicies that match only the `demo-apply` Kubernetes user, fail closed, and deny every write unless `default/jaeger-demo-deployment-revisions` is held by a manual run (`manual/<run>/<attempt>`). Each object that carries privilege must keep its current security surface or take exactly the one in this commit's server-defaulted render: Role and ClusterRole rules, binding subjects and role references, the pod spec of every Deployment, DaemonSet and StatefulSet, the `Prometheus` and `Alertmanager` specs the operator turns into pods, Service type, `externalIPs` and `externalName`, and admission webhooks apart from the rotated `caBundle`. A StatefulSet's storage identity (claim templates, PVC retention policy, selector, service name and replica count) must stay as it is, whatever the pod spec does, because a `Delete` retention policy with fewer replicas lets the controller delete data volumes. The twelve monitoring hooks must match the reviewed hooks exactly, as for the executor. Secret writes must be canonical Helm history records of the six releases, except the three chart-owned monitoring Secrets. |
| `grants.json` | Roles bound to the same user: named `get,patch` on the reviewed matrix records of the three components, named `escalate` and `bind` on the chart's existing roles, the hook lifecycle verbs, history Secret writes in `default`, `opensearch` and `otel-demo`, and the reads Helm's `--wait` needs. No namespace, storage, CRD, exec or port-forward rights. |

The executor's own policies match only its service account and a running daily
request, so the two packages do not overlap. Their object names share no prefix
(`jaeger-demo-manual-*`).

The policies match every request by the user, so they also apply to its Stage 1
rights. Every `demo-apply` workflow (deploy, OpenSearch recovery and volume
expansion) takes the manual lock first. The Secret policy admits canonical history
records of both Jaeger releases, and the hook inventory and RBAC preservation
policies cover only the hooks' namespace and roles, so Jaeger upgrades and the
recovery script's ConfigMaps keep working.

## Render and review

Run from a clean checkout of the reviewed commit, with Helm 4.3.2, PyYAML and an
independently verified operator context. The user name is the `User` subject of
the existing Stage 1 apply bindings; it is an input, not a repository value.

```bash
python3 ops/executor/manual_upgrade.py --user "$demo_apply_user" \
  --kubeconfig "$operator_kubeconfig" --context "$context" \
  --cluster-uid "$verified_kube_system_uid" --output "$new_review_directory"
```

The script verifies the chart archives against `ops/executor/chart-archives.json`,
including archives passed with `--charts`,
renders the four releases as upgrades, and asks the API server to default a renamed
copy of each pinned object with a nonpersistent `create --dry-run=server`. It refuses
a dirty checkout, a rendered privileged object without a matrix record, and a
record the charts no longer render. `summary.json` records the commit, the archive
digests and the object counts. Review `grants.json` in full before installing it.

## Install, use and remove

1. Apply `guards.json` with `kubectl create -f`. The hook CRD archive is too large
   for a client-side apply annotation.
2. Apply `grants.json`. Grants never go in before their guards.
3. Check the boundary: `kubectl auth can-i --list --as "$demo_apply_user"` for each
   namespace, and one dry-run patch as the user that the lock policy denies.
4. Dispatch the manual upgrade from `main` at the same commit. The job takes the
   deployment lock, which is what the policies check.
5. For the next reviewed commit, delete `grants.json`, then `guards.json`, and
   install the new package in the same order. To roll back to an earlier commit,
   render the package from that commit.

Removing the grants returns `demo-apply` to its Stage 1 scope.

## Residual risk

- Named patches on Services (other than the pinned fields), ConfigMaps,
  PrometheusRules, ServiceMonitors, PodDisruptionBudgets and ServiceAccounts have
  no content pin. A ServiceMonitor can make Prometheus present Secrets from its
  namespace to a scrape target, and a ConfigMap can change collector or dashboard
  configuration.
- The three chart-owned monitoring Secrets (Grafana, Alertmanager configuration,
  scrape configuration) may change, including the Grafana administrator password
  and Alertmanager receivers.
- Helm reads history by listing Secrets, which returns every Secret in
  `default`, `opensearch` and `otel-demo`.
- OpenSearch recovery (`opensearch_recovery=required`) needs storage access this
  package does not grant. Use the separate recovery workflow or record a waiver.
- While the package is installed, the user cannot create or delete a ServiceAccount,
  ConfigMap, Role, RoleBinding or Job in `default` other than the reviewed hooks. A
  Jaeger chart version that adds such an object in `default` needs the package
  removed for its upgrade.

## Tests

`scripts/demo/manual_upgrade_test.py` checks that every write grant has a guard on
the same target, that every policy matches only the supplied user and requires the
manual lock, and that no name collides with the executor package. The `Monitoring
hooks and Helm history` check in `executor-ci.yml` renders the pinned charts,
installs the package on a disposable API server and, as the manual user, denies a
wildcard rule on an escalatable ClusterRole, image, service account, hostPath and
privileged changes to workloads, an OpenSearch PVC retention or replica change, Service `externalIPs`, a `Prometheus` service
account change, a binding subject change, a webhook policy change, a hook-named
binding to the user, a cluster-admin binding, arbitrary Secrets, an unreviewed
ConfigMap in `default`, and every write without a manual lock or parameter. It
admits metadata changes, a restored reviewed rule set, a webhook CA rotation, the
reviewed hooks and canonical history records. With a stand-in for the Stage 1
Roles, which are not in the repository, it also admits Jaeger history records in
`default` and `jaeger` and a recovery ConfigMap in `opensearch`. A real Helm upgrade under these grants is a separate
rehearsal.

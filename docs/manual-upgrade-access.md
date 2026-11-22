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
| `guards.json` | ValidatingAdmissionPolicies that match only the `demo-apply` Kubernetes user, fail closed, and deny every write unless `default/jaeger-demo-deployment-revisions` is held by a manual run (`manual/<run>/<attempt>`). Each object that carries privilege must keep its current security surface or take exactly the one in this commit's server-defaulted render: Role and ClusterRole rules, ClusterRole labels (an `aggregationRule` such as `view`'s copies the rules of every ClusterRole whose labels it selects), binding subjects and role references, the pod spec of every Deployment, DaemonSet and StatefulSet, the `Prometheus` and `Alertmanager` specs the operator turns into pods, Service type, `externalIPs` and `externalName`, and admission webhooks apart from the rotated `caBundle`. A StatefulSet's storage identity (claim templates, PVC retention policy, selector, service name and replica count) must stay as it is, whatever the pod spec does, because a `Delete` retention policy with fewer replicas lets the controller delete data volumes. The twelve monitoring hooks must match the reviewed hooks exactly, as for the executor, on update as well as create and delete. Secret writes must be canonical Helm history records of the six releases, except updates of the three chart-owned monitoring Secrets while the charts still render them (their named lock gate guards those). When the upgrade adds objects, creates are admitted only for those names (and the hooks), and each added privileged object must be created with exactly the reviewed surface. Deletes of the objects it drops need the lock; a dropped name is never admitted for a create, since no pin covers its content. |
| `probes.json` | Server dry-run probes for `manual_upgrade_verify.py`, one per pin, hook, hook inventory target, create gate, history namespace and lock gate. An added object is probed with a create, since it does not exist yet. |
| `grants.json` | Roles bound to the same user: named `get,patch` on the reviewed matrix records of the three components, named `escalate` on the chart's existing roles, named `bind` in each binding's own namespace (cluster scope only for ClusterRoleBindings), the hook lifecycle verbs, history Secret writes in `default`, `opensearch` and `otel-demo`, and the reads Helm's `--wait` needs. Named `patch` on the hooks, because Helm 4 creates them with server-side apply when the release uses it. When the upgrade changes the object set: `create` on the added objects' kinds in their namespaces (RBAC cannot name a created object) plus named `patch` for a server-side apply that creates, with create-time `escalate` where roles are added, and named `get,delete` on the objects it drops. No namespace, storage, CRD, exec or port-forward rights. |

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
renders the four releases as upgrades, compares the render with each release's
deployed manifest (`helm get manifest`) to find the objects the upgrade adds and
drops, and asks the API server to default a renamed
copy of each pinned object with a nonpersistent `create --dry-run=server`. Helm 4
installs releases with server-side apply and upgrades them with the method they
last used. A server-side apply merges into the live object, so it can keep fields
other managers own or another list order. For each pinned object last applied
that way, the script also runs a nonpersistent `apply --server-side --dry-run=server`
of the render under the same field manager. The pin then admits either result. It refuses
a dirty checkout and a rendered object without a matrix record. Records the
charts no longer render, which the matrix keeps while a rollback target needs them,
get no grant, pin or lock gate; if the release still has such an object, it counts
as dropped, and the upgrade deletes it. Hooks are defaulted under renamed copies too,
because a failed hook stays in the cluster (the chart deletes hooks only on
success or before recreating them) and must not block the retry package. A dropped object of a kind the
components do not render, or outside their namespaces, is refused for an operator.
`summary.json` records the commit, the archive digests, the object counts and the
added and dropped objects. Review `grants.json` in full before installing it.

## Install, use and remove

Admission policies and RBAC changes reach the API server asynchronously, so
creating the guards first does not by itself prove they enforce before the grants
do. Lock-only gates also return the same lock message as the content pins, so a
lock denial does not show that a pin is loaded.
[`ops/executor/manual_upgrade_verify.py`](../ops/executor/manual_upgrade_verify.py)
checks that every guard in `guards.json` is installed unchanged and that none of
the twelve hooks is left from a failed run, then runs the server dry-run probes in `probes.json` as the user. Each probe must be denied with
its own policy's message. Close the window procedurally, with the `demo-apply`
environment reviewer as the gate:

1. Install only a package rendered from a commit whose real Helm upgrade rehearsal
   of the component under these grants has passed
   ([#93](https://github.com/jaegertracing/jaeger-demo/pull/93)); the admission
   probes alone do not show that Helm can complete the upgrade. Confirm that no
   Deploy, OpenSearch recovery or volume expansion run is in
   progress or waiting for approval, and that `default/jaeger-demo-deployment-revisions`
   exists and is not held by a `manual/` run. Read the live Stage 1 Roles and
   bindings of the `demo-apply` user and stop if they allow creating RoleBindings
   or ClusterRoleBindings outside the hook names, since this package grants `bind`
   on chart roles. Approve no `demo-apply` deployment until step 6 passes. Delete any hook left by a failed monitoring upgrade as an
   administrator: Helm deletes it before recreating it, and the guards check that
   delete against this commit's hook, so a leftover from an earlier chart version
   would block the retry.
2. Apply `guards.json` with `kubectl create -f`. The hook CRD archive is too large
   for a client-side apply annotation.
3. Apply `grants.json`. Grants never go in before their guards.
4. With no manual holder, run the verifier with `--phase unlocked`. Every lock gate
   must deny a label patch.
5. Set a controlled holder, for example
   `kubectl -n default annotate configmap jaeger-demo-deployment-revisions jaegertracing.io/deployment-lock-holder=manual/0/0`,
   outside the daily preparation and run (from 12:20 UTC until the 13:00 UTC run
   finishes), and run the verifier with
   `--phase held`. Every pin, hook, hook inventory target and history namespace
   must deny its content probe. Then remove the annotation.
6. Check `kubectl auth can-i --list --as "$demo_apply_user"` in each namespace,
   then approve the manual upgrade from `main` at the same commit. The job takes
   the deployment lock, which is what the policies check.
7. For the next reviewed commit, repeat step 1, delete `grants.json`, then
   `guards.json`, and install the new package from step 2. To roll back to an
   earlier commit, render the package from that commit.

```bash
python3 ops/executor/manual_upgrade_verify.py --package "$review_directory" \
  --user "$demo_apply_user" --kubeconfig "$operator_kubeconfig" --context "$context" \
  --phase unlocked
```

The RBAC preservation policies for hook roles have no probe: they apply only while
a hook role exists during a hook run.

Removing the grants returns `demo-apply` to its Stage 1 scope.

Keep the checkpoint ConfigMap in place while the package is installed. The API
server resolves a binding's parameter before its match conditions, so without the
checkpoint every guard denies the writes it matches from every client, not only
from `demo-apply`: Secret writes in every namespace, hook-kind creates in
`default` and updates to the pinned objects. The bindings exclude the checkpoint
itself, so an operator can recreate it.

## What the guards do and do not contain

The guards make sure that the approved run applies this commit's reviewed render
and nothing else. They do not contain a stolen `demo-apply` credential:

- The deployment lock coordinates with the daily run; it is not an authorization
  control. `demo-apply` takes the manual lock itself (`manual-acquire`), so anyone
  holding the credential can take it too.
- Stage 1 lets the credential patch `deployments/jaeger` in `default` with no
  content check (below). Run as the prometheus-operator service account, that
  Deployment can read every Secret in the cluster and create StatefulSets, which
  amounts to cluster-admin and node root, with or without this package.

The `demo-apply` environment reviewer and the reviewed code on `main` are what keep
the credential to its intended use.

## Residual risk

- Objects an upgrade adds are created with the same content limits as patches:
  privileged kinds take the reviewed surface, and other kinds are limited by name
  only.
- Named patches on Services (other than the pinned fields), ConfigMaps,
  PrometheusRules, ServiceMonitors, PodDisruptionBudgets and ServiceAccounts have
  no content pin. A ServiceMonitor can make Prometheus present Secrets from its
  namespace to a scrape target, and a ConfigMap can change collector or dashboard
  configuration. The monitoring values leave the chart default
  `prometheus.prometheusSpec.arbitraryFSAccessThroughSMs: false`, so a patched
  ServiceMonitor can also point `bearerTokenFile` or TLS file paths at files in the
  Prometheus container, including its service account token, and relabel
  `__address__` to send them to any host.
- The three chart-owned monitoring Secrets (Grafana, Alertmanager configuration,
  scrape configuration) may change, including the Grafana administrator password
  and Alertmanager receivers.
- Helm reads history by listing Secrets, which returns every Secret in
  `default`, `opensearch` and `otel-demo`.
- OpenSearch recovery (`opensearch_recovery=required`) needs storage access this
  package does not grant. Use the separate recovery workflow or record a waiver.
- Stage 1 already lets the user patch `deployments/jaeger` in `default`
  ([cutover.md](cutover.md)). That Deployment shares the namespace with the
  prometheus-operator service account, so it can be switched to run as that account.
  This package does not widen or close that path. It is an accepted risk: every
  `demo-apply` run needs reviewer approval and runs reviewed code from `main`
  (see above).
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
wildcard rule or an aggregation label on an escalatable ClusterRole, image, service account, hostPath and
added-capability changes to workloads, an OpenSearch PVC retention or replica change, Service `externalIPs`, a `Prometheus` service
account change, a binding subject change, a webhook policy change, a hook-named
binding to the user, a cluster-admin binding, arbitrary Secrets, an unreviewed
ConfigMap in `default`, and every write without a manual lock or parameter. It
admits metadata changes, a restored reviewed rule set, a webhook CA rotation, the
reviewed hooks and canonical history records. It renders the package with a failed
hook Job from an earlier chart version left in the cluster, shows that the user
cannot delete it and the verifier refuses until an administrator does, and runs `manual_upgrade_verify.py` in both phases
against the installed package, skipping lock gates of kinds the fixture never
creates. With a stand-in for the Stage 1
Roles, which are not in the repository, it also admits Jaeger history records in
`default` and `jaeger` and a recovery ConfigMap in `opensearch`. For a release
that lacks three rendered objects and carries two dropped ones, it admits
Helm-style creates of the added Deployment, ConfigMap and ClusterRole and deletes
of the dropped objects and a create of an added chart Secret. It denies an added
Deployment under another service account, a ConfigMap the commit does not add, a
delete of a kept object, a retired ClusterRoleBinding recreated with the operator
role, and an update of a dropped Secret. `manual_upgrade_test.py` also pins the
SHA-256 of every executor permission stage to its value on `main`. A real
Helm upgrade under these grants is a separate rehearsal.

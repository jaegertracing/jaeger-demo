<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Supplemental resource adoption

The local `demo-resources` chart defines one release in each of the four existing namespaces. The chart reads its allowlist from its own package. A render fails if a resource is missing, duplicated, of the wrong API version, outside the namespace, or outside the allowlist. It also fails if a source manifest carries any `helm.sh/hook` annotation, because Helm would not own that object and would delete and recreate it before each run. Every rendered object carries the Helm keep annotation. The chart takes the same Ingress, Service, Deployment and generator inputs as the extracted scripts. A ConfigMap embeds the exact script bytes for each generator. [Allowlist](../charts/demo-resources/allowlist.yaml)

## Render the public sources

Run these commands from the repository root with Python, PyYAML and the locked adoption tools installed. Write the generated inputs to a private temporary directory. The companion state is separate from the six application releases so operators can review the ownership transition independently.

```bash
work=$(mktemp -d)
python3 scripts/demo/companion_values.py > "$work/companions.json"
python3 scripts/demo/helmfile_tools.py run -- helmfile -f helmfile-resources.yaml.gotmpl \
  --state-values-file "$work/companions.json" template --skip-deps > "$work/companions.yaml"
```

## Inspect existing ownership

The planner performs twelve named GETs, or fewer with `--namespace`. It rejects controller-owned objects, old or partial Helm ownership, conflicting retention, missing identities and deletion in progress. An already owned object returns an empty patch. Proposed changes modify top-level metadata only. The UID and resource version in each JSON Patch protect against replacement or concurrent change. A stale patch requires a fresh check of the object and its proposed inputs.

It stays read-only and needs no credentials of its own. In production it runs with the deployer kubeconfig of the [operator shell](cutover.md#operator-shell): the cluster administrator's kubeconfig with every request impersonating the manual deployer identity, written by `scripts/demo/deployer_kubeconfig.py`, so it has exactly the named `get` access of [ops/rbac/deployer.yaml](../ops/rbac/deployer.yaml). `$KUBECONFIG` and `$CONTEXT` are that file and its only context, and `$RECORD` is the runbook's private record directory:

```bash
python3 scripts/demo/companion_ownership.py --kubeconfig "$KUBECONFIG" --context "$CONTEXT" \
  --namespace "$ns" > "$RECORD/ownership-$ns.json"
```

Apply a reviewed plan with `scripts/demo/apply-ownership-patches.sh`, which records each applied patch and needs only `patch` on the named objects, and reverse that record with the planner's `--de-adopt` option, through the same kubeconfig. The exact commands, one namespace at a time and in the adoption order, are in [Apply the ownership patches](cutover.md#apply-the-ownership-patches) and [De-adopt companion releases](cutover.md#de-adopt-companion-releases); the [runbook rehearsal](cutover.md#how-the-commands-run-and-how-they-are-rehearsed) runs them verbatim as a user bound to the deployer role.

Review this ownership plan alongside the adoption render before applying the ownership patches. The production decision also requires full configuration and live-state comparison, including chart inputs, Services, pod templates and rollback state. Keep raw production inventory and release data in private storage.

## Rehearse in a fresh local cluster

The test requires a fresh loopback kind cluster. The cluster admin builds the fixture and applies the ownership patches. Helm and Helmfile run as a user bound to the committed deployer roles in [deployer access](deployer-access.md), so the first install, the applies and the chart upgrade need only what those roles grant. The access model lists the companions' Helm records and the first install among the [Helmfile path](deployer-access.md#helmfile-path) commands. It seeds all twelve objects as kubectl-managed resources and restarts both generators, as the extracted scripts do. It checks foreign ownership rejection, runs the planner with named GET permissions, applies metadata patches, then runs Helmfile twice. It compares each complete spec, ConfigMap payload and UID across adoption. The second apply must preserve every Helm revision. A final chart update drops one Service and verifies that its UID and spec survive.

The direct `helmfile apply` of all four namespaces in this test checks the chart and the state file. It reads the working tree and is not a production procedure. As decided in [#99](https://github.com/jaegertracing/jaeger-demo/issues/99), the first production install of a companion release is a manual dispatch of the deploy workflow for one namespace, after its ownership patches, running a plain `helm install` that is not atomic and has no `--take-ownership`. Before it, the [Helmfile engine](releases.md#helmfile-engine) reads every allowlisted object of that namespace and refuses the run if one is missing or lacks the ownership the patches add, so Helm cannot create a missing object with a new UID. The arguments come from `install_args()` (server-side apply, `--wait=legacy` with a 300 second timeout, no rollback on failure) in `scripts/demo/release_phases.py`, which the rehearsal also uses. `release_phases.run` selects that install only for an absent companion that the caller names as the single selected release, and it refuses every other absent release. Helm's `upgrade --install` cannot require an absent release, whereas `install` fails on any stored record of that name and refuses an existing object that lacks this release's ownership metadata.

The test runs that install first, on objects that only kubectl and the ownership patches have written, as in production. A `helmfile apply` of the absent release and an install over an object without this release's ownership metadata are each refused with no stored release and no change to any UID, spec, payload, label or annotation. For an absent release the apply's diff fails first because of `--allow-unreleased=false`; `--install=false` in the sync arguments also stops Helmfile's `upgrade --install` from installing it. The install must then produce deployed revision 1 with those same fields unchanged. A second install is refused, and a `helmfile apply` of that release adds no revision. The kind run does this in the `otel-demo` and `jaeger` namespaces. Envtest covers `otel-demo` only, because it runs no workload controllers. The direct apply that follows must find nothing to change in those namespaces.

The last step rehearses recovery from a failed first install in `opensearch`. It marks the stored revision 1 as failed, confirms that an install is refused, removes only that stored release record and confirms that no object changed. The same install sequence then adopts the unchanged objects again. The test never runs `helm uninstall`.

The test applies the adoption plan with the documented script. One patch is made stale so that the first run stops partway, and a retry with a fresh plan must complete the same record without changing its earlier entries. At the end it de-adopts the first-contact namespaces with the documented commands. Every label and annotation must equal its value before adoption, the planner must propose the original adoption again, and each source manifest must show no difference.

```bash
kind create cluster --name jaeger-demo-adoption-fixture --kubeconfig "$work/kind.yaml" \
  --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
python3 scripts/demo/helmfile_tools.py run -- python3 scripts/demo/rehearsals/companion-adoption/exercise.py \
  --kubeconfig "$work/kind.yaml" --context kind-jaeger-demo-adoption-fixture
kind delete cluster --name jaeger-demo-adoption-fixture --kubeconfig "$work/kind.yaml"
```

The same test can use an explicitly isolated envtest API server with context `fixture` to check Kubernetes defaults, Helm behavior and ownership. Envtest runs the API server and etcd. The kind rehearsal additionally runs workload controllers and Pods. [kind 0.33.0 and its Kubernetes 1.37.0 node image](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0)

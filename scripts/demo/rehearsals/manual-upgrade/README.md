<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Manual upgrade as demo-apply under the guarded package

[`rehearse.py`](rehearse.py) upgrades one component with real Helm, hooks,
controllers and `--wait` as the impersonated manual user, on a disposable
single-node kind cluster. It runs in CI as the `Manual upgrade as demo-apply`
checks of `executor-ci.yml`. [Manual upgrade access](../../../../docs/manual-upgrade-access.md)
describes the package.

1. The fixture administrator installs the component at its
   [baseline](baselines.json): the recorded commit's chart, fetched from the chart
   repository and checked against its SHA-256, with that commit's values. A null
   commit installs this checkout's own pins. `--apply server` or `client` sets the
   baseline's Helm apply method, which the manual job's upgrade (`--server-side
   auto`, Helm's default) reuses. CI runs both.
2. It renders the package for that component from this checkout with
   `ops/executor/manual_upgrade.py`, checks that the objects the upgrade adds and
   drops are the recorded ones, and installs the guards before the grants.
3. `manual_upgrade_verify.py` proves enforcement without a manual holder and
   with a controlled one.
4. Holding the lock as `manual/1/1`, the user runs the manual job's commands. They
   use the deploy scripts' Helm arguments, with the verified archives in place
   of repository names.
5. If a pin refuses the upgrade, the rehearsal prints how the live object, the
   reviewed render and the reviewed server-side apply differ in the pinned fields.
6. The rehearsal checks that every release is deployed one revision later, that
   added objects exist and dropped ones are gone, and that the OpenSearch claims
   keep their UIDs and volumes. It also checks that an upgrade without the lock
   and an upgrade that changes a pinned pod spec are refused.

When a chart pin changes, set the component's baseline to the previous pin and
record the objects the upgrade adds and drops. Then this check rehearses the
production manual upgrade before it merges.

## Reproduce

Use Python 3 with PyYAML, Helm 4.3.2, kubectl and kind with a Linux container
runtime. From the repository root, with the verified archives from
`scheduled_inputs.charts_for` in `$charts`:

```bash
kind create cluster --name jaeger-demo-manual-upgrade --kubeconfig "$work/kubeconfig" --wait 5m \
  --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
python3 scripts/demo/rehearsals/manual-upgrade/rehearse.py --component otel-demo --charts "$charts" \
  --kubeconfig "$work/kubeconfig" --context kind-jaeger-demo-manual-upgrade --receipt "$work/receipt.json"
kind delete cluster --name jaeger-demo-manual-upgrade --kubeconfig "$work/kubeconfig"
```

Fetch the baseline commit first if the clone is shallow. Use a new cluster for
each run.

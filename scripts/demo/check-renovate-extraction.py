#!/usr/bin/env python3

# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

"""Regression check against Renovate's actual extraction, not its regex syntax."""

import json
import re
import sys


expected = {
    ("examples/oci/deploy-all.sh", "kube-prometheus-stack"),
    ("examples/oci/jaeger-values.yaml", "jaegertracing/example-hotrod"),
    ("examples/oci/load-generator/load-generator.yaml", "python"),
    ("examples/otel-demo/deploy-all.sh", "opensearch"),
    ("examples/otel-demo/deploy-all.sh", "opensearch-dashboards"),
    ("examples/otel-demo/deploy-all.sh", "opentelemetry-demo"),
    ("examples/otel-demo/jaeger-values.yaml", "jaegertracing/jaeger-es-index-cleaner"),
    ("examples/otel-demo/otel-demo-values.yaml", "docker.io/busybox"),
    ("examples/otel-demo/load-generator.yaml", "python"),
    ("scripts/demo/jaeger-chart.sh", "https://github.com/jaegertracing/helm-charts.git"),
    ("scripts/utils/lint-requirements.txt", "ruff"),
}
expected.update(("helmfile.yaml", chart) for chart in (
    "kube-prometheus-stack", "opensearch", "opensearch-dashboards", "opentelemetry-demo"))
monitoring_hooks = {
    "docker.io/library/busybox",
    "registry.k8s.io/kubectl",
    "ghcr.io/jkroepke/kube-webhook-certgen",
}
monitoring_values = "examples/oci/monitoring-values.yaml"
expected.update((monitoring_values, image) for image in monitoring_hooks)
tool_lock = "scripts/demo/helmfile-tools.lock.json"
for tool in ("helm/helm", "helmfile/helmfile", "databus23/helm-diff", "yannh/kubeconform"):
    expected.add((tool_lock, tool))
found = set()
with open(sys.argv[1], encoding="utf-8") as source:
    for line in source:
        entry = json.loads(line)
        if entry.get("msg") == "Extracted dependencies":
            for files in entry["packageFiles"].values():
                for package in files:
                    for dep in package["deps"]:
                        file, image = package["packageFile"], dep["depName"]
                        if file == monitoring_values and image in monitoring_hooks:
                            if dep.get("datasource") != "docker" or not re.fullmatch(
                                    r"sha256:[0-9a-f]{64}", dep.get("currentDigest", "")):
                                raise SystemExit(f"Renovate did not extract the Docker digest for {image}")
                        found.add((file, image))
missing = expected - found
if missing:
    raise SystemExit("Renovate did not discover hosted demo dependencies: " + repr(sorted(missing)))
print(f"Renovate discovered all {len(expected)} required hosted demo dependency locations")

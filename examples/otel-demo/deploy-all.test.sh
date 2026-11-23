#!/bin/bash

# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

SHUNIT2="${SHUNIT2:?'expecting SHUNIT2 env var pointing to a dir with https://github.com/kward/shunit2 clone'}"

SCRIPT="$(cd "$(dirname "$0")" && pwd)/deploy-all.sh"

run_route() {
  local mode=$1
  local scope=$2
  local predicate=$3

  bash -c 'source "$1"; MODE="$2"; DEPLOY_SCOPE="$3"; "$4"' \
    _ "$SCRIPT" "$mode" "$scope" "$predicate"
}

assertRouteEnabled() {
  local mode=$1
  local scope=$2
  local predicate=$3

  run_route "$mode" "$scope" "$predicate" >/dev/null 2>&1
  assertEquals "$mode/$scope should enable $predicate" 0 $?
}

assertRouteDisabled() {
  local mode=$1
  local scope=$2
  local predicate=$3

  run_route "$mode" "$scope" "$predicate" >/dev/null 2>&1
  assertEquals "$mode/$scope should disable $predicate" 1 $?
}

testJaegerUpgradeRouting() {
  assertRouteDisabled upgrade jaeger deploy_full_stack
  assertRouteDisabled upgrade jaeger deploy_opensearch_stack
  assertRouteEnabled upgrade jaeger deploy_jaeger_stack
  assertRouteDisabled upgrade jaeger deploy_otel_demo
  assertRouteEnabled upgrade jaeger reconcile_ingress
}

testOtelDemoOnlyUpgradeRouting() {
  assertRouteDisabled upgrade otel-demo deploy_full_stack
  assertRouteDisabled upgrade otel-demo deploy_opensearch_stack
  assertRouteDisabled upgrade otel-demo deploy_jaeger_stack
  assertRouteEnabled upgrade otel-demo deploy_otel_demo
  assertRouteDisabled upgrade otel-demo reconcile_ingress
}

testOpenSearchOnlyUpgradeRouting() {
  assertRouteDisabled upgrade opensearch deploy_full_stack
  assertRouteEnabled upgrade opensearch deploy_opensearch_stack
  assertRouteDisabled upgrade opensearch deploy_jaeger_stack
  assertRouteDisabled upgrade opensearch deploy_otel_demo
  assertRouteDisabled upgrade opensearch reconcile_ingress
}

testAllUpgradeRouting() {
  assertRouteEnabled upgrade all deploy_full_stack
  assertRouteEnabled upgrade all deploy_opensearch_stack
  assertRouteEnabled upgrade all deploy_jaeger_stack
  assertRouteEnabled upgrade all deploy_otel_demo
  assertRouteEnabled upgrade all reconcile_ingress
}

testCleanAlwaysUsesFullStackRouting() {
  assertRouteEnabled clean otel-demo deploy_full_stack
  assertRouteEnabled clean otel-demo deploy_opensearch_stack
  assertRouteEnabled clean otel-demo deploy_jaeger_stack
  assertRouteEnabled clean otel-demo deploy_otel_demo
  assertRouteEnabled clean otel-demo reconcile_ingress
}

testInvalidScopeIsRejected() {
  output=$(bash -c 'source "$1"; MODE=upgrade; DEPLOY_SCOPE=invalid; validate_options' _ "$SCRIPT" 2>&1)
  rc=$?

  assertEquals "invalid scope should fail" 1 $rc
  assertContains "$output" "Invalid deploy scope 'invalid'"
}

testInvalidModeIsRejected() {
  output=$(bash -c 'source "$1"; MODE=invalid; DEPLOY_SCOPE=jaeger; validate_options' _ "$SCRIPT" 2>&1)
  rc=$?

  assertEquals "invalid mode should fail" 1 $rc
  assertContains "$output" "Invalid mode 'invalid'"
}

testInvalidOpenSearchRecoveryPolicyIsRejected() {
  output=$(bash -c '
    source "$1"
    MODE=upgrade
    DEPLOY_SCOPE=opensearch
    OPENSEARCH_RECOVERY=invalid
    validate_options
  ' _ "$SCRIPT" 2>&1)
  rc=$?

  assertEquals "invalid recovery policy should fail" 1 $rc
  assertContains "$output" "Invalid OpenSearch recovery policy 'invalid'"
}

testOpenSearchRecoveryWaiverAcceptsOnlyManualMainIsolatedUpgrade() {
  output=$(GITHUB_EVENT_NAME=workflow_dispatch \
    GITHUB_REF=refs/heads/main \
    JAEGER_DEMO_STACK=otel \
    JAEGER_OTEL_DEMO_DEPLOY_SCOPE=opensearch \
    JAEGER_OTEL_DEMO_OPENSEARCH_RECOVERY=waive \
    bash -c 'source "$1"; MODE=upgrade; DEPLOY_SCOPE=opensearch; validate_options' _ "$SCRIPT" 2>&1)
  rc=$?

  assertEquals "exact manual waiver route should pass" 0 $rc
  assertEquals "" "$output"
}

testOpenSearchRecoveryWaiverRejectsOtherRoutes() {
  local cases=(
    "schedule|refs/heads/main|otel|upgrade|opensearch"
    "workflow_dispatch|refs/heads/feature|otel|upgrade|opensearch"
    "workflow_dispatch|refs/heads/main|oci|upgrade|opensearch"
    "workflow_dispatch|refs/heads/main|otel|clean|opensearch"
    "workflow_dispatch|refs/heads/main|otel|upgrade|all"
  )
  local values

  for values in "${cases[@]}"; do
    IFS='|' read -r event ref stack mode scope <<<"$values"
    output=$(GITHUB_EVENT_NAME="$event" \
      GITHUB_REF="$ref" \
      JAEGER_DEMO_STACK="$stack" \
      JAEGER_OTEL_DEMO_DEPLOY_SCOPE="$scope" \
      JAEGER_OTEL_DEMO_OPENSEARCH_RECOVERY=waive \
      bash -c 'source "$1"; MODE="$2"; validate_options' _ "$SCRIPT" "$mode" 2>&1)
    rc=$?

    assertEquals "$values should reject the waiver" 1 $rc
    assertContains "$output" "OpenSearch recovery may be waived only"
  done
}

testMainIsGuardedWhenSourced() {
  output=$(bash -c 'source "$1"; printf sourced-only' _ "$SCRIPT" 2>&1)
  rc=$?

  assertEquals "source should succeed without running main" 0 $rc
  assertEquals "sourced-only" "$output"
}

testSharedUIConfigPassesPreflight() {
  output=$(bash -c 'source "$1"; check_required_files' _ "$SCRIPT" 2>&1)
  rc=$?

  assertEquals "shared UI config should pass preflight" 0 "$rc"
  assertEquals "" "$output"
}

testMissingUIConfigStopsBeforeClusterAccess() {
  output=$(bash -c '
    source "$1"
    UI_CONFIG_FILE="$2/missing-ui-config.json"
    MODE=upgrade
    DEPLOY_SCOPE=jaeger
    log() { :; }
    need() { :; }
    check_cluster() { printf "unsafe cluster access\n"; exit 1; }
    main
  ' _ "$SCRIPT" "$SHUNIT_TMPDIR" 2>&1)
  rc=$?

  assertNotEquals "missing UI config must stop deployment" 0 "$rc"
  assertContains "$output" "Missing required file: $SHUNIT_TMPDIR/missing-ui-config.json"
  assertNotContains "$output" "unsafe cluster access"
}

run_jaeger_deploy() {
  bash -c '
    source "$1"
    MODE="$2"
    EXPECTED_CONFIG_SHA="${3:-}"
    DEPLOY_SCOPE="${4:-jaeger}"
    RUN_PUBLIC_SMOKE_TESTS=false
    log() { :; }
    need() { :; }
    check_cluster() { :; }
    check_handover() { :; }
    cleanup() { :; }
    clone_jaeger_v2() { :; }
    verified_chart() { printf "/verified/%s-%s.tgz" "$1" "$2"; }
    wait_for_statefulset() { :; }
    wait_for_opensearch_dashboards() { :; }
    bash() { printf "bash %s\n" "$*"; }
    adopt_resource_for_helm_release() { :; }
    delete_deployment_before_helm_upgrade() { :; }
    wait_for_deployment() { :; }
    wait_for_service_endpoints() { :; }
    wait_for_daemonset() { :; }
    deploy_ingress() { :; }
    kubectl() { :; }
    helm() {
      if [[ "$1" != repo ]]; then
        printf "helm"; printf "${TEST_HELM_ARG_FORMAT:- %s}" "$@"; printf "\n"
      fi
    }
    main
  ' _ "$SCRIPT" "$1" "${2:-}" "${3:-jaeger}"
}

expected_jaeger_helm_args() {
  # Source with the same positional parameters run_jaeger_deploy uses.
  bash -c '
    source "$1"
    printf " [%s]" \
      --set allInOne.enabled=true \
      --set storage.type=elasticsearch \
      --set storage.elasticsearch.anonymous=true \
      --set storage.elasticsearch.usePassword=false \
      --set storage.elasticsearch.host=opensearch-cluster-single.opensearch.svc.cluster.local \
      --set storage.elasticsearch.port=9200 \
      --set "allInOne.image.repository=$JAEGER_IMAGE_REPOSITORY" \
      --set "allInOne.image.tag=$JAEGER_IMAGE_TAG" \
      --set "allInOne.image.pullPolicy=$IMAGE_PULL_POLICY" \
      --set "hotrod.image.repository=$HOTROD_IMAGE_REPOSITORY" \
      --set "hotrod.image.tag=$HOTROD_IMAGE_TAG" \
      --set "hotrod.image.pullPolicy=$IMAGE_PULL_POLICY" \
      --set-file "userconfig=$SCRIPT_DIR/jaeger-config.yaml" \
      --set-file "uiconfig=$UI_CONFIG_FILE" \
      -f "$SCRIPT_DIR/jaeger-values.yaml" \
      --timeout 10m
  ' _ "$SCRIPT" "$1"
}

testJaegerHelmArgvIsExactInEveryMode() {
  local dir expected_args mode reviewed command namespace output
  dir=$(dirname "$SCRIPT")
  # Every argument is bracketed so a merged or split word changes the output.
  for mode in upgrade handover clean; do
    expected_args=$(expected_jaeger_helm_args "${mode/handover/upgrade}")
    assertContains "$expected_args" " [--set] [allInOne.image.tag="
    reviewed=""
    namespace=" [--namespace] [jaeger] [--create-namespace]"
    case "$mode" in
      upgrade) command=" [upgrade] [--install] [--wait] [--atomic]" ;;
      handover)
        reviewed=reviewed
        command=" [upgrade] [--wait] [--atomic]"
        namespace=" [--namespace] [jaeger]"
        ;;
      clean) command=" [install] [--wait]" ;;
    esac
    output=$(TEST_HELM_ARG_FORMAT=" [%s]" run_jaeger_deploy "${mode/handover/upgrade}" "$reviewed" | grep '^helm .* \[jaeger\] ')
    assertEquals "$mode Jaeger helm argv" \
      "helm${command} [jaeger] [$dir/helm-charts/charts/jaeger]${namespace}${expected_args}" "$output"
  done
}

testJaegerUpgradeIncludesSharedUIConfig() {
  output=$(run_jaeger_deploy upgrade)
  rc=$?

  assertEquals "Jaeger upgrade should succeed" 0 "$rc"
  assertContains "$output" "helm upgrade --install --wait --atomic jaeger"
  assertContains "$output" "--namespace jaeger --create-namespace"
  assertContains "$output" "--set-file uiconfig=$(dirname "$SCRIPT")/../oci/ui-config.json"
  assertContains "$output" "--set-file userconfig=$(dirname "$SCRIPT")/jaeger-config.yaml"
}

testJaegerCleanInstallIncludesSharedUIConfig() {
  output=$(run_jaeger_deploy clean)
  rc=$?

  assertEquals "Jaeger install should succeed" 0 "$rc"
  assertContains "$output" "helm install --wait jaeger"
  assertContains "$output" "--namespace jaeger --create-namespace"
  assertContains "$output" "--set-file uiconfig=$(dirname "$SCRIPT")/../oci/ui-config.json"
}

testHandoverHelmCommandCannotInstallOrCreateNamespace() {
  output=$(run_jaeger_deploy upgrade reviewed)
  rc=$?

  assertEquals "handover Jaeger upgrade should succeed" 0 "$rc"
  assertContains "$output" "helm upgrade --wait --atomic jaeger"
  assertContains "$output" "--namespace jaeger"
  assertNotContains "$output" "--install"
  assertNotContains "$output" "--create-namespace"
}

testScheduledIngressUpdateFailureStopsReconciliation() {
  local output rc
  output=$(GITHUB_EVENT_NAME=schedule run_ingress_check "" ingress-jaeger.yaml)
  rc=$?
  assertNotEquals "failed ingress update must fail the scheduled component" 0 "$rc"
  assertEquals "stop at the first failed ingress" 1 "$(wc -l < "$SHUNIT_TMPDIR/ingress-commands" | tr -d ' ')"
}

testScheduledHotrodMutationsRequireExistingReleaseOwnership() {
  local ownership operation output rc
  for ownership in Helm/jaeger/jaeger Helm/default/jaeger Helm/jaeger/foreign "" denied; do
    for operation in serviceaccount service deployment; do
      output=$(GITHUB_EVENT_NAME=schedule bash -c '
        source "$1"
        log() { :; }
        check_handover() { :; }
        kubectl() {
          if [[ "$1" == get && "$*" == *jsonpath=* ]]; then
            [[ "$OWNER" != denied ]] || return 1
            printf "%s" "$OWNER"
          else
            printf "mutation %s\n" "$*"
          fi
        }
        OWNER="$2"
        if [[ "$3" == deployment ]]; then
          delete_deployment_before_helm_upgrade jaeger jaeger-hotrod
        else
          adopt_resource_for_helm_release jaeger "$3" jaeger-hotrod jaeger
        fi
      ' _ "$SCRIPT" "$ownership" "$operation")
      rc=$?
      if [[ "$ownership" == Helm/jaeger/jaeger ]]; then
        assertEquals "owned $operation" 0 "$rc"
        assertNotContains "$output" mutation
      else
        assertNotEquals "unowned/unreadable $operation" 0 "$rc"
        assertNotContains "$output" mutation
      fi
    done
  done
}

testScheduledOtelUpgradesCannotInstallOrCreateNamespaces() {
  local output
  output=$(GITHUB_EVENT_NAME=schedule GITHUB_REF=refs/heads/main run_jaeger_deploy upgrade "" all)
  assertEquals "scheduled upgrade should succeed" 0 "$?"
  assertNotContains "$output" "opensearch-recovery.sh"
  assertContains "$output" "helm upgrade opensearch /verified/opensearch-$(pinned_version OPENSEARCH_CHART_VERSION).tgz"
  assertContains "$output" \
    "helm upgrade opensearch-dashboards /verified/opensearch-dashboards-$(pinned_version OPENSEARCH_DASHBOARDS_CHART_VERSION).tgz"
  assertContains "$output" "helm upgrade --wait --atomic jaeger"
  assertContains "$output" "helm upgrade otel-demo /verified/opentelemetry-demo-$(pinned_version OTEL_DEMO_CHART_VERSION).tgz"
  assertNotContains "$output" "--install"
  assertNotContains "$output" "--create-namespace"
}

pinned_version() {
  bash -c 'source "$1"; printf "%s" "${!2}"' _ "$SCRIPT" "$1"
}

testReleasesInstallOnlyVerifiedArchives() {
  local output
  output=$(run_jaeger_deploy upgrade "" all)
  assertEquals "upgrade should succeed" 0 "$?"
  assertEquals "four releases" 4 "$(grep -c '^helm upgrade ' <<<"$output")"
  assertNotContains "$output" "--version"
  assertNotContains "$output" "opensearch/opensearch"
  assertNotContains "$output" "open-telemetry/opentelemetry-demo"
}

testVerifiedChartCallsTheLockHelper() {
  local output
  output=$(bash -c '
    source "$1"
    CHART_ARCHIVE_DIR=/archives
    python3() { printf "python3"; printf " [%s]" "$@"; }
    verified_chart opensearch 3.10.0
  ' _ "$SCRIPT")
  assertEquals "python3 [$(dirname "$SCRIPT")/../../scripts/demo/chart_archives.py] [pull] [opensearch] [3.10.0] [/archives]" \
    "$output"
}

# Run main with cluster writes reported as unsafe and the chart helper answered
# by a python3 stub that records the archive directory it was given.
run_with_chart_helper() {
  bash -c '
    source "$1"
    MODE="$2"
    DEPLOY_SCOPE="$3"
    helper="$4"
    record="$5"
    RUN_PUBLIC_SMOKE_TESTS=false
    log() { :; }
    need() { :; }
    check_cluster() { :; }
    check_handover() { :; }
    clone_jaeger_v2() { printf "jaeger chart prepared\n"; }
    cleanup() { printf "unsafe cleanup\n"; }
    bash() { printf "unsafe bash %s\n" "$*"; }
    kubectl() { printf "unsafe kubectl %s\n" "$*"; }
    wait_for_statefulset() { :; }
    wait_for_opensearch_dashboards() { :; }
    wait_for_deployment() { :; }
    wait_for_service_endpoints() { :; }
    wait_for_daemonset() { :; }
    adopt_resource_for_helm_release() { :; }
    delete_deployment_before_helm_upgrade() { :; }
    deploy_ingress() { :; }
    helm() {
      local argument
      for argument in "$@"; do
        if [[ "$argument" == *.tgz && ! -f "$argument" ]]; then printf "missing archive %s\n" "$argument"; fi
      done
      printf "helm %s\n" "$*"
    }
    python3() {
      if [[ "$1" == */recovery_policy.py ]]; then command python3 "$@"; return; fi
      if [[ "$1" != */scripts/demo/chart_archives.py || "$2" != pull ]]; then
        printf "unexpected python3 %s\n" "$*"
        return 1
      fi
      printf "%s\n" "$5" > "$record"
      if [[ "$helper" == mismatch && "$3" == "$MISMATCH_CHART" ]]; then
        printf "error: Chart archive differs from approved SHA-256: %s-%s.tgz\n" "$3" "$4" >&2
        return 1
      fi
      printf "pull %s %s\n" "$3" "$4" >&2
      printf "archive %s-%s\n" "$3" "$4" > "$5/$3-$4.tgz"
      printf "%s/%s-%s.tgz\n" "$5" "$3" "$4"
    }
    main
  ' _ "$SCRIPT" "$1" "$2" "$3" "$SHUNIT_TMPDIR/chart-archive-dir" 2>&1
}

chart_variable() {
  case "$1" in
    opensearch) echo OPENSEARCH_CHART_VERSION ;;
    opensearch-dashboards) echo OPENSEARCH_DASHBOARDS_CHART_VERSION ;;
    opentelemetry-demo) echo OTEL_DEMO_CHART_VERSION ;;
  esac
}

testChartDigestMismatchStopsBeforeAnyClusterMutation() {
  local values mode scope chart output rc
  for values in upgrade/opensearch/opensearch upgrade/opensearch/opensearch-dashboards \
      upgrade/otel-demo/opentelemetry-demo upgrade/all/opentelemetry-demo clean/all/opensearch; do
    IFS=/ read -r mode scope chart <<<"$values"
    output=$(MISMATCH_CHART="$chart" run_with_chart_helper "$mode" "$scope" mismatch)
    rc=$?
    assertNotEquals "$values mismatch must fail" 0 "$rc"
    assertContains "$values" "$output" "differs from approved SHA-256: $chart-"
    assertContains "$values" "$output" "Chart $chart $(pinned_version "$(chart_variable "$chart")") could not be verified"
    assertNotContains "$values" "$output" "unsafe"
    assertNotContains "$values" "$output" "helm "
    assertNotContains "$values" "$output" "unexpected python3"
  done
}

testVerifiedArchivesPrecedeCleanupAndAreRemovedOnExit() {
  local output directory repository
  rm -f "$SHUNIT_TMPDIR/chart-archive-dir"
  output=$(run_with_chart_helper clean all verified)
  assertEquals "clean deployment with stubs should succeed" 0 "$?"
  directory=$(cat "$SHUNIT_TMPDIR/chart-archive-dir")
  assertNotEquals "the archive directory was recorded" "" "$directory"
  assertFalse "archive directory must be removed on exit" "[[ -e '$directory' ]]"
  repository=$(cd "$(dirname "$SCRIPT")/../.." && pwd -P)
  assertNotContains "archives must stay outside the repository" "$(cd "$(dirname "$directory")" && pwd -P)/" "$repository/"
  assertNotContains "$output" "missing archive"
  assertNotContains "$output" "unexpected python3"
  assertEquals "charts are verified before cleanup" \
    "jaeger chart prepared|pull opensearch|pull opensearch-dashboards|pull opentelemetry-demo|unsafe cleanup" \
    "$(grep -E '^(jaeger chart prepared|pull |unsafe cleanup)' <<<"$output" |
      sed -E 's/^(pull [a-z-]+) .*/\1/' | paste -sd'|' -)"
  assertContains "$output" "helm upgrade --install opensearch $directory/opensearch-"
  assertContains "$output" "helm upgrade --install opensearch-dashboards $directory/opensearch-dashboards-"
  assertContains "$output" "helm upgrade --install otel-demo $directory/opentelemetry-demo-"
}

testJaegerDependencyMismatchStopsBeforeHelmUpgrade() {
  local output rc
  output=$(bash -c '
    source "$1"
    MODE=upgrade
    DEPLOY_SCOPE=jaeger
    RUN_PUBLIC_SMOKE_TESTS=false
    log() { :; }
    need() { :; }
    check_cluster() { :; }
    check_handover() { :; }
    git() { :; }
    kubectl() { printf "unsafe kubectl %s\n" "$*"; }
    helm() {
      case "$1" in
        repo|dependency) ;;
        *) printf "unsafe helm %s\n" "$*" ;;
      esac
    }
    python3() {
      if [[ "$1" == */recovery_policy.py ]]; then command python3 "$@"; return; fi
      printf "python3 %s\n" "$*"
      if [[ "$1" == */chart_archives.py && "$2" == dependencies ]]; then
        printf "error: Chart archive differs from approved SHA-256: kafka-26.6.2.tgz\n" >&2
        return 1
      fi
    }
    main
  ' _ "$SCRIPT" 2>&1)
  rc=$?
  assertNotEquals "a dependency mismatch must fail" 0 "$rc"
  assertContains "$output" "chart_archives.py dependencies $(dirname "$SCRIPT")/helm-charts/charts/jaeger/charts"
  assertContains "$output" "differs from approved SHA-256: kafka-26.6.2.tgz"
  assertNotContains "$output" "unsafe"
}

testPinsCompatibleOtelDemoChart() {
  output=$(bash -c 'source "$1"; printf "%s" "$OTEL_DEMO_CHART_VERSION"' _ "$SCRIPT")
  assertTrue "OTel demo should pin an explicit chart release" "[[ '$output' =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]"
}

testPinnedChartRendersCollectorStartupProbe() {
  local chart_version
  local rendered
  local render_rc
  local startup_probe_count
  local startup_probe
  local liveness_probe
  local readiness_probe
  local resources

  chart_version=$(bash -c \
    'source "$1"; printf "%s" "$OTEL_DEMO_CHART_VERSION"' \
    _ "$SCRIPT")

  rendered=$(helm template otel-demo opentelemetry-demo \
    --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
    --version "$chart_version" \
    --namespace otel-demo \
    --values "$(dirname "$SCRIPT")/otel-demo-values.yaml" \
    --show-only charts/opentelemetry-collector/templates/daemonset.yaml)
  render_rc=$?

  assertEquals "pinned OTel Demo chart should render" 0 "$render_rc"
  [[ "$render_rc" -eq 0 ]] || return

  assertContains "$rendered" "kind: DaemonSet"
  assertContains "$rendered" "name: otel-collector-agent"
  assertContains "$rendered" "- name: opentelemetry-collector"

  startup_probe_count=$(printf '%s\n' "$rendered" | grep -c '^[[:space:]]*startupProbe:$' || true)
  assertEquals "collector should render exactly one startup probe" 1 "$startup_probe_count"

  startup_probe=$(printf '%s\n' "$rendered" |
    sed -n '/^[[:space:]]*startupProbe:$/,/^[[:space:]]*resources:$/p')
  assertContains "$startup_probe" "periodSeconds: 10"
  assertContains "$startup_probe" "timeoutSeconds: 1"
  assertContains "$startup_probe" "failureThreshold: 30"
  assertContains "$startup_probe" "httpGet:"
  assertContains "$startup_probe" "path: /"
  assertContains "$startup_probe" "port: 13133"

  liveness_probe=$(printf '%s\n' "$rendered" |
    sed -n '/^[[:space:]]*livenessProbe:$/,/^[[:space:]]*readinessProbe:$/p')
  assertContains "$liveness_probe" "path: /"
  assertContains "$liveness_probe" "port: 13133"

  readiness_probe=$(printf '%s\n' "$rendered" |
    sed -n '/^[[:space:]]*readinessProbe:$/,/^[[:space:]]*startupProbe:$/p')
  assertContains "$readiness_probe" "path: /"
  assertContains "$readiness_probe" "port: 13133"

  resources=$(printf '%s\n' "$rendered" |
    sed -n '/^[[:space:]]*resources:$/,/^[[:space:]]*volumeMounts:$/p')
  # Chart default; 0.42 raised it from 200Mi.
  assertContains "$resources" "memory: 400Mi"
}

testPinnedChartRendersReviewedApplicationShape() {
  local chart_version
  local rendered
  local render_rc
  local count
  local pipeline
  local attribute
  local setting
  local value

  chart_version=$(bash -c \
    'source "$1"; printf "%s" "$OTEL_DEMO_CHART_VERSION"' \
    _ "$SCRIPT")

  rendered=$(helm template otel-demo opentelemetry-demo \
    --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
    --version "$chart_version" \
    --namespace otel-demo \
    --values "$(dirname "$SCRIPT")/otel-demo-values.yaml")
  render_rc=$?

  assertEquals "pinned OTel Demo chart should render" 0 "$render_rc"
  [[ "$render_rc" -eq 0 ]] || return

  # The load generator asks the agent, which answers from the chart's recorded
  # responses. The MCP server and the public chat UI stay off.
  count=$(printf '%s\n' "$rendered" | grep -c -E "image: .*/demo:[0-9.]+-agent'?\$" || true)
  assertEquals "the agent should be deployed" 1 "$count"
  assertContains "$rendered" "name: agent-fixtures"
  count=$(printf '%s\n' "$rendered" | grep -c -E "image: .*/demo:[0-9.]+-(mcp|chatbot)'?\$" || true)
  assertEquals "the MCP server and the chat UI should stay disabled" 0 "$count"
  # A prompt with no recorded match is sent to LLM_BASE_URL, and the chart's
  # placeholder host resolves. No rendered service may name a reachable model
  # endpoint or carry a key.
  for setting in 'LLM_BASE_URL=value: https://llm.invalid' 'USE_VCR=value: "True"' 'API_KEY=value: ""'; do
    count=$(printf '%s\n' "$rendered" | grep -c -- "- name: ${setting%%=*}\$" || true)
    assertEquals "only the agent should set ${setting%%=*}" 1 "$count"
    value=$(printf '%s\n' "$rendered" | grep -A1 -- "- name: ${setting%%=*}\$" | tail -1 | sed 's/^ *//')
    assertEquals "the agent's ${setting%%=*}" "${setting#*=}" "$value"
  done
  assertNotContains "$rendered" "local-llm.com"
  # The shop's public proxy would expose the OpAMP server's unauthenticated interface.
  count=$(printf '%s\n' "$rendered" | grep -c -E "image: .*/demo:[0-9.]+-opamp-server'?\$" || true)
  assertEquals "the OpAMP server should stay disabled" 0 "$count"
  count=$(printf '%s\n' "$rendered" | grep -c -E 'value: "?wss?://opamp-server' || true)
  assertEquals "no service should be configured to reach an OpAMP server" 0 "$count"
  count=$(printf '%s\n' "$rendered" | grep -c -E "image: .*/demo:[0-9.]+-telemetry-docs'?\$" || true)
  assertEquals "the telemetry documentation should be deployed" 1 "$count"

  # Node images on OKE 1.37 refuse short image names such as postgres:18.4.
  count=$(printf '%s\n' "$rendered" | sed -n -E "s/^ +image: ['\"]?([^'\" ]+).*/\1/p" |
    awk -F/ 'NF == 1 || $1 !~ /[.:]/ && $1 != "localhost"' | wc -l | tr -d ' ')
  assertEquals "every image should name its registry" 0 "$count"
  assertContains "$rendered" "image: 'docker.io/library/postgres:18.4@sha256:"
  count=$(printf '%s\n' "$rendered" | grep -E 'image: .*busybox' | grep -c -v '@sha256:' || true)
  assertEquals "every busybox init container should be pinned by digest" 0 "$count"

  # product-catalog emits no traces without the chart's SDK configuration file.
  assertContains "$rendered" "value: /otel-config.yml"
  assertContains "$rendered" "astronomy_user:astronomy_password@astronomy-db/astronomy_db"
  assertNotContains "$rendered" "@postgresql/"

  # Jaeger is public. The chart's emitRawPii flag puts the customer's email, card
  # number and CVV on checkout and payment spans, and the collector override
  # replaces the chart pipeline that would remove them.
  # awk stops reading early. A here-string does not report a broken pipe for the
  # rest of the render the way a pipe from printf does.
  pipeline=$(awk '
    /^ +pipelines:$/ { inside = 1; next }
    inside && /^ +processors:$/ { list = 1; next }
    list && /^ +- / { sub(/^ +- /, ""); printf "%s ", $0; next }
    list { exit }
  ' <<<"$rendered")
  assertEquals "the trace pipeline should drop raw personal data before batching" \
    "memory_limiter k8s_attributes resource transform/drop_raw_pii batch " "$pipeline"
  for attribute in user.email demo.payment.card_number demo.payment.card_cvv; do
    count=$(printf '%s\n' "$rendered" | grep -c -F "delete_key(attributes, \"$attribute\")" || true)
    assertEquals "the collector should delete $attribute from spans" 1 "$count"
  done
}

testPinnedChartRendersCheckoutMemoryHeadroom() {
  local chart_version
  local rendered
  local render_rc
  local checkout
  local gomemlimit_count
  local gomemlimit
  local resources

  chart_version=$(bash -c \
    'source "$1"; printf "%s" "$OTEL_DEMO_CHART_VERSION"' \
    _ "$SCRIPT")

  rendered=$(helm template otel-demo opentelemetry-demo \
    --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
    --version "$chart_version" \
    --namespace otel-demo \
    --values "$(dirname "$SCRIPT")/otel-demo-values.yaml" \
    --show-only templates/component.yaml)
  render_rc=$?

  assertEquals "pinned OTel Demo chart should render" 0 "$render_rc"
  [[ "$render_rc" -eq 0 ]] || return

  checkout=$(printf '%s\n' "$rendered" | awk '
    /^---$/ { if (dep && named) printf "%s", doc; doc = ""; dep = 0; named = 0; next }
    { doc = doc $0 "\n" }
    /^kind: Deployment$/ { dep = 1 }
    /^  name: checkout$/ { named = 1 }
    END { if (dep && named) printf "%s", doc }
  ')
  assertContains "$checkout" "kind: Deployment"

  gomemlimit_count=$(printf '%s\n' "$checkout" | grep -c -- '- name: GOMEMLIMIT$' || true)
  assertEquals "checkout should render exactly one GOMEMLIMIT" 1 "$gomemlimit_count"

  gomemlimit=$(printf '%s\n' "$checkout" | grep -A1 -- '- name: GOMEMLIMIT$' | tail -1)
  assertContains "$gomemlimit" "48MiB"

  resources=$(printf '%s\n' "$checkout" | grep -A2 '^[[:space:]]*resources:$')
  assertContains "$resources" "memory: 64Mi"
}

testPinnedChartRendersHeadlessLoadGeneratorWithoutFlagdUi() {
  local chart_version
  local rendered
  local render_rc
  local load_generator
  local headless_count
  local headless
  local autostart
  local users
  local spawn_rate
  local flagd_deploy
  local flagd_service

  chart_version=$(bash -c \
    'source "$1"; printf "%s" "$OTEL_DEMO_CHART_VERSION"' \
    _ "$SCRIPT")

  rendered=$(helm template otel-demo opentelemetry-demo \
    --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
    --version "$chart_version" \
    --namespace otel-demo \
    --values "$(dirname "$SCRIPT")/otel-demo-values.yaml" \
    --show-only templates/component.yaml)
  render_rc=$?

  assertEquals "pinned OTel Demo chart should render" 0 "$render_rc"
  [[ "$render_rc" -eq 0 ]] || return

  # Issue #57: the public /loadgen/ and /feature routes must have no backend.
  # Headless Locust opens no web UI and the flagd-ui sidecar is not rendered.
  load_generator=$(printf '%s\n' "$rendered" | awk '
    /^---$/ { if (dep && named) printf "%s", doc; doc = ""; dep = 0; named = 0; next }
    { doc = doc $0 "\n" }
    /^kind: Deployment$/ { dep = 1 }
    /^  name: load-generator$/ { named = 1 }
    END { if (dep && named) printf "%s", doc }
  ')
  assertContains "$load_generator" "kind: Deployment"

  headless_count=$(printf '%s\n' "$load_generator" | grep -c -- '- name: LOCUST_HEADLESS$' || true)
  assertEquals "load generator should render exactly one LOCUST_HEADLESS" 1 "$headless_count"

  headless=$(printf '%s\n' "$load_generator" | grep -A1 -- '- name: LOCUST_HEADLESS$' | tail -1)
  assertContains "$headless" '"true"'

  # Load must still autostart from the reviewed users and spawn rate.
  autostart=$(printf '%s\n' "$load_generator" | grep -A1 -- '- name: LOCUST_AUTOSTART$' | tail -1)
  assertContains "$autostart" '"true"'

  users=$(printf '%s\n' "$load_generator" | grep -A1 -- '- name: LOCUST_USERS$' | tail -1)
  assertContains "$users" '"5"'

  spawn_rate=$(printf '%s\n' "$load_generator" | grep -A1 -- '- name: LOCUST_SPAWN_RATE$' | tail -1)
  assertContains "$spawn_rate" '"2"'

  assertNotContains "$rendered" "flagd-ui"

  # flagd itself keeps serving the reviewed flag file: the init container that
  # copies it onto the shared writable volume stays, only the UI sidecar goes.
  flagd_deploy=$(printf '%s\n' "$rendered" | awk '
    /^---$/ { if (dep && named) printf "%s", doc; doc = ""; dep = 0; named = 0; next }
    { doc = doc $0 "\n" }
    /^kind: Deployment$/ { dep = 1 }
    /^  name: flagd$/ { named = 1 }
    END { if (dep && named) printf "%s", doc }
  ')
  assertContains "$flagd_deploy" "kind: Deployment"
  assertContains "$flagd_deploy" "name: init-config"
  assertContains "$flagd_deploy" "name: config-rw"

  flagd_service=$(printf '%s\n' "$rendered" | awk '
    /^---$/ { if (svc && named) printf "%s", doc; doc = ""; svc = 0; named = 0; next }
    { doc = doc $0 "\n" }
    /^kind: Service$/ { svc = 1 }
    /^  name: flagd$/ { named = 1 }
    END { if (svc && named) printf "%s", doc }
  ')
  assertContains "$flagd_service" "kind: Service"
  assertContains "$flagd_service" "port: 8013"
  assertContains "$flagd_service" "port: 8016"
  assertNotContains "$flagd_service" "4000"
}

testPinsCompatibleOpenSearchCharts() {
  output=$(bash -c 'source "$1"; printf "%s|%s" "$OPENSEARCH_CHART_VERSION" "$OPENSEARCH_DASHBOARDS_CHART_VERSION"' _ "$SCRIPT")
  local opensearch=${output%%|*}
  local dashboards=${output##*|}
  assertTrue "OpenSearch should pin a 3.x chart" "[[ '$opensearch' =~ ^3\.[0-9]+\.[0-9]+$ ]]"
  assertEquals "OpenSearch and Dashboards charts must match" "$opensearch" "$dashboards"
}

testPinnedOpenSearchChartsRenderMatchingImagesAndStorageIdentity() {
  local chart_version
  local rendered
  chart_version=$(bash -c 'source "$1"; printf "%s" "$OPENSEARCH_CHART_VERSION"' _ "$SCRIPT")
  rendered=$(helm template opensearch opensearch --repo https://opensearch-project.github.io/helm-charts \
    --version "$chart_version" --namespace opensearch --values "$(dirname "$SCRIPT")/opensearch-values.yaml")
  assertEquals "OpenSearch chart should render" 0 $?
  assertContains "$rendered" "opensearchproject/opensearch:$chart_version"
  assertContains "$rendered" "name: opensearch-cluster-single"
  assertContains "$rendered" "replicas: 1"
  assertContains "$rendered" 'storageClassName: "oci-bv"'
  assertContains "$rendered" 'storage: "10Gi"'
  assertContains "$rendered" "-Xms3g -Xmx3g"
  assertContains "$rendered" "memory: 6Gi"
  assertContains "$rendered" "cpu: 1000m"
  rendered=$(helm template opensearch-dashboards opensearch-dashboards \
    --repo https://opensearch-project.github.io/helm-charts --version "$chart_version" \
    --namespace opensearch --values "$(dirname "$SCRIPT")/opensearch-dashboard-values.yaml")
  assertEquals "Dashboards chart should render" 0 $?
  assertContains "$rendered" "opensearchproject/opensearch-dashboards:$chart_version"
  # The public, unauthenticated Dashboards must not expose the REST proxy or
  # carry credentials that the security-disabled cluster never checks.
  # Match whole lines so a commented-out key fails.
  for setting in console.enabled opensearch_index_management.enabled opensearch_security.enabled; do
    printf '%s\n' "$rendered" | grep -qxE "[[:space:]]*${setting//./\\.}: false"
    assertTrue "Dashboards should render $setting: false" $?
  done
  assertNotContains "$rendered" "opensearch.username"
  assertNotContains "$rendered" "opensearch.password"
  # Explore runs only inside a workspace, and the workspace UI needs the new
  # navigation. A data source would let any visitor make the pod connect to
  # in-cluster addresses, so it must stay off.
  for setting in workspace.enabled explore.enabled '"home:useNewHomePage"'; do
    printf '%s\n' "$rendered" | grep -qxE "[[:space:]]*${setting//./\\.}: true"
    assertTrue "Dashboards should render $setting: true" $?
  done
  # A visitor's second workspace must not take over the landing page.
  printf '%s\n' "$rendered" | grep -qxE "[[:space:]]*defaultWorkspace: observability"
  assertTrue "Dashboards should pin the default workspace" $?
  assertNotContains "$rendered" "data_source."
  # The workspace sidecar must not change the Dashboards container itself.
  assertNotContains "$rendered" "postStart:"
  assertContains "$rendered" "- name: workspace-bootstrap"
  assertContains "$rendered" "image: \"docker.io/opensearchproject/opensearch-dashboards:$chart_version\""
}

# Write a fake curl that answers like Dashboards in $HOOK_SCENARIO and records
# each call with its time limit, and a fake sleep that counts passes of the
# sidecar loop and stops it with TERM after $HOOK_PASSES passes.
make_workspace_sidecar_fakes() {
  local bin="$SHUNIT_TMPDIR/sidecar-bin"
  mkdir -p "$bin"
  cat > "$bin/curl" <<'FAKE'
#!/bin/bash
body= method=GET limit=none
while [ $# -gt 1 ]; do
  case $1 in
    -d) body=$2; shift ;;
    -X) method=$2; shift ;;
    --max-time) limit=$2; shift ;;
    -H|-o|-w|--connect-timeout) shift ;;
  esac
  shift
done
printf '%s %s max=%s\n' "$method" "$1" "$limit" >> "$HOOK_LOG"
pass=$(cat "$HOOK_LOG.passes" 2>/dev/null || echo 0)
case $HOOK_SCENARIO in
  down) exit 7 ;;
  stall) exit 28 ;;
esac
case "$method $1" in
  "GET http://localhost:5601/api/workspaces/observability")
    if [ "$HOOK_SCENARIO" = missing ]; then echo '{"success":false,"error":"not found"}'
    else echo '{"success":true,"result":{"id":"observability"}}'; fi ;;
  "POST http://localhost:5601/api/workspaces")
    printf '%s' "$body" > "$HOOK_LOG.workspace"; echo '{"success":true,"result":{"id":"observability"}}' ;;
  "GET http://localhost:5601/api/index_patterns/_fields_for_wildcard?"*)
    if [ "$HOOK_SCENARIO" = latespans ] && [ "$pass" -lt 2 ]; then exit 22; fi
    printf '%s' '{"fields":[{"name":"startTimeMillis","type":"date"},{"name":"q\"uote\\back","type":"string"}]}' ;;
  "GET http://localhost:5601/w/observability/api/saved_objects/index-pattern/jaeger-spans")
    [ -e "$HOOK_LOG.spans" ] || exit 22 ;;
  "POST http://localhost:5601/w/observability/api/saved_objects/index-pattern/jaeger-spans?overwrite=true")
    printf '%s' "$body" > "$HOOK_LOG.spans" ;;
  *) exit 22 ;;
esac
FAKE
  cat > "$bin/sleep" <<'FAKE'
#!/bin/bash
pass=$(( $(cat "$HOOK_LOG.passes" 2>/dev/null || echo 0) + 1 ))
echo "$pass" > "$HOOK_LOG.passes"
echo "sleep $1" >> "$HOOK_LOG"
# A visitor deletes the index pattern between the second and third passes.
if [ "$HOOK_SCENARIO" = deleted ] && [ "$pass" = 2 ]; then rm -f "$HOOK_LOG.spans"; fi
if [ "$pass" -ge "$HOOK_PASSES" ]; then kill -TERM "$PPID"; fi
FAKE
  chmod +x "$bin/curl" "$bin/sleep"
}

workspace_sidecar_script() {
  python3 -c 'import sys, yaml
values = yaml.safe_load(open(sys.argv[1]))
print(yaml.safe_load(values["extraContainers"])[0]["command"][2])' \
    "$(dirname "$SCRIPT")/opensearch-dashboard-values.yaml"
}

# Run the sidecar loop for $2 passes in scenario $1. Every request must carry
# a time limit of at most ten seconds.
run_workspace_sidecar() {
  rm -f "$SHUNIT_TMPDIR"/sidecar-log*
  HOOK_LOG="$SHUNIT_TMPDIR/sidecar-log" HOOK_SCENARIO=$1 HOOK_PASSES=$2 \
    PATH="$SHUNIT_TMPDIR/sidecar-bin:$PATH" sh -c "$(workspace_sidecar_script)"
  local status=$?
  assertEquals "$1: TERM must stop the sidecar cleanly" 0 "$status"
  assertEquals "$1: every request needs a time limit" "" \
    "$(grep -E '^(GET|POST) ' "$SHUNIT_TMPDIR/sidecar-log" | grep -vE ' max=([1-9]|10)$')"
}

count_sidecar_calls() {
  grep -c -- "$1" "$SHUNIT_TMPDIR/sidecar-log"
}

testWorkspaceSidecarCreatesMissingWorkspaceAndSpanPattern() {
  make_workspace_sidecar_fakes
  output=$(run_workspace_sidecar missing 2)
  assertEquals "workspace-bootstrap: the Observability workspace and span index pattern exist" "$output"
  python3 - "$SHUNIT_TMPDIR/sidecar-log.workspace" <<'PY'
import json, sys
attributes = json.load(open(sys.argv[1]))["attributes"]
assert attributes["id"] == "observability", attributes
assert attributes["features"] == ["use-case-observability"], attributes
PY
  assertEquals "workspace body should name the observability use case" 0 $?
  assertEquals "check again a minute after success" "sleep 60" "$(grep '^sleep' "$SHUNIT_TMPDIR/sidecar-log" | head -1)"
}

testWorkspaceSidecarRefreshesSpanFieldsOnceThenOnlyChecks() {
  make_workspace_sidecar_fakes
  run_workspace_sidecar existing 3 >/dev/null
  assertEquals "an existing workspace must not be recreated" 0 "$(count_sidecar_calls 'POST http://localhost:5601/api/workspaces ')"
  assertEquals "overwrite the pattern once per start" 1 "$(count_sidecar_calls 'POST .*index-pattern/jaeger-spans')"
  # The stored field list must survive JSON string escaping intact.
  python3 - "$SHUNIT_TMPDIR/sidecar-log.spans" <<'PY'
import json, sys
attributes = json.load(open(sys.argv[1]))["attributes"]
assert attributes["title"] == "jaeger-main-jaeger-span-*", attributes
assert attributes["timeFieldName"] == "startTimeMillis", attributes
assert json.loads(attributes["fields"]) == [
    {"name": "startTimeMillis", "type": "date"},
    {"name": 'q"uote\\back', "type": "string"},
], attributes["fields"]
PY
  assertEquals "span index pattern should carry the escaped field list" 0 $?
}

testWorkspaceSidecarRecreatesADeletedSpanPattern() {
  make_workspace_sidecar_fakes
  run_workspace_sidecar deleted 3 >/dev/null
  assertEquals "recreate the pattern after a visitor deletes it" 2 "$(count_sidecar_calls 'POST .*index-pattern/jaeger-spans')"
  assertTrue "the pattern exists again" "[ -e '$SHUNIT_TMPDIR/sidecar-log.spans' ]"
}

testWorkspaceSidecarWaitsForTheFirstJaegerSpanIndex() {
  # On a fresh install Jaeger starts only after Dashboards is ready.
  make_workspace_sidecar_fakes
  output=$(run_workspace_sidecar latespans 3)
  assertEquals "workspace-bootstrap: waiting for Dashboards and a Jaeger span index
workspace-bootstrap: the Observability workspace and span index pattern exist" "$output"
  assertEquals "retry every 15 seconds while waiting, then every minute" "sleep 15
sleep 15
sleep 60" "$(grep '^sleep' "$SHUNIT_TMPDIR/sidecar-log")"
  assertTrue "the pattern exists once spans arrive" "[ -e '$SHUNIT_TMPDIR/sidecar-log.spans' ]"
}

testWorkspaceSidecarKeepsWaitingWhenDashboardsIsDownOrStalls() {
  make_workspace_sidecar_fakes
  for scenario in down stall; do
    output=$(run_workspace_sidecar "$scenario" 4)
    assertEquals "$scenario: log the state once, not every pass" \
      "workspace-bootstrap: waiting for Dashboards and a Jaeger span index" "$output"
    assertEquals "$scenario: keep retrying" 4 "$(count_sidecar_calls '^sleep 15$')"
    assertFalse "$scenario: no index pattern" "[ -e '$SHUNIT_TMPDIR/sidecar-log.spans' ]"
  done
}

testDeploysPinnedOpenSearchChartsInOrder() {
  output=$(bash -c '
    source "$1"
    MODE=upgrade
    OPENSEARCH_CHART_ARCHIVE="/verified/opensearch-$OPENSEARCH_CHART_VERSION.tgz"
    OPENSEARCH_DASHBOARDS_CHART_ARCHIVE="/verified/opensearch-dashboards-$OPENSEARCH_DASHBOARDS_CHART_VERSION.tgz"
    log() { :; }
    bash() { printf "bash"; printf " %s" "$@"; printf "\n"; }
    helm() { printf "helm"; printf " %s" "$@"; printf "\n"; }
    wait_for_statefulset() { printf "wait-for-statefulset"; printf " %s" "$@"; printf "\n"; }
    wait_for_opensearch_dashboards() { printf "wait-for-dashboards"; printf " %s" "$@"; printf "\n"; }
    deploy_opensearch_releases
  ' _ "$SCRIPT")

  local chart_versions
  local opensearch_version
  local dashboards_version
  chart_versions=$(bash -c 'source "$1"; printf "%s|%s" "$OPENSEARCH_CHART_VERSION" "$OPENSEARCH_DASHBOARDS_CHART_VERSION"' _ "$SCRIPT")
  opensearch_version=${chart_versions%%|*}
  dashboards_version=${chart_versions##*|}
  # The chart token is the verified archive; a version flag would refer to a repository chart.
  expected="bash $(dirname "$SCRIPT")/opensearch-recovery.sh verify
helm upgrade --install opensearch /verified/opensearch-$opensearch_version.tgz --namespace opensearch --create-namespace -f $(dirname "$SCRIPT")/opensearch-values.yaml --wait --timeout 10m
wait-for-statefulset opensearch opensearch-cluster-single 600s
helm upgrade --install opensearch-dashboards /verified/opensearch-dashboards-$dashboards_version.tgz --namespace opensearch -f $(dirname "$SCRIPT")/opensearch-dashboard-values.yaml --wait --timeout 60m"
  expected="$expected
wait-for-dashboards 600s"
  assertEquals "$expected" "$output"
}

testDashboardsHelmTimeoutCanBeOverridden() {
  output=$(OPENSEARCH_DASHBOARDS_HELM_TIMEOUT=75m bash -c '
    source "$1"
    MODE=clean
    OPENSEARCH_CHART_ARCHIVE=/verified/opensearch.tgz
    OPENSEARCH_DASHBOARDS_CHART_ARCHIVE=/verified/opensearch-dashboards.tgz
    log() { :; }
    helm() { printf "helm"; printf " %s" "$@"; printf "\n"; }
    wait_for_statefulset() { :; }
    wait_for_opensearch_dashboards() { :; }
    deploy_opensearch_releases
  ' _ "$SCRIPT")

  assertContains "$output" "helm upgrade --install opensearch-dashboards /verified/opensearch-dashboards.tgz"
  assertContains "$output" "--wait --timeout 75m"
}

testDashboardsHelmFailureCollectsDiagnosticsAndStops() {
  output=$(bash -c '
    source "$1"
    MODE=clean
    OPENSEARCH_CHART_ARCHIVE=/verified/opensearch.tgz
    OPENSEARCH_DASHBOARDS_CHART_ARCHIVE=/verified/opensearch-dashboards.tgz
    log() { :; }
    helm() {
      if [[ "$*" == *"opensearch-dashboards /verified/opensearch-dashboards.tgz"* ]]; then
        return 1
      fi
      return 0
    }
    wait_for_statefulset() { printf "wait-for-statefulset\n"; }
    wait_for_opensearch_dashboards() { printf "unsafe wait-for-deployment\n"; }
    diagnose_opensearch_dashboards_failure() { printf "dashboards diagnostics\n"; }
    deploy_opensearch_releases
  ' _ "$SCRIPT" 2>&1)
  rc=$?

  assertNotEquals "Dashboards Helm failure should fail deployment" 0 "$rc"
  assertContains "$output" "dashboards diagnostics"
  assertEquals "diagnostics should run exactly once" 1 "$(grep -c '^dashboards diagnostics$' <<<"$output")"
  assertContains "$output" "Helm release opensearch-dashboards failed"
  assertNotContains "$output" "unsafe wait-for-deployment"
}

testDashboardsDiagnosticsAreSanitized() {
  output=$(bash -c '
    source "$1"
    log() { printf "log %s\n" "$*"; }
    helm() {
      case "$1" in
        status)
          printf "%s\n" '\''{"version":12,"info":{"status":"failed"},"chart":{"values":{"password":"raw-status-value"}},"app_version":"2.19.6"}'\''
          ;;
        history)
          printf "%s\n" '\''[{"revision":12,"status":"failed","chart":"opensearch-dashboards-2.34.0","app_version":"2.19.6"},{"revision":13,"status":{"value":"failed"},"chart":{"values":{"password":"raw-history-value"}},"app_version":{"token":"raw-app-value"}}]'\''
          ;;
      esac
    }
    kubectl() {
      case "$*" in
        *"get deployment opensearch-dashboards -o json"*)
          printf "%s\n" '\''{"metadata":{"generation":2},"status":{"observedGeneration":2,"replicas":1,"readyReplicas":0,"conditions":[{"type":"Available","status":"False","reason":"MinimumReplicasUnavailable","lastTransitionTime":"2026-08-22T11:45:52Z"}]}}'\''
          ;;
        *"get pods -l app.kubernetes.io/name=opensearch-dashboards,app.kubernetes.io/instance=opensearch-dashboards -o json"*)
          printf "%s\n" '\''{"items":[{"metadata":{"name":"opensearch-dashboards-private-id"},"spec":{"nodeName":"oke-private-node"},"status":{"phase":"Pending","conditions":[{"type":"PodScheduled","status":"False","reason":"Unschedulable"}],"containerStatuses":[{"name":"dashboards","ready":false,"started":false,"restartCount":1,"state":{"waiting":{"reason":"ImagePullBackOff"}},"lastState":{"terminated":{"reason":"Error","exitCode":1}}}]}}]}'\''
          ;;
        *"get events -o json"*)
          printf "%s\n" '\''{"items":[{"involvedObject":{"name":"opensearch-dashboards-private-id"},"type":"Warning","reason":"FailedScheduling","count":2,"lastTimestamp":"2026-08-22T11:46:00Z","message":"private event text"}]}'\''
          ;;
        *"get pods -l app.kubernetes.io/name=opensearch-dashboards,app.kubernetes.io/instance=opensearch-dashboards -o name"*)
          printf "%s\n" "pod/opensearch-dashboards-abc123-def45"
          ;;
        *"logs pod/opensearch-dashboards-abc123-def45 -c dashboards --previous --tail=300"*)
          printf "%s\n" "warning: startup timed out" "Basic dXNlcjpwYXNz" "HTTPS://alice:uRLp4ss@example.invalid/"
          ;;
        *"logs pod/opensearch-dashboards-abc123-def45 -c dashboards --tail=300"*)
          printf "%s\n" "error: connection refused" '\''{"password":"json-value"}'\'' '\''{"scheme":"Bearer eyJhbGciOiJIUzI1NiJ9.opaque"}'\'' "endpoint 10.1.2.3" "endpoint 2001:db8::1" "pod opensearch-dashboards-abc123-def45" "node oke-private-node"
          ;;
      esac
    }
    diagnose_opensearch_dashboards_failure
  ' _ "$SCRIPT" 2>&1)

  assertContains "$output" '"chart":"opensearch-dashboards-2.34.0"'
  assertContains "$output" '"waitingReason":"ImagePullBackOff"'
  assertContains "$output" '"reason":"FailedScheduling"'
  assertContains "$output" "Current Dashboards container log summary (pod 1)"
  assertContains "$output" "lines=7 errors=1 warnings=0 timeouts=0 connection_failures=1 memory_failures=0 readiness_failures=0"
  assertContains "$output" "Previous Dashboards container log summary (pod 1)"
  assertContains "$output" "lines=3 errors=0 warnings=1 timeouts=1 connection_failures=0 memory_failures=0 readiness_failures=0"
  assertNotContains "$output" "dXNlcjpwYXNz"
  assertNotContains "$output" "uRLp4ss"
  assertNotContains "$output" "json-value"
  assertNotContains "$output" "eyJhbGciOiJIUzI1NiJ9.opaque"
  assertNotContains "$output" "10.1.2.3"
  assertNotContains "$output" "2001:db8::1"
  assertNotContains "$output" "opensearch-dashboards-abc123-def45"
  assertNotContains "$output" "opensearch-dashboards-private-id"
  assertNotContains "$output" "oke-private-node"
  assertNotContains "$output" "private event text"
  assertNotContains "$output" "raw-status-value"
  assertNotContains "$output" "raw-history-value"
  assertNotContains "$output" "raw-app-value"
}

testDashboardsRolloutFailureCollectsSanitizedDiagnostics() {
  output=$(bash -c '
    source "$1"
    log() { :; }
    kubectl() { printf "kubectl"; printf " %s" "$@"; printf "\n"; return 1; }
    diagnose_opensearch_dashboards_failure() { printf "dashboards diagnostics\n"; }
    wait_for_opensearch_dashboards 45s
  ' _ "$SCRIPT" 2>&1)
  rc=$?

  assertNotEquals "Dashboards rollout failure should fail deployment" 0 "$rc"
  assertContains "$output" "kubectl rollout status deployment/opensearch-dashboards -n opensearch --timeout=45s"
  assertContains "$output" "dashboards diagnostics"
  assertContains "$output" "OpenSearch Dashboards failed to remain ready"
  assertNotContains "$output" "-o wide"
  assertNotContains "$output" "describe"
}

testRecoveryGatePrecedesOpenSearchUpgrade() {
  output=$(bash -c '
    source "$1"
    MODE=upgrade
    log() { :; }
    bash() { printf "recovery %s %s\n" "$1" "$2"; return 1; }
    helm() { printf "unsafe helm call\n"; }
    deploy_opensearch_releases
  ' _ "$SCRIPT" 2>&1)
  rc=$?

  assertNotEquals "missing recovery point must stop deployment" 0 "$rc"
  assertContains "$output" "recovery $(dirname "$SCRIPT")/opensearch-recovery.sh verify"
  assertNotContains "$output" "unsafe helm call"
}

testRecoveryWaiverSkipsOnlyRecoveryGate() {
  output=$(bash -c '
    source "$1"
    MODE=upgrade
    OPENSEARCH_RECOVERY=waive
    GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF=refs/heads/main JAEGER_DEMO_STACK=otel
    export GITHUB_EVENT_NAME GITHUB_REF JAEGER_DEMO_STACK
    DEPLOY_SCOPE=opensearch
    OPENSEARCH_CHART_ARCHIVE=/verified/opensearch.tgz
    OPENSEARCH_DASHBOARDS_CHART_ARCHIVE=/verified/opensearch-dashboards.tgz
    log() { printf "log %s\n" "$*"; }
    bash() { printf "unsafe recovery call\n"; }
    helm() { printf "helm"; printf " %s" "$@"; printf "\n"; }
    wait_for_statefulset() { printf "wait-for-statefulset"; printf " %s" "$@"; printf "\n"; }
    wait_for_opensearch_dashboards() { printf "wait-for-dashboards"; printf " %s" "$@"; printf "\n"; }
    deploy_opensearch_releases
  ' _ "$SCRIPT")

  assertContains "$output" "WARNING: OpenSearch recovery was explicitly waived"
  assertNotContains "$output" "unsafe recovery call"
  assertContains "$output" "helm upgrade --install opensearch /verified/opensearch.tgz"
  assertContains "$output" "wait-for-statefulset opensearch opensearch-cluster-single 600s"
  assertContains "$output" "helm upgrade --install opensearch-dashboards /verified/opensearch-dashboards.tgz"
  assertContains "$output" "wait-for-dashboards 600s"
}

testInvalidRecoveryPolicyCannotCallHelm() {
  output=$(bash -c '
    source "$1"
    MODE=upgrade
    OPENSEARCH_RECOVERY=invalid
    log() { :; }
    helm() { printf "unsafe helm call\n"; }
    deploy_opensearch_releases
  ' _ "$SCRIPT" 2>&1)
  rc=$?

  assertNotEquals "invalid recovery policy must stop deployment" 0 "$rc"
  assertContains "$output" "Invalid OpenSearch recovery policy 'invalid'"
  assertNotContains "$output" "unsafe helm call"
}

testHandoverPreflightStopsBeforeWritesAndRechecksAfterChartPreparation() {
  local stop_at
  for stop_at in 1 2; do
    output=$(bash -c '
      source "$1"
      MODE=upgrade
      DEPLOY_SCOPE=jaeger
      EXPECTED_CONFIG_SHA=reviewed
      RUN_PUBLIC_SMOKE_TESTS=false
      checks=0
      stop_at=$2
      log() { :; }
      need() { :; }
      check_cluster() { :; }
      clone_jaeger_v2() { printf "chart prepared\n"; }
      python3() {
        if [[ "$1" == */recovery_policy.py ]]; then command python3 "$@"; return; fi
        checks=$((checks + 1))
        printf "preflight %s: %s\n" "$checks" "$*"
        [[ "$checks" -ne "$stop_at" ]]
      }
      helm() { [[ "$1" == repo ]] || printf "unsafe helm mutation\n"; }
      kubectl() { printf "unsafe kubectl mutation\n"; }
      main
    ' _ "$SCRIPT" "$stop_at" 2>&1)
    rc=$?

    assertNotEquals "handover check $stop_at must stop deployment" 0 "$rc"
    assertContains "$output" "preflight $stop_at: $(dirname "$SCRIPT")/../../scripts/demo/handover.py otel upgrade jaeger"
    assertNotContains "$output" "unsafe helm mutation"
    assertNotContains "$output" "unsafe kubectl mutation"
    if [[ "$stop_at" -eq 2 ]]; then
      assertContains "$output" "chart prepared"
    else
      assertNotContains "$output" "chart prepared"
    fi
  done
}

run_resource_read_failure() {
  bash -c '
    source "$1"
    EXPECTED_CONFIG_SHA=$2
    operation=$3
    failed_read=$4
    check_handover() { :; }
    kubectl() {
      if [[ "$1" == get ]]; then
        if [[ "$failed_read" == object || "$*" == *jsonpath* ]]; then
          printf "Forbidden\n" >&2
          return 1
        fi
        return 0
      fi
      printf "unsafe mutation: %s\n" "$*"
    }
    if [[ "$operation" == adopt ]]; then
      adopt_resource_for_helm_release jaeger serviceaccount jaeger-hotrod jaeger
    else
      delete_deployment_before_helm_upgrade jaeger jaeger-hotrod
    fi
  ' _ "$SCRIPT" "$1" "$2" "$3" 2>&1
}

testHandoverResourceReadFailuresCannotBecomeAbsenceOrPermitMutation() {
  local operation
  for operation in adopt delete; do
    output=$(run_resource_read_failure reviewed "$operation" object)
    rc=$?
    assertNotEquals "$operation must fail on an unreadable required resource" 0 "$rc"
    assertContains "$output" "Cannot read required"
    assertNotContains "$output" "unsafe mutation"
  done
  output=$(run_resource_read_failure reviewed adopt ownership)
  rc=$?
  assertNotEquals "unreadable Helm ownership must stop adoption" 0 "$rc"
  assertContains "$output" "Cannot read Helm ownership"
  assertNotContains "$output" "unsafe mutation"
}

testHandoverRechecksImmediatelyBeforeHotrodDeletion() {
  output=$(bash -c '
    source "$1"
    EXPECTED_CONFIG_SHA=reviewed
    check_handover() { printf "release state changed\n"; return 1; }
    kubectl() { printf "unsafe kubectl operation: %s\n" "$*"; }
    delete_deployment_before_helm_upgrade jaeger jaeger-hotrod
  ' _ "$SCRIPT" 2>&1)
  rc=$?

  assertNotEquals "changed release state must prevent HotROD deletion" 0 "$rc"
  assertContains "$output" "release state changed"
  assertNotContains "$output" "unsafe kubectl operation"
}

testOrdinaryMissingResourceBehaviorIsPreserved() {
  local operation
  for operation in adopt delete; do
    output=$(run_resource_read_failure '' "$operation" object)
    rc=$?
    assertEquals "ordinary $operation still tolerates a missing resource" 0 "$rc"
    assertNotContains "$output" "unsafe mutation"
  done
}

run_ingress_check() {
  rm -f "$SHUNIT_TMPDIR/ingress-commands"
  bash -c '
    source "$1"
    EXPECTED_CONFIG_SHA=$2
    failed_command=$3
    commands=$4
    sleep() { :; }
    kubectl() {
      printf "%s\n" "$*" >> "$commands"
      if [[ -n "$failed_command" && "$*" == *"$failed_command"* ]]; then
        printf "Forbidden\n" >&2
        return 1
      fi
      case "$1" in
        apply) printf "ingress configured\n" ;;
        get) [[ "$*" != *jsonpath* ]] || printf "True\n" ;;
      esac
      return 0
    }
    deploy_ingress
  ' _ "$SCRIPT" "$1" "$2" "$SHUNIT_TMPDIR/ingress-commands" 2>&1
}

testHandoverStopsOnEachFailedIngressApply() {
  local ingress
  local count=0
  for ingress in ingress-jaeger.yaml ingress-opensearch.yaml ingress-otel-demo.yaml; do
    count=$((count + 1))
    output=$(run_ingress_check reviewed "$ingress")
    rc=$?
    assertNotEquals "$ingress failure must fail handover" 0 "$rc"
    assertContains "$output" "Failed to apply"
    assertEquals "later applies must not continue" "$count" "$(wc -l < "$SHUNIT_TMPDIR/ingress-commands" | tr -d ' ')"
    assertNotContains "$output" "All SSL certificates ready"
  done
}

testHandoverCertificateReadErrorsAreNotReportedAsMissing() {
  local failed_read
  for failed_read in 'get namespace jaeger' 'get certificate -n jaeger' 'get certificate -n jaeger -o'; do
    output=$(run_ingress_check reviewed "$failed_read")
    rc=$?
    assertNotEquals "$failed_read must fail handover" 0 "$rc"
    assertContains "$output" "Cannot"
    assertNotContains "$output" "No certificates found"
    assertNotContains "$output" "All SSL certificates ready"
  done
}

testHandoverIngressSuccessAndOrdinaryFailureBehavior() {
  output=$(run_ingress_check reviewed '')
  rc=$?
  assertEquals "successful handover ingress must pass" 0 "$rc"
  assertContains "$output" "All SSL certificates ready (3/3)"

  output=$(run_ingress_check '' apply)
  rc=$?
  assertEquals "ordinary ingress failures keep their existing behavior" 0 "$rc"
  assertEquals "ordinary path still attempts every ingress" 3 "$(grep -c '^apply ' "$SHUNIT_TMPDIR/ingress-commands")"
}

testHandoverRequiresEveryIngressFileBeforeFirstApply() {
  local directory="$SHUNIT_TMPDIR/handover-ingress"
  local missing
  mkdir -p "$directory/ingress"
  for missing in ingress-jaeger.yaml ingress-opensearch.yaml ingress-otel-demo.yaml; do
    cp "$(dirname "$SCRIPT")/ingress/ingress-"*.yaml "$directory/ingress/"
    rm "$directory/ingress/$missing"
    output=$(bash -c '
      source "$1"
      SCRIPT_DIR=$2
      EXPECTED_CONFIG_SHA=reviewed
      kubectl() { printf "unsafe ingress apply\n"; }
      deploy_ingress
    ' _ "$SCRIPT" "$directory" 2>&1)
    rc=$?
    assertNotEquals "$missing must be required" 0 "$rc"
    assertContains "$output" "Missing required handover ingress file: $missing"
    assertNotContains "$output" "unsafe ingress apply"
  done
}

# shellcheck disable=SC1091
source "${SHUNIT2}/shunit2"

#!/bin/bash

# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0

SHUNIT2="${SHUNIT2:?'expecting SHUNIT2 env var pointing to a dir with https://github.com/kward/shunit2 clone'}"

SCRIPT="$(cd "$(dirname "$0")" && pwd)/resolve-demo-snapshot-tags.sh"
REPO="jaegertracing/jaeger-snapshot"
PREFERRED="1111111111111111111111111111111111111111"
NEWEST="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MAIN_SHA="2222222222222222222222222222222222222222"
PUBLISHED="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
OLDER="cccccccccccccccccccccccccccccccccccccccc"

oneTimeSetUp() {
  TMPDIR_TEST=$(mktemp -d)
  EMPTY_BIN="$TMPDIR_TEST/empty-bin"
  mkdir -p "$EMPTY_BIN"
  MOCK_CURL="$TMPDIR_TEST/mock-curl.sh"
  cat > "$MOCK_CURL" <<EOF
#!/usr/bin/env bash
set -eu
outfile=""
write_fmt=""
url=""

while [[ \$# -gt 0 ]]; do
  case "\$1" in
    -o) outfile=\$2; shift 2 ;;
    -w) write_fmt=\$2; shift 2 ;;
    -*) shift ;;
    *) url=\$1; shift ;;
  esac
done

emit() {
  local status=\$1
  local body=\$2
  if [[ -n "\$outfile" ]]; then
    printf '%s' "\$body" > "\$outfile"
  fi
  if [[ -n "\$write_fmt" ]]; then
    printf '%s' "\$status"
  fi
}

printf '%s\n' "\$url" >> "\${HUB_ALL_REQUESTS:-/dev/null}"
if [[ "\$url" == */tags/\?page_size=* ]]; then
  printf '%s\n' "\$url" >> "\${HUB_REQUESTS:-/dev/null}"
fi
no_sha_page='"next":"more","results":[{"name":"latest","last_updated":"2026-07-11T00:00:00Z"}]'
if [[ "\$url" == */tags/\?page_size=*ordering=last_updated* && -n "\${HUB_PAGES:-}" ]]; then
  # Every page lists only a non-SHA tag and announces a further page.
  case "\$HUB_PAGES" in
    # 30 pages, so a missing limit fails this test instead of stalling it.
    many) emit "200" '{"count":3000,'"\$no_sha_page"'}' ;;
    short-count) emit "200" '{"count":100,'"\$no_sha_page"'}' ;;
    no-count) emit "200" '{'"\$no_sha_page"'}' ;;
    *) emit "500" '{}' ;;
  esac
  exit 0
fi

case "\$url" in
  */tags/latest/)
    emit "\${LATEST_STATUS:-200}" '{"digest":"sha256:published"}'
    ;;
  */tags/${PUBLISHED}/)
    emit "200" '{"digest":"sha256:published"}'
    ;;
  */tags/${PREFERRED}/)
    emit "404" '{}'
    ;;
  */tags/${MAIN_SHA}/)
    emit "\${MAIN_STATUS:-404}" '{}'
    ;;
  */tags/${NEWEST}/)
    emit "\${NEWEST_STATUS:-200}" '{"digest":"sha256:newest"}'
    ;;
  */tags/${OLDER}/)
    emit "200" '{"digest":"sha256:older"}'
    ;;
  */tags/?page_size=*ordering=last_updated\&page=2)
    if [[ "\${PAGE_TWO:-}" == "true" ]]; then
      emit "200" '{"results":[{"name":"'${NEWEST}'","last_updated":"2026-07-10T00:00:00Z"}],"next":null}'
      exit 0
    fi
    # Only an older tag: a search that pages past a first page with SHA tags selects it.
    emit "200" '{"results":[{"name":"'${OLDER}'","last_updated":"2023-01-01T00:00:00Z"}],"next":null}'
    ;;
  */tags/?page_size=*ordering=last_updated*)
    if [[ "\${PAGE_TWO:-}" == "true" ]]; then
      emit "200" '{"count":102,"next":"page2","results":[{"name":"latest","last_updated":"2026-07-11T00:00:00Z"}]}'
      exit 0
    fi
    # Like the live repositories: SHA tags on the first page and a further page announced.
    emit "200" '{"count":250,"next":"https://hub.docker.com/v2/repositories/other/tags/?page=2","results":[{"name":"latest","last_updated":"2026-07-11T00:00:00Z"},{"name":"'${OLDER}'","last_updated":"2023-01-01T00:00:00Z"},{"name":"'${NEWEST}'","last_updated":"2026-07-10T00:00:00Z"}]}'
    ;;
  */tags/?page_size=*)
    emit "500" '{"error":"unexpected ordering"}'
    ;;
  *)
    emit "500" '{}'
    ;;
esac
EOF
  chmod +x "$MOCK_CURL"
}

oneTimeTearDown() {
  rm -rf "$TMPDIR_TEST"
}

run_script() {
  env \
    GITHUB_ENV= \
    DOCKERHUB_CURL="$MOCK_CURL" \
    MAIN_SHA="$MAIN_SHA" \
    JAEGER_DEMO_JAEGER_IMAGE_REPOSITORY="$REPO" \
    JAEGER_DEMO_HOTROD_IMAGE_REPOSITORY="$REPO" \
    "$@" \
    bash "$SCRIPT"
}

testRequiresMainSha() {
  err=$(env -i bash "$SCRIPT" 2>&1)
  rc=$?
  assertEquals "exit 1 without MAIN_SHA" 1 $rc
  assertContains "$err" "MAIN_SHA must be set to the resolved jaegertracing/jaeger commit"
}

testConfigRepositoryShaCannotBecomeAnImageTag() {
  err=$(env -i GITHUB_SHA="$PUBLISHED" bash "$SCRIPT" 2>&1)
  rc=$?
  assertEquals "the config SHA is not an image fallback" 1 $rc
  assertContains "$err" "MAIN_SHA must be set to the resolved jaegertracing/jaeger commit"
}

testUsesCoreShaInsteadOfConfigRepositorySha() {
  out=$(run_script MAIN_SHA="$PUBLISHED" GITHUB_SHA="$NEWEST" 2>&1)
  rc=$?
  assertEquals "exit 0" 0 $rc
  assertContains "$out" "Jaeger tag=$PUBLISHED"
  assertContains "$out" "HotROD tag=$PUBLISHED"
}

testRequiresJq() {
  err=$(env -i PATH="$EMPTY_BIN" MAIN_SHA="$MAIN_SHA" /bin/bash "$SCRIPT" 2>&1)
  rc=$?
  assertEquals "exit 1 without jq" 1 $rc
  assertContains "$err" "jq is required but not installed"
}

testUsesPreferredTagWhenPublished() {
  out=$(run_script \
    GITHUB_EVENT_NAME=schedule \
    JAEGER_DEMO_JAEGER_IMAGE_TAG="$PUBLISHED" \
    JAEGER_DEMO_HOTROD_IMAGE_TAG="$PUBLISHED" 2>&1)
  rc=$?
  assertEquals "exit 0" 0 $rc
  assertContains "$out" "Jaeger tag=$PUBLISHED"
  assertContains "$out" "HotROD tag=$PUBLISHED"
}

testScheduledFallsBackToNewestPublishedSha() {
  out=$(run_script GITHUB_EVENT_NAME=schedule 2>&1)
  rc=$?
  assertEquals "exit 0" 0 $rc
  assertContains "$out" "Jaeger tag=$NEWEST"
  assertContains "$out" "HotROD tag=$NEWEST"
  assertContains "$out" "Main HEAD ${MAIN_SHA} has no published Jaeger snapshot"
}

testScheduledFallbackUsesOnlyTheFirstPageWhenItHoldsShaTags() {
  requests="$TMPDIR_TEST/hub-requests"
  : > "$requests"
  out=$(run_script GITHUB_EVENT_NAME=schedule HUB_REQUESTS="$requests" 2>&1)
  rc=$?
  assertEquals "exit 0" 0 "$rc"
  assertContains "$out" "Jaeger tag=$NEWEST"
  assertContains "$out" "HotROD tag=$NEWEST"
  assertEquals "one list request per repository" 2 "$(wc -l < "$requests" | tr -d ' ')"
  assertEquals "the announced next page is never requested" 0 "$(grep -c 'page=' "$requests")"
}

testScheduledRunFailsWhenDockerHubCannotAnswer() {
  out=$(run_script GITHUB_EVENT_NAME=schedule MAIN_STATUS=503 2>&1)
  rc=$?
  assertEquals "an unverifiable core tag does not fall back" 1 "$rc"
  assertContains "$out" "Failed to verify Docker Hub tag for ${REPO}:${MAIN_SHA} (Jaeger); Docker Hub API returned HTTP 503."
  assertNotContains "$out" "Jaeger tag="

  out=$(run_script GITHUB_EVENT_NAME=schedule HUB_PAGES=unavailable 2>&1)
  rc=$?
  assertEquals "a failed tag list fails the run" 1 "$rc"
  assertContains "$out" "Docker Hub tag list for ${REPO} returned HTTP 500"
  assertNotContains "$out" "Jaeger tag="
}

testFallbackTagIsVerifiedBeforeItIsExported() {
  out=$(run_script GITHUB_EVENT_NAME=schedule NEWEST_STATUS=404 2>&1)
  rc=$?
  assertEquals "a listed tag that no longer exists is rejected" 1 "$rc"
  assertContains "$out" "Snapshot image tag not found on Docker Hub: ${REPO}:${NEWEST} (Jaeger)"
  assertNotContains "$out" "Jaeger tag="
}

testExplicitTagMustUseDockerTagSyntax() {
  requests="$TMPDIR_TEST/hub-all-requests"
  long=$(printf 'a%.0s' $(seq 1 129))
  for event in workflow_dispatch schedule; do
    for tag in '?' '#' '.' '..' 'a/b' '-leading' "$long"; do
      : > "$requests"
      out=$(run_script GITHUB_EVENT_NAME="$event" JAEGER_DEMO_IMAGE_TAG="$tag" HUB_ALL_REQUESTS="$requests" 2>&1)
      rc=$?
      assertEquals "$event: tag '$tag' is rejected" 1 "$rc"
      assertContains "$out" "Invalid snapshot image tag syntax (Jaeger)"
      assertEquals "$event: no request is made for '$tag'" 0 "$(wc -l < "$requests" | tr -d ' ')"
    done
  done
}

testScheduledFallbackFindsShaBeyondFirstHubPage() {
  out=$(run_script GITHUB_EVENT_NAME=schedule PAGE_TWO=true 2>&1)
  rc=$?
  assertEquals "scheduled selection searches the next page when needed" 0 "$rc"
  assertContains "$out" "Jaeger tag=$NEWEST"
}

testMissingExplicitScheduledOverrideCannotFallBack() {
  out=$(run_script GITHUB_EVENT_NAME=schedule JAEGER_DEMO_JAEGER_IMAGE_TAG="$PREFERRED" 2>&1)
  rc=$?
  assertEquals "scheduled explicit override must exist" 1 "$rc"
  assertContains "$out" "Snapshot image tag not found"

  out=$(run_script GITHUB_EVENT_NAME=schedule JAEGER_DEMO_IMAGE_TAG="$PREFERRED" 2>&1)
  rc=$?
  assertEquals "scheduled shared override must exist" 1 "$rc"
}

testIndependentScheduledFallbackKeepsPublishedOverride() {
  out=$(run_script GITHUB_EVENT_NAME=schedule JAEGER_DEMO_JAEGER_IMAGE_TAG="$PUBLISHED" 2>&1)
  rc=$?
  assertEquals "published Jaeger override and HotROD fallback" 0 "$rc"
  assertContains "$out" "Jaeger tag=$PUBLISHED"
  assertContains "$out" "HotROD tag=$NEWEST"
}

testLatestInputResolvesToNewestPublishedSha() {
  out=$(run_script \
    GITHUB_EVENT_NAME=workflow_dispatch \
    JAEGER_DEMO_JAEGER_IMAGE_TAG=latest \
    JAEGER_DEMO_HOTROD_IMAGE_TAG=latest 2>&1)
  rc=$?
  assertEquals "exit 0" 0 $rc
  assertContains "$out" "Jaeger tag=$NEWEST"
  assertContains "$out" "HotROD tag=$NEWEST"
}

testExplicitLatestMustExistBeforeItIsResolved() {
  for event in workflow_dispatch schedule; do
    out=$(run_script GITHUB_EVENT_NAME="$event" JAEGER_DEMO_IMAGE_TAG=latest LATEST_STATUS=404 2>&1)
    rc=$?
    assertEquals "$event: a missing latest tag is rejected" 1 "$rc"
    assertContains "$out" "Snapshot image tag not found on Docker Hub: ${REPO}:latest (Jaeger)"
    assertNotContains "$out" "Jaeger tag="
  done

  out=$(run_script GITHUB_EVENT_NAME=workflow_dispatch JAEGER_DEMO_HOTROD_IMAGE_TAG=latest \
    MAIN_SHA="$PUBLISHED" LATEST_STATUS=503 2>&1)
  rc=$?
  assertEquals "an unverifiable latest tag is rejected" 1 "$rc"
  assertContains "$out" "Failed to verify Docker Hub tag for ${REPO}:latest (HotROD); Docker Hub API returned HTTP 503."
  assertNotContains "$out" "HotROD tag="
}

testScheduledFallbackStopsAtThePageLimit() {
  requests="$TMPDIR_TEST/hub-requests"
  : > "$requests"
  out=$(run_script GITHUB_EVENT_NAME=schedule HUB_PAGES=many HUB_REQUESTS="$requests" 2>&1)
  rc=$?
  assertEquals "a long listing without SHA tags fails" 1 "$rc"
  assertContains "$out" "No snapshot SHA tag in the first 10 pages of ${REPO} tags"
  assertNotContains "$out" "Jaeger tag="
  assertEquals "exactly the limit of list requests" 10 "$(wc -l < "$requests" | tr -d ' ')"
  assertEquals "the last request is the tenth page" 1 "$(tail -n 1 "$requests" | grep -c '&page=10$')"
}

testScheduledFallbackRejectsInconsistentPagination() {
  for listing in short-count no-count; do
    requests="$TMPDIR_TEST/hub-requests"
    : > "$requests"
    out=$(run_script GITHUB_EVENT_NAME=schedule HUB_PAGES="$listing" HUB_REQUESTS="$requests" 2>&1)
    rc=$?
    assertEquals "$listing: a further page beyond the reported count fails" 1 "$rc"
    assertContains "$out" "Invalid pagination for ${REPO} SHA tags"
    assertEquals "$listing: no second page is requested" 1 "$(wc -l < "$requests" | tr -d ' ')"
  done
}

testManualDispatchFailsWhenTagMissing() {
  out=$(run_script \
    GITHUB_EVENT_NAME=workflow_dispatch \
    JAEGER_DEMO_JAEGER_IMAGE_TAG="$PREFERRED" \
    JAEGER_DEMO_HOTROD_IMAGE_TAG="$PREFERRED" 2>&1)
  rc=$?
  assertEquals "exit 1" 1 $rc
  assertContains "$out" "Snapshot image tag not found"
}

# shellcheck disable=SC1091
source "${SHUNIT2}/shunit2"

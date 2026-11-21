#!/usr/bin/env bash

# Copyright (c) 2026 The Jaeger Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Resolve and verify Jaeger demo snapshot image tags from Docker Hub.
#
# Scheduled runs without an explicit tag prefer MAIN_SHA when published;
# otherwise they deploy the newest published commit SHA tag (never the mutable
# :latest tag). The search reads at most MAX_TAG_PAGES pages of the tag list.
#
# An explicit tag must use Docker tag syntax and exist on Docker Hub for every
# event, including a schedule. An explicit latest is verified, then resolved to
# a concrete SHA tag.
#
# Manual workflow_dispatch runs use the requested tag (default MAIN_SHA) and
# fail if that tag is missing.
#
# Jaeger and HotROD tags are resolved independently; each image is
# published to its own repository and may fall back to a different SHA.
#
# Exports (and appends to GITHUB_ENV when set):
#   JAEGER_DEMO_JAEGER_IMAGE_TAG
#   JAEGER_DEMO_HOTROD_IMAGE_TAG
#   JAEGER_DEMO_IMAGE_TAG

set -euo pipefail

: "${JAEGER_DEMO_JAEGER_IMAGE_REPOSITORY:=jaegertracing/jaeger-snapshot}"
: "${JAEGER_DEMO_HOTROD_IMAGE_REPOSITORY:=jaegertracing/example-hotrod-snapshot}"
: "${MAIN_SHA:=}"
: "${GITHUB_EVENT_NAME:=workflow_dispatch}"

# Both snapshot repositories publish a SHA tag for almost every tag, so the
# newest one is normally on the first page. The limit bounds a malformed or
# unexpected listing.
readonly MAX_TAG_PAGES=10

if [[ -z "$MAIN_SHA" ]]; then
  echo "MAIN_SHA must be set to the resolved jaegertracing/jaeger commit" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required but not installed" >&2
  exit 1
fi

run_curl() {
  if [[ -n "${DOCKERHUB_CURL:-}" ]]; then
    "$DOCKERHUB_CURL" "$@"
  else
    curl -sS --connect-timeout 5 --max-time 15 --retry 3 --retry-delay 2 "$@"
  fi
}

dockerhub_tag_status() {
  local repo=$1
  local tag=$2
  local status

  status=$(run_curl -o /dev/null -w "%{http_code}" \
    "https://hub.docker.com/v2/repositories/${repo}/tags/${tag}/" || true)
  status=${status:-000}
  echo "$status"
}

newest_published_sha() {
  local repo=$1
  local status tag tags_file
  local page=1
  local url="https://hub.docker.com/v2/repositories/${repo}/tags/?page_size=100&ordering=last_updated"

  tags_file=$(mktemp "${TMPDIR:-/tmp}/docker-tags-page.XXXXXX")
  while :; do
    status=$(run_curl -o "$tags_file" -w "%{http_code}" "$url" || true)
    if [[ "${status:-000}" != "200" ]]; then
      rm -f "$tags_file"
      echo "Docker Hub tag list for ${repo} returned HTTP ${status:-000}" >&2
      return 1
    fi
    if ! tag=$(jq -r '[.results[] | select(.name | test("^[0-9a-f]{40}$"))] | sort_by(.last_updated) | last | .name // empty' "$tags_file"); then
      rm -f "$tags_file"
      return 1
    fi
    if [[ -n "$tag" ]] || ! jq -e '.next != null' "$tags_file" >/dev/null; then
      break
    fi
    # Keep requests on the intended repository when following ordered pages.
    page=$((page + 1))
    if (( page > MAX_TAG_PAGES )); then
      rm -f "$tags_file"
      echo "No snapshot SHA tag in the first ${MAX_TAG_PAGES} pages of ${repo} tags" >&2
      return 1
    fi
    if ! jq -e --argjson page "$page" '.count > (($page - 1) * 100)' "$tags_file" >/dev/null; then
      rm -f "$tags_file"
      echo "Invalid pagination for ${repo} SHA tags" >&2
      return 1
    fi
    url="https://hub.docker.com/v2/repositories/${repo}/tags/?page_size=100&ordering=last_updated&page=${page}"
  done
  rm -f "$tags_file"

  if [[ -z "$tag" ]]; then
    echo "No published snapshot SHA tags found for ${repo}" >&2
    return 1
  fi
  echo "$tag"
}

verify_deploy_tag() {
  local repo=$1
  local tag=$2
  local label=$3
  local status

  status=$(dockerhub_tag_status "$repo" "$tag")
  if [[ "$status" == "404" ]]; then
    echo "::error::Snapshot image tag not found on Docker Hub: ${repo}:${tag} (${label})" >&2
    return 1
  fi
  if [[ "$status" != "200" ]]; then
    echo "::error::Failed to verify Docker Hub tag for ${repo}:${tag} (${label}); Docker Hub API returned HTTP ${status}." >&2
    return 1
  fi
}

resolve_snapshot_tag() {
  local repo=$1
  local preferred=$2
  local label=$3
  local override=$4
  local status
  local resolved

  if [[ -z "$preferred" ]]; then
    echo "Preferred tag is empty for ${label}" >&2
    return 1
  fi

  # Docker tag syntax only. A value such as "?" or ".." addresses another Docker Hub
  # endpoint that answers 200, which would pass as an existing tag.
  if [[ ! "$preferred" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
    echo "::error::Invalid snapshot image tag syntax (${label})" >&2
    return 1
  fi

  if [[ "$preferred" == "latest" ]]; then
    verify_deploy_tag "$repo" "$preferred" "$label" || return 1
    resolved=$(newest_published_sha "$repo") || return 1
    echo "$resolved"
    return 0
  fi

  status=$(dockerhub_tag_status "$repo" "$preferred")
  if [[ "$status" == "200" ]]; then
    echo "$preferred"
    return 0
  fi
  if [[ "$status" != "404" ]]; then
    echo "::error::Failed to verify Docker Hub tag for ${repo}:${preferred} (${label}); Docker Hub API returned HTTP ${status}." >&2
    return 1
  fi

  if [[ "$GITHUB_EVENT_NAME" != "schedule" || -n "$override" ]]; then
    echo "::error::Snapshot image tag not found on Docker Hub: ${repo}:${preferred} (${label})" >&2
    return 1
  fi

  resolved=$(newest_published_sha "$repo") || return 1
  echo "$resolved"
}

set_github_env() {
  local key=$1
  local value=$2

  if [[ -n "${GITHUB_ENV:-}" ]]; then
    printf '%s=%s\n' "$key" "$value" >> "$GITHUB_ENV"
  fi
  export "$key=$value"
}

main() {
  local jaeger_preferred
  local hotrod_preferred
  local jaeger_tag
  local hotrod_tag
  local main_status

  jaeger_preferred="${JAEGER_DEMO_JAEGER_IMAGE_TAG:-${JAEGER_DEMO_IMAGE_TAG:-}}"
  hotrod_preferred="${JAEGER_DEMO_HOTROD_IMAGE_TAG:-${JAEGER_DEMO_IMAGE_TAG:-}}"

  jaeger_tag=$(resolve_snapshot_tag "$JAEGER_DEMO_JAEGER_IMAGE_REPOSITORY" "${jaeger_preferred:-$MAIN_SHA}" "Jaeger" "$jaeger_preferred")
  hotrod_tag=$(resolve_snapshot_tag "$JAEGER_DEMO_HOTROD_IMAGE_REPOSITORY" "${hotrod_preferred:-$MAIN_SHA}" "HotROD" "$hotrod_preferred")

  verify_deploy_tag "$JAEGER_DEMO_JAEGER_IMAGE_REPOSITORY" "$jaeger_tag" "Jaeger"
  verify_deploy_tag "$JAEGER_DEMO_HOTROD_IMAGE_REPOSITORY" "$hotrod_tag" "HotROD"

  if [[ "$GITHUB_EVENT_NAME" == "schedule" && "$jaeger_tag" != "$MAIN_SHA" ]]; then
    main_status=$(dockerhub_tag_status "$JAEGER_DEMO_JAEGER_IMAGE_REPOSITORY" "$MAIN_SHA")
    if [[ "$main_status" == "404" ]]; then
      echo "Main HEAD ${MAIN_SHA} has no published Jaeger snapshot; deploying ${jaeger_tag}"
    fi
  fi
  if [[ "$GITHUB_EVENT_NAME" == "schedule" && "$hotrod_tag" != "$MAIN_SHA" ]]; then
    main_status=$(dockerhub_tag_status "$JAEGER_DEMO_HOTROD_IMAGE_REPOSITORY" "$MAIN_SHA")
    if [[ "$main_status" == "404" ]]; then
      echo "Main HEAD ${MAIN_SHA} has no published HotROD snapshot; deploying ${hotrod_tag}"
    fi
  fi

  set_github_env JAEGER_DEMO_JAEGER_IMAGE_TAG "$jaeger_tag"
  set_github_env JAEGER_DEMO_HOTROD_IMAGE_TAG "$hotrod_tag"
  set_github_env JAEGER_DEMO_IMAGE_TAG "$jaeger_tag"

  echo "Deploy: main=${MAIN_SHA}, Jaeger tag=${jaeger_tag}, HotROD tag=${hotrod_tag}"
}

main "$@"

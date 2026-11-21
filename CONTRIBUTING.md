<!-- Copyright (c) 2026 The Jaeger Authors. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Contributing

Open an issue describing the requested change before a PR. Keep one issue per PR,
update documentation alongside behavior, and retain existing copyright/license
headers. New files use the current-year Jaeger copyright and Apache-2.0 SPDX
identifier. Maintainers review changes under
[CODEOWNERS](.github/CODEOWNERS) and the organization
[AI Usage Policy](AI_POLICY.md).

Sign each new commit with `git commit -s`. Capitalize the first word after a
conventional prefix, for example `fix(deploy): Resolve the core image commit`.
The extraction PR must use a history-preserving merge; see
[extraction.md](docs/extraction.md). Do not rewrite imported authored commits to
make them look newly contributed.

## Changelog

Add an entry to [CHANGELOG.md](CHANGELOG.md) in the same PR for any change an
operator or contributor would notice: deployed configuration, image or chart pins,
workflows, alerts, scripts, commands and runbooks. Typo fixes, test-only refactors
and changes that only restate existing behavior need no entry.

- Put the entry under the UTC date the PR merges, creating the heading at the top
  if needed. If the final branch update before merge lands on a later date, move
  the entry to that date's heading.
- Use the Keep a Changelog category (Added, Changed, Fixed, Removed, Security) and
  write one or two sentences about the effect, not the implementation.
- End with the PR link and `fixes` or `refs` the issue, matching the PR body.
- Append **Executor image.** when `scripts/demo/guarded_changes.py` lists a changed
  file, so the required executor release is visible.
- A merge is not a deployment. Record production evidence in the release record
  and [docs/MIGRATION.md](docs/MIGRATION.md), not in the changelog.

The `Changelog entry` check enforces this on every PR. It fails when a changed
file other than a test (`*_test.py`, `*.test.sh`) or `CHANGELOG.md` has no new
entry under a date and category heading. Rewording or moving an existing entry
does not count as a new one. For a change that needs no entry, a maintainer applies
the `skip-changelog` label, which reruns the check. The check also validates the
heading structure (dates newest first, known categories) and warns when a new
entry is not under today's UTC date or does not link the PR yet. Run it locally
before pushing:

```bash
python3 scripts/demo/changelog_check.py origin/main HEAD
```

## Local checks

Install Bash, Python 3.11 or later with PyYAML and cryptography, Git, jq, Helm, ShellCheck, actionlint and ruff
(`python3 -m pip install -r scripts/utils/lint-requirements.txt`). Keep production
kubeconfig and cloud credentials out of the test environment. Obtain shunit2:

```bash
git clone --branch v2.1.8 --depth 1 https://github.com/kward/shunit2.git .tools/shunit2
export SHUNIT2="$PWD/.tools/shunit2"
make lint
make test
```

`make check` runs `lint`, `test` and `renovate-discovery` in that order.

The tests mock deployment commands and render pinned public charts. Public network
access is required for the chart repositories, but no production credentials are
used. Install the constrained Python libraries in a virtual environment with
`python3 -m pip install -c scripts/demo/oci-cli-constraints.txt PyYAML cryptography`.
The constrained `pycparser`, which `cryptography` pulls in through `cffi`, sets the
Python 3.11 minimum. If regenerated constraints change it, update this section and
the README Development section.
The same shell/Python suites run in the `Demo tests` CI check. New behavior
needs tests; bugs need regression coverage. Preserve imported test assertions.

`make lint` runs ShellCheck for shared and example scripts, actionlint for workflows, JSON
syntax validation, and ruff for Python with the rules in `ruff.toml`. The script
handover uses the production writer's Helm version. The toolchain and schema
checks for Helmfile adoption are documented below.

## Dependency pins with a second location

Renovate updates the first location of a pin only. Each second location below fails
a test with the command or files to change, so a bump PR cannot pass CI and fail at
preparation.

- **Chart versions.** After a chart pin changes in either `deploy-all.sh`, run
  `python3 scripts/utils/update-chart-archives.py` and commit the resulting
  `ops/executor/chart-archives.json`. It downloads each of the four pinned archives,
  requires its SHA-256 to equal the digest in the publisher's `index.yaml`, and
  rewrites the `charts` map only. `--check` changes nothing and exits non-zero with
  the differences. Executor handling of these files is described in
  [in-cluster-executor.md](docs/in-cluster-executor.md).
- **Helm and Kubernetes versions.** `scripts/demo/version_literals_test.py` reads
  the versions in `scripts/demo/scheduled_adapter.py` and lists every other literal
  that disagrees. Change all listed files in one commit.
- **OCI CLI.** `scripts/demo/oci-cli-constraints.txt` records what the deployment
  runner installed, including packages already present, not only new ones (see its
  header). To regenerate it, take the full `pip freeze` output of the runner that
  performed the deployment after `oci-cli` was installed, keep the license header and
  provenance comment with the new run URL, and replace the pins. Update the literal
  assertions in `scripts/demo/verify_oci_cli_test.py` (`oci-cli`, `oci` and
  `python-dateutil`) in the same commit.

## Renovate discovery

The `Renovate discovery` CI check validates configuration and verifies actual
extraction of every hosted dependency location. With Docker available, reproduce it
without credentials:

```bash
make renovate-discovery
```

The script runs the configuration validator, the extraction, and the checker. Set
`CONTAINER_ENGINE=podman` to use Podman instead of Docker. Renovate considers hosted updates daily, waits
seven days, and never automerges them. Preserve paired OpenSearch/Dashboards 3.x
updates. Do not duplicate dependency
bumps already managed by automation.

## Production changes

Tests and PRs require no production access. Keep production workflows main-only. Scheduled deployment requires the
[activation and verification procedure](docs/cutover.md#review-scheduled-scope). Repository
protection and DCO/Renovate app coverage must be verified, not inferred from files.

The first access transition, script handover, Helmfile adoption and final
acceptance require human verification of concrete inputs and evidence. Subsequent
routine operations follow the accepted [release process](docs/releases.md).
Never place credentials, raw secrets or unreviewed live configuration in a commit,
issue, PR or test fixture.

## Helmfile adoption tooling

Local commands and the credential-free CI check use the same [toolchain lock](scripts/demo/helmfile-tools.lock.json).
It pins Helm 4.3.2, the major and version recorded by the deployment writer, plus
Helmfile, helm-diff and kubeconform. Linux and macOS on amd64 and arm64 are supported.
Python 3, curl and PyYAML are required. No Kubernetes or Oracle credentials are needed
to verify tools or validate a local render.

```bash
make helmfile-tools
python3 scripts/demo/helmfile_tools.py run -- helmfile --version
make validate-manifests MANIFESTS='rendered-applications.yaml rendered-companions.yaml'
```

The wrapper downloads official release archives into the ignored `.tools/helmfile-archives`
cache, checks SHA-256 on every use, and extracts the pinned executable and diff plugin
into a temporary directory for each command. It selects that plugin explicitly. The
`run` subcommand inherits the caller's environment, including any configured kubeconfig.
Use credential-free inputs for local render and validation. The cache includes the
hash-verified Kubernetes OpenAPI specification when running schema validation.

Pass complete YAML streams with the monitoring chart's CRDs included. The validator
uses the upstream Kubernetes v1.36.1 OpenAPI definitions and the exact served CRD
schemas in those streams. It checks exact API group, version and kind, applies
strict field and type checks, and fails on missing schemas. Native optional fields
accept Kubernetes' null-as-omitted representation. Server-side defaulting, CEL and
webhook behavior are tested against Kubernetes during adoption rehearsals.
The credential-free rehearsal rendered 222 application objects plus 12 supplemental
objects and validated all 234, including ten CRDs, with zero skips.

Renovate discovers all four tool releases and opens reviewable updates. For a version
change, refresh that tool's checksums from its official release and review the lock diff:

```bash
python3 scripts/demo/helmfile_tools.py refresh helmfile
make helmfile-tools
```

The SHA-256 keys include the version, so changing a version alone fails verification.
For a Kubernetes version update, select the production API version and review the
SHA-256 of `api/openapi-spec/swagger.json` from that tagged release. Verify the
combined render and include the changes to versions, checksums and validation results
in the release record.

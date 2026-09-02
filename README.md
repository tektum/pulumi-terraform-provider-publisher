# pulumi-terraform-provider-publisher

A reusable GitHub Actions workflow that turns a Terraform provider into published
Pulumi SDKs for **TypeScript/JavaScript, Python, Go, C#, Java and YAML**.

`pulumi package add terraform-provider` is documented as a *local* SDK generation
command: it writes an SDK into your project and stops there. This repository adds
the publication automation around it, so a provider release can fan out to npm,
PyPI, the Go module proxy, NuGet and Maven Central from one `workflow_call`.

## What it does

1. Checks out the **caller** repository.
2. Installs the Pulumi CLI.
3. Extracts the Pulumi package schema from the bridged Terraform provider, either
   from a registry at an exact pinned version (`mode: registry`) or from a provider
   binary built by the checked-out commit (`mode: local`).
4. Pins the SDK version and the language package coordinates into the schema.
5. Generates and builds one SDK per requested runtime, and verifies each one
   against the exact layout the publisher consumes.
6. Publishes npm, PyPI, NuGet and Maven SDKs with
   [`pulumi/pulumi-package-publisher`](https://github.com/pulumi/pulumi-package-publisher).
7. Publishes the Go SDK with
   [`pulumi/publish-go-sdk-action`](https://github.com/pulumi/publish-go-sdk-action).
8. Generates and **validates** the YAML package. YAML has no publishable artifact,
   so nothing is uploaded for it.

## Usage

```yaml
name: Publish Pulumi SDKs

on:
  push:
    tags: ["v*.*.*"]

permissions: {}

concurrency:
  group: publish-pulumi-sdks-${{ github.ref }}
  cancel-in-progress: false

jobs:
  publish:
    permissions:
      contents: write # publish-go-sdk-action pushes the Go SDK tag
    uses: omercnet/pulumi-terraform-provider-publisher/.github/workflows/publish-terraform-provider-sdks.yml@<commit-sha>
    with:
      mode: registry
      terraform-provider: descope/descope
      terraform-provider-version: 0.3.16
      languages: nodejs,python,go,dotnet,java,yaml
      publish: true

      nodejs-package-name: "@descope/pulumi-descope"
      python-package-name: descope_pulumi_descope
      dotnet-root-namespace: Descope.Pulumi
      java-base-package: com.descope.pulumi

      go-sdk-repository: descope/pulumi-descope
      go-sdk-path: sdk/go
    secrets:
      npm-token: ${{ secrets.NPM_TOKEN }}
      pypi-password: ${{ secrets.PYPI_API_TOKEN }}
      nuget-publish-key: ${{ secrets.NUGET_PUBLISH_KEY }}
      maven-username: ${{ secrets.SONATYPE_USERNAME }}
      maven-password: ${{ secrets.SONATYPE_PASSWORD }}
      maven-signing-key: ${{ secrets.PGP_PRIVATE_KEY }}
      maven-signing-password: ${{ secrets.PGP_PASSPHRASE }}
```

Complete, runnable callers:

- [`examples/registry-mode.yml`](examples/registry-mode.yml) - registry-backed
  generation, pinned provider version.
- [`examples/local-binary-mode.yml`](examples/local-binary-mode.yml) - generation
  from a provider binary built by the checked-out commit.

Pin `uses:` to a commit SHA. `job_workflow_sha` is how the workflow finds its own
helper scripts, so calling it by a mutable branch name also means the scripts can
change under you.

## Two generation modes

### `mode: registry`

Resolves the provider through the Terraform/OpenTofu registry protocol.
`terraform-provider-version` must be an **exact** semver version; ranges, partial
versions and `latest` are rejected, because a published SDK that cannot be
reproduced is worse than a failed build.

```yaml
mode: registry
terraform-provider: descope/descope           # or registry.opentofu.org/descope/descope
terraform-provider-version: 0.3.16
```

### `mode: local`

Extracts the schema by executing a provider binary inside the caller checkout, so
the SDKs describe exactly the checked-out source rather than whatever the registry
serves.

```yaml
mode: local
provider-binary-path: bin/terraform-provider-descope
build-command: go build -o bin/terraform-provider-descope .
sdk-version: 0.4.0    # required: nothing in the source tree implies a version
```

`provider-binary-path` must be relative to the caller checkout and must not escape
it. `sdk-version` is required, because a working tree carries no release version.

Supplying registry inputs in local mode (or the reverse) is a hard error rather
than a silently ignored field.

## Trust boundary: `build-command`

`build-command` is **executed** on the runner, in the caller checkout, before
schema extraction. It exists because there is no other way to build an arbitrary
provider from source. Consequences:

- Treat it exactly like any other first-party build script in your repository.
  Whoever can change the caller workflow can run code in the publish pipeline.
- It runs in the `schema` and `validate-yaml` jobs, which hold **no publishing
  secrets**. Secrets are only present in the `publish` and `publish-go` jobs, which
  never execute it.
- It is passed through the environment (`CALLER_BUILD_COMMAND`) and run with an
  explicit `bash -c`, so this workflow never splices it into a larger shell
  program. That prevents *this repository* from adding an injection point; it does
  not and cannot sandbox the command itself.
- Do not build it from a pull-request-controlled expression such as
  `github.event.pull_request.title`.

Everything else, including provider addresses, versions and package coordinates,
is validated against strict patterns and passed as explicit argument arrays, never
interpolated into a shell program.

## Inputs

| input | type | default | description |
| --- | --- | --- | --- |
| `mode` | string | *required* | `registry` or `local`. |
| `terraform-provider` | string | `""` | Registry address. `registry` mode only. |
| `terraform-provider-version` | string | `""` | Exact provider semver. `registry` mode only. |
| `provider-binary-path` | string | `""` | Provider binary path in the caller checkout. `local` mode only. |
| `build-command` | string | `""` | Command run before extraction. `local` mode only. See trust boundary. |
| `sdk-version` | string | `""` | Version stamped on every SDK. Defaults to `terraform-provider-version`. |
| `languages` | string | `all` | Subset of `nodejs,python,go,dotnet,java,yaml`, or `all`. |
| `publish` | boolean | `false` | Publish. Forced off on `pull_request`. |
| `provider-name` | string | `""` | Override the generated Pulumi package name. |
| `nodejs-package-name` | string | `""` | npm name. Default `@pulumi/<package>`. |
| `python-package-name` | string | `""` | PyPI name. Default `pulumi_<package>`. |
| `go-module-path` | string | derived | Go module path. Derived from `go-sdk-repository`/`go-sdk-path`. |
| `go-sdk-repository` | string | caller repo | Repository hosting the Go module. |
| `go-sdk-path` | string | `sdk/go` | Sub-path holding the Go module. |
| `go-sdk-base-ref` | string | `main` | Parent commit for the Go SDK push. |
| `go-sdk-skip-go-get` | boolean | `false` | Skip warming `proxy.golang.org`. |
| `dotnet-root-namespace` | string | `""` | .NET root namespace. Default `Pulumi`. |
| `java-base-package` | string | `""` | Java base package and Maven groupId. |
| `maven-publish-repo-url` | string | `""` | Override the Maven repository URL. |
| `maven-staging-url` | string | `""` | Override the Sonatype staging API URL. |
| `assert-prerelease` | boolean | `false` | Refuse to publish a non-prerelease version. |
| `ref` | string | `""` | Caller ref to check out. Defaults to the triggering SHA. |
| `runs-on` | string | `ubuntu-latest` | Runner label. |
| `pulumi-version` | string | `^3` | Pulumi CLI constraint. |
| `pulumictl-version` | string | `v0.0.50` | pulumictl version used by the publisher. |
| `node-version` | string | `20.x` | |
| `python-version` | string | `3.11` | |
| `go-version` | string | `1.25` | |
| `dotnet-version` | string | `8.0.x` | |
| `java-version` | string | `11` | |

### Secrets

| secret | needed for | maps to |
| --- | --- | --- |
| `npm-token` | npm | `NODE_AUTH_TOKEN` |
| `pypi-username` | PyPI | `PYPI_USERNAME`, defaults to `__token__` |
| `pypi-password` | PyPI | `PYPI_PASSWORD` |
| `nuget-publish-key` | NuGet | `NUGET_PUBLISH_KEY` |
| `maven-username` | Maven Central | `PUBLISH_REPO_USERNAME` |
| `maven-password` | Maven Central | `PUBLISH_REPO_PASSWORD` |
| `maven-signing-key` | Maven Central | `SIGNING_KEY` (ASCII-armored PGP key) |
| `maven-signing-password` | Maven Central | `SIGNING_PASSWORD` |

Only pass the secrets for the languages you publish. A language whose secret is
absent fails in its own publisher step, not before.

### Outputs

| output | description |
| --- | --- |
| `version` | Normalized semver version stamped on every SDK. |
| `package-name` | Generated Pulumi package name. |
| `published` | Comma separated languages actually published by this run. |

## Package coordinates

Generated defaults are `@pulumi/<package>`, `pulumi_<package>`,
`Pulumi.<Package>`, and a Go import path under
`github.com/pulumi/pulumi-terraform-provider/...`. Those are correct for packages
Pulumi publishes and wrong for everybody else, so all of them are overridable.

Coordinates are applied by rewriting the package schema **before** generation.
That is the only safe place: rewriting generated sources afterwards leaves import
paths, plugin descriptors and build files disagreeing with each other.

The Go module path is not freely settable. `go get` requires the module path to
match where the code is hosted, so it is derived as
`github.com/<go-sdk-repository>/<go-sdk-path>`. Passing a `go-module-path` that
contradicts that is rejected instead of producing an unimportable SDK.

### Known limitation: cross-repository Go publication

`pulumi/publish-go-sdk-action` checks out and pushes with the job's
`GITHUB_TOKEN`, which it does not expose as an input. `go-sdk-repository` must
therefore be a repository the caller's `GITHUB_TOKEN` can write, and the caller
must grant `permissions: contents: write`. Publishing the Go SDK to an unrelated
repository is not supported; no personal-access-token knob is offered here,
because there is no supported way to feed one to that action.

## Parameterization metadata

A bridged Terraform provider is a *parameterized* Pulumi package: the SDK carries
`pulumi.parameterization` metadata (base provider name, base provider version and
an opaque base64 parameter) that lets it resolve the real provider at runtime.
Strip it and the package installs cleanly and then fails at `pulumi up`.

The schema patcher never touches the `parameterization` block, refuses to run on a
schema that lacks one, and `scripts/verify_layout.py` re-asserts its presence in
every built SDK: `bin/package.json` for npm, `pulumi-plugin.json` for Python, Go
and .NET, and the `genPulumiResources` task for Java.

## Idempotency

Published versions are immutable, so re-running a release must be a no-op rather
than a failure:

- `scripts/check_published.py` queries npm, PyPI, NuGet and Maven Central over
  their public read APIs and removes already-published languages from the
  publisher's `sdk` list. `npm publish` is the reason this exists: unlike twine's
  `--skip-existing` and `dotnet nuget push --skip-duplicate`, it hard-fails on a
  duplicate version and takes the whole job with it.
- A registry that cannot be reached is treated as "not published", so an outage
  cannot silently skip a real publication.
- `pulumi/publish-go-sdk-action` already checks whether the tag exists.

## npm packaging check

`npm publish` uploads exactly what `npm pack` produces. A package can compile and
still be unusable, because the `files` allowlist omits something a lifecycle
script or an import needs. That is how one published provider SDK shipped a
`postinstall` hook whose `scripts/` directory had been excluded: every consumer's
`npm install` failed.

`scripts/verify_npm_install.sh` therefore packs the real tarball, asserts the
compiled entrypoint and manifest are inside it, installs it into an empty project
**with lifecycle scripts enabled**, and `require()`s it to confirm the exports and
the parameterization metadata survived. `scripts/verify_layout.py` additionally
rejects any lifecycle script whose target file is missing from the package or
excluded by `files`.

## YAML is validation only

The YAML runtime has no SDK to compile and no artifact to publish. `pulumi package
add` records the package in `Pulumi.yaml` and writes
`sdks/<name>/<name>-<version>.yaml`. The workflow asserts that the `packages:`
block names the package, points at `terraform-provider`, records the pinned
provider version in `parameters:`, and that the generated descriptor carries a
complete `parameterization` block. Nothing is uploaded, and the smoke test asserts
no YAML artifact is produced.

## Security posture

- Every third-party action is pinned to an immutable commit SHA.
- `permissions: {}` at the workflow level; each job opts in to the minimum. Only
  `publish-go` gets `contents: write`.
- `persist-credentials: false` on every checkout.
- No dependency caching in a workflow that holds publishing credentials.
- Untrusted inputs are never interpolated into shell programs: they are validated
  against strict patterns, passed through the environment, and expanded into
  explicit argument arrays. `build-command` is the single documented exception.
- Publication is blocked on `pull_request` events regardless of `publish`.
- `assert-prerelease` makes an accidental stable publish from a non-release branch
  impossible.

`actionlint`, `shellcheck` and `zizmor --persona=pedantic --min-severity=low` run
in CI and are treated as blocking. The only suppressions are one narrowly scoped
`actionlint` ignore for a stale context schema (`job_workflow_sha`) and one
`shellcheck` disable for JavaScript template literals inside a single-quoted node
program. Both are annotated in place.

`pulumi-package-publisher` also exposes an `assertPrerelease` input. It is not
used: its implementation is `if [[ ... ]] then`, which is a bash syntax error, so
the check is performed here instead.

## Repository layout

```
.github/workflows/publish-terraform-provider-sdks.yml   the reusable workflow
.github/workflows/ci.yml                                unit tests, linters, smoke test
.github/workflows/self-test.yml                         real workflow_call dry run
scripts/validate_inputs.py                              input validation and normalization
scripts/patch_schema.py                                 version and coordinate pinning
scripts/build_sdk.sh                                    per-language build into publisher layout
scripts/verify_layout.py                                publisher layout and parameterization asserts
scripts/verify_npm_install.sh                            npm pack plus clean-room install
scripts/check_published.py                              registry idempotency filter
scripts/validate_yaml_package.py                        YAML package validation
scripts/pep440_version.py                               semver to PEP 440
tests/                                                  unit tests and the end-to-end smoke test
examples/                                               runnable caller workflows
```

## Local development

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v

# End-to-end: generate, build and validate against a real provider.
# Needs network access; needs no publishing credentials.
tests/smoke_test.sh descope/descope 0.3.16

actionlint
shellcheck scripts/*.sh tests/*.sh
zizmor --persona=pedantic --min-severity=low .github/workflows examples
```

## License

Apache-2.0. See [LICENSE](LICENSE).

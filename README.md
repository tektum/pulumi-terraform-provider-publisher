# Pulumi Terraform Provider Publisher

Generate and publish parameterized Pulumi SDKs for a Terraform provider to npm,
PyPI, NuGet, Maven Central, and a Go module repository. YAML packages are generated
and validated without publishing a YAML artifact.

The repository provides two public surfaces backed by the same scripts and
composite actions:

1. **Root action:** short, registry-backed, sequential, intended for most users.
2. **Reusable workflow:** parallel multi-job execution with isolated permissions;
   required for checked-out-source generation.

## Quick start: registry provider

Copy this workflow into the SDK repository:

```yaml
name: Publish Pulumi SDKs

on:
  push:
    tags: ["v*.*.*"]

permissions: {}

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: write # required only for Go SDK publication
    steps:
      - uses: tektum/pulumi-terraform-provider-publisher@v1.0.0
        with:
          terraform-provider: descope/descope
          # Defaults to github.ref_name, so a v0.3.16 tag publishes 0.3.16.
          namespace: descope
          publish: true

          npm-token: ${{ secrets.NPM_TOKEN }}
          pypi-password: ${{ secrets.PYPI_API_TOKEN }}
          nuget-publish-key: ${{ secrets.NUGET_PUBLISH_KEY }}
          maven-username: ${{ secrets.SONATYPE_USERNAME }}
          maven-password: ${{ secrets.SONATYPE_PASSWORD }}
          maven-signing-key: ${{ secrets.PGP_PRIVATE_KEY }}
          maven-signing-password: ${{ secrets.PGP_PASSPHRASE }}
```

`namespace` defaults to the Terraform registry namespace. For
`descope/descope`, these coordinates are derived:

| Registry | Derived coordinate |
| --- | --- |
| npm | `@descope/pulumi-descope` |
| PyPI | `descope_pulumi_descope` |
| NuGet | `Descope.Pulumi.Descope` |
| Maven | `com.descope.pulumi:descope` |
| Go | `github.com/<caller-repo>/sdk/go` |

Every coordinate remains explicitly overridable for existing package names.

The full manual-dispatch example is in
[`examples/registry-mode.yml`](examples/registry-mode.yml).

## Why the root action is registry-only

Composite actions run inside one caller job. They cannot create jobs, define a
matrix, or narrow permissions between internal phases. Running caller-controlled
source builds and registry publication in the same job would expose publishing
credentials and a write-capable token to the build environment.

The root action therefore:

- accepts only a registry provider at an exact version;
- never runs `build-command`;
- builds selected languages serially;
- preserves artifact layout, clean-room npm installation, idempotency checks, and
  post-publish registry verification;
- rejects publication on pull-request events.

Use the reusable workflow for local source generation.

## Advanced: exact checked-out source

```yaml
jobs:
  publish:
    permissions:
      contents: write
    uses: tektum/pulumi-terraform-provider-publisher/.github/workflows/publish.yml@v1.0.0
    with:
      mode: local
      provider-binary-path: bin/terraform-provider-descope
      build-command: go build -o bin/terraform-provider-descope .
      sdk-version: ${{ github.ref_name }}

      runtime-provider: descope/descope
      runtime-provider-version: ${{ github.ref_name }}

      namespace: descope
      publish: true
      go-sdk-repository: descope/pulumi-descope
    secrets:
      npm-token: ${{ secrets.NPM_TOKEN }}
      pypi-password: ${{ secrets.PYPI_API_TOKEN }}
      nuget-publish-key: ${{ secrets.NUGET_PUBLISH_KEY }}
      maven-username: ${{ secrets.SONATYPE_USERNAME }}
      maven-password: ${{ secrets.SONATYPE_PASSWORD }}
      maven-signing-key: ${{ secrets.PGP_PRIVATE_KEY }}
      maven-signing-password: ${{ secrets.PGP_PASSPHRASE }}
```

The complete example, including pull-request dry runs, is in
[`examples/local-binary-mode.yml`](examples/local-binary-mode.yml).

### Local-mode trust boundary

`build-command` is executed in the checked-out caller repository. The reusable
workflow runs it only in credential-free schema and YAML jobs. Package publishing
happens in separate jobs that never execute caller source.

A schema extracted from a local binary embeds that runner-local path in its
parameterization value. A published SDK could not resolve that path on another
machine. Local publication therefore requires `runtime-provider` and
`runtime-provider-version`; the workflow copies the complete parameterization
block from a real registry extraction rather than constructing the opaque value.

## Generated outputs

For each requested language:

- **Node.js:** compiles TypeScript, packs the real npm tarball, installs it in a
  clean room with lifecycle scripts enabled, and verifies exports plus
  `pulumi.parameterization` metadata.
- **Python:** creates a wheel and source distribution under the exact directory
  consumed by `pulumi/pulumi-package-publisher`.
- **.NET:** builds the NuGet package under `bin/Debug`.
- **Java:** validates the Gradle publication project and generated runtime-resource
  task; publication is performed by the upstream publisher.
- **Go:** builds the generated module, then publishes it with
  `pulumi/publish-go-sdk-action`.
- **YAML:** runs `pulumi package add terraform-provider`, validates `Pulumi.yaml`
  and the generated parameterization descriptor, and creates no artifact.

The schema is patched before code generation. Post-generation renames are not
used because they leave imports, plugin descriptors, and build files inconsistent.

## Root action inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `terraform-provider` | required | Registry address such as `descope/descope`. |
| `terraform-provider-version` | triggering tag | Exact provider semver. |
| `namespace` | registry namespace | Derives language package coordinates. |
| `languages` | `all` | `nodejs,python,go,dotnet,java,yaml` subset. |
| `publish` | `false` | Publish after generation and validation. |
| `provider-name` | provider name | Pulumi package name override. |
| `nodejs-package-name` | derived | npm override. |
| `python-package-name` | derived | PyPI override. |
| `dotnet-root-namespace` | derived | .NET namespace override. |
| `java-base-package` | derived | Java package/Maven group override. |
| `go-sdk-repository` | caller repo | Repository receiving the Go SDK tag. |
| `go-sdk-path` | `sdk/go` | Go module path within that repository. |
| `assert-prerelease` | `false` | Reject stable versions. |

Toolchain inputs are available for pinned environments: `pulumi-version`,
`pulumictl-version`, `node-version`, `python-version`, `go-version`,
`dotnet-version`, and `java-version`.

Registry credentials are action inputs named `npm-token`, `pypi-username`,
`pypi-password`, `nuget-publish-key`, `maven-username`, `maven-password`,
`maven-signing-key`, and `maven-signing-password`. Pass only credentials for the
selected languages.

The reusable workflow accepts the same registry inputs plus `mode`,
`provider-binary-path`, `build-command`, `runtime-provider`,
`runtime-provider-version`, `sdk-version`, `ref`, and `runs-on`. Its credentials
are typed `workflow_call` secrets with the same names.

## Versioning and updates

Production examples use immutable semantic release tags such as `v1.0.0`. Enable
Dependabot or Renovate for reviewed publisher updates:

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
```

For maximum content-addressing assurance, replace `@v1.0.0` with that release's
full commit SHA and retain a same-line `# v1.0.0` comment. Do not use a moving
`@v1` tag for a workflow that receives package-registry credentials unless that
mutable trust model is intentional.

## Security properties

- Every third-party action is pinned to an immutable commit SHA.
- Workflow-level permissions are empty; jobs opt into the minimum.
- Only Go publication receives `contents: write`.
- Every checkout disables persisted credentials.
- Caller build code never shares a job with package-registry credentials.
- Provider addresses, versions, package coordinates, paths, and language lists are
  validated before shell execution.
- Publication is disabled on pull-request events.
- Published immutable versions are checked before publication and read back from
  each registry afterwards.
- The reusable workflow uses GitHub's `$/` self-repository action syntax, so the
  composites and scripts resolve from the same revision selected by the caller.

`$/` requires GitHub-hosted runners version 2.336.0 or newer and is not supported
on GitHub Enterprise Server. GHES consumers must use a release that retains the
checkout-based compatibility implementation.

### Go repository limitation

`pulumi/publish-go-sdk-action` does not expose a token input. It pushes with the
caller's `GITHUB_TOKEN`, so `go-sdk-repository` must be writable by that token.
Cross-repository Go publication is unsupported unless GitHub grants that token
write access.

## Architecture

```text
action.yml                         registry-only convenience facade
actions/validate/action.yml        shared input contract
actions/schema/action.yml          schema extraction and coordinate patching
actions/build/action.yml           per-language generation and packaging
actions/validate-yaml/action.yml   YAML-only validation
actions/publish/action.yml         npm/PyPI/NuGet/Maven publication
actions/publish-go/action.yml      Go publication
.github/workflows/publish.yml      isolated parallel reusable workflow
scripts/                           behavior and invariant checks
tests/                             focused tests and live smoke tests
```

Both public surfaces call the same composites and scripts. There is no second SDK
generation or publication implementation.

## Development

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
shellcheck scripts/*.sh tests/*.sh
actionlint
zizmor --persona=pedantic --min-severity=low \
  .github/workflows action.yml actions examples

tests/smoke_test.sh descope/descope 0.3.16
tests/smoke_test_local.sh
```

CI additionally executes the real reusable workflow and root action in dry-run
mode, including Node.js, Python, Go, .NET, Java, YAML, and npm clean-room install.

## License

Apache-2.0. See [LICENSE](LICENSE).

#!/usr/bin/env python3
"""Validate and normalize reusable-workflow inputs.

Reads every input from the environment (never from argv/shell interpolation) so
that untrusted caller-supplied strings are never expanded by a shell.

Writes normalized values to $GITHUB_OUTPUT when that variable is set, and always
prints them so local runs and CI logs agree.
"""

from __future__ import annotations

import json
import os
import re
import sys

ALL_LANGUAGES = ("nodejs", "python", "go", "dotnet", "java", "yaml")

# Languages that pulumi/pulumi-package-publisher knows how to publish.
PUBLISHER_LANGUAGES = ("nodejs", "python", "dotnet", "java")

# Languages that produce no publishable artifact.
VALIDATE_ONLY_LANGUAGES = ("yaml",)

MODES = ("registry", "local")

# Semver 2.0.0, anchored, optional leading "v".
SEMVER_RE = re.compile(
    r"^v?(?P<core>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<build>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# "namespace/name" or "host/namespace/name" as accepted by the OpenTofu/Terraform
# registry protocol. Deliberately strict: no schemes, no query strings, no "..".
REGISTRY_ADDRESS_RE = re.compile(
    r"^(?:[a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9]/)?"
    r"[a-zA-Z0-9][a-zA-Z0-9._-]*/"
    r"[a-zA-Z0-9][a-zA-Z0-9._-]*$"
)

GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

NPM_NAME_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
PYPI_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
GO_MODULE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._~-]*(?:\.[a-zA-Z0-9._~-]+)+(?:/[a-zA-Z0-9._~-]+)+$")
DOTNET_NS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
JAVA_PKG_RE = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*$")
# Relative, non-escaping repository sub-path, e.g. "sdk/go". "." and ".." segments
# are excluded by the leading character class so the path cannot escape the repo.
REL_PATH_RE = re.compile(
    r"^[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$"
)


class InputError(Exception):
    """A caller supplied an invalid or incompatible combination of inputs."""


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if raw == "":
        return default
    if raw.lower() in ("true", "1", "yes"):
        return True
    if raw.lower() in ("false", "0", "no"):
        return False
    raise InputError(f"{name} must be a boolean, got {raw!r}")


def normalize_version(raw: str, field: str) -> str:
    if not raw:
        raise InputError(f"{field} is required")
    match = SEMVER_RE.match(raw)
    if not match:
        raise InputError(
            f"{field} must be an exact semver 2.0.0 version (optionally 'v'-prefixed), "
            f"got {raw!r}. Ranges, 'latest' and partial versions are rejected so that "
            f"published SDKs are reproducible."
        )
    if match.group("build"):
        raise InputError(
            f"{field} must not carry semver build metadata ({raw!r}); package registries "
            f"cannot represent '+build' suffixes."
        )
    return raw.lstrip("v")


def parse_languages(raw: str) -> list[str]:
    if not raw:
        raise InputError("languages must not be empty")
    seen: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        item = chunk.strip().lower()
        if not item:
            continue
        if item == "all":
            for lang in ALL_LANGUAGES:
                if lang not in seen:
                    seen.append(lang)
            continue
        if item not in ALL_LANGUAGES:
            raise InputError(
                f"unknown language {item!r}; supported: {', '.join(ALL_LANGUAGES)} (or 'all')"
            )
        if item not in seen:
            seen.append(item)
    if not seen:
        raise InputError("languages must resolve to at least one language")
    return [lang for lang in ALL_LANGUAGES if lang in seen]


def check(pattern: re.Pattern[str], value: str, field: str, hint: str) -> None:
    if value and not pattern.match(value):
        raise InputError(f"{field}={value!r} is invalid: {hint}")


def validate() -> dict[str, str]:
    mode = env("MODE").lower()
    if mode not in MODES:
        raise InputError(f"mode must be one of {', '.join(MODES)}, got {env('MODE')!r}")

    provider = env("TF_PROVIDER")
    provider_version = env("TF_PROVIDER_VERSION")
    provider_binary = env("PROVIDER_BINARY_PATH")
    build_command = env("BUILD_COMMAND")

    if mode == "registry":
        if not provider:
            raise InputError("mode=registry requires terraform-provider (e.g. 'descope/descope')")
        check(
            REGISTRY_ADDRESS_RE,
            provider,
            "terraform-provider",
            "expected '<namespace>/<name>' or '<host>/<namespace>/<name>'",
        )
        provider_version = normalize_version(provider_version, "terraform-provider-version")
        if provider_binary:
            raise InputError(
                "provider-binary-path is only valid for mode=local; mode=registry resolves the "
                "provider from the Terraform registry."
            )
        if build_command:
            raise InputError(
                "build-command is only valid for mode=local; mode=registry does not build "
                "provider source."
            )
    else:
        if not provider_binary:
            raise InputError(
                "mode=local requires provider-binary-path pointing at the Terraform provider "
                "binary (or its directory) inside the checked-out caller repository."
            )
        if provider_binary.startswith("/"):
            raise InputError(
                "provider-binary-path must be relative to the caller checkout, got an absolute "
                f"path {provider_binary!r}"
            )
        if ".." in provider_binary.split("/"):
            raise InputError(
                f"provider-binary-path must not escape the caller checkout, got {provider_binary!r}"
            )
        if provider:
            raise InputError(
                "terraform-provider is only valid for mode=registry; mode=local reads the schema "
                "from the checked-out provider binary."
            )
        if provider_version:
            raise InputError(
                "terraform-provider-version is only valid for mode=registry; mode=local publishes "
                "sdk-version from the checked-out source."
            )
        provider_version = ""

    sdk_version = normalize_version(env("SDK_VERSION"), "sdk-version")
    # An omitted LANGUAGES falls back to "all"; an explicitly empty one is an error,
    # because silently publishing every language is not a safe reading of "".
    languages = parse_languages(
        "all" if os.environ.get("LANGUAGES") is None else env("LANGUAGES")
    )
    publish = env_bool("PUBLISH", False)

    provider_name = env("PROVIDER_NAME")
    if provider_name and not re.match(r"^[a-z][a-z0-9-]*$", provider_name):
        raise InputError(
            f"provider-name={provider_name!r} must be a lowercase kebab-case token"
        )

    nodejs_pkg = env("NODEJS_PACKAGE_NAME")
    python_pkg = env("PYTHON_PACKAGE_NAME")
    go_module = env("GO_MODULE_PATH")
    go_repo = env("GO_SDK_REPOSITORY")
    # An explicit empty go-sdk-path means "publish at the repository root"; only an
    # absent variable falls back to the conventional sdk/go.
    go_path = "sdk/go" if os.environ.get("GO_SDK_PATH") is None else env("GO_SDK_PATH")
    go_base_ref = env("GO_SDK_BASE_REF", "main")
    dotnet_ns = env("DOTNET_ROOT_NAMESPACE")
    java_pkg = env("JAVA_BASE_PACKAGE")

    check(NPM_NAME_RE, nodejs_pkg, "nodejs-package-name", "expected an npm name like '@scope/name'")
    check(PYPI_NAME_RE, python_pkg, "python-package-name", "expected a PEP 503 compatible name")
    check(GO_MODULE_RE, go_module, "go-module-path", "expected a Go module path like 'github.com/org/repo/sdk/go'")
    check(GITHUB_REPO_RE, go_repo, "go-sdk-repository", "expected '<owner>/<repo>'")
    check(REL_PATH_RE, go_path, "go-sdk-path", "expected a relative path like 'sdk/go'")
    check(DOTNET_NS_RE, dotnet_ns, "dotnet-root-namespace", "expected a .NET namespace like 'Acme.Pulumi'")
    check(JAVA_PKG_RE, java_pkg, "java-base-package", "expected a Java package like 'com.acme.pulumi'")

    if "go" in languages:
        if publish and not go_repo:
            raise InputError(
                "publishing the Go SDK requires go-sdk-repository (the repository that hosts the "
                "Go module, e.g. 'acme/pulumi-acme'). Drop 'go' from languages to skip it."
            )
        if go_repo and not go_module:
            # The Go module path is fully determined by the target repository and sub-path;
            # derive it so that generated import paths match where the code is published.
            go_module = f"github.com/{go_repo}/{go_path}" if go_path else f"github.com/{go_repo}"
        if go_module and go_repo:
            expected = f"github.com/{go_repo}/{go_path}" if go_path else f"github.com/{go_repo}"
            if go_module != expected:
                raise InputError(
                    f"go-module-path={go_module!r} does not match the publication target "
                    f"{expected!r} derived from go-sdk-repository/go-sdk-path. Mismatched module "
                    f"paths produce a Go SDK that cannot be imported."
                )

    publisher_sdks = [lang for lang in languages if lang in PUBLISHER_LANGUAGES]
    build_languages = [lang for lang in languages if lang not in VALIDATE_ONLY_LANGUAGES]

    if publish and not publisher_sdks and "go" not in languages:
        raise InputError(
            "publish=true but the selected languages produce no publishable artifact "
            f"({', '.join(languages)}). yaml is validation-only."
        )

    return {
        "mode": mode,
        "terraform-provider": provider,
        "terraform-provider-version": provider_version,
        "provider-binary-path": provider_binary,
        "has-build-command": str(bool(build_command)).lower(),
        "provider-name": provider_name,
        "version": sdk_version,
        "version-v": f"v{sdk_version}",
        "languages": ",".join(languages),
        "languages-json": json.dumps(languages),
        "build-languages-json": json.dumps(build_languages),
        "publisher-sdks": ",".join(publisher_sdks),
        "publish-nodejs": str("nodejs" in languages).lower(),
        "publish-python": str("python" in languages).lower(),
        "publish-dotnet": str("dotnet" in languages).lower(),
        "publish-java": str("java" in languages).lower(),
        "publish-go": str("go" in languages).lower(),
        "validate-yaml": str("yaml" in languages).lower(),
        "publish": str(publish).lower(),
        "nodejs-package-name": nodejs_pkg,
        "python-package-name": python_pkg,
        "go-module-path": go_module,
        "go-sdk-repository": go_repo,
        "go-sdk-path": go_path,
        "go-sdk-base-ref": go_base_ref,
        "dotnet-root-namespace": dotnet_ns,
        "java-base-package": java_pkg,
    }


def main() -> int:
    try:
        outputs = validate()
    except InputError as exc:
        print(f"::error title=Invalid workflow inputs::{exc}", file=sys.stderr)
        return 1

    rendered = "\n".join(f"{key}={value}" for key, value in outputs.items())
    print(rendered)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

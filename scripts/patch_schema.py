#!/usr/bin/env python3
"""Pin the SDK version and language package coordinates in a Pulumi package schema.

Rewriting the schema before `pulumi package gen-sdk` is the only supported way to
control generated package coordinates. The alternative -- rewriting generated
sources after the fact -- silently breaks import paths, so it is not used here.

The `parameterization` block is never touched: it carries the base provider name,
base provider version and the opaque base64 parameter that lets the generated SDK
resolve the bridged Terraform provider at runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

IDENT_RE = re.compile(r"[^a-z0-9]+")


def go_root_package(schema: dict) -> str:
    existing = schema.get("language", {}).get("go", {}).get("rootPackageName")
    if existing:
        return str(existing)
    return IDENT_RE.sub("", str(schema.get("name", "provider")).lower()) or "provider"


def patch(schema: dict, opts: argparse.Namespace) -> dict:
    if "parameterization" not in schema:
        raise SystemExit(
            "::error title=Missing parameterization::the extracted schema has no "
            "'parameterization' block, so the generated SDK could not resolve its bridged "
            "Terraform provider at runtime. Refusing to generate a broken SDK."
        )

    schema["version"] = opts.version

    languages = schema.setdefault("language", {})

    nodejs = languages.setdefault("nodejs", {})
    if opts.nodejs_package_name:
        nodejs["packageName"] = opts.nodejs_package_name
    # `respectSchemaVersion` makes the generated package.json carry the schema version
    # verbatim instead of the ${VERSION} placeholder that provider Makefiles substitute.
    nodejs["respectSchemaVersion"] = True

    python = languages.setdefault("python", {})
    if opts.python_package_name:
        python["packageName"] = opts.python_package_name
    python["respectSchemaVersion"] = True

    go = languages.setdefault("go", {})
    root = go_root_package(schema)
    go["rootPackageName"] = root
    if opts.go_module_path:
        go["importBasePath"] = f"{opts.go_module_path.rstrip('/')}/{root}"
    go["respectSchemaVersion"] = True

    csharp = languages.setdefault("csharp", {})
    if opts.dotnet_root_namespace:
        csharp["rootNamespace"] = opts.dotnet_root_namespace
    csharp["respectSchemaVersion"] = True

    java = languages.setdefault("java", {})
    if opts.java_base_package:
        java["basePackage"] = opts.java_base_package

    return schema


def resolved_coordinates(schema: dict) -> dict:
    languages = schema.get("language", {})
    name = str(schema.get("name", ""))
    nodejs = languages.get("nodejs", {}).get("packageName") or f"@pulumi/{name}"
    python = languages.get("python", {}).get("packageName") or f"pulumi_{name.replace('-', '_')}"
    go_import = languages.get("go", {}).get("importBasePath", "")
    go_module = go_import.rsplit("/", 1)[0] if go_import else ""
    csharp_ns = languages.get("csharp", {}).get("rootNamespace") or "Pulumi"
    dotnet_pkg = f"{csharp_ns}.{name[:1].upper()}{name[1:]}" if name else csharp_ns
    java_pkg = languages.get("java", {}).get("basePackage") or ""
    return {
        "name": name,
        "version": str(schema.get("version", "")),
        "nodejs": nodejs,
        "python": python,
        "go_module": go_module,
        "go_import": go_import,
        "dotnet": dotnet_pkg,
        "java_group": java_pkg,
        "base_provider": schema.get("parameterization", {}).get("baseProvider", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True, help="path to the extracted schema JSON")
    parser.add_argument("--out", required=True, help="path to write the patched schema JSON")
    parser.add_argument("--coordinates-out", help="path to write resolved coordinates JSON")
    parser.add_argument("--version", required=True)
    parser.add_argument("--nodejs-package-name", default="")
    parser.add_argument("--python-package-name", default="")
    parser.add_argument("--go-module-path", default="")
    parser.add_argument("--dotnet-root-namespace", default="")
    parser.add_argument("--java-base-package", default="")
    opts = parser.parse_args()

    with open(opts.schema, encoding="utf-8") as handle:
        schema = json.load(handle)

    schema = patch(schema, opts)

    with open(opts.out, "w", encoding="utf-8") as handle:
        json.dump(schema, handle, indent=2, sort_keys=True)

    coordinates = resolved_coordinates(schema)
    if opts.coordinates_out:
        with open(opts.coordinates_out, "w", encoding="utf-8") as handle:
            json.dump(coordinates, handle, indent=2, sort_keys=True)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"package-name={coordinates['name']}\n")
            handle.write(f"package-version={coordinates['version']}\n")
            handle.write(f"base-provider-version={coordinates['base_provider'].get('version', '')}\n")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = [
        "### Resolved package coordinates",
        "",
        "| target | coordinate |",
        "| --- | --- |",
        f"| package | `{coordinates['name']}` |",
        f"| version | `{coordinates['version']}` |",
        f"| npm | `{coordinates['nodejs']}` |",
        f"| PyPI | `{coordinates['python']}` |",
        f"| Go module | `{coordinates['go_module'] or '(default)'}` |",
        f"| NuGet | `{coordinates['dotnet']}` |",
        f"| Maven group | `{coordinates['java_group'] or '(default)'}` |",
        "",
    ]
    print("\n".join(lines))
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Assert that a built SDK matches the layout pulumi/pulumi-package-publisher expects.

The publisher does not generate or build anything: it downloads an artifact named
`<language>-sdk.tar.gz`, untars it into `sdk/<language>` and then reads fixed paths.
Getting those paths wrong fails late, inside a job that holds publishing
credentials, so they are asserted here instead.

Also asserts that the runtime `parameterization` metadata survived the build. A
parameterized SDK without it installs fine and then fails at `pulumi up`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


class LayoutError(Exception):
    pass


def require(path: Path, description: str) -> Path:
    if not path.exists():
        raise LayoutError(f"missing {description}: {path}")
    return path


def require_glob(root: Path, pattern: str, description: str) -> list[Path]:
    matches = sorted(root.glob(pattern))
    if not matches:
        raise LayoutError(f"missing {description}: no match for {root}/{pattern}")
    return matches


def check_parameterization(payload: dict, source: Path) -> None:
    parameterization = payload.get("parameterization")
    if not parameterization:
        raise LayoutError(
            f"{source} has no 'parameterization' block; the published SDK would not be able "
            f"to resolve its bridged Terraform provider at runtime"
        )
    for field in ("name", "version", "value"):
        if not parameterization.get(field):
            raise LayoutError(f"{source} parameterization is missing '{field}'")


def check_nodejs(root: Path, version: str) -> list[str]:
    bin_dir = require(root / "bin", "compiled nodejs output directory (tsc outDir)")
    require(bin_dir / "index.js", "compiled nodejs entrypoint")
    package_json = require(bin_dir / "package.json", "nodejs bin/package.json read by the publisher")
    payload = json.loads(package_json.read_text(encoding="utf-8"))

    if payload.get("version") != version:
        raise LayoutError(
            f"bin/package.json version {payload.get('version')!r} != expected {version!r}; "
            f"the publisher's version assertion would fail"
        )
    check_parameterization(payload.get("pulumi", {}), package_json)

    notes = []
    # anomalyco/provider shipped a postinstall script while excluding the scripts/
    # directory from the package, so `npm install` failed for every consumer. Assert
    # that every lifecycle script the tarball declares is actually present.
    scripts = payload.get("scripts", {}) or {}
    lifecycle = {"preinstall", "install", "postinstall", "prepare", "prepublishOnly"}
    files_field = payload.get("files")
    for name in sorted(lifecycle & set(scripts)):
        command = scripts[name]
        notes.append(f"lifecycle script {name}: {command}")
        for token in str(command).split():
            candidate = token.strip("'\"")
            if candidate.endswith((".js", ".sh", ".cjs", ".mjs")):
                require(bin_dir / candidate, f"file referenced by the '{name}' lifecycle script")
                if files_field is not None:
                    top = candidate.split("/", 1)[0]
                    if not any(str(entry).split("/", 1)[0] == top for entry in files_field):
                        raise LayoutError(
                            f"'{name}' script runs {candidate} but package.json 'files' does not "
                            f"include {top!r}; npm install would fail for consumers"
                        )
    if not scripts.get("postinstall") and not (bin_dir / "scripts").exists():
        notes.append("no install-time lifecycle scripts (plugin resolves at runtime)")
    return notes


def check_python(root: Path, version: str) -> list[str]:
    bin_dir = require(root / "bin", "python build root (sdk/python/bin)")
    dist = require(bin_dir / "dist", "python dist directory read by twine")
    wheels = require_glob(dist, "*.whl", "python wheel")
    require_glob(dist, "*.tar.gz", "python source distribution")
    package_dirs = [
        path
        for path in bin_dir.iterdir()
        if path.is_dir() and (path / "pulumi-plugin.json").exists()
    ]
    if not package_dirs:
        raise LayoutError(f"no python package directory with pulumi-plugin.json under {bin_dir}")
    payload = json.loads((package_dirs[0] / "pulumi-plugin.json").read_text(encoding="utf-8"))
    check_parameterization(payload, package_dirs[0] / "pulumi-plugin.json")
    return [f"wheel: {wheels[0].name}", f"expected version: {version}"]


def check_dotnet(root: Path, version: str) -> list[str]:
    packages = require_glob(
        root, "bin/Debug/*.nupkg", "NuGet package in bin/Debug read by the publisher"
    )
    require_glob(root, "*.csproj", "dotnet project file")
    plugin = require(root / "pulumi-plugin.json", "dotnet pulumi-plugin.json")
    check_parameterization(json.loads(plugin.read_text(encoding="utf-8")), plugin)
    matching = [pkg for pkg in packages if version in pkg.name]
    if not matching:
        raise LayoutError(
            f"no .nupkg in bin/Debug carries version {version!r}: "
            f"{[pkg.name for pkg in packages]}"
        )
    return [f"nupkg: {matching[0].name}"]


def check_java(root: Path, version: str) -> list[str]:
    build_gradle = require(
        root / "build.gradle", "java build.gradle used by the publisher's gradle build"
    )
    require(root / "settings.gradle", "java settings.gradle")
    require_glob(root, "src/main/java/**/*.java", "generated java sources")

    # pulumi-java-gen embeds the runtime plugin descriptor in a `genPulumiResources`
    # gradle task rather than shipping a checked-in resource file, so version.txt and
    # plugin.json only exist after `gradle build`. Assert the generator instead.
    body = build_gradle.read_text(encoding="utf-8")
    for needle, description in (
        ("genPulumiResources", "genPulumiResources task that emits plugin.json"),
        ("parameterization", "parameterization block inside genPulumiResources"),
        ('new File(outDir, "version.txt")', "version.txt emitter"),
        ("nexusPublishing", "nexusPublishing block that defines the publishToSonatype task"),
        ('System.getenv("PACKAGE_VERSION")', "PACKAGE_VERSION override the publisher sets"),
    ):
        if needle not in body:
            raise LayoutError(f"build.gradle is missing the {description}")

    group = re.search(r'^group\s*=\s*"([^"]+)"', body, re.MULTILINE)
    artifact = re.search(r'^\s*artifactId\s*=\s*"([^"]+)"', body, re.MULTILINE)
    if not group or not group.group(1):
        raise LayoutError("build.gradle has no maven group; set java-base-package")
    return [
        f"maven coordinates: {group.group(1)}:{artifact.group(1) if artifact else '?'}",
        f"expected version: {version} (applied via PACKAGE_VERSION)",
    ]


def check_go(root: Path, version: str) -> list[str]:
    go_mod = require(root / "go.mod", "go.mod at the root of the published Go module")
    module_line = next(
        (
            line.split(None, 1)[1].strip()
            for line in go_mod.read_text(encoding="utf-8").splitlines()
            if line.startswith("module ")
        ),
        "",
    )
    if not module_line:
        raise LayoutError(f"{go_mod} has no module directive")
    plugins = require_glob(root, "**/pulumi-plugin.json", "go pulumi-plugin.json")
    check_parameterization(json.loads(plugins[0].read_text(encoding="utf-8")), plugins[0])
    return [f"module: {module_line}", f"expected version: {version}"]


CHECKS = {
    "nodejs": check_nodejs,
    "python": check_python,
    "dotnet": check_dotnet,
    "java": check_java,
    "go": check_go,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", required=True, choices=sorted(CHECKS))
    parser.add_argument("--root", required=True, help="path to sdk/<language>")
    parser.add_argument("--version", required=True)
    opts = parser.parse_args()

    try:
        notes = CHECKS[opts.language](Path(opts.root), opts.version)
    except LayoutError as exc:
        print(f"::error title=Bad {opts.language} SDK layout::{exc}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, OSError) as exc:
        print(f"::error title=Unreadable {opts.language} SDK::{exc}", file=sys.stderr)
        return 1

    print(f"{opts.language} SDK layout OK")
    for note in notes:
        print(f"  - {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Validate the YAML-runtime result of `pulumi package add`.

The YAML runtime has no SDK to compile and no artifact to publish: `pulumi package
add` records the package in Pulumi.yaml and writes a package descriptor under
sdks/<name>/<name>-<version>.yaml. Publication is therefore intentionally out of
scope; what has to be proven is that a YAML program can resolve the package with
the exact provider version that was pinned.

Both documents are generator-written and use a tiny YAML subset (nested block
mappings plus block sequences of scalars), so they are parsed here with a small
indentation-driven reader rather than adding a PyYAML dependency to the workflow.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KEY_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*(.*)$")
ITEM_RE = re.compile(r"^(\s*)-\s*(.*)$")


class YamlValidationError(Exception):
    pass


def scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_block(text: str) -> dict:
    """Parse nested block mappings and scalar block sequences.

    Returns a dict whose values are either strings, lists of strings, or nested
    dicts. Unsupported constructs (anchors, flow collections, multi-line scalars)
    are not produced by the Pulumi generators being inspected and are not handled.
    """
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    root: dict = {}
    # Stack of (indent, container) where container is the dict being filled.
    stack: list[tuple[int, dict]] = [(-1, root)]

    index = 0
    while index < len(lines):
        line = lines[index]
        match = KEY_RE.match(line)
        if not match:
            index += 1
            continue

        indent = len(match.group(1))
        key, raw_value = match.group(2), match.group(3).strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        container = stack[-1][1]

        if raw_value:
            container[key] = scalar(raw_value)
            index += 1
            continue

        # Empty value: either a nested mapping or a block sequence. Look ahead.
        following = lines[index + 1] if index + 1 < len(lines) else ""
        item = ITEM_RE.match(following)
        if item and len(item.group(1)) > indent:
            items: list[str] = []
            index += 1
            while index < len(lines):
                candidate = ITEM_RE.match(lines[index])
                if not candidate or len(candidate.group(1)) <= indent:
                    break
                items.append(scalar(candidate.group(2)))
                index += 1
            container[key] = items
            continue

        nested: dict = {}
        container[key] = nested
        stack.append((indent, nested))
        index += 1

    return root


def validate(
    project_dir: Path,
    package_name: str,
    version: str | None,
    base_provider: str,
    expect_parameters: list[str],
    expect_package_version: str | None = None,
) -> list[str]:
    project_file = project_dir / "Pulumi.yaml"
    if not project_file.exists():
        raise YamlValidationError(f"missing {project_file}")

    project = parse_block(project_file.read_text(encoding="utf-8"))
    packages = project.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise YamlValidationError(
            f"{project_file} has no 'packages:' block; `pulumi package add` did not record "
            f"the package, so a YAML program could not resolve it"
        )

    entry = packages.get(package_name)
    if not isinstance(entry, dict):
        raise YamlValidationError(
            f"{project_file} does not declare a package named {package_name!r}; "
            f"found {sorted(packages)}"
        )

    if entry.get("source") != base_provider:
        raise YamlValidationError(
            f"{project_file} package source is {entry.get('source')!r}, "
            f"expected {base_provider!r}"
        )

    parameters = entry.get("parameters") or []
    if expect_parameters and parameters != expect_parameters:
        raise YamlValidationError(
            f"{project_file} parameters are {parameters!r}, expected {expect_parameters!r}; "
            f"the pinned Terraform provider version was not recorded"
        )

    # `pulumi package add` names the descriptor after the *parameterized package*
    # version (the Terraform provider version), not the base provider version, so
    # the file is discovered rather than assumed unless a version is pinned.
    package_dir = project_dir / "sdks" / package_name
    discovered = sorted(package_dir.glob(f"{package_name}-*.yaml"))
    if version:
        descriptor = package_dir / f"{package_name}-{version}.yaml"
    elif len(discovered) == 1:
        descriptor = discovered[0]
    elif not discovered:
        descriptor = package_dir / f"{package_name}-<version>.yaml"
    else:
        raise YamlValidationError(
            f"expected exactly one package descriptor under "
            f"{package_dir.relative_to(project_dir)}, found "
            f"{[path.name for path in discovered]}"
        )
    if not descriptor.exists():
        sdks_dir = project_dir / "sdks"
        candidates = (
            sorted(str(path.relative_to(project_dir)) for path in sdks_dir.rglob("*.yaml"))
            if sdks_dir.exists()
            else []
        )
        raise YamlValidationError(
            f"missing generated package descriptor {descriptor.relative_to(project_dir)}; "
            f"found {candidates or 'nothing'}"
        )

    document = parse_block(descriptor.read_text(encoding="utf-8"))
    if document.get("name") != base_provider:
        raise YamlValidationError(
            f"{descriptor} base provider name is {document.get('name')!r}, "
            f"expected {base_provider!r}"
        )

    parameterization = document.get("parameterization")
    if not isinstance(parameterization, dict):
        raise YamlValidationError(
            f"{descriptor} has no 'parameterization' block; the YAML program would not be "
            f"able to resolve the bridged Terraform provider"
        )
    for field in ("name", "version", "value"):
        if not parameterization.get(field):
            raise YamlValidationError(
                f"{descriptor} parameterization is missing '{field}'; the YAML program would "
                f"not be able to resolve the bridged Terraform provider"
            )

    if expect_package_version and parameterization["version"] != expect_package_version:
        raise YamlValidationError(
            f"{descriptor} parameterization version is {parameterization['version']!r}, "
            f"expected {expect_package_version!r}; the pinned Terraform provider version "
            f"did not reach the generated package"
        )

    if parameterization["name"] != package_name:
        raise YamlValidationError(
            f"{descriptor} parameterization names package {parameterization['name']!r}, "
            f"expected {package_name!r}"
        )

    return [
        f"Pulumi.yaml declares package {package_name!r} from {base_provider!r}",
        f"parameters: {parameters}",
        f"descriptor: {descriptor.relative_to(project_dir)}",
        f"parameterized package: {parameterization['name']}@{parameterization['version']}",
        f"base provider: {base_provider}@{document.get('version')}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--package-name", required=True)
    parser.add_argument(
        "--version",
        default="",
        help="expected parameterized package version; discovered from sdks/ when omitted",
    )
    parser.add_argument(
        "--expect-package-version",
        default="",
        help="assert the descriptor's parameterization version equals this value",
    )
    parser.add_argument("--base-provider", default="terraform-provider")
    parser.add_argument(
        "--expect-parameter",
        action="append",
        default=[],
        help="expected `parameters:` entry, repeatable and order sensitive",
    )
    opts = parser.parse_args()

    try:
        notes = validate(
            Path(opts.project_dir),
            opts.package_name,
            opts.version or None,
            opts.base_provider,
            opts.expect_parameter,
            opts.expect_package_version or None,
        )
    except YamlValidationError as exc:
        print(f"::error title=YAML package validation failed::{exc}", file=sys.stderr)
        return 1

    print("YAML package validation OK")
    for note in notes:
        print(f"  - {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Wait until every requested immutable SDK version is visible in its registry.

This is required because pulumi/pulumi-package-publisher marks its Java publication
step `continue-on-error: true`. Without an independent read-back, a failed Maven
publication can leave the workflow green. The other registries are checked too so
that the workflow output only reports artifacts that consumers can actually fetch.

Registry reads are unauthenticated. A transient network failure is retried and can
never be interpreted as success.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import check_published


def state(language: str, coordinates: dict, version: str) -> tuple[str, bool]:
    if language == "nodejs":
        name = coordinates["nodejs"]
        return name, check_published.npm_published(name, version)
    if language == "python":
        name = coordinates["python"]
        return name, check_published.pypi_published(name, version)
    if language == "dotnet":
        name = coordinates["dotnet"]
        return name, check_published.nuget_published(name, version)
    if language == "java":
        group = coordinates.get("java_group", "")
        artifact = coordinates.get("name", "")
        name = f"{group}:{artifact}"
        return name, check_published.maven_published(group, artifact, version)
    raise ValueError(f"unsupported registry language: {language}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coordinates", required=True)
    parser.add_argument("--languages", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--interval", type=float, default=30.0)
    opts = parser.parse_args()

    if opts.attempts < 1:
        parser.error("--attempts must be at least 1")
    if opts.interval < 0:
        parser.error("--interval must be non-negative")

    with open(opts.coordinates, encoding="utf-8") as handle:
        coordinates = json.load(handle)

    unresolved = [item.strip() for item in opts.languages.split(",") if item.strip()]
    if not unresolved:
        print("no newly published SDKs to verify")
        return 0

    for attempt in range(1, opts.attempts + 1):
        print(f"registry verification attempt {attempt}/{opts.attempts}")
        remaining: list[str] = []
        for language in unresolved:
            name, published = state(language, coordinates, opts.version)
            print(
                f"  {language}: {name}@{opts.version} "
                f"{'is visible' if published else 'is not visible yet'}"
            )
            if not published:
                remaining.append(language)

        unresolved = remaining
        if not unresolved:
            print("every published SDK is visible in its registry")
            return 0
        if attempt < opts.attempts:
            time.sleep(opts.interval)

    print(
        "::error title=Published SDK verification failed::"
        f"these SDKs are still absent after {opts.attempts} attempts: "
        f"{','.join(unresolved)}. pulumi-package-publisher may have failed or the "
        "registry may still be unavailable.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Convert a semver version into the PEP 440 form used by Pulumi Python SDKs.

Mirrors `pulumictl convert-version --language python` for the cases the publisher
pipeline can encounter, without requiring pulumictl to be installed.

    1.2.3              -> 1.2.3
    1.2.3-alpha.4      -> 1.2.3a4
    1.2.3-beta.4       -> 1.2.3b4
    1.2.3-rc.4         -> 1.2.3rc4
    1.2.3-dev.4        -> 1.2.3.dev4
"""

from __future__ import annotations

import re
import sys

PRERELEASE_MAP = {"alpha": "a", "beta": "b", "rc": "rc", "dev": ".dev"}


def convert(version: str) -> str:
    version = version.strip().lstrip("v")
    core, _, prerelease = version.partition("-")
    if not prerelease:
        return core
    match = re.match(r"^(alpha|beta|rc|dev)\.?(\d+)$", prerelease)
    if not match:
        raise SystemExit(
            f"cannot convert prerelease {prerelease!r} to PEP 440; use "
            f"alpha.N, beta.N, rc.N or dev.N"
        )
    kind, number = match.groups()
    return f"{core}{PRERELEASE_MAP[kind]}{number}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: pep440_version.py <semver>")
    print(convert(sys.argv[1]))

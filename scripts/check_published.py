#!/usr/bin/env python3
"""Filter the publisher's language list down to versions that are not published yet.

Public package registries treat published versions as immutable. Re-running a
release workflow must therefore be a no-op rather than a hard failure, and it must
never attempt to overwrite an existing version.

twine (`--skip-existing`), `dotnet nuget push` (`--skip-duplicate`) and
publish-go-sdk-action (tag existence check) are already idempotent. `npm publish`
is not: it exits non-zero on a duplicate version and fails the whole publish job.
This script queries each registry over its public read API and emits the reduced
`sdk` value for pulumi/pulumi-package-publisher, so an already-published language
is skipped instead of failing.

Read-only, unauthenticated requests only. A registry that cannot be reached is
reported as "not published" so that a transient outage cannot silently skip a
real publication.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 20
USER_AGENT = "pulumi-terraform-provider-publisher/1"


def http_status(url: str) -> int | None:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  warning: {url} unreachable ({exc}); treating as not published")
        return None


def npm_published(package: str, version: str) -> bool:
    # npm scoped names must keep their '@' but have '/' percent-encoded.
    encoded = urllib.parse.quote(package, safe="@")
    return http_status(f"https://registry.npmjs.org/{encoded}/{version}") == 200


def pypi_published(package: str, version: str) -> bool:
    encoded = urllib.parse.quote(package, safe="")
    return http_status(f"https://pypi.org/pypi/{encoded}/{version}/json") == 200


def nuget_published(package: str, version: str) -> bool:
    package_id = package.lower()
    encoded_id = urllib.parse.quote(package_id, safe="")
    encoded_version = urllib.parse.quote(version.lower(), safe="")
    url = (
        "https://api.nuget.org/v3-flatcontainer/"
        f"{encoded_id}/{encoded_version}/{encoded_id}.{encoded_version}.nupkg"
    )
    return http_status(url) == 200


def maven_published(group: str, artifact: str, version: str) -> bool:
    if not group or not artifact:
        return False
    group_path = group.replace(".", "/")
    url = (
        "https://repo1.maven.org/maven2/"
        f"{group_path}/{artifact}/{version}/{artifact}-{version}.pom"
    )
    return http_status(url) == 200


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coordinates", required=True, help="coordinates JSON from patch_schema.py")
    parser.add_argument("--languages", required=True, help="comma separated candidate languages")
    parser.add_argument("--version", required=True)
    opts = parser.parse_args()

    with open(opts.coordinates, encoding="utf-8") as handle:
        coordinates = json.load(handle)

    candidates = [item.strip() for item in opts.languages.split(",") if item.strip()]
    version = opts.version

    remaining: list[str] = []
    skipped: list[str] = []

    for language in candidates:
        if language == "nodejs":
            name = coordinates["nodejs"]
            published = npm_published(name, version)
        elif language == "python":
            name = coordinates["python"]
            published = pypi_published(name, version)
        elif language == "dotnet":
            name = coordinates["dotnet"]
            published = nuget_published(name, version)
        elif language == "java":
            group = coordinates.get("java_group", "")
            artifact = coordinates.get("name", "")
            name = f"{group}:{artifact}"
            published = maven_published(group, artifact, version)
        else:
            print(f"  {language}: no registry check, will publish")
            remaining.append(language)
            continue

        if published:
            print(f"  {language}: {name}@{version} already published, skipping")
            skipped.append(language)
        else:
            print(f"  {language}: {name}@{version} not published, will publish")
            remaining.append(language)

    outputs = {
        "sdk": ",".join(remaining),
        "skipped": ",".join(skipped),
        "any": str(bool(remaining)).lower(),
    }
    rendered = "\n".join(f"{key}={value}" for key, value in outputs.items())
    print(rendered)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

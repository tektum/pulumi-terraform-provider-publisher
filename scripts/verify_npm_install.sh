#!/usr/bin/env bash
# Pack the publishable npm package and install the resulting tarball in a clean room.
#
# Usage: verify_npm_install.sh <package-root>
#
# Rationale: `npm publish` uploads exactly what `npm pack` produces. A package can
# type-check, compile and pass every unit test while still being unusable, because
# the "files" allowlist omits something a lifecycle script or an import needs. That
# is how anomalyco/provider shipped a broken package: a postinstall script survived
# while the scripts/ directory it invoked was excluded, so `npm install` failed for
# every consumer. Installing the real tarball into an empty project is the only
# check that catches it before publication.
set -euo pipefail

PACKAGE_ROOT="${1:?package root required}"
PACKAGE_ROOT="$(cd -- "${PACKAGE_ROOT}" && pwd)"

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "${WORKDIR}"; }
trap cleanup EXIT

echo "packing ${PACKAGE_ROOT}"
TARBALL_NAME="$(npm pack --pack-destination "${WORKDIR}" --json "${PACKAGE_ROOT}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["filename"])')"
TARBALL="${WORKDIR}/${TARBALL_NAME}"
echo "tarball: ${TARBALL}"

PACKAGE_NAME="$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["name"])' \
  "${PACKAGE_ROOT}/package.json")"
PACKAGE_VERSION="$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["version"])' \
  "${PACKAGE_ROOT}/package.json")"

CONTENTS="$(tar -tzf "${TARBALL}" | sort)"
echo "::group::tarball contents"
printf '%s\n' "${CONTENTS}"
echo "::endgroup::"

# Assert the compiled entrypoint and the package manifest are inside the tarball,
# not merely on disk next to it. The listing is captured once: piping `tar` into
# `grep -q` would make grep exit early, SIGPIPE tar, and trip `pipefail`.
for required in "package/index.js" "package/index.d.ts" "package/package.json"; do
  if ! printf '%s\n' "${CONTENTS}" | grep -qxF "${required}"; then
    echo "::error title=npm tarball is missing ${required}::the published package would be unusable" >&2
    exit 1
  fi
done

CLEANROOM="${WORKDIR}/cleanroom"
mkdir -p "${CLEANROOM}"
cat >"${CLEANROOM}/package.json" <<'JSON'
{
  "name": "pulumi-sdk-cleanroom",
  "version": "0.0.0",
  "private": true,
  "type": "commonjs"
}
JSON

echo "::group::clean-room npm install"
# --ignore-scripts is deliberately NOT passed: install-time lifecycle scripts are
# precisely what this check must exercise.
( cd "${CLEANROOM}" && npm install --no-audit --no-fund --loglevel=warn "${TARBALL}" )
echo "::endgroup::"

echo "::group::clean-room require()"
# shellcheck disable=SC2016  # the single quotes are deliberate: the ${...} below
# are JavaScript template literals evaluated by node, not shell expansions.
( cd "${CLEANROOM}" && node -e '
const name = process.argv[1];
const expected = process.argv[2];
const sdk = require(name);
const manifest = require(name + "/package.json");
if (manifest.version !== expected) {
  throw new Error(`installed version ${manifest.version} != expected ${expected}`);
}
const parameterization = manifest.pulumi && manifest.pulumi.parameterization;
if (!parameterization || !parameterization.name || !parameterization.value) {
  throw new Error("installed package lost its pulumi.parameterization metadata");
}
const exported = Object.keys(sdk);
if (exported.length === 0) {
  throw new Error("installed package exports nothing");
}
console.log(`require("${name}") ok: ${exported.length} exports`);
console.log(`parameterization: ${parameterization.name}@${parameterization.version}`);
' "${PACKAGE_NAME}" "${PACKAGE_VERSION}" )
echo "::endgroup::"

echo "npm package ${PACKAGE_NAME}@${PACKAGE_VERSION} installs and loads cleanly"

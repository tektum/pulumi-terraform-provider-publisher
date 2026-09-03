#!/usr/bin/env bash
# Build one generated Pulumi SDK into the exact layout
# pulumi/pulumi-package-publisher consumes, then tar it as <sdk-root>/<language>.tar.gz.
#
# Usage: build_sdk.sh <language> <sdk-root> <version>
#   language   one of nodejs|python|dotnet|java|go
#   sdk-root   directory holding the generated per-language SDKs (e.g. ./sdk)
#   version    semver version already baked into the generated SDK metadata
#
# Inputs arrive as positional arguments, never as inline shell interpolation, so a
# caller-controlled string cannot become shell syntax.
set -euo pipefail

LANGUAGE="${1:?language required}"
SDK_ROOT="${2:?sdk root required}"
VERSION="${3:?version required}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LANG_DIR="${SDK_ROOT}/${LANGUAGE}"

if [[ ! -d "${LANG_DIR}" ]]; then
  echo "::error title=Missing generated SDK::${LANG_DIR} does not exist" >&2
  exit 1
fi

group() { echo "::group::$*"; }
endgroup() { echo "::endgroup::"; }

build_nodejs() {
  group "npm install"
  npm --prefix "${LANG_DIR}" install --no-audit --no-fund
  endgroup

  group "tsc"
  ( cd "${LANG_DIR}" && npx --no-install tsc )
  endgroup

  # `tsc` writes compiled output to bin/ but nothing else; the publishable package
  # root is bin/, so the manifest and docs have to be copied in alongside it.
  cp "${LANG_DIR}/package.json" "${LANG_DIR}/bin/package.json"
  if [[ -f "${LANG_DIR}/README.md" ]]; then
    cp "${LANG_DIR}/README.md" "${LANG_DIR}/bin/README.md"
  fi
  if [[ -f "${SCRIPT_DIR}/../LICENSE" ]]; then
    cp "${SCRIPT_DIR}/../LICENSE" "${LANG_DIR}/bin/LICENSE"
  fi

  # node_modules must never reach the artifact: it is multi-hundred-megabyte, and the
  # publisher publishes bin/ verbatim.
  rm -rf "${LANG_DIR}/node_modules"
}

build_python() {
  # Pulumi provider SDKs build from a copy at sdk/python/bin so that the publisher's
  # fixed sdk/python/bin/dist path resolves.
  rm -rf "${LANG_DIR:?}/bin"
  mkdir -p "${LANG_DIR}/bin"
  tar -cf - -C "${LANG_DIR}" --exclude=./bin . | tar -xf - -C "${LANG_DIR}/bin"

  local pep440
  pep440="$(python3 "${SCRIPT_DIR}/pep440_version.py" "${VERSION}")"
  group "pin PEP 440 version ${pep440}"
  python3 - "${LANG_DIR}/bin/pyproject.toml" "${pep440}" <<'PY'
import re
import sys

path, version = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    body = handle.read()
patched, count = re.subn(
    r'(?m)^(\s*version\s*=\s*)"[^"]*"', lambda m: f'{m.group(1)}"{version}"', body, count=1
)
if count != 1:
    raise SystemExit(f"could not find a version assignment in {path}")
with open(path, "w", encoding="utf-8") as handle:
    handle.write(patched)
print(f"pyproject version -> {version}")
PY
  endgroup

  group "build wheel and sdist"
  # PEP 517 build front end, in order of preference. GitHub runners provide pip via
  # actions/setup-python; uv is accepted so the same script runs on hosts whose
  # system Python is externally managed and has no pip.
  if python3 -c 'import build' >/dev/null 2>&1; then
    python3 -m build --outdir "${LANG_DIR}/bin/dist" "${LANG_DIR}/bin"
  elif python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install --quiet --upgrade build
    python3 -m build --outdir "${LANG_DIR}/bin/dist" "${LANG_DIR}/bin"
  elif command -v uv >/dev/null 2>&1; then
    uv build --out-dir "${LANG_DIR}/bin/dist" "${LANG_DIR}/bin"
  else
    echo "::error title=No Python build front end::install the 'build' module (pip install build) or uv" >&2
    exit 1
  fi
  endgroup
}

build_dotnet() {
  group "dotnet build"
  # GeneratePackageOnBuild is set in the generated csproj, so a Debug build emits the
  # .nupkg into bin/Debug -- exactly where the publisher looks.
  dotnet build "${LANG_DIR}" --configuration Debug -p:ContinuousIntegrationBuild=true
  endgroup
}

build_java() {
  # The publisher runs gradle itself against sdk/java, so nothing is compiled here.
  # Only the generated gradle project needs to reach the artifact.
  group "java project inventory"
  find "${LANG_DIR}" -maxdepth 1 -mindepth 1 -printf '%f\n' | sort
  endgroup
}

build_go() {
  group "go build"
  ( cd "${LANG_DIR}" && go mod tidy -e && go build ./... )
  endgroup
}

"build_${LANGUAGE}"

group "verify layout"
python3 "${SCRIPT_DIR}/verify_layout.py" \
  --language "${LANGUAGE}" \
  --root "${LANG_DIR}" \
  --version "${VERSION}"
endgroup

if [[ "${LANGUAGE}" == "nodejs" ]]; then
  group "verify npm tarball installs cleanly"
  "${SCRIPT_DIR}/verify_npm_install.sh" "${LANG_DIR}/bin"
  endgroup
fi

# The publisher untars this into sdk/<language>, so the archive must contain the
# directory *contents*, not the directory itself.
group "tar ${LANGUAGE}.tar.gz"
tar -czf "${SDK_ROOT}/${LANGUAGE}.tar.gz" -C "${LANG_DIR}" .
ls -l "${SDK_ROOT}/${LANGUAGE}.tar.gz"
endgroup

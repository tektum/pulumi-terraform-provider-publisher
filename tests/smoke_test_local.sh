#!/usr/bin/env bash
# End-to-end smoke test for local mode: extract the schema from a provider binary
# built from source, re-point the runtime parameterization at a released provider,
# and prove the published SDK would resolve that release rather than a local path.
#
# Usage:
#   tests/smoke_test_local.sh [provider-repo] [tag] [runtime-provider] [runtime-version]
#
# Defaults clone and build descope/terraform-provider-descope at v0.3.16 and point
# the runtime parameterization at descope/descope 0.3.16.
#
# Requires network access and a Go toolchain. Requires no publishing credentials.
set -euo pipefail

PROVIDER_REPO="${1:-descope/terraform-provider-descope}"
PROVIDER_TAG="${2:-v0.3.16}"
RUNTIME_PROVIDER="${3:-descope/descope}"
RUNTIME_VERSION="${4:-0.3.16}"
SDK_VERSION="${SDK_VERSION:-0.4.0}"

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "${WORKDIR}"; }
trap cleanup EXIT

export PULUMI_HOME="${WORKDIR}/.pulumi"
export PULUMI_SKIP_UPDATE_CHECK=true

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

CALLER="${WORKDIR}/caller"
BINARY_PATH="bin/terraform-provider-under-test"

step "Clone ${PROVIDER_REPO}@${PROVIDER_TAG}"
git clone -q --depth 1 --branch "${PROVIDER_TAG}" \
  "https://github.com/${PROVIDER_REPO}.git" "${CALLER}"

step "Run the caller build command"
# Mirrors how the workflow executes a caller-supplied build-command: through the
# environment and an explicit interpreter, in the caller checkout.
CALLER_BUILD_COMMAND="go build -o ${BINARY_PATH} ."
( cd "${CALLER}" && bash -euo pipefail -c "${CALLER_BUILD_COMMAND}" )
[[ -x "${CALLER}/${BINARY_PATH}" ]] || fail "build-command did not produce ${BINARY_PATH}"

step "Validate local-mode inputs (publish requires a runtime provider)"
if MODE=local \
   PROVIDER_BINARY_PATH="${BINARY_PATH}" \
   SDK_VERSION="${SDK_VERSION}" \
   LANGUAGES=nodejs \
   PUBLISH=true \
   GO_SDK_REPOSITORY=tektum/pulumi-smoke-descope \
     python3 "${REPO_ROOT}/scripts/validate_inputs.py" >/dev/null 2>&1; then
  fail "publish=true without runtime-provider must be rejected"
fi
echo "rejected, as intended"

MODE=local \
PROVIDER_BINARY_PATH="${BINARY_PATH}" \
SDK_VERSION="${SDK_VERSION}" \
LANGUAGES=nodejs \
PUBLISH=true \
RUNTIME_PROVIDER="${RUNTIME_PROVIDER}" \
RUNTIME_PROVIDER_VERSION="${RUNTIME_VERSION}" \
GO_SDK_REPOSITORY=tektum/pulumi-smoke-descope \
  python3 "${REPO_ROOT}/scripts/validate_inputs.py" >/dev/null
echo "accepted with runtime-provider ${RUNTIME_PROVIDER}@${RUNTIME_VERSION}"

step "Extract the schema from the built binary"
( cd "${CALLER}" \
  && pulumi package get-schema terraform-provider -- "./${BINARY_PATH}" \
     >"${WORKDIR}/schema.json" )
python3 - "${WORKDIR}/schema.json" <<'PY'
import base64
import json
import sys

schema = json.load(open(sys.argv[1], encoding="utf-8"))
parameter = base64.b64decode(schema["parameterization"]["parameter"]).decode()
print(f"local schema version:   {schema['version']}")
print(f"local schema parameter: {parameter}")
if "local" not in parameter:
    raise SystemExit("expected the local schema to carry a local-path parameter")
PY

step "Extract the runtime parameterization from the registry"
pulumi package get-schema terraform-provider \
  -- "${RUNTIME_PROVIDER}" "${RUNTIME_VERSION}" \
  >"${WORKDIR}/runtime-schema.json" 2>/dev/null

step "Pin version, coordinates and runtime parameterization"
python3 "${REPO_ROOT}/scripts/patch_schema.py" \
  --schema "${WORKDIR}/schema.json" \
  --out "${WORKDIR}/schema-patched.json" \
  --coordinates-out "${WORKDIR}/coordinates.json" \
  --version "${SDK_VERSION}" \
  --nodejs-package-name "@smoke/pulumi-local-descope" \
  --runtime-parameterization "${WORKDIR}/runtime-schema.json"

step "Generate and build the nodejs SDK"
BUILD_DIR="${WORKDIR}/build"
mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"
pulumi package gen-sdk "${WORKDIR}/schema-patched.json" --language nodejs --out ./sdk \
  2>&1 | grep -vE '^warning: <nil>' || true
"${REPO_ROOT}/scripts/build_sdk.sh" nodejs ./sdk "${SDK_VERSION}"

step "Assert the published SDK resolves a released provider, not a local path"
python3 - ./sdk/nodejs/bin/package.json "${SDK_VERSION}" "${RUNTIME_VERSION}" <<'PY'
import base64
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
expected_sdk_version, expected_runtime_version = sys.argv[2], sys.argv[3]

parameterization = manifest["pulumi"]["parameterization"]
parameter = base64.b64decode(parameterization["value"]).decode()
print(f"sdk version:   {manifest['version']}")
print(f"npm name:      {manifest['name']}")
print(f"parameter:     {parameter}")

if manifest["version"] != expected_sdk_version:
    raise SystemExit(f"sdk version {manifest['version']} != {expected_sdk_version}")
if "local" in parameter:
    raise SystemExit("the published SDK still carries a machine-local provider path")
payload = json.loads(parameter)
if "remote" not in payload:
    raise SystemExit(f"expected a remote parameter, got {payload}")
if payload["remote"]["version"] != expected_runtime_version:
    raise SystemExit(
        f"runtime provider version {payload['remote']['version']} "
        f"!= {expected_runtime_version}"
    )
print("published SDK resolves", payload["remote"]["url"], "@", payload["remote"]["version"])
PY

printf '\n\033[32mLocal-mode smoke test passed (%s@%s -> %s@%s)\033[0m\n' \
  "${PROVIDER_REPO}" "${PROVIDER_TAG}" "${RUNTIME_PROVIDER}" "${RUNTIME_VERSION}"

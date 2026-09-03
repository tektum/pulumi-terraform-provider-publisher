#!/usr/bin/env bash
# End-to-end smoke test: run the same generation, build and validation pipeline the
# reusable workflow runs, against a real registry-backed Terraform provider.
#
# Requires network access to the OpenTofu registry and the Pulumi plugin CDN.
# Requires no publishing credentials and creates no cloud resources.
#
# Usage:
#   tests/smoke_test.sh [provider] [version] [languages]
#
# Defaults to descope/descope 0.3.16 and every language whose toolchain is present.
set -euo pipefail

PROVIDER="${1:-descope/descope}"
PROVIDER_VERSION="${2:-0.3.16}"

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

detect_languages() {
  local detected=()
  command -v npm  >/dev/null 2>&1 && detected+=("nodejs")
  command -v python3 >/dev/null 2>&1 && detected+=("python")
  command -v go   >/dev/null 2>&1 && detected+=("go")
  command -v dotnet >/dev/null 2>&1 && detected+=("dotnet")
  detected+=("java")
  echo "${detected[*]}"
}

LANGUAGES="${3:-$(detect_languages)}"
LANGUAGES="${LANGUAGES//,/ }"

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "${WORKDIR}"; }
trap cleanup EXIT

export PULUMI_HOME="${WORKDIR}/.pulumi"
export PULUMI_SKIP_UPDATE_CHECK=true

# Coordinates deliberately differ from every generator default, so a silent
# fallback to the default names fails the test.
NODEJS_PACKAGE_NAME="@smoke/pulumi-smoke-descope"
PYTHON_PACKAGE_NAME="smoke_pulumi_descope"
GO_MODULE_PATH="github.com/tektum/pulumi-smoke-descope/sdk/go"
DOTNET_ROOT_NAMESPACE="Smoke.Pulumi"
JAVA_BASE_PACKAGE="com.smoke.pulumi"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

step "Validate inputs (${PROVIDER}@${PROVIDER_VERSION})"
MODE=registry \
TF_PROVIDER="${PROVIDER}" \
TF_PROVIDER_VERSION="${PROVIDER_VERSION}" \
SDK_VERSION="${PROVIDER_VERSION}" \
LANGUAGES="all" \
PUBLISH=false \
GO_SDK_REPOSITORY="tektum/pulumi-smoke-descope" \
GO_SDK_PATH="sdk/go" \
NODEJS_PACKAGE_NAME="${NODEJS_PACKAGE_NAME}" \
PYTHON_PACKAGE_NAME="${PYTHON_PACKAGE_NAME}" \
DOTNET_ROOT_NAMESPACE="${DOTNET_ROOT_NAMESPACE}" \
JAVA_BASE_PACKAGE="${JAVA_BASE_PACKAGE}" \
  python3 "${REPO_ROOT}/scripts/validate_inputs.py" >/dev/null

step "Extract schema"
pulumi package get-schema terraform-provider -- "${PROVIDER}" "${PROVIDER_VERSION}" \
  >"${WORKDIR}/schema.json" 2>"${WORKDIR}/get-schema.log" \
  || { cat "${WORKDIR}/get-schema.log" >&2; fail "pulumi package get-schema failed"; }
printf 'schema: %s bytes\n' "$(wc -c <"${WORKDIR}/schema.json")"

step "Pin version and coordinates"
python3 "${REPO_ROOT}/scripts/patch_schema.py" \
  --schema "${WORKDIR}/schema.json" \
  --out "${WORKDIR}/schema-patched.json" \
  --coordinates-out "${WORKDIR}/coordinates.json" \
  --version "${PROVIDER_VERSION}" \
  --nodejs-package-name "${NODEJS_PACKAGE_NAME}" \
  --python-package-name "${PYTHON_PACKAGE_NAME}" \
  --go-module-path "${GO_MODULE_PATH}" \
  --dotnet-root-namespace "${DOTNET_ROOT_NAMESPACE}" \
  --java-base-package "${JAVA_BASE_PACKAGE}"

BUILD_DIR="${WORKDIR}/build"
mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

for language in ${LANGUAGES}; do
  [[ "${language}" == "yaml" ]] && continue
  step "Generate ${language} SDK"
  pulumi package gen-sdk "${WORKDIR}/schema-patched.json" \
    --language "${language}" --out ./sdk 2>&1 | grep -vE '^warning: <nil>' || true
  [[ -d "./sdk/${language}" ]] || fail "no sdk/${language} directory was generated"

  step "Build and verify ${language} SDK"
  "${REPO_ROOT}/scripts/build_sdk.sh" "${language}" ./sdk "${PROVIDER_VERSION}"
done

step "Assert generated coordinates were honoured"
if [[ -f ./sdk/nodejs/package.json ]]; then
  actual="$(python3 -c 'import json;print(json.load(open("./sdk/nodejs/package.json"))["name"])')"
  [[ "${actual}" == "${NODEJS_PACKAGE_NAME}" ]] \
    || fail "npm package name is ${actual}, expected ${NODEJS_PACKAGE_NAME}"
  echo "npm name: ${actual}"
fi
if [[ -f ./sdk/go/go.mod ]]; then
  actual="$(awk '/^module /{print $2}' ./sdk/go/go.mod)"
  [[ "${actual}" == "${GO_MODULE_PATH}" ]] \
    || fail "go module is ${actual}, expected ${GO_MODULE_PATH}"
  echo "go module: ${actual}"
fi
if [[ -f ./sdk/java/build.gradle ]]; then
  grep -q "group = \"${JAVA_BASE_PACKAGE}\"" ./sdk/java/build.gradle \
    || fail "java group is not ${JAVA_BASE_PACKAGE}"
  echo "java group: ${JAVA_BASE_PACKAGE}"
fi

step "Generate and validate the YAML package"
YAML_PROJECT="${WORKDIR}/yaml-project"
mkdir -p "${YAML_PROJECT}"
cat >"${YAML_PROJECT}/Pulumi.yaml" <<'YAML'
name: pulumi-sdk-yaml-validation
runtime: yaml
description: Ephemeral project used to validate YAML package resolution.
YAML
( cd "${YAML_PROJECT}" \
  && pulumi package add terraform-provider "${PROVIDER}" "${PROVIDER_VERSION}" >/dev/null )
cat "${YAML_PROJECT}/Pulumi.yaml"

PACKAGE_NAME="$(python3 -c \
  'import json,sys;print(json.load(open(sys.argv[1]))["name"])' "${WORKDIR}/coordinates.json")"
python3 "${REPO_ROOT}/scripts/validate_yaml_package.py" \
  --project-dir "${YAML_PROJECT}" \
  --package-name "${PACKAGE_NAME}" \
  --expect-package-version "${PROVIDER_VERSION}" \
  --expect-parameter "${PROVIDER}" \
  --expect-parameter "${PROVIDER_VERSION}"

step "Assert no YAML artifact is produced for publication"
if compgen -G "./sdk/yaml*" >/dev/null; then
  fail "the yaml runtime must not produce a publishable artifact"
fi
echo "yaml runtime is validation-only, as intended"

step "Artifacts ready for pulumi-package-publisher"
ls -l ./sdk/*.tar.gz

printf '\n\033[32mSmoke test passed for %s@%s (%s)\033[0m\n' \
  "${PROVIDER}" "${PROVIDER_VERSION}" "${LANGUAGES}"

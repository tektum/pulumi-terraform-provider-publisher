#!/usr/bin/env python3
"""Tests for scripts/validate_inputs.py.

These assert the observable contract of the reusable workflow's input surface:
which combinations are accepted, which are rejected, and what normalized values
downstream jobs receive. They need no network and no publishing credentials.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import validate_inputs  # noqa: E402


REGISTRY_BASE = {
    "MODE": "registry",
    "TF_PROVIDER": "descope/descope",
    "TF_PROVIDER_VERSION": "0.3.16",
    "SDK_VERSION": "0.3.16",
    "LANGUAGES": "all",
    "PUBLISH": "false",
    "GO_SDK_REPOSITORY": "omercnet/pulumi-descope",
    "GO_SDK_PATH": "sdk/go",
}

LOCAL_BASE = {
    "MODE": "local",
    "PROVIDER_BINARY_PATH": "bin/terraform-provider-descope",
    "SDK_VERSION": "0.4.0",
    "LANGUAGES": "all",
    "PUBLISH": "false",
    "GO_SDK_REPOSITORY": "omercnet/pulumi-descope",
    "GO_SDK_PATH": "sdk/go",
}


class ValidateInputsTest(unittest.TestCase):
    def run_validate(self, **overrides: str) -> dict[str, str]:
        env = {key: value for key, value in overrides.items() if value is not None}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            return validate_inputs.validate()

    def expect_error(self, fragment: str, **overrides: str) -> None:
        with self.assertRaises(validate_inputs.InputError) as caught:
            self.run_validate(**overrides)
        self.assertIn(fragment, str(caught.exception))

    # --- mode ---------------------------------------------------------------

    def test_unknown_mode_rejected(self):
        self.expect_error("mode must be one of", **{**REGISTRY_BASE, "MODE": "hybrid"})

    def test_empty_mode_rejected(self):
        self.expect_error("mode must be one of", **{**REGISTRY_BASE, "MODE": ""})

    # --- registry mode ------------------------------------------------------

    def test_registry_mode_happy_path(self):
        outputs = self.run_validate(**REGISTRY_BASE)
        self.assertEqual(outputs["mode"], "registry")
        self.assertEqual(outputs["version"], "0.3.16")
        self.assertEqual(outputs["version-v"], "v0.3.16")
        self.assertEqual(outputs["terraform-provider-version"], "0.3.16")
        self.assertEqual(
            outputs["languages"], "nodejs,python,go,dotnet,java,yaml"
        )
        self.assertEqual(outputs["publisher-sdks"], "nodejs,python,dotnet,java")
        self.assertEqual(outputs["validate-yaml"], "true")

    def test_registry_mode_accepts_host_qualified_address(self):
        outputs = self.run_validate(
            **{**REGISTRY_BASE, "TF_PROVIDER": "registry.opentofu.org/descope/descope"}
        )
        self.assertEqual(outputs["terraform-provider"], "registry.opentofu.org/descope/descope")

    def test_registry_mode_requires_provider(self):
        self.expect_error(
            "mode=registry requires terraform-provider",
            **{**REGISTRY_BASE, "TF_PROVIDER": ""},
        )

    def test_registry_mode_rejects_version_range(self):
        self.expect_error(
            "exact semver",
            **{**REGISTRY_BASE, "TF_PROVIDER_VERSION": "^0.3.0", "SDK_VERSION": "0.3.16"},
        )

    def test_registry_mode_rejects_latest(self):
        self.expect_error(
            "exact semver",
            **{**REGISTRY_BASE, "TF_PROVIDER_VERSION": "latest"},
        )

    def test_registry_mode_rejects_partial_version(self):
        self.expect_error(
            "exact semver",
            **{**REGISTRY_BASE, "TF_PROVIDER_VERSION": "0.3"},
        )

    def test_registry_mode_rejects_missing_version(self):
        self.expect_error(
            "terraform-provider-version is required",
            **{**REGISTRY_BASE, "TF_PROVIDER_VERSION": ""},
        )

    def test_registry_mode_rejects_provider_url(self):
        self.expect_error(
            "is invalid",
            **{**REGISTRY_BASE, "TF_PROVIDER": "https://registry.opentofu.org/descope/descope"},
        )

    def test_registry_mode_rejects_path_traversal_in_address(self):
        self.expect_error(
            "is invalid",
            **{**REGISTRY_BASE, "TF_PROVIDER": "../../etc/passwd"},
        )

    def test_registry_mode_rejects_local_only_inputs(self):
        self.expect_error(
            "provider-binary-path is only valid for mode=local",
            **{**REGISTRY_BASE, "PROVIDER_BINARY_PATH": "bin/provider"},
        )
        self.expect_error(
            "build-command is only valid for mode=local",
            **{**REGISTRY_BASE, "BUILD_COMMAND": "make build"},
        )

    def test_build_metadata_rejected(self):
        self.expect_error(
            "build metadata",
            **{**REGISTRY_BASE, "TF_PROVIDER_VERSION": "0.3.16+deadbeef"},
        )

    def test_v_prefix_normalized(self):
        outputs = self.run_validate(
            **{**REGISTRY_BASE, "TF_PROVIDER_VERSION": "v0.3.16", "SDK_VERSION": "v0.3.16"}
        )
        self.assertEqual(outputs["version"], "0.3.16")
        self.assertEqual(outputs["terraform-provider-version"], "0.3.16")

    def test_prerelease_accepted(self):
        outputs = self.run_validate(
            **{**REGISTRY_BASE, "SDK_VERSION": "1.0.0-alpha.7"}
        )
        self.assertEqual(outputs["version"], "1.0.0-alpha.7")

    # --- local mode ---------------------------------------------------------

    def test_local_mode_happy_path(self):
        outputs = self.run_validate(**{**LOCAL_BASE, "BUILD_COMMAND": "make provider"})
        self.assertEqual(outputs["mode"], "local")
        self.assertEqual(outputs["version"], "0.4.0")
        self.assertEqual(outputs["terraform-provider-version"], "")
        self.assertEqual(outputs["has-build-command"], "true")

    def test_local_mode_build_command_optional(self):
        outputs = self.run_validate(**LOCAL_BASE)
        self.assertEqual(outputs["has-build-command"], "false")

    def test_local_mode_requires_binary_path(self):
        self.expect_error(
            "mode=local requires provider-binary-path",
            **{**LOCAL_BASE, "PROVIDER_BINARY_PATH": ""},
        )

    def test_local_mode_rejects_absolute_binary_path(self):
        self.expect_error(
            "absolute path",
            **{**LOCAL_BASE, "PROVIDER_BINARY_PATH": "/usr/local/bin/provider"},
        )

    def test_local_mode_rejects_escaping_binary_path(self):
        self.expect_error(
            "must not escape",
            **{**LOCAL_BASE, "PROVIDER_BINARY_PATH": "../../etc/passwd"},
        )

    def test_local_mode_rejects_registry_only_inputs(self):
        self.expect_error(
            "terraform-provider is only valid for mode=registry",
            **{**LOCAL_BASE, "TF_PROVIDER": "descope/descope"},
        )
        self.expect_error(
            "terraform-provider-version is only valid for mode=registry",
            **{**LOCAL_BASE, "TF_PROVIDER_VERSION": "0.3.16"},
        )

    def test_local_mode_requires_sdk_version(self):
        self.expect_error(
            "sdk-version is required",
            **{**LOCAL_BASE, "SDK_VERSION": ""},
        )

    # --- runtime parameterization (local mode publication) -------------------

    def test_local_publish_without_runtime_provider_is_rejected(self):
        self.expect_error(
            "requires runtime-provider",
            **{**LOCAL_BASE, "PUBLISH": "true"},
        )

    def test_local_publish_with_runtime_provider_accepted(self):
        outputs = self.run_validate(
            **{
                **LOCAL_BASE,
                "PUBLISH": "true",
                "RUNTIME_PROVIDER": "descope/descope",
                "RUNTIME_PROVIDER_VERSION": "v0.3.16",
            }
        )
        self.assertEqual(outputs["runtime-provider"], "descope/descope")
        self.assertEqual(outputs["runtime-provider-version"], "0.3.16")
        self.assertEqual(outputs["needs-runtime-graft"], "true")

    def test_local_dry_run_needs_no_runtime_provider(self):
        outputs = self.run_validate(**LOCAL_BASE)
        self.assertEqual(outputs["needs-runtime-graft"], "false")

    def test_local_dry_run_may_still_graft(self):
        outputs = self.run_validate(
            **{
                **LOCAL_BASE,
                "RUNTIME_PROVIDER": "descope/descope",
                "RUNTIME_PROVIDER_VERSION": "0.3.16",
            }
        )
        self.assertEqual(outputs["needs-runtime-graft"], "true")

    def test_runtime_provider_version_without_provider_is_rejected(self):
        self.expect_error(
            "requires runtime-provider",
            **{**LOCAL_BASE, "RUNTIME_PROVIDER_VERSION": "0.3.16"},
        )

    def test_runtime_provider_version_must_be_exact(self):
        self.expect_error(
            "exact semver",
            **{
                **LOCAL_BASE,
                "RUNTIME_PROVIDER": "descope/descope",
                "RUNTIME_PROVIDER_VERSION": "^0.3.0",
            },
        )

    def test_runtime_provider_rejected_in_registry_mode(self):
        self.expect_error(
            "only valid for mode=local",
            **{**REGISTRY_BASE, "RUNTIME_PROVIDER": "descope/descope"},
        )

    # --- languages ----------------------------------------------------------

    def test_language_subset_and_ordering(self):
        outputs = self.run_validate(**{**REGISTRY_BASE, "LANGUAGES": "yaml, nodejs"})
        self.assertEqual(outputs["languages"], "nodejs,yaml")
        self.assertEqual(outputs["build-languages-json"], '["nodejs"]')
        self.assertEqual(outputs["publisher-sdks"], "nodejs")
        self.assertEqual(outputs["publish-go"], "false")

    def test_yaml_only_produces_empty_build_matrix(self):
        outputs = self.run_validate(**{**REGISTRY_BASE, "LANGUAGES": "yaml"})
        self.assertEqual(outputs["build-languages-json"], "[]")
        self.assertEqual(outputs["publisher-sdks"], "")

    def test_duplicate_languages_deduplicated(self):
        outputs = self.run_validate(**{**REGISTRY_BASE, "LANGUAGES": "go,go,nodejs,go"})
        self.assertEqual(outputs["languages"], "nodejs,go")

    def test_unknown_language_rejected(self):
        self.expect_error("unknown language 'ruby'", **{**REGISTRY_BASE, "LANGUAGES": "ruby"})

    def test_empty_languages_rejected(self):
        self.expect_error("must not be empty", **{**REGISTRY_BASE, "LANGUAGES": ""})

    def test_yaml_only_publish_rejected(self):
        self.expect_error(
            "yaml is validation-only",
            **{**REGISTRY_BASE, "LANGUAGES": "yaml", "PUBLISH": "true"},
        )

    def test_publish_must_be_boolean(self):
        self.expect_error("must be a boolean", **{**REGISTRY_BASE, "PUBLISH": "maybe"})

    # --- go coordinates -----------------------------------------------------

    def test_go_module_path_derived_from_repository(self):
        outputs = self.run_validate(**REGISTRY_BASE)
        self.assertEqual(
            outputs["go-module-path"], "github.com/omercnet/pulumi-descope/sdk/go"
        )

    def test_go_module_path_mismatch_rejected(self):
        self.expect_error(
            "does not match the publication target",
            **{**REGISTRY_BASE, "GO_MODULE_PATH": "github.com/someone/else/sdk/go"},
        )

    def test_go_module_path_consistent_accepted(self):
        outputs = self.run_validate(
            **{
                **REGISTRY_BASE,
                "GO_SDK_PATH": "sdk",
                "GO_MODULE_PATH": "github.com/omercnet/pulumi-descope/sdk",
            }
        )
        self.assertEqual(outputs["go-module-path"], "github.com/omercnet/pulumi-descope/sdk")

    def test_go_publish_requires_repository(self):
        self.expect_error(
            "requires go-sdk-repository",
            **{
                **REGISTRY_BASE,
                "LANGUAGES": "go",
                "PUBLISH": "true",
                "GO_SDK_REPOSITORY": "",
            },
        )

    def test_go_publish_without_go_language_needs_no_repository(self):
        outputs = self.run_validate(
            **{
                **REGISTRY_BASE,
                "LANGUAGES": "nodejs",
                "PUBLISH": "true",
                "GO_SDK_REPOSITORY": "",
            }
        )
        self.assertEqual(outputs["publish-go"], "false")

    def test_go_sdk_path_traversal_rejected(self):
        self.expect_error(
            "go-sdk-path",
            **{**REGISTRY_BASE, "GO_SDK_PATH": "../outside"},
        )

    # --- package coordinates ------------------------------------------------

    def test_valid_coordinates_pass_through(self):
        outputs = self.run_validate(
            **{
                **REGISTRY_BASE,
                "NODEJS_PACKAGE_NAME": "@descope/pulumi-descope",
                "PYTHON_PACKAGE_NAME": "descope-pulumi",
                "DOTNET_ROOT_NAMESPACE": "Descope.Pulumi",
                "JAVA_BASE_PACKAGE": "com.descope.pulumi",
            }
        )
        self.assertEqual(outputs["nodejs-package-name"], "@descope/pulumi-descope")
        self.assertEqual(outputs["python-package-name"], "descope-pulumi")
        self.assertEqual(outputs["dotnet-root-namespace"], "Descope.Pulumi")
        self.assertEqual(outputs["java-base-package"], "com.descope.pulumi")

    def test_invalid_npm_name_rejected(self):
        self.expect_error(
            "nodejs-package-name",
            **{**REGISTRY_BASE, "NODEJS_PACKAGE_NAME": "Not A Package"},
        )

    def test_invalid_java_package_rejected(self):
        self.expect_error(
            "java-base-package",
            **{**REGISTRY_BASE, "JAVA_BASE_PACKAGE": "Com.Descope.Pulumi"},
        )

    def test_invalid_dotnet_namespace_rejected(self):
        self.expect_error(
            "dotnet-root-namespace",
            **{**REGISTRY_BASE, "DOTNET_ROOT_NAMESPACE": "9Descope"},
        )

    def test_invalid_provider_name_rejected(self):
        self.expect_error(
            "provider-name",
            **{**REGISTRY_BASE, "PROVIDER_NAME": "Descope_Provider"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

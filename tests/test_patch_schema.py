#!/usr/bin/env python3
"""Tests for scripts/patch_schema.py and scripts/pep440_version.py."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import patch_schema  # noqa: E402
import pep440_version  # noqa: E402


def base_schema() -> dict:
    return {
        "name": "descope",
        "version": "0.3.16",
        "parameterization": {
            "baseProvider": {"name": "terraform-provider", "version": "1.4.0"},
            "parameter": "eyJyZW1vdGUiOnt9fQ==",
        },
        "language": {
            "go": {
                "importBasePath": "github.com/pulumi/pulumi-terraform-provider/sdks/go/descope/descope",
                "rootPackageName": "descope",
            },
            "nodejs": {"packageDescription": "bridged"},
        },
    }


def options(**overrides) -> argparse.Namespace:
    defaults = {
        "version": "0.3.16",
        "nodejs_package_name": "",
        "python_package_name": "",
        "go_module_path": "",
        "dotnet_root_namespace": "",
        "java_base_package": "",
        "runtime_parameterization": "",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class PatchSchemaTest(unittest.TestCase):
    def test_parameterization_is_preserved_verbatim(self):
        schema = base_schema()
        original = json.loads(json.dumps(schema["parameterization"]))
        patched = patch_schema.patch(schema, options())
        self.assertEqual(patched["parameterization"], original)

    def test_missing_parameterization_is_fatal(self):
        schema = base_schema()
        del schema["parameterization"]
        with self.assertRaises(SystemExit) as caught:
            patch_schema.patch(schema, options())
        self.assertIn("parameterization", str(caught.exception))

    def test_version_is_pinned(self):
        patched = patch_schema.patch(base_schema(), options(version="1.2.3"))
        self.assertEqual(patched["version"], "1.2.3")

    def test_go_import_base_path_is_rebuilt_under_module_path(self):
        patched = patch_schema.patch(
            base_schema(),
            options(go_module_path="github.com/omercnet/pulumi-descope/sdk/go"),
        )
        self.assertEqual(
            patched["language"]["go"]["importBasePath"],
            "github.com/omercnet/pulumi-descope/sdk/go/descope",
        )
        self.assertEqual(patched["language"]["go"]["rootPackageName"], "descope")

    def test_go_module_path_trailing_slash_tolerated(self):
        patched = patch_schema.patch(
            base_schema(), options(go_module_path="github.com/o/p/sdk/go/")
        )
        self.assertEqual(
            patched["language"]["go"]["importBasePath"], "github.com/o/p/sdk/go/descope"
        )

    def test_go_root_package_derived_when_absent(self):
        schema = base_schema()
        schema["name"] = "my-provider"
        del schema["language"]["go"]
        patched = patch_schema.patch(schema, options(go_module_path="github.com/o/p/sdk/go"))
        self.assertEqual(patched["language"]["go"]["rootPackageName"], "myprovider")
        self.assertEqual(
            patched["language"]["go"]["importBasePath"], "github.com/o/p/sdk/go/myprovider"
        )

    def test_coordinates_are_overridden_when_supplied(self):
        patched = patch_schema.patch(
            base_schema(),
            options(
                nodejs_package_name="@descope/pulumi-descope",
                python_package_name="descope_pulumi",
                dotnet_root_namespace="Descope.Pulumi",
                java_base_package="com.descope.pulumi",
            ),
        )
        languages = patched["language"]
        self.assertEqual(languages["nodejs"]["packageName"], "@descope/pulumi-descope")
        self.assertEqual(languages["python"]["packageName"], "descope_pulumi")
        self.assertEqual(languages["csharp"]["rootNamespace"], "Descope.Pulumi")
        self.assertEqual(languages["java"]["basePackage"], "com.descope.pulumi")

    def test_existing_language_settings_are_not_dropped(self):
        patched = patch_schema.patch(base_schema(), options())
        self.assertEqual(patched["language"]["nodejs"]["packageDescription"], "bridged")

    def test_respect_schema_version_is_forced(self):
        patched = patch_schema.patch(base_schema(), options())
        for language in ("nodejs", "python", "go", "csharp"):
            self.assertTrue(
                patched["language"][language]["respectSchemaVersion"],
                f"{language} must carry the schema version into its package metadata",
            )

    def test_resolved_coordinates_defaults(self):
        patched = patch_schema.patch(base_schema(), options())
        coordinates = patch_schema.resolved_coordinates(patched)
        self.assertEqual(coordinates["nodejs"], "@pulumi/descope")
        self.assertEqual(coordinates["python"], "pulumi_descope")
        self.assertEqual(coordinates["dotnet"], "Pulumi.Descope")
        self.assertEqual(coordinates["base_provider"]["name"], "terraform-provider")

    def test_resolved_coordinates_overrides(self):
        patched = patch_schema.patch(
            base_schema(),
            options(
                nodejs_package_name="@descope/pulumi-descope",
                python_package_name="descope_pulumi",
                dotnet_root_namespace="Descope.Pulumi",
                java_base_package="com.descope.pulumi",
                go_module_path="github.com/omercnet/pulumi-descope/sdk/go",
            ),
        )
        coordinates = patch_schema.resolved_coordinates(patched)
        self.assertEqual(coordinates["nodejs"], "@descope/pulumi-descope")
        self.assertEqual(coordinates["python"], "descope_pulumi")
        self.assertEqual(coordinates["dotnet"], "Descope.Pulumi.Descope")
        self.assertEqual(coordinates["java_group"], "com.descope.pulumi")
        self.assertEqual(coordinates["go_module"], "github.com/omercnet/pulumi-descope/sdk/go")


class GraftParameterizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def runtime_schema(self, payload: dict) -> str:
        path = self.root / "runtime.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def local_schema(self) -> dict:
        schema = base_schema()
        # This is verbatim what `pulumi package get-schema terraform-provider -- ./bin/x`
        # produces: a parameter naming a path on the build machine.
        schema["parameterization"] = {
            "baseProvider": {"name": "terraform-provider", "version": "1.4.0"},
            "parameter": "eyJsb2NhbCI6eyJwYXRoIjoiLi9iaW4vdGVycmFmb3JtLXByb3ZpZGVyLWRlc2NvcGUifX0=",
        }
        return schema

    def test_local_parameter_is_replaced_by_registry_parameter(self):
        runtime = {
            "parameterization": {
                "baseProvider": {"name": "terraform-provider", "version": "1.4.0"},
                "parameter": "eyJyZW1vdGUiOnsidXJsIjoicmVnaXN0cnkub3BlbnRvZnUub3JnL2Rlc2NvcGUvZGVzY29wZSIsInZlcnNpb24iOiIwLjMuMTYifX0=",
            }
        }
        patched = patch_schema.patch(
            self.local_schema(),
            options(runtime_parameterization=self.runtime_schema(runtime)),
        )
        parameter = base64.b64decode(patched["parameterization"]["parameter"]).decode()
        self.assertIn("remote", parameter)
        self.assertNotIn("local", parameter)

    def test_graft_is_skipped_when_not_requested(self):
        patched = patch_schema.patch(self.local_schema(), options())
        parameter = base64.b64decode(patched["parameterization"]["parameter"]).decode()
        self.assertIn("local", parameter)

    def test_runtime_schema_without_parameter_is_fatal(self):
        runtime = {"parameterization": {"baseProvider": {"name": "terraform-provider"}}}
        with self.assertRaises(SystemExit) as caught:
            patch_schema.patch(
                self.local_schema(),
                options(runtime_parameterization=self.runtime_schema(runtime)),
            )
        self.assertIn("Unusable runtime parameterization", str(caught.exception))

    def test_base_provider_mismatch_is_fatal(self):
        runtime = {
            "parameterization": {
                "baseProvider": {"name": "some-other-bridge", "version": "1.0.0"},
                "parameter": "e30=",
            }
        }
        with self.assertRaises(SystemExit) as caught:
            patch_schema.patch(
                self.local_schema(),
                options(runtime_parameterization=self.runtime_schema(runtime)),
            )
        self.assertIn("Base provider mismatch", str(caught.exception))


class Pep440Test(unittest.TestCase):
    def test_release_versions_unchanged(self):
        self.assertEqual(pep440_version.convert("0.3.16"), "0.3.16")
        self.assertEqual(pep440_version.convert("v1.0.0"), "1.0.0")

    def test_prerelease_conversion(self):
        self.assertEqual(pep440_version.convert("1.2.3-alpha.4"), "1.2.3a4")
        self.assertEqual(pep440_version.convert("1.2.3-beta.4"), "1.2.3b4")
        self.assertEqual(pep440_version.convert("1.2.3-rc.4"), "1.2.3rc4")
        self.assertEqual(pep440_version.convert("1.2.3-dev.4"), "1.2.3.dev4")

    def test_unsupported_prerelease_is_rejected(self):
        with self.assertRaises(SystemExit):
            pep440_version.convert("1.2.3-nightly")


if __name__ == "__main__":
    unittest.main(verbosity=2)

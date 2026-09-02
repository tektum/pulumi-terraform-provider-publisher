#!/usr/bin/env python3
"""Tests for scripts/patch_schema.py and scripts/pep440_version.py."""

from __future__ import annotations

import argparse
import json
import sys
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

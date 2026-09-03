#!/usr/bin/env python3
"""Tests for scripts/verify_layout.py and scripts/validate_yaml_package.py.

Layout fixtures mirror the exact paths pulumi/pulumi-package-publisher reads, and
the negative cases mirror real breakages: a missing bin/ directory, a version that
drifted from the requested one, stripped parameterization metadata, and the
"postinstall script without its scripts/ directory" packaging bug.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import validate_yaml_package  # noqa: E402
import verify_layout  # noqa: E402

PARAMETERIZATION = {
    "name": "descope",
    "version": "0.3.16",
    "value": "eyJyZW1vdGUiOnt9fQ==",
}

PLUGIN_JSON = {
    "resource": True,
    "name": "terraform-provider",
    "version": "1.4.0",
    "parameterization": PARAMETERIZATION,
}


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_json(path: Path, payload: dict) -> Path:
    return write(path, json.dumps(payload, indent=2))


class TempTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class NodejsLayoutTest(TempTreeTest):
    def build(self, **package_overrides) -> Path:
        sdk = self.root / "nodejs"
        package = {
            "name": "@descope/pulumi-descope",
            "version": "0.3.16",
            "pulumi": PLUGIN_JSON,
        }
        package.update(package_overrides)
        write(sdk / "bin" / "index.js", "module.exports = {};\n")
        write_json(sdk / "bin" / "package.json", package)
        return sdk

    def test_valid_layout_passes(self):
        sdk = self.build()
        notes = verify_layout.check_nodejs(sdk, "0.3.16")
        self.assertTrue(any("no install-time lifecycle" in note for note in notes))

    def test_missing_bin_directory_is_rejected(self):
        sdk = self.root / "nodejs"
        write(sdk / "index.ts", "export const x = 1;\n")
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_nodejs(sdk, "0.3.16")
        self.assertIn("compiled nodejs output directory", str(caught.exception))

    def test_version_drift_is_rejected(self):
        sdk = self.build(version="0.3.15")
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_nodejs(sdk, "0.3.16")
        self.assertIn("version assertion", str(caught.exception))

    def test_stripped_parameterization_is_rejected(self):
        sdk = self.build(pulumi={"resource": True, "name": "terraform-provider"})
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_nodejs(sdk, "0.3.16")
        self.assertIn("no 'parameterization' block", str(caught.exception))

    def test_incomplete_parameterization_is_rejected(self):
        broken = dict(PLUGIN_JSON, parameterization={"name": "descope", "version": "0.3.16"})
        sdk = self.build(pulumi=broken)
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_nodejs(sdk, "0.3.16")
        self.assertIn("missing 'value'", str(caught.exception))

    def test_postinstall_without_its_script_file_is_rejected(self):
        # The anomalyco/provider breakage: a postinstall hook whose target file is
        # not in the package.
        sdk = self.build(scripts={"postinstall": "node scripts/install-pulumi-plugin.js"})
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_nodejs(sdk, "0.3.16")
        self.assertIn("install-pulumi-plugin.js", str(caught.exception))

    def test_postinstall_excluded_by_files_allowlist_is_rejected(self):
        sdk = self.build(
            scripts={"postinstall": "node scripts/install-pulumi-plugin.js"},
            files=["index.js", "package.json"],
        )
        write(sdk / "bin" / "scripts" / "install-pulumi-plugin.js", "// noop\n")
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_nodejs(sdk, "0.3.16")
        self.assertIn("'files' does not include", str(caught.exception))

    def test_postinstall_with_allowlisted_script_passes(self):
        sdk = self.build(
            scripts={"postinstall": "node scripts/install-pulumi-plugin.js"},
            files=["index.js", "package.json", "scripts"],
        )
        write(sdk / "bin" / "scripts" / "install-pulumi-plugin.js", "// noop\n")
        notes = verify_layout.check_nodejs(sdk, "0.3.16")
        self.assertTrue(any("lifecycle script postinstall" in note for note in notes))


class PythonLayoutTest(TempTreeTest):
    def build(self) -> Path:
        sdk = self.root / "python"
        write(sdk / "bin" / "dist" / "pulumi_descope-0.3.16-py3-none-any.whl", "wheel")
        write(sdk / "bin" / "dist" / "pulumi_descope-0.3.16.tar.gz", "sdist")
        write_json(sdk / "bin" / "pulumi_descope" / "pulumi-plugin.json", PLUGIN_JSON)
        return sdk

    def test_valid_layout_passes(self):
        verify_layout.check_python(self.build(), "0.3.16")

    def test_missing_wheel_is_rejected(self):
        sdk = self.root / "python"
        write(sdk / "bin" / "dist" / "pulumi_descope-0.3.16.tar.gz", "sdist")
        write_json(sdk / "bin" / "pulumi_descope" / "pulumi-plugin.json", PLUGIN_JSON)
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_python(sdk, "0.3.16")
        self.assertIn("python wheel", str(caught.exception))

    def test_missing_dist_directory_is_rejected(self):
        sdk = self.root / "python"
        write_json(sdk / "bin" / "pulumi_descope" / "pulumi-plugin.json", PLUGIN_JSON)
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_python(sdk, "0.3.16")
        self.assertIn("python dist directory", str(caught.exception))


class DotnetLayoutTest(TempTreeTest):
    def build(self, nupkg: str = "Pulumi.Descope.0.3.16.nupkg") -> Path:
        sdk = self.root / "dotnet"
        write(sdk / "Pulumi.Descope.csproj", "<Project />")
        write(sdk / "bin" / "Debug" / nupkg, "nupkg")
        write_json(sdk / "pulumi-plugin.json", PLUGIN_JSON)
        return sdk

    def test_valid_layout_passes(self):
        verify_layout.check_dotnet(self.build(), "0.3.16")

    def test_wrong_version_nupkg_is_rejected(self):
        sdk = self.build(nupkg="Pulumi.Descope.0.3.15.nupkg")
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_dotnet(sdk, "0.3.16")
        self.assertIn("carries version", str(caught.exception))

    def test_missing_nupkg_is_rejected(self):
        sdk = self.root / "dotnet"
        write(sdk / "Pulumi.Descope.csproj", "<Project />")
        write_json(sdk / "pulumi-plugin.json", PLUGIN_JSON)
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_dotnet(sdk, "0.3.16")
        self.assertIn("bin/Debug", str(caught.exception))


GOOD_BUILD_GRADLE = """
plugins { id("signing") }
group = "com.descope.pulumi"
def resolvedVersion = System.getenv("PACKAGE_VERSION") ?: "0.3.16"
def genPulumiResources = tasks.register('genPulumiResources') {
    doLast {
        new File(outDir, "version.txt").text = resolvedVersion
        builder {
            parameterization {
                name "descope"
            }
        }
    }
}
publishing {
    publications {
        mainPublication(MavenPublication) {
            groupId = "com.descope.pulumi"
            artifactId = "descope"
            version = resolvedVersion
        }
    }
}
nexusPublishing { repositories { sonatype { nexusUrl.set(uri(publishStagingURL)) } } }
"""


class JavaLayoutTest(TempTreeTest):
    def build(self, gradle: str = GOOD_BUILD_GRADLE) -> Path:
        sdk = self.root / "java"
        write(sdk / "build.gradle", gradle)
        write(sdk / "settings.gradle", 'rootProject.name = "com.descope.pulumi.descope"')
        write(sdk / "src" / "main" / "java" / "com" / "descope" / "Provider.java", "class P {}")
        return sdk

    def test_valid_layout_passes(self):
        notes = verify_layout.check_java(self.build(), "0.3.16")
        self.assertIn("com.descope.pulumi:descope", notes[0])

    def test_missing_sources_are_rejected(self):
        sdk = self.root / "java"
        write(sdk / "build.gradle", GOOD_BUILD_GRADLE)
        write(sdk / "settings.gradle", "rootProject.name = 'x'")
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_java(sdk, "0.3.16")
        self.assertIn("generated java sources", str(caught.exception))

    def test_missing_parameterization_emitter_is_rejected(self):
        gradle = GOOD_BUILD_GRADLE.replace("parameterization {", "somethingElse {")
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_java(self.build(gradle), "0.3.16")
        self.assertIn("parameterization block", str(caught.exception))

    def test_empty_group_is_rejected(self):
        gradle = GOOD_BUILD_GRADLE.replace('group = "com.descope.pulumi"', 'group = ""')
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_java(self.build(gradle), "0.3.16")
        self.assertIn("maven group", str(caught.exception))


class GoLayoutTest(TempTreeTest):
    def build(self, module: str = "module github.com/tektum/pulumi-descope/sdk/go") -> Path:
        sdk = self.root / "go"
        write(sdk / "go.mod", f"{module}\n\ngo 1.25\n")
        write_json(sdk / "descope" / "pulumi-plugin.json", PLUGIN_JSON)
        write(sdk / "descope" / "provider.go", "package descope\n")
        return sdk

    def test_valid_layout_passes(self):
        notes = verify_layout.check_go(self.build(), "0.3.16")
        self.assertIn("github.com/tektum/pulumi-descope/sdk/go", notes[0])

    def test_missing_go_mod_is_rejected(self):
        sdk = self.root / "go"
        write(sdk / "descope" / "provider.go", "package descope\n")
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_go(sdk, "0.3.16")
        self.assertIn("go.mod", str(caught.exception))

    def test_missing_plugin_json_is_rejected(self):
        sdk = self.root / "go"
        write(sdk / "go.mod", "module github.com/o/p/sdk/go\n")
        with self.assertRaises(verify_layout.LayoutError) as caught:
            verify_layout.check_go(sdk, "0.3.16")
        self.assertIn("pulumi-plugin.json", str(caught.exception))


PULUMI_YAML = """name: pulumi-sdk-yaml-validation
runtime: yaml
description: Ephemeral project used to validate YAML package resolution.
packages:
  descope:
    source: terraform-provider
    version: 1.4.0
    parameters:
      - descope/descope
      - 0.3.16
"""

DESCRIPTOR_YAML = """packageDeclarationVersion: 1
name: terraform-provider
version: 1.4.0
parameterization:
    name: descope
    version: 0.3.16
    value: eyJyZW1vdGUiOnt9fQ==
"""


class YamlValidationTest(TempTreeTest):
    def build(self, project: str = PULUMI_YAML, descriptor: str = DESCRIPTOR_YAML) -> Path:
        root = self.root / "yaml-project"
        write(root / "Pulumi.yaml", project)
        write(root / "sdks" / "descope" / "descope-0.3.16.yaml", descriptor)
        return root

    def test_valid_project_passes(self):
        notes = validate_yaml_package.validate(
            self.build(),
            "descope",
            None,
            "terraform-provider",
            ["descope/descope", "0.3.16"],
            "0.3.16",
        )
        self.assertTrue(any("descope-0.3.16.yaml" in note for note in notes))

    def test_missing_packages_block_is_rejected(self):
        project = "name: p\nruntime: yaml\n"
        with self.assertRaises(validate_yaml_package.YamlValidationError) as caught:
            validate_yaml_package.validate(
                self.build(project=project), "descope", None, "terraform-provider", []
            )
        self.assertIn("packages:", str(caught.exception))

    def test_wrong_pinned_version_is_rejected(self):
        with self.assertRaises(validate_yaml_package.YamlValidationError) as caught:
            validate_yaml_package.validate(
                self.build(),
                "descope",
                None,
                "terraform-provider",
                ["descope/descope", "0.3.17"],
            )
        self.assertIn("pinned Terraform provider version", str(caught.exception))

    def test_missing_descriptor_is_rejected(self):
        root = self.root / "yaml-project"
        write(root / "Pulumi.yaml", PULUMI_YAML)
        (root / "sdks").mkdir(parents=True, exist_ok=True)
        with self.assertRaises(validate_yaml_package.YamlValidationError) as caught:
            validate_yaml_package.validate(
                root, "descope", None, "terraform-provider", []
            )
        self.assertIn("missing generated package descriptor", str(caught.exception))

    def test_descriptor_without_parameterization_value_is_rejected(self):
        descriptor = DESCRIPTOR_YAML.replace("    value: eyJyZW1vdGUiOnt9fQ==\n", "")
        with self.assertRaises(validate_yaml_package.YamlValidationError) as caught:
            validate_yaml_package.validate(
                self.build(descriptor=descriptor),
                "descope",
                None,
                "terraform-provider",
                [],
            )
        self.assertIn("missing 'value'", str(caught.exception))

    def test_pinned_package_version_mismatch_is_rejected(self):
        with self.assertRaises(validate_yaml_package.YamlValidationError) as caught:
            validate_yaml_package.validate(
                self.build(), "descope", None, "terraform-provider", [], "0.3.17"
            )
        self.assertIn("did not reach the generated package", str(caught.exception))

    def test_ambiguous_descriptors_are_rejected(self):
        root = self.build()
        write(root / "sdks" / "descope" / "descope-0.3.15.yaml", DESCRIPTOR_YAML)
        with self.assertRaises(validate_yaml_package.YamlValidationError) as caught:
            validate_yaml_package.validate(root, "descope", None, "terraform-provider", [])
        self.assertIn("exactly one package descriptor", str(caught.exception))

    def test_unknown_package_name_is_rejected(self):
        with self.assertRaises(validate_yaml_package.YamlValidationError) as caught:
            validate_yaml_package.validate(
                self.build(), "elsewhere", None, "terraform-provider", []
            )
        self.assertIn("does not declare a package named", str(caught.exception))

    def test_parser_handles_quoted_scalars_and_nesting(self):
        parsed = validate_yaml_package.parse_block(
            'top: 1\n'
            'packages:\n'
            '  descope:\n'
            '    source: terraform-provider\n'
            '    parameters:\n'
            '      - "a"\n'
            "      - 'b'\n"
            'after: 2\n'
        )
        self.assertEqual(parsed["top"], "1")
        self.assertEqual(parsed["after"], "2")
        self.assertEqual(parsed["packages"]["descope"]["source"], "terraform-provider")
        self.assertEqual(parsed["packages"]["descope"]["parameters"], ["a", "b"])

    def test_parser_prefers_top_level_keys_over_nested_duplicates(self):
        parsed = validate_yaml_package.parse_block(
            "name: terraform-provider\n"
            "version: 1.4.0\n"
            "parameterization:\n"
            "    name: descope\n"
            "    version: 0.3.16\n"
        )
        self.assertEqual(parsed["name"], "terraform-provider")
        self.assertEqual(parsed["version"], "1.4.0")
        self.assertEqual(parsed["parameterization"]["name"], "descope")
        self.assertEqual(parsed["parameterization"]["version"], "0.3.16")


if __name__ == "__main__":
    unittest.main(verbosity=2)

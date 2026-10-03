from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_package",
    ROOT / "tools" / "validate_package.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load tools/validate_package.py")
validate_package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_package)


class PackageValidationTests(unittest.TestCase):
    def test_packaged_readme_is_optional(self) -> None:
        wheel = "wheels/oead-test-cp311-cp311-win_amd64.whl"
        manifest = {"wheels": [f"./{wheel}"]}

        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "odyssey_toolkit.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name in (
                    "CHANGELOG.md",
                    "LICENSE",
                    "__init__.py",
                    "blender_manifest.toml",
                    wheel,
                ):
                    archive.writestr(name, b"synthetic test data")

            validate_package.validate_archive(archive_path, manifest)

    def test_repository_bl_info_matches_manifest(self) -> None:
        source = ROOT / "odyssey_toolkit"
        manifest = validate_package.load_manifest(
            source / validate_package.MANIFEST_NAME
        )

        validate_package.validate_bl_info_version(source, manifest)

    def test_mismatched_bl_info_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "__init__.py").write_text(
                'bl_info = {"name": "Test", "version": (1, 2, 3)}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not match"):
                validate_package.validate_bl_info_version(
                    source,
                    {"version": "1.2.4"},
                )

    def test_missing_bl_info_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "__init__.py").write_text("", encoding="utf-8")

            validate_package.validate_bl_info_version(
                source,
                {"version": "1.2.3"},
            )


if __name__ == "__main__":
    unittest.main()

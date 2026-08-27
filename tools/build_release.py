from __future__ import annotations

from pathlib import Path
import runpy
import shutil
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PROJECT_ROOT / "smo_kingdom_importer"
DIST_DIR = PROJECT_ROOT / "dist"
HYGIENE = runpy.run_path(str(PROJECT_ROOT / "tests" / "release_hygiene.py"))
ALLOWED_PACKAGE_FILES = HYGIENE["ALLOWED_PACKAGE_FILES"]
ROOT_RELEASE_FILES = frozenset({"CHANGELOG.md", "LICENSE"})


def source_path(relative_name: str) -> Path:
    base = PROJECT_ROOT if relative_name in ROOT_RELEASE_FILES else PACKAGE_DIR
    return base / relative_name


def archive_info(relative_name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(relative_name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build() -> tuple[Path, Path]:
    version = HYGIENE["validate_source_version"]()
    HYGIENE["validate_test_inventory"]()

    missing = sorted(
        name for name in ALLOWED_PACKAGE_FILES if not source_path(name).is_file()
    )
    if missing:
        raise RuntimeError(f"Release sources are missing: {missing}")

    DIST_DIR.mkdir(exist_ok=True)
    versioned = DIST_DIR / f"odyssey_toolkit_v{version}.zip"
    generic = DIST_DIR / "odyssey_toolkit.zip"

    with zipfile.ZipFile(
        versioned,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative_name in sorted(ALLOWED_PACKAGE_FILES):
            archive.writestr(
                archive_info(relative_name),
                source_path(relative_name).read_bytes(),
            )

    shutil.copyfile(versioned, generic)
    HYGIENE["validate_zip"](versioned, version)
    HYGIENE["validate_zip"](generic, version)
    print(f"BUILD_RELEASE: PASS version={version} path={versioned}")
    return versioned, generic


if __name__ == "__main__":
    build()

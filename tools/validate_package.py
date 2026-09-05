from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import tomllib
import zipfile


EXPECTED_MANIFEST_ID = "odyssey_toolkit"
MANIFEST_NAME = "blender_manifest.toml"
REQUIRED_ROOT_FILES = {
    "CHANGELOG.md",
    "LICENSE",
    "__init__.py",
    MANIFEST_NAME,
}
FORBIDDEN_PARTS = {
    ".git",
    ".github",
    ".pytest_cache",
    "__pycache__",
    "classification_app",
    "release-test-output",
    "resolution_audit",
    "research",
    "smo_kingdom_importer_v0.36.3",
    "tests",
    "tools",
}
FORBIDDEN_SUFFIXES = {
    ".bfres",
    ".blend",
    ".blend1",
    ".blend2",
    ".bntx",
    ".byml",
    ".docx",
    ".pack",
    ".pdn",
    ".pyc",
    ".sarc",
    ".szs",
}
FORBIDDEN_FILENAMES = {
    "artwork_base.png",
    "bfres_animation_inventory.csv",
    "bfres_animation_inventory.json",
    "bfres_animation_inventory.md",
    "guide.docx",
    "post.png",
    "shinetowertotalcount_21.png",
    "trees.txt",
}
WHEEL_PATTERN = re.compile(
    r"^oead-[^-]+-cp311-cp311-win_amd64\.whl$",
    re.IGNORECASE,
)


def load_manifest(path: Path) -> dict[str, object]:
    try:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"could not parse manifest {path}: {exc}") from exc

    required_values = {
        "schema_version": str,
        "id": str,
        "version": str,
        "name": str,
        "tagline": str,
        "maintainer": str,
        "type": str,
        "platforms": list,
        "wheels": list,
        "blender_version_min": str,
        "license": list,
    }
    for key, expected_type in required_values.items():
        value = manifest.get(key)
        if not isinstance(value, expected_type) or not value:
            raise ValueError(
                f"manifest field {key!r} must be a non-empty "
                f"{expected_type.__name__}"
            )

    if manifest["id"] != EXPECTED_MANIFEST_ID:
        raise ValueError(
            f"manifest id must remain {EXPECTED_MANIFEST_ID!r}, "
            f"got {manifest['id']!r}"
        )
    if manifest["type"] != "add-on":
        raise ValueError("manifest type must be 'add-on'")
    return manifest


def manifest_wheel_paths(
    source: Path,
    manifest: dict[str, object],
) -> tuple[Path, ...]:
    paths: list[Path] = []
    for raw_path in manifest["wheels"]:
        if not isinstance(raw_path, str):
            raise ValueError("every manifest wheel entry must be a string")
        relative_path = PurePosixPath(raw_path.removeprefix("./"))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"unsafe manifest wheel path: {raw_path!r}")
        wheel_path = source.joinpath(*relative_path.parts)
        if not wheel_path.is_file():
            raise ValueError(f"bundled wheel does not exist: {wheel_path}")
        if not WHEEL_PATTERN.fullmatch(wheel_path.name):
            raise ValueError(
                "bundled oead wheel must target CPython 3.11 on Windows x64: "
                f"{wheel_path.name}"
            )
        paths.append(wheel_path)
    if len(paths) != 1:
        raise ValueError("manifest must reference exactly one bundled oead wheel")
    return tuple(paths)


def validate_archive_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ValueError(f"archive member uses a backslash: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive member path: {name!r}")
    lowered_parts = {part.casefold() for part in path.parts}
    if lowered_parts & FORBIDDEN_PARTS:
        raise ValueError(f"forbidden directory in archive: {name!r}")
    lowered_name = path.name.casefold()
    if lowered_name in FORBIDDEN_FILENAMES:
        raise ValueError(f"ignored/research file in archive: {name!r}")
    if any(lowered_name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        raise ValueError(f"game-data or build-artifact file in archive: {name!r}")
    return path


def validate_archive(
    archive_path: Path,
    manifest: dict[str, object],
) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(f"corrupt ZIP member: {bad_member!r}")
            file_names = [
                info.filename for info in archive.infolist() if not info.is_dir()
            ]
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"could not read archive {archive_path}: {exc}") from exc

    if len(file_names) != len(set(file_names)):
        raise ValueError("archive contains duplicate member names")
    members = {validate_archive_member(name) for name in file_names}
    root_files = {path.name for path in members if len(path.parts) == 1}
    missing = REQUIRED_ROOT_FILES - root_files
    if missing:
        raise ValueError(
            "archive is missing required root files: " + ", ".join(sorted(missing))
        )
    if any(path.parts[0] == EXPECTED_MANIFEST_ID for path in members):
        raise ValueError("archive must not contain an odyssey_toolkit wrapper folder")

    for raw_path in manifest["wheels"]:
        wheel_member = PurePosixPath(str(raw_path).removeprefix("./"))
        if wheel_member not in members:
            raise ValueError(f"manifest wheel is missing from archive: {raw_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Odyssey Toolkit manifest and release ZIP."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.source / MANIFEST_NAME)
    manifest_wheel_paths(args.source, manifest)
    validate_archive(args.archive, manifest)
    print(f"Validated manifest and package: {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

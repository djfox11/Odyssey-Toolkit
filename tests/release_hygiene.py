r"""Static release checks and a repeatable Blender regression runner.

This file uses only the Python standard library, so source and ZIP checks work
under system Python even when Blender-only modules are unavailable.

PowerShell examples:
    python .\tests\release_hygiene.py
    python .\tests\release_hygiene.py --zip .\smo_kingdom_importer_v0.25.9.zip
    python .\tests\release_hygiene.py --run --blender BLENDER_EXE --romfs ROMFS
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tomllib
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PROJECT_ROOT / "smo_kingdom_importer"
TESTS_DIR = PROJECT_ROOT / "tests"
MANUAL_DIAGNOSTIC = "saved_blend_texture_diagnostic.py"
EXPECTED_EXTENSION_ID = "smo_kingdom_importer"
EXPECTED_EXTENSION_NAME = "Odyssey Toolkit"
EXPECTED_BLENDER_MIN = "4.5.0"
EXPECTED_PLATFORMS = ["windows-x64"]
EXPECTED_WHEELS = [
    "./wheels/oead-1.2.9.post4-cp311-cp311-win_amd64.whl"
]

ALLOWED_PACKAGE_FILES = frozenset(
    {
        "CHANGELOG.md",
        "LICENSE",
        "__init__.py",
        "actor_registry.py",
        "bfres_animation.py",
        "bfres_animation_import.py",
        "bfres_camera_import.py",
        "bfres_mesh.py",
        "blender_manifest.toml",
        "bntx_texture.py",
        "model_expectation.py",
        "model_category_registry.py",
        "model_report.py",
        "object_data.py",
        "performance.py",
        "placement_classifier.py",
        "previews/scenarios/_scenario.png",
        "previews/scenarios/balloon_world.png",
        "previews/scenarios/main_story_clear.png",
        "previews/scenarios/moon_rock.png",
        "previews/scenarios/postgame.png",
        "README.md",
        "registry_report.py",
        "resource_rules.py",
        "resolved_model_categories.json",
        "smd_animation.py",
        "stage_catalog.py",
        "stage_data.py",
        "stage_lighting.py",
        "standalone_import.py",
        "static_model_import.py",
        "texture_cache.py",
        "world_list.py",
        "wheels/oead-1.2.9.post4-cp311-cp311-win_amd64.whl",
    }
)


class HygieneError(RuntimeError):
    """An actionable release-check failure."""


@dataclass(frozen=True, slots=True)
class TestSpec:
    filename: str
    arguments: tuple[str, ...] = ()
    note: str = ""


# {romfs} is expanded only after the supplied ROMFS has been validated. This
# explicit inventory prevents a new test from being silently skipped.
TEST_SPECS = (
    TestSpec("actor_registry_regression.py", ("{romfs}",)),
    TestSpec("bfres_asset_blender_smoke.py", ("{romfs}", "Car"), "BFRES smoke asset: Car"),
    TestSpec(
        "bfres_animation_regression.py",
        ("{romfs}",),
        "native FSKA discovery and Blender Action regression",
    ),
    TestSpec(
        "bfres_camera_animation_regression.py",
        ("{romfs}",),
        "native FSCN/FCAM camera parsing and Blender camera regression",
    ),
    TestSpec(
        "bfres_scale_compensation_regression.py",
        ("{romfs}",),
        "KoopaHack and Bowser hierarchy-preserving scale regression",
    ),
    TestSpec(
        "bfres_uniform_scale_regression.py",
        ("{romfs}",),
        "authored uniform-scale axis-stretch regression",
    ),
    TestSpec("boss_knuckle_texture_regression.py", ("{romfs}",)),
    TestSpec("ceremony_resolution_regression.py", ("{romfs}",)),
    TestSpec("city_material_regression.py", ("{romfs}",)),
    TestSpec("coin_stack_resolution_regression.py", ("{romfs}",)),
    TestSpec("deep_woods_texture_regression.py", ("{romfs}",)),
    TestSpec(
        "demo_hack_first_background_animation_regression.py",
        ("{romfs}",),
        "dormant FUV tex_mtx0 material-animation regression",
    ),
    TestSpec("dynamic_actor_resolution_regression.py", ("{romfs}",)),
    TestSpec("image_persistence_regression.py", ("{romfs}",)),
    TestSpec("metro_subarea_regression.py", ("{romfs}",)),
    TestSpec("multiple_uv_regression.py", ("{romfs}",)),
    TestSpec("model_expectation_regression.py"),
    TestSpec("model_identification_regression.py", ("{romfs}",)),
    TestSpec("model_category_registry_regression.py"),
    TestSpec("mario_cap_texture_regression.py", ("{romfs}",)),
    TestSpec("peach_picture_room_texture_regression.py", ("{romfs}",)),
    TestSpec("parser_cleanup_regression.py"),
    TestSpec("performance_benchmark.py", ("{romfs}",), "representative performance benchmark"),
    TestSpec("phase1_blender_regression.py"),
    TestSpec("phase1_romfs_regression.py", ("{romfs}",)),
    TestSpec(
        "rigging_regression.py",
        ("{romfs}",),
        "FSKL armature and skin-deformation regression",
    ),
    TestSpec(
        "smd_animation_regression.py",
        ("{romfs}",),
        "Switch Toolbox SMD animation conversion regression",
    ),
    TestSpec("sand_ice_cave_sky_regression.py", ("{romfs}",)),
    TestSpec("shader_material_regression.py", ("{romfs}",)),
    TestSpec("shader_uv_transform_regression.py", ("{romfs}",)),
    TestSpec("seaside_ocean_regression.py", ("{romfs}",)),
    TestSpec("stage_data_lazy_regression.py", ("{romfs}",)),
    TestSpec("stage_lighting_regression.py", ("{romfs}",)),
    TestSpec("stage_selector_romfs_regression.py", ("{romfs}",)),
    TestSpec("standalone_import_blender_smoke.py", ("{romfs}",)),
    TestSpec(
        "preferences_performance_regression.py",
        ("{romfs}",),
        "Preferences redraw performance guard",
    ),
    TestSpec("texture_cache_regression.py", ("{romfs}",), "persistent texture cache benchmark"),
    TestSpec("ui_qol_regression.py", ("{romfs}",)),
)


def _semver(version: tuple[int, ...]) -> str:
    return ".".join(str(component) for component in version)


def _bl_info_version(source: str, source_name: str) -> tuple[int, ...]:
    try:
        tree = ast.parse(source, filename=source_name)
    except SyntaxError as exc:
        raise HygieneError(f"Cannot parse {source_name}: {exc}") from exc

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "bl_info"
            for target in node.targets
        ):
            continue
        try:
            bl_info = ast.literal_eval(node.value)
        except (TypeError, ValueError) as exc:
            raise HygieneError(f"{source_name} bl_info is not a literal mapping") from exc
        version = bl_info.get("version") if isinstance(bl_info, dict) else None
        if (
            not isinstance(version, tuple)
            or len(version) != 3
            or any(type(component) is not int or component < 0 for component in version)
        ):
            raise HygieneError(f"{source_name} has an invalid bl_info version: {version!r}")
        return version

    raise HygieneError(f"{source_name} does not define bl_info")


def _manifest_version(source: str, source_name: str) -> str:
    try:
        manifest = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise HygieneError(f"Cannot parse {source_name}: {exc}") from exc
    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        raise HygieneError(f"{source_name} has no valid string version")
    return version


def validate_manifest_contract(source: str, source_name: str) -> None:
    try:
        manifest = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise HygieneError(f"Cannot parse {source_name}: {exc}") from exc
    expected = {
        "id": EXPECTED_EXTENSION_ID,
        "name": EXPECTED_EXTENSION_NAME,
        "blender_version_min": EXPECTED_BLENDER_MIN,
        "platforms": EXPECTED_PLATFORMS,
        "wheels": EXPECTED_WHEELS,
    }
    mismatches = {
        key: {"expected": value, "actual": manifest.get(key)}
        for key, value in expected.items()
        if manifest.get(key) != value
    }
    if mismatches:
        raise HygieneError(
            f"{source_name} violates the Toolkit manifest contract: {mismatches}"
        )


def validate_version_pair(
    init_source: str,
    manifest_source: str,
    init_name: str,
    manifest_name: str,
) -> str:
    bl_info_version = _bl_info_version(init_source, init_name)
    manifest_version = _manifest_version(manifest_source, manifest_name)
    expected = _semver(bl_info_version)
    if manifest_version != expected:
        raise HygieneError(
            f"Version mismatch: {init_name} is {expected}, "
            f"but {manifest_name} is {manifest_version}"
        )
    return expected


def validate_source_version() -> str:
    init_path = PACKAGE_DIR / "__init__.py"
    manifest_path = PACKAGE_DIR / "blender_manifest.toml"
    version = validate_version_pair(
        init_path.read_text(encoding="utf-8"),
        manifest_path.read_text(encoding="utf-8"),
        str(init_path),
        str(manifest_path),
    )
    validate_manifest_contract(
        manifest_path.read_text(encoding="utf-8"),
        str(manifest_path),
    )
    print(f"SOURCE_VERSION: PASS version={version}")
    return version


def _archive_relative_names(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, zipfile.ZipInfo], str]:
    file_infos: list[zipfile.ZipInfo] = []
    seen_names: set[str] = set()
    for info in archive.infolist():
        name = info.filename
        if "\\" in name or name.startswith("/"):
            raise HygieneError(f"ZIP contains an unsafe path: {name!r}")
        path = PurePosixPath(name.rstrip("/"))
        if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise HygieneError(f"ZIP contains an unsafe path: {name!r}")
        folded = name.rstrip("/").casefold()
        if folded in seen_names:
            raise HygieneError(f"ZIP contains a duplicate path: {name!r}")
        seen_names.add(folded)
        mode = info.external_attr >> 16
        if mode and stat.S_ISLNK(mode):
            raise HygieneError(f"ZIP contains a symbolic link: {name!r}")
        if not info.is_dir():
            file_infos.append(info)

    if not file_infos:
        raise HygieneError("ZIP is empty")

    package_prefix = "smo_kingdom_importer/"
    prefixed = [info.filename.startswith(package_prefix) for info in file_infos]
    if all(prefixed):
        layout = "package-directory"
    elif any(prefixed):
        raise HygieneError("ZIP mixes package-prefixed and top-level files")
    else:
        layout = "extension-root"

    relative: dict[str, zipfile.ZipInfo] = {}
    for info in file_infos:
        name = info.filename
        if layout == "package-directory":
            name = name[len(package_prefix) :]
        if not name:
            raise HygieneError("ZIP contains an invalid empty package filename")
        relative[name] = info
    return relative, layout


def validate_zip(zip_path: Path, source_version: str) -> None:
    if not zip_path.is_file():
        raise HygieneError(f"ZIP does not exist: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise HygieneError(f"ZIP CRC check failed for {bad_member!r}")
            members, layout = _archive_relative_names(archive)
            names = frozenset(members)
            unexpected = sorted(names - ALLOWED_PACKAGE_FILES)
            missing = sorted(ALLOWED_PACKAGE_FILES - names)
            if unexpected or missing:
                details: list[str] = []
                if unexpected:
                    details.append(f"unexpected={unexpected}")
                if missing:
                    details.append(f"missing={missing}")
                raise HygieneError(
                    "ZIP contents are not release-allowlisted: " + "; ".join(details)
                )

            init_source = archive.read(members["__init__.py"]).decode("utf-8-sig")
            manifest_source = archive.read(
                members["blender_manifest.toml"]
            ).decode("utf-8-sig")
            zip_version = validate_version_pair(
                init_source,
                manifest_source,
                f"{zip_path}!/__init__.py",
                f"{zip_path}!/blender_manifest.toml",
            )
            validate_manifest_contract(
                manifest_source,
                f"{zip_path}!/blender_manifest.toml",
            )
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise HygieneError(f"Cannot validate ZIP {zip_path}: {exc}") from exc

    if zip_version != source_version:
        raise HygieneError(
            f"ZIP version {zip_version} does not match source version {source_version}"
        )
    print(
        f"ZIP: PASS version={zip_version} files={len(members)} "
        f"layout={layout} path={zip_path}"
    )


def validate_test_inventory() -> None:
    expected = {spec.filename for spec in TEST_SPECS}
    ignored = {Path(__file__).name, MANUAL_DIAGNOSTIC}
    actual = {path.name for path in TESTS_DIR.glob("*.py")} - ignored
    missing = sorted(expected - actual)
    unlisted = sorted(actual - expected)
    if missing or unlisted:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unlisted:
            details.append(f"unlisted={unlisted}")
        raise HygieneError("Headless test inventory is stale: " + "; ".join(details))
    print(
        f"TEST_INVENTORY: PASS headless={len(TEST_SPECS)} "
        f"excluded_manual={MANUAL_DIAGNOSTIC}"
    )


def _power_shell_command(arguments: list[str]) -> str:
    quoted = ["'" + argument.replace("'", "''") + "'" for argument in arguments]
    return "& " + " ".join(quoted)


def _command(spec: TestSpec, blender: str, romfs: str) -> list[str]:
    command = [
        blender,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(TESTS_DIR / spec.filename),
    ]
    if spec.arguments:
        command.append("--")
        command.extend(
            romfs if argument == "{romfs}" else argument
            for argument in spec.arguments
        )
    return command


def print_test_matrix(blender: Path | None, romfs: Path | None) -> None:
    blender_text = str(blender) if blender is not None else "<BLENDER_EXE>"
    romfs_text = str(romfs) if romfs is not None else "<ROMFS>"
    print("HEADLESS_TEST_MATRIX:")
    for spec in TEST_SPECS:
        suffix = f"  # {spec.note}" if spec.note else ""
        print(
            f"  {_power_shell_command(_command(spec, blender_text, romfs_text))}"
            f"{suffix}"
        )
    print(
        f"  # Excluded: {TESTS_DIR / MANUAL_DIAGNOSTIC} "
        "(manual saved-.blend diagnostic, not a release gate)"
    )


def run_tests(blender: Path | None, romfs: Path | None) -> None:
    if blender is None:
        raise HygieneError("--run requires --blender")
    if romfs is None:
        raise HygieneError("--run requires --romfs")
    if not blender.is_file():
        raise HygieneError(f"Blender executable does not exist: {blender}")
    if not (romfs / "StageData").is_dir() or not (romfs / "ObjectData").is_dir():
        raise HygieneError(f"ROMFS is missing StageData or ObjectData: {romfs}")

    print(f"HEADLESS_TESTS: START count={len(TEST_SPECS)}")
    for index, spec in enumerate(TEST_SPECS, 1):
        print(f"[{index:02d}/{len(TEST_SPECS):02d}] {spec.filename}", flush=True)
        completed = subprocess.run(
            _command(spec, str(blender), str(romfs)),
            cwd=PROJECT_ROOT,
            check=False,
        )
        if completed.returncode != 0:
            raise HygieneError(
                f"{spec.filename} failed with exit code {completed.returncode}"
            )
    print(f"HEADLESS_TESTS: PASS count={len(TEST_SPECS)}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate release versions/ZIP contents and document or run Blender tests."
    )
    parser.add_argument("--zip", type=Path, help="Installable extension ZIP to validate")
    parser.add_argument("--run", action="store_true", help="Run every listed Blender test")
    parser.add_argument("--blender", type=Path, help="Path to blender.exe for --run")
    parser.add_argument("--romfs", type=Path, help="Read-only SMO ROMFS for --run")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        source_version = validate_source_version()
        validate_test_inventory()
        if arguments.zip is not None:
            validate_zip(arguments.zip.resolve(), source_version)
        print_test_matrix(arguments.blender, arguments.romfs)
        if arguments.run:
            run_tests(arguments.blender, arguments.romfs)
    except HygieneError as exc:
        print(f"RELEASE_HYGIENE: FAIL: {exc}", file=sys.stderr)
        return 1
    print("RELEASE_HYGIENE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.bntx_texture import BntxTextureArchive
from smo_kingdom_importer.resource_rules import texture_archive_rule_names
from smo_kingdom_importer.standalone_import import SMO_OT_import_test_model
from smo_kingdom_importer.static_model_import import SMO_OT_import_static_models
from smo_kingdom_importer.world_list import extract_file, read_szs


TARGET_ASSET = "MarioCap"
TARGET_ARCHIVE = "MarioHeadTexture.szs"
TARGET_TEXTURES = {
    "MarioCap_alb",
    "MarioCap_mtl",
    "MarioCap_nrm",
    "MarioCap_rgh",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def texture_names(path: Path) -> frozenset[str]:
    archive = read_szs(path)
    names: set[str] = set()

    for entry in archive.get_files():
        if Path(entry.name).suffix.casefold() != ".bfres":
            continue

        textures = BntxTextureArchive.from_bfres(bytes(entry.data))

        if textures is not None:
            names.update(textures.names)

    return frozenset(names)


def required_texture_names(path: Path) -> frozenset[str]:
    archive = read_szs(path)
    names: set[str] = set()

    for entry in archive.get_files():
        if Path(entry.name).suffix.casefold() != ".bfres":
            continue

        data = bytes(extract_file(archive, entry.name))

        for model in read_static_bfres(data):
            for mesh in model.meshes:
                names.update(mesh.texture_names)

    return frozenset(names)


def run(romfs_root: Path) -> None:
    object_data = romfs_root / "ObjectData"
    asset_path = object_data / f"{TARGET_ASSET}.szs"
    texture_path = object_data / TARGET_ARCHIVE
    check(asset_path.is_file(), f"Missing {asset_path}")
    check(texture_path.is_file(), f"Missing {texture_path}")
    check(
        texture_archive_rule_names(TARGET_ASSET) == (TARGET_ARCHIVE,),
        "MarioCap does not resolve MarioHeadTexture.szs",
    )
    check(
        texture_archive_rule_names("MarioAlohaCap")
        == ("MarioAlohaHeadTexture.szs",),
        "Mario costume cap rule did not generalise",
    )
    check(
        texture_archive_rule_names("MarioRangoHairNoCap") == (),
        "A NoCap asset was incorrectly treated as a cap",
    )
    check(
        texture_archive_rule_names("BirdCap") == (),
        "A non-Mario cap was incorrectly assigned a head archive",
    )

    required = required_texture_names(asset_path)
    available = texture_names(texture_path)
    check(
        TARGET_TEXTURES <= required,
        f"MarioCap FMAT references changed: {sorted(required)}",
    )
    check(
        required <= available,
        f"{TARGET_ARCHIVE} is missing {sorted(required - available)}",
    )

    checked_pairs = 0

    for cap_path in sorted(
        object_data.glob("Mario*Cap.szs"),
        key=lambda path: path.name.casefold(),
    ):
        if cap_path.stem.casefold().endswith("nocap"):
            continue

        rule_names = texture_archive_rule_names(cap_path.stem)
        check(rule_names, f"No companion rule for {cap_path.name}")
        head_path = object_data / rule_names[-1]

        if not head_path.is_file():
            continue

        unresolved = (
            required_texture_names(cap_path)
            - texture_names(cap_path)
            - texture_names(head_path)
        )
        check(
            not unresolved,
            f"{head_path.name} does not resolve {cap_path.name}: "
            f"{sorted(unresolved)}",
        )
        checked_pairs += 1

    check(
        checked_pairs >= 40,
        f"Unexpectedly few Mario cap/head archive pairs: {checked_pairs}",
    )

    classified = SimpleNamespace(
        placement=SimpleNamespace(source_stage_name="CapWorldHomeStage"),
        resource=SimpleNamespace(archive_path=asset_path),
    )
    stage_host = SimpleNamespace(
        _stage_name="CapWorldHomeStage",
        _object_data_dir=object_data,
    )
    stage_paths = SMO_OT_import_static_models._shared_texture_paths(
        stage_host,
        classified,
    )
    check(
        stage_paths and stage_paths[0].name == TARGET_ARCHIVE,
        f"Stage texture candidate precedence is wrong: {stage_paths}",
    )

    standalone_host = SimpleNamespace(
        use_selected_stage_textures=True,
        _infer_stage_texture=SMO_OT_import_test_model._infer_stage_texture,
    )
    standalone_context = SimpleNamespace(scene=SimpleNamespace())
    standalone_paths = SMO_OT_import_test_model._shared_texture_paths(
        standalone_host,
        standalone_context,
        asset_path,
    )
    check(
        standalone_paths and standalone_paths[0].name == TARGET_ARCHIVE,
        f"Standalone texture candidate precedence is wrong: {standalone_paths}",
    )
    check(
        all(path.name != "MarioCaptainHeadTexture.szs" for path in standalone_paths),
        f"Standalone heuristic added an unrelated archive: {standalone_paths}",
    )

    print(
        "MARIO_CAP_TEXTURE_REGRESSION: PASS "
        + json.dumps(
            {
                "asset": TARGET_ASSET,
                "archive": TARGET_ARCHIVE,
                "checked_costume_pairs": checked_pairs,
                "required_textures": sorted(required),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: blender --python mario_cap_texture_regression.py -- ROMFS"
        )

    run(Path(sys.argv[-1]).resolve())

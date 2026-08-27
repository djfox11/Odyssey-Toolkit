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
from smo_kingdom_importer.standalone_import import SMO_OT_import_test_model
from smo_kingdom_importer.static_model_import import SMO_OT_import_static_models
from smo_kingdom_importer.world_list import extract_file, read_szs


TARGETS = {
    "PeachWorldPictureRoom": {
        "FrescoCloudWall00_alb",
        "FrescoCloudWall00_nrm",
        "FrescoCloudWall00_rgh",
        "GoldDecoWall00_alb",
        "GoldDecoWall00_mtl",
        "GoldDecoWall00_nrm",
        "GoldDecoWall00_rgh",
        "LightGroundShop00",
        "MarbleCheckFloor00_alb",
        "MarbleCheckFloor00_nrm",
        "MarbleCheckFloor00_rgh",
    },
    "PeachWorldPictureRoomDokan": {
        "FrescoCloudWall00_alb",
        "FrescoCloudWall00_nrm",
        "FrescoCloudWall00_rgh",
        "GoldDecoWall00_alb",
        "GoldDecoWall00_mtl",
        "GoldDecoWall00_nrm",
        "GoldDecoWall00_rgh",
        "MarbleCheckFloor00_alb",
        "MarbleCheckFloor00_nrm",
        "MarbleCheckFloor00_rgh",
    },
}
EXPECTED_ARCHIVE = "PeachWorldCastleTexture.szs"


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
    castle_path = object_data / EXPECTED_ARCHIVE
    check(castle_path.is_file(), f"Missing {castle_path}")
    castle_textures = texture_names(castle_path)
    stage_candidates: dict[str, list[str]] = {}
    standalone_candidates: dict[str, list[str]] = {}

    for asset_name, reported_names in TARGETS.items():
        asset_path = object_data / f"{asset_name}.szs"
        check(asset_path.is_file(), f"Missing {asset_path}")
        required = required_texture_names(asset_path)
        check(required, f"{asset_name} has no FMAT texture references")
        check(
            reported_names <= required,
            (
                f"{asset_name} no longer references reported textures: "
                f"{sorted(reported_names - required, key=str.casefold)}"
            ),
        )
        check(
            required <= castle_textures,
            (
                f"{EXPECTED_ARCHIVE} does not resolve all {asset_name} "
                f"textures: {sorted(required - castle_textures, key=str.casefold)}"
            ),
        )

        classified = SimpleNamespace(
            placement=SimpleNamespace(
                source_stage_name="PeachWorldPictureRoomZone"
            ),
            resource=SimpleNamespace(archive_path=asset_path),
        )
        stage_host = SimpleNamespace(
            _stage_name="PeachWorldHomeStage",
            _object_data_dir=object_data,
        )
        stage_paths = SMO_OT_import_static_models._shared_texture_paths(
            stage_host,
            classified,
        )
        stage_names = [path.name for path in stage_paths]
        stage_candidates[asset_name] = stage_names
        check(
            stage_names and stage_names[0] == EXPECTED_ARCHIVE,
            f"Stage candidate precedence is wrong for {asset_name}: {stage_names}",
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
        standalone_names = [path.name for path in standalone_paths]
        standalone_candidates[asset_name] = standalone_names
        check(
            standalone_names and standalone_names[0] == EXPECTED_ARCHIVE,
            (
                f"Standalone candidate precedence is wrong for {asset_name}: "
                f"{standalone_names}"
            ),
        )

    print(
        "PEACH_PICTURE_ROOM_TEXTURE_REGRESSION: PASS "
        + json.dumps(
            {
                "archive": EXPECTED_ARCHIVE,
                "stage_candidates": stage_candidates,
                "standalone_candidates": standalone_candidates,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: blender --python peach_picture_room_texture_regression.py "
            "-- ROMFS"
        )

    run(Path(sys.argv[-1]).resolve())

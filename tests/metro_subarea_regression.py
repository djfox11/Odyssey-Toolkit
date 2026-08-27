from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.bntx_texture import BntxTextureArchive
from smo_kingdom_importer.object_data import ObjectDataIndex
from smo_kingdom_importer.placement_classifier import classify_stage_scenario
from smo_kingdom_importer.stage_data import read_stage_scenario
from smo_kingdom_importer.standalone_import import SMO_OT_import_test_model
from smo_kingdom_importer.static_model_import import (
    SMO_OT_import_static_models,
    _stage_texture_archive_names,
)
from smo_kingdom_importer.world_list import extract_file, read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def bfres_data(path: Path) -> tuple[str, bytes]:
    sarc = read_szs(path)
    name = next(
        entry.name
        for entry in sarc.get_files()
        if entry.name and entry.name.casefold().endswith(".bfres")
    )
    return name, bytes(extract_file(sarc, name))


def run(romfs_root: Path) -> None:
    object_data_dir = romfs_root / "ObjectData"
    city = read_stage_scenario(romfs_root, "CityWorldHomeStage", 2)
    trex = read_stage_scenario(romfs_root, "TrexBikeExStage", 1)

    city_sky_paths = {
        placement.zone_path
        for placement in city.placements
        if placement.category == "SkyList"
        and placement.unit_config_name.startswith("Sky")
    }
    expected_zone_paths = {
        tuple(expanded_zone.split("/"))
        for expanded_zone in city.expanded_zones
    }
    check(
        expected_zone_paths <= city_sky_paths,
        "Not every expanded City zone inherited a sky sphere: "
        f"{sorted(expected_zone_paths - city_sky_paths)}",
    )

    trex_synthesised_skies = [
        placement
        for placement in trex.placements
        if placement.category == "SkyList"
        and placement.raw.get("SMOSynthesised")
    ]
    check(
        [placement.unit_config_name for placement in trex_synthesised_skies]
        == ["SkyCityDayLight"],
        "T-Rex chase did not resolve its City sky sphere",
    )

    texture_names = _stage_texture_archive_names("TrexBikeExStage")
    check(
        texture_names
        == ("TrexBikeExStageTexture.szs", "TrexBikeExTexture.szs"),
        f"Unexpected T-Rex stage texture candidates: {texture_names}",
    )
    texture_path = object_data_dir / "TrexBikeExTexture.szs"
    check(texture_path.is_file(), "TrexBikeExTexture.szs is missing")
    check(
        SMO_OT_import_test_model._infer_stage_texture(
            object_data_dir,
            "TrexBikeExGround001",
            "",
        )
        == texture_path,
        "Standalone importer did not infer TrexBikeExTexture.szs",
    )

    classified = classify_stage_scenario(trex, ObjectDataIndex(romfs_root))
    ground = next(
        item
        for item in classified
        if item.placement.unit_config_name == "TrexBikeExGround001"
    )
    shared_paths = SMO_OT_import_static_models._shared_texture_paths(
        SimpleNamespace(
            _object_data_dir=object_data_dir,
            _stage_name="TrexBikeExStage",
        ),
        ground,
    )
    check(
        texture_path in shared_paths,
        f"Kingdom importer omitted TrexBikeExTexture.szs: {shared_paths}",
    )

    shared_bfres_name, shared_data = bfres_data(texture_path)
    shared_archive = BntxTextureArchive.from_bfres(shared_data)
    check(shared_archive is not None, "T-Rex shared BFRES has no BNTX")
    asset_path = ground.resource.archive_path
    check(asset_path is not None, "T-Rex ground has no asset archive")
    asset_bfres_name, asset_data = bfres_data(asset_path)
    meshes = tuple(
        mesh
        for model in read_static_bfres(asset_data)
        for mesh in model.meshes
        if mesh.texture_names
    )
    declared = {
        texture_name
        for mesh in meshes
        for texture_name in mesh.texture_names
    }
    available = declared & shared_archive.names
    check(available, "T-Rex ground has no textures in its shared archive")

    material_source = min(
        (
            mesh
            for mesh in meshes
            if mesh.albedo_texture_name in shared_archive.names
        ),
        key=lambda mesh: len(mesh.texture_names),
    )
    material_harness = SimpleNamespace(
        _decoded_texture_cache={},
        _image_cache={},
        _image_transparency={},
        _material_cache={},
        _missing_albedo_textures=set(),
        _fallback_display_textures=set(),
        _texture_errors={},
        _load_texture_archive=lambda asset_key, data: (
            BntxTextureArchive.from_bfres(data)
        ),
        _load_shared_texture_archive=lambda archive_path: (
            (texture_path, shared_bfres_name),
            shared_archive,
        ),
    )
    material = SMO_OT_import_static_models._material_for_mesh(
        material_harness,
        material_source,
        (asset_path, asset_bfres_name),
        asset_data,
        shared_paths,
        "TrexBikeExGround001",
    )
    loaded_textures = json.loads(material["smo_loaded_textures"])
    connected_textures = json.loads(material["smo_connected_textures"])
    check(
        loaded_textures == list(material_source.texture_names),
        "T-Rex ground material did not load every declared FMAT texture",
    )
    check(
        connected_textures.get("ALBEDO")
        == material_source.albedo_texture_name,
        f"T-Rex ground material has no albedo: {connected_textures}",
    )
    check(
        not material_harness._texture_errors,
        f"T-Rex ground material texture errors: "
        f"{material_harness._texture_errors}",
    )

    decoded_name = sorted(available)[0]
    decoded = shared_archive.decode(decoded_name)
    check(
        decoded.width > 0 and decoded.height > 0 and bool(decoded.rgba8),
        f"T-Rex shared texture {decoded_name!r} did not decode",
    )

    result = {
        "city_expanded_zones": list(city.expanded_zones),
        "city_zone_sky_paths": [list(path) for path in sorted(city_sky_paths)],
        "trex_sky": trex_synthesised_skies[0].unit_config_name,
        "trex_shared_texture": texture_path.name,
        "trex_declared_texture_count": len(declared),
        "trex_available_texture_count": len(available),
        "material": {
            "name": material_source.material_name,
            "connected": connected_textures,
        },
        "decoded_texture": {
            "name": decoded_name,
            "width": decoded.width,
            "height": decoded.height,
        },
    }
    print(
        "SMO_METRO_SUBAREA_REGRESSION="
        + json.dumps(result, sort_keys=True)
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python "
            "metro_subarea_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())
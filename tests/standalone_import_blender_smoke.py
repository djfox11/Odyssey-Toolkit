from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon
from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.standalone_import import SMO_OT_import_test_model
from smo_kingdom_importer.static_model_import import _source_mesh_display_name
from smo_kingdom_importer.world_list import read_szs


SCRIPT_ARGS = (
    sys.argv[sys.argv.index("--") + 1 :]
    if "--" in sys.argv
    else []
)
ROMFS = Path(SCRIPT_ARGS[0]) if SCRIPT_ARGS else Path(
    r"D:\The Models Resource\Game Dumps\Super Mario Odyssey\romfs"
)
ASSET = ROMFS / "ObjectData" / "WaterfallWorldBreakParts000.szs"
MARIO_CAP_ASSET = ROMFS / "ObjectData" / "MarioCap.szs"
MARIO_CAP_TEXTURES = {
    "MarioCap_alb",
    "MarioCap_mtl",
    "MarioCap_nrm",
    "MarioCap_rgh",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    archive = read_szs(ASSET)
    bfres_file = next(
        entry
        for entry in archive.get_files()
        if entry.name and entry.name.casefold().endswith(".bfres")
    )
    expected_mesh_names = {
        _source_mesh_display_name(mesh, ASSET.stem)
        for model in read_static_bfres(bytes(bfres_file.data))
        for mesh in model.meshes
    }

    with tempfile.TemporaryDirectory() as temporary_directory:
        raw_path = Path(temporary_directory) / bfres_file.name
        raw_path.write_bytes(bytes(bfres_file.data))
        raw_files = SMO_OT_import_test_model._bfres_files(raw_path)
        check(
            raw_files == ((bfres_file.name, bytes(bfres_file.data)),),
            "Raw BFRES input was not read correctly",
        )

    addon.register()

    try:
        settings = bpy.context.scene.smo_settings
        settings.romfs_path = str(ROMFS)
        settings.kingdom = "CapWorldHomeStage"
        result = bpy.ops.smo.import_test_model(
            filepath=str(ASSET),
            use_selected_stage_textures=True,
        )
        check(result == {"FINISHED"}, f"Standalone import returned {result}")

        collection = bpy.data.collections.get(
            "SMO Test - WaterfallWorldBreakParts000"
        )
        check(collection is not None, "Standalone test collection was not created")
        root = collection.objects.get("WaterfallWorldBreakParts000 Test Root")
        check(root is not None, "Standalone test root was not created")
        check(
            bpy.context.view_layer.objects.active is root
            and root.select_get(),
            "Standalone result was not selected and activated",
        )
        check(root["smo_mesh_object_count"] == 6, "Unexpected test mesh count")
        imported_mesh_objects = {
            obj
            for obj in collection.objects
            if obj.type == "MESH" and obj.parent is root
        }
        check(
            {obj.name for obj in imported_mesh_objects} == expected_mesh_names,
            "Standalone objects are not named "
            "SZSName_MeshName_MaterialName: "
            f"{sorted(obj.name for obj in imported_mesh_objects)} != "
            f"{sorted(expected_mesh_names)}",
        )
        check(
            {obj.data.name for obj in imported_mesh_objects}
            == expected_mesh_names,
            "Standalone mesh datablocks are not named "
            "SZSName_MeshName_MaterialName: "
            f"{sorted(obj.data.name for obj in imported_mesh_objects)} != "
            f"{sorted(expected_mesh_names)}",
        )
        generated_before_failure = tuple(collection.objects)
        mesh_count_before_failure = len(bpy.data.meshes)
        performance = json.loads(root["smo_performance_timings"])
        totals = performance["totals_seconds"]
        check(
            {
                "preparation_total",
                "szs_loading",
                "bfres_parsing",
                "bntx_texture_decoding",
                "blender_image_creation",
                "blender_mesh_creation",
                "import_total",
            }
            <= totals.keys(),
            f"Standalone performance telemetry is incomplete: {totals.keys()}",
        )
        check(
            root["smo_performance_total_seconds"] > 0.0,
            "Standalone total import timing was not stored",
        )
        check(
            "WaterfallWorldHomeStageTexture.szs"
            in root["smo_shared_texture_archives"],
            "Waterfall StageTexture archive was not inferred",
        )
        check(
            "CapWorldHomeStageTexture.szs"
            not in root["smo_shared_texture_archives"],
            "Unrelated selected-stage textures were searched",
        )
        check(
            json.loads(root["smo_texture_errors"]) == {},
            f"Standalone texture errors: {root['smo_texture_errors']}",
        )

        material = next(
            material
            for material in bpy.data.materials
            if "WaterfallWorldBreakParts000 - RockBreak00" in material.name
        )
        image_nodes = {
            node.get("smo_texture_role"): node
            for node in material.node_tree.nodes
            if node.type == "TEX_IMAGE"
        }
        check(
            {"ALBEDO", "NORMAL", "ROUGHNESS"} <= image_nodes.keys(),
            f"RockBreak00 nodes are incomplete: {image_nodes.keys()}",
        )

        normal = image_nodes["NORMAL"].image
        roughness = image_nodes["ROUGHNESS"].image
        check(
            max(normal.pixels[index] for index in range(2, 16384, 4)) > 0.9,
            "RockBreak00 normal image remained black",
        )
        check(
            max(roughness.pixels[index] for index in range(0, 4096, 4)) > 0.5,
            "RockBreak00 roughness image remained black",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            corrupt_path = (
                Path(temporary_directory)
                / "WaterfallWorldBreakParts000.bfres"
            )
            corrupt_path.write_bytes(b"FRES-corrupt-regression")
            try:
                failed_result = bpy.ops.smo.import_test_model(
                    filepath=str(corrupt_path),
                    use_selected_stage_textures=False,
                )
            except RuntimeError as exc:
                check(
                    "little-endian Switch BFRES" in str(exc),
                    f"Corrupt reimport raised the wrong error: {exc}",
                )
                failed_result = {"CANCELLED"}

        check(
            failed_result == {"CANCELLED"},
            f"Corrupt standalone reimport returned {failed_result}",
        )
        check(
            tuple(collection.objects) == generated_before_failure,
            "Failed standalone reimport replaced the previous result",
        )
        check(
            len(bpy.data.meshes) == mesh_count_before_failure,
            "Failed standalone reimport left orphan mesh datablocks",
        )

        mario_result = bpy.ops.smo.import_test_model(
            filepath=str(MARIO_CAP_ASSET),
            use_selected_stage_textures=True,
        )
        check(
            mario_result == {"FINISHED"},
            f"MarioCap standalone import returned {mario_result}",
        )
        mario_collection = bpy.data.collections.get("SMO Test - MarioCap")
        check(
            mario_collection is not None,
            "MarioCap standalone collection was not created",
        )
        mario_root = mario_collection.objects.get("MarioCap Test Root")
        check(mario_root is not None, "MarioCap test root was not created")
        shared_archives = {
            Path(path).name
            for path in json.loads(mario_root["smo_shared_texture_archives"])
        }
        check(
            "MarioHeadTexture.szs" in shared_archives,
            f"MarioCap did not search MarioHeadTexture.szs: {shared_archives}",
        )
        loaded_textures: set[str] = set()

        for obj in mario_collection.objects:
            if obj.type != "MESH":
                continue

            for material in obj.data.materials:
                if material is not None:
                    loaded_textures.update(
                        json.loads(material.get("smo_loaded_textures", "[]"))
                    )

        check(
            MARIO_CAP_TEXTURES <= loaded_textures,
            "MarioCap did not load all head-archive textures: "
            f"{sorted(MARIO_CAP_TEXTURES - loaded_textures)}",
        )
        check(
            json.loads(mario_root["smo_missing_textures"]) == [],
            f"MarioCap textures remained missing: "
            f"{mario_root['smo_missing_textures']}",
        )
        check(
            json.loads(mario_root["smo_texture_errors"]) == {},
            f"MarioCap texture errors: {mario_root['smo_texture_errors']}",
        )

        print(
            "STANDALONE_IMPORT_SMOKE: PASS - "
            f"{root['smo_mesh_object_count']} Waterfall meshes, "
            f"{len(image_nodes)} RockBreak00 image nodes, rollback preserved; "
            f"MarioCap loaded {len(MARIO_CAP_TEXTURES)} companion textures."
        )
    finally:
        addon.unregister()


if __name__ == "__main__":
    run()

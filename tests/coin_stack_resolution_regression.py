from __future__ import annotations

from pathlib import Path
import json
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon
from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.object_data import ObjectDataIndex
from smo_kingdom_importer.placement_classifier import (
    PlacementCategory,
    classify_stage_scenario,
)
from smo_kingdom_importer.stage_data import (
    StageScenario,
    read_stage_layer,
)
from smo_kingdom_importer.world_list import extract_file, read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    stage_name = "SandWorldPressExStage"
    layer = read_stage_layer(romfs_root, stage_name, 1, "Map")
    stage = StageScenario(
        stage_name=stage_name,
        scenario_number=1,
        layers={"Map": layer},
        missing_layers=("Design", "Sound"),
    )
    classified = classify_stage_scenario(stage, ObjectDataIndex(romfs_root))
    stack_groups = [
        item
        for item in classified
        if item.placement.unit_config_name == "CoinStackGroup"
    ]
    check(stack_groups, "Ice Cave contains no CoinStackGroup placements")
    check(
        all(item.resource.has_model for item in stack_groups),
        "At least one Ice Cave CoinStackGroup remains modelless",
    )
    check(
        all(
            item.resource.source_field == "ActorResourceAlias"
            and item.resource.archive_path is not None
            and item.resource.archive_path.name == "CoinStack.szs"
            and item.resource.bfres_files == ("CoinStack.bfres",)
            for item in stack_groups
        ),
        "CoinStackGroup did not resolve consistently to CoinStack.szs",
    )
    check(
        all(item.category == PlacementCategory.COLLECTIBLES for item in stack_groups),
        "CoinStackGroup was not classified as a collectible",
    )

    obj210 = next(
        item for item in stack_groups if item.placement.identifier == "obj210"
    )
    check(
        obj210.placement.model_name is None,
        "obj210 unexpectedly gained a StageData ModelName",
    )
    check(
        obj210.placement.raw.get("StacksAmount") == 3,
        "obj210 lost its StacksAmount metadata",
    )

    archive = read_szs(romfs_root / "ObjectData" / "CoinStack.szs")
    models = read_static_bfres(extract_file(archive, "CoinStack.bfres"))
    meshes = tuple(mesh for model in models for mesh in model.meshes)
    check(
        tuple(mesh.name for mesh in meshes)
        == tuple(f"Coin{index}__BodyMT" for index in range(5)),
        "CoinStack.bfres does not contain the expected five visible coins",
    )

    addon.register()

    try:
        result = bpy.ops.smo.import_test_model(
            filepath=str(romfs_root / "ObjectData" / "CoinStack.szs"),
            use_selected_stage_textures=False,
        )
        check(result == {"FINISHED"}, f"CoinStack test import returned {result}")
        collection = bpy.data.collections.get("SMO Test - CoinStack")
        check(collection is not None, "CoinStack test collection was not created")
        root = collection.objects.get("CoinStack Test Root")
        check(root is not None, "CoinStack test root was not created")
        check(
            root.get("smo_mesh_object_count") == 5,
            "CoinStack standalone import did not create five meshes",
        )
        check(
            json.loads(root.get("smo_texture_errors", "{}")) == {},
            f"CoinStack texture errors: {root.get('smo_texture_errors')}",
        )
    finally:
        addon.unregister()

    print(
        "COIN_STACK_RESOLUTION_REGRESSION: PASS "
        f"placements={len(stack_groups)} obj210_stacks_amount=3 "
        f"meshes={len(meshes)}"
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: coin_stack_resolution_regression.py -- ROMFS")

    run(Path(arguments[0]).resolve())

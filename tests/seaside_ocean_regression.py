from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.model_expectation import assess_model_expectation
from smo_kingdom_importer.object_data import ObjectDataIndex
from smo_kingdom_importer.placement_classifier import (
    ClassifiedPlacement,
    PlacementCategory,
    classify_placement,
)
from smo_kingdom_importer.stage_data import read_stage_scenario
from smo_kingdom_importer.static_model_import import (
    SMO_OT_import_static_models,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    scenario = read_stage_scenario(
        romfs_root,
        "SeaWorldHomeStage",
        1,
    )
    oceans = [
        placement
        for placement in scenario.placements
        if placement.unit_config_name == "OceanWave"
    ]
    check(len(oceans) == 1, "Seaside scenario 1 did not contain one OceanWave")
    ocean = oceans[0]
    check(ocean.translate.y == 470.0, "OceanWave water height changed")

    resource = ObjectDataIndex(romfs_root).resolve(ocean)
    check(resource.archive_path is not None, "OceanWave archive did not resolve")
    check(not resource.bfres_files, "OceanWave unexpectedly contains a BFRES")
    check(
        classify_placement(ocean, resource) == PlacementCategory.ENVIRONMENT,
        "OceanWave was not classified as environment",
    )

    classified = ClassifiedPlacement(
        placement=ocean,
        category=PlacementCategory.ENVIRONMENT,
        resource=resource,
        model_expectation=assess_model_expectation(
            unit_config_name=ocean.unit_config_name,
            parameter_config_name=str(
                ocean.unit_config.get("ParameterConfigName") or ""
            ),
            stage_layers=(ocean.stage_layer,),
            placement_categories=(ocean.category,),
            import_category=PlacementCategory.ENVIRONMENT.value,
            resource=resource,
        ),
    )
    collection = bpy.data.collections.new("Seaside Ocean Regression")
    bpy.context.scene.collection.children.link(collection)
    root = bpy.data.objects.new("Seaside Ocean Root", None)
    collection.objects.link(root)
    operator = SimpleNamespace()
    operator._collection = collection
    operator._root = root
    operator._group_scope = "Seaside Kingdom S1"
    operator._mesh_object_count = 0
    operator._model_placement_count = 0
    operator._procedural_ocean_count = 0
    SMO_OT_import_static_models._process_placement(operator, classified)

    imported = [
        obj
        for child in collection.children
        for obj in child.objects
        if obj.get("smo_id") == ocean.identifier
    ]
    check(len(imported) == 1, "OceanWave did not create one mesh object")
    ocean_object = imported[0]
    check(ocean_object.type == "MESH", "OceanWave remained an empty fallback")
    check(
        ocean_object.get("smo_representation") == "PROCEDURAL_OCEAN",
        "OceanWave representation metadata is wrong",
    )
    check(
        abs(ocean_object.location.z - 4.7) < 1e-6,
        "OceanWave did not use the StageData water height",
    )
    check(
        operator._procedural_ocean_count == 1,
        "Procedural ocean count was not updated",
    )
    check(
        operator._model_placement_count == 0,
        "Procedural ocean was counted as a BFRES placement",
    )
    print("SEASIDE_OCEAN_REGRESSION: PASS")


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python seaside_ocean_regression.py "
            "-- ROMFS"
        )

    run(Path(arguments[0]).resolve())

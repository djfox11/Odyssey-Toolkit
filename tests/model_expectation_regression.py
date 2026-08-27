from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.model_expectation import (
    ModelExpectation,
    assess_model_expectation,
)
from smo_kingdom_importer.object_data import ObjectResource
from smo_kingdom_importer.placement_classifier import (
    ClassifiedPlacement,
    PlacementCategory,
)
from smo_kingdom_importer.stage_data import StagePlacement, Vector3
from smo_kingdom_importer.static_model_import import SMO_OT_import_static_models


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    sound = assess_model_expectation(
        unit_config_name="SePlayRail",
        stage_layers=("Sound",),
        placement_categories=("ObjList",),
    )
    check(
        sound.expectation == ModelExpectation.CONFIRMED_NON_MESH,
        "Sound-only actor was not recognised as non-mesh",
    )
    area = assess_model_expectation(
        unit_config_name="CameraArea",
        stage_layers=("Design",),
        placement_categories=("AreaList",),
    )
    check(
        area.expectation == ModelExpectation.CONFIRMED_NON_MESH,
        "AreaList actor was not recognised as non-mesh",
    )
    group = assess_model_expectation(
        unit_config_name="CoinStackGroup",
        parameter_config_name="CoinStackGroup",
        stage_layers=("Map",),
        placement_categories=("ObjList",),
    )
    check(
        group.expectation == ModelExpectation.RUNTIME_VISUAL,
        "Group actor was not recognised as runtime-composed",
    )
    subactor = assess_model_expectation(
        unit_config_name="RuntimeActor",
        archive_metadata=SimpleNamespace(
            init_model_references=(),
            sub_actor_model_names=("ChildModel",),
        ),
    )
    check(
        subactor.expectation == ModelExpectation.RUNTIME_VISUAL
        and subactor.confidence == "HIGH",
        "InitSubActor evidence was not treated as high-confidence runtime visual",
    )
    runtime_config = assess_model_expectation(
        unit_config_name="EffectDrivenActor",
        archive_metadata=SimpleNamespace(
            init_model_references=(),
            sub_actor_model_names=(),
            byml_files=("InitEffect.byml",),
        ),
    )
    check(
        runtime_config.expectation == ModelExpectation.RUNTIME_VISUAL
        and runtime_config.confidence == "HIGH",
        "ObjectData runtime BYML evidence was not recognised",
    )
    expected = assess_model_expectation(
        unit_config_name="EnemyExample",
        explicit_model_name="EnemyExampleModel",
    )
    check(
        expected.expectation == ModelExpectation.MODEL_EXPECTED
        and expected.confidence == "HIGH",
        "Explicit ModelName was not treated as high-confidence model evidence",
    )
    unknown = assess_model_expectation(unit_config_name="MysteryController")
    check(
        unknown.expectation == ModelExpectation.UNKNOWN,
        "Weak evidence was not left unknown",
    )
    for helper_name in (
        "StageSwitch",
        "PlayerStartObj",
        "TRexSubRouteKey",
        "CityWorldHomeCollision001",
        "PeachWorldPictureRoomZone",
    ):
        helper = assess_model_expectation(unit_config_name=helper_name)
        check(
            helper.expectation == ModelExpectation.CONFIRMED_NON_MESH,
            f"{helper_name} polluted the missing-model queue",
        )
    for runtime_name in (
        "PlayerActorHakoniwa",
        "CoinCirclePlacement",
        "RiseMapPartsHolder",
    ):
        runtime = assess_model_expectation(unit_config_name=runtime_name)
        check(
            runtime.expectation == ModelExpectation.RUNTIME_VISUAL,
            f"{runtime_name} was not separated as a runtime visual",
        )
    zone = assess_model_expectation(
        unit_config_name="AnyStageZone",
        placement_categories=("ZoneList",),
    )
    check(
        zone.expectation == ModelExpectation.CONFIRMED_NON_MESH,
        "ZoneList placement polluted the missing-model queue",
    )

    placement = StagePlacement(
        identifier="diagnostic_runtime_group",
        unit_config_name="CoinStackGroup",
        model_name=None,
        category="ObjList",
        stage_layer="Map",
        placement_file_name="TestMap.byml",
        layer_config_name="Common",
        translate=Vector3(0.0, 0.0, 0.0),
        rotate=Vector3(0.0, 0.0, 0.0),
        scale=Vector3(1.0, 1.0, 1.0),
        rotation_quaternion=(0.0, 0.0, 0.0, 1.0),
        links={},
        unit_config={"ParameterConfigName": "CoinStackGroup"},
        is_link_destination=False,
        is_root=True,
        raw={},
        source_stage_name="TestStage",
    )
    resource = ObjectResource(
        source_field=None,
        requested_name=None,
        archive_path=None,
        bfres_files=(),
    )
    classified = ClassifiedPlacement(
        placement=placement,
        category=PlacementCategory.UNKNOWN_MODELLESS,
        resource=resource,
        model_expectation=group,
    )
    collection = bpy.data.collections.new("Model Expectation Regression")
    bpy.context.scene.collection.children.link(collection)
    root = bpy.data.objects.new("Model Expectation Root", None)
    collection.objects.link(root)
    operator = SimpleNamespace(
        _collection=collection,
        _root=root,
        _group_scope="Diagnostic",
        _import_stage_lighting=False,
        _asset_errors={},
        _fallback_count=0,
        _model_expectation_counts={},
    )
    SMO_OT_import_static_models._process_placement(operator, classified)
    fallback = next(
        obj
        for obj in bpy.data.objects
        if obj.get("smo_id") == placement.identifier
    )
    check(
        fallback.get("smo_model_expectation") == "RUNTIME_VISUAL",
        "Fallback object did not retain its model expectation",
    )
    check(
        fallback.get("smo_model_expectation_confidence") == "MEDIUM",
        "Fallback object did not retain expectation confidence",
    )
    check(
        json.loads(fallback["smo_model_expectation_reasons"]),
        "Fallback object did not retain expectation evidence",
    )
    check(
        fallback.get("smo_parameter_config_name") == "CoinStackGroup",
        "Fallback object did not retain ParameterConfigName",
    )
    check(
        operator._model_expectation_counts == {"RUNTIME_VISUAL": 1},
        "Fallback expectation aggregate was not updated",
    )

    print("MODEL_EXPECTATION_REGRESSION: PASS")


if __name__ == "__main__":
    run()

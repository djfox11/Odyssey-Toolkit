from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import bpy
import tempfile


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.actor_registry import (
    ActorRegistry,
    RegistryActorEvidence,
    romfs_key,
)
from smo_kingdom_importer.model_expectation import (
    ModelExpectation,
    assess_model_expectation,
)
from smo_kingdom_importer.object_data import ObjectDataIndex
from smo_kingdom_importer.placement_classifier import (
    ClassifiedPlacement,
    PlacementCategory,
)
from smo_kingdom_importer.stage_data import StagePlacement, Vector3
from smo_kingdom_importer.static_model_import import SMO_OT_import_static_models
from smo_kingdom_importer.registry_report import (
    COMPOSITE_RESOURCE_MAPPING,
    CURATED_RESOURCE_MAPPING,
    build_registry_report,
)
from smo_kingdom_importer.resource_rules import (
    ACTOR_COMPOSITE_RESOURCE_ALIASES,
    ACTOR_RESOURCE_ALIASES,
)


USER_IDENTIFIED_ALIASES = {
    "Chorobon2D3D": "Chorobon2D",
    "DemoActorCapManHero": "CapManHero",
    "DemoActorCapManHeroine": "CapManHeroine",
    "DemoActorKoopaShip": "KoopaShip",
    "DemoActorPeach": "Peach",
    "DemoActorShineTower": "ShineTower",
    "DemoPeachWedding": "PeachWedding",
    "ElectricWireKoopa": "ElectricWireMoverKoopa",
    "FigureWalkingNpc": "NokonokoNpc",
    "FireBrosPossessed": "FireBros",
    "GrowPlantSeedTop": "GrowPlantPartsTop",
    "HackCar": "Car",
    "HammerBrosPossessed": "HammerBros",
    "KoopaChurch": "Koopa",
    "KoopaLv1": "Koopa",
    "KoopaLv2": "Koopa",
    "KoopaLv3": "Koopa",
    "Kuribo2D3D": "Kuribo2D",
    "KuriboPossessed": "Kuribo",
    "OpeningStageStartCapManHero": "CapManHero",
    "RadiconNpc": "CityMan",
    "SnowManRaceNpc": "SnowMan",
    "VolleyballNpc": "SeaMan",
    "Yukimaru": "SnowMan",
    "YukimaruRacePlayer": "SnowManRacer",
    "YukimaruRacer": "SnowManRacer",
    "YukimaruRacerTiago": "SnowManRacer",
}

USER_IDENTIFIED_COMPOSITES = {
    "BossKnuckleLv2": ("BossKnuckleBody", "BossKnuckleHead"),
    "Gunetter": ("GunetterBody", "GunetterHead"),
    "GunetterMove": ("GunetterBody", "GunetterHead"),
    "Mofumofu": ("MofumofuBody", "MofumofuHead"),
    "MofumofuLv2": ("MofumofuBody", "MofumofuHead"),
    "PaulineAtCeremony": ("CityMayorDress", "CityMayorFace"),
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _placement(unit_config_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        unit_config_name=unit_config_name,
        model_name=None,
        source_stage_name="ManualIdentificationStage",
        unit_config={"ParameterConfigName": ""},
        raw={},
    )


def _actor(unit_config_name: str) -> RegistryActorEvidence:
    return RegistryActorEvidence(
        unit_config_name=unit_config_name,
        parameter_config_name="",
        models=(),
        modelless_occurrence_count=1,
        modelless_source_stages=("ManualIdentificationStage",),
        modelless_stage_layers=("Map",),
        modelless_categories=("ObjList",),
    )


def _test_composite_mesh_flow(resource: object) -> None:
    parent = bpy.data.collections.new("SMO Composite Regression")
    bpy.context.scene.collection.children.link(parent)
    root = bpy.data.objects.new("SMO Composite Root", None)
    parent.objects.link(root)

    placement = StagePlacement(
        identifier="CompositeRegression",
        unit_config_name="Gunetter",
        model_name=None,
        category="ObjList",
        stage_layer="Map",
        placement_file_name="Map",
        layer_config_name="Map",
        translate=Vector3(100.0, 200.0, 300.0),
        rotate=Vector3(0.0, 0.0, 0.0),
        scale=Vector3(1.0, 1.0, 1.0),
        rotation_quaternion=(0.0, 0.0, 0.0, 1.0),
        links={},
        unit_config={"ParameterConfigName": ""},
        is_link_destination=False,
        is_root=True,
        raw={},
        source_stage_name="CompositeRegressionStage",
    )
    assessment = assess_model_expectation(
        unit_config_name=placement.unit_config_name,
        resource=resource,
    )
    classified = ClassifiedPlacement(
        placement=placement,
        category=PlacementCategory.CHARACTERS,
        resource=resource,
        model_expectation=assessment,
    )

    meshes_by_archive = {}
    source_meshes = []

    for component in resource.model_resources:
        archive_name = component.archive_path.stem
        mesh = bpy.data.meshes.new(f"{archive_name}_RegressionMesh")
        mesh["smo_display_name"] = f"{archive_name}_RegressionMesh"
        meshes_by_archive[archive_name] = (mesh,)
        source_meshes.append(mesh)

    operator = SimpleNamespace(
        _collection=parent,
        _root=root,
        _group_scope="Composite Regression",
        _import_stage_lighting=False,
        _mesh_object_count=0,
        _model_placement_count=0,
        _procedural_ocean_count=0,
        _fallback_count=0,
        _model_expectation_counts={},
        _asset_errors={},
        _meshes_for_resource=lambda _classified, component: (
            meshes_by_archive[component.archive_path.stem]
        ),
    )
    SMO_OT_import_static_models._process_placement(operator, classified)
    imported = tuple(
        obj
        for obj in bpy.data.objects
        if obj.parent == root and obj.data in source_meshes
    )

    check(len(imported) == 2, "Composite import did not create two mesh objects")
    check(operator._model_placement_count == 1, "Composite counted as two placements")
    check(operator._mesh_object_count == 2, "Composite mesh-object count is incorrect")

    for obj in imported:
        check(
            obj.get("smo_representation") == "COMPOSITE_MODEL",
            "Composite object lost its representation metadata",
        )
        check(
            len(json.loads(obj["smo_resource_components"])) == 2,
            "Composite component metadata is incomplete",
        )
        check(
            tuple(round(value, 6) for value in obj.location)
            == (1.0, -3.0, 2.0),
            "Composite component did not receive the placement transform",
        )

    for obj in imported:
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.objects.remove(root, do_unlink=True)

    for mesh in source_meshes:
        bpy.data.meshes.remove(mesh)

    for child in tuple(parent.children):
        bpy.data.collections.remove(child)
    bpy.data.collections.remove(parent)

def run(romfs_root: Path) -> None:
    check(
        all(
            ACTOR_RESOURCE_ALIASES.get(actor_name) == archive_name
            for actor_name, archive_name in USER_IDENTIFIED_ALIASES.items()
        ),
        "A supplied single-archive identification is missing from curated rules",
    )
    check(
        all(
            ACTOR_COMPOSITE_RESOURCE_ALIASES.get(actor_name) == archive_names
            for actor_name, archive_names in USER_IDENTIFIED_COMPOSITES.items()
        ),
        "A supplied multi-archive identification is missing or misspelled",
    )

    object_data = ObjectDataIndex(romfs_root)

    for actor_name, archive_name in USER_IDENTIFIED_ALIASES.items():
        resource = object_data.resolve(_placement(actor_name))
        check(resource.has_model, f"{actor_name} alias contains no BFRES")
        check(
            resource.archive_path is not None
            and resource.archive_path.name == f"{archive_name}.szs",
            f"{actor_name} resolved to {resource.archive_path}",
        )

    bird_placement = _placement("BirdCarryMeat")
    bird_placement.source_stage_name = "LavaWorldHomeStage"
    bird = object_data.resolve(bird_placement)
    check(
        bird.has_model
        and bird.source_field == "StageResourceRule"
        and bird.archive_path is not None
        and bird.archive_path.name == "BirdLava.szs",
        "Lava bird carrying meat did not resolve to its stage-family model",
    )

    for actor_name, archive_names in USER_IDENTIFIED_COMPOSITES.items():
        resource = object_data.resolve(_placement(actor_name))
        check(resource.has_model, f"{actor_name} composite contains no BFRES")
        check(
            resource.source_field == "CompositeActorResource",
            f"{actor_name} did not resolve through its composite rule",
        )
        check(
            tuple(
                component.archive_path.stem
                for component in resource.model_resources
                if component.archive_path is not None
            )
            == archive_names,
            f"{actor_name} resolved the wrong component archives",
        )

        for component in resource.model_resources:
            check(
                component.archive_path is not None
                and component.bfres_files,
                f"{actor_name} component contains no BFRES",
            )

    _test_composite_mesh_flow(object_data.resolve(_placement("Gunetter")))

    for helper_name in (
        "AllDeadWatcher",
        "CandlestandBgmDirector",
        "FogRequester",
        "KeyMoveCameraRailMove",
        "RailRabbit",
        "StackerCapWorldCtrl",
        "TransparentWall2D",
    ):
        assessment = assess_model_expectation(unit_config_name=helper_name)
        check(
            assessment.expectation == ModelExpectation.CONFIRMED_NON_MESH,
            f"{helper_name} was not classified as a non-mesh helper",
        )

    registry = ActorRegistry(
        romfs_key=romfs_key(romfs_root),
        created_utc=datetime.now(timezone.utc).isoformat(),
        archive_inventory_digest="report-only",
        archives_scanned=0,
        scenario_count=0,
        placement_count=3,
        placement_with_model_count=0,
        build_errors=(),
        actors=(
            _actor("AllDeadWatcher"),
            _actor("CapFlower"),
            _actor("Gunetter"),
        ),
    )

    with tempfile.TemporaryDirectory() as temporary_directory:
        report = build_registry_report(
            registry,
            romfs_root,
            Path(temporary_directory),
        )

    check(
        report.counts.get(CURATED_RESOURCE_MAPPING) == 1,
        "Existing CapFlower alias was not credited by the report",
    )
    check(
        report.counts.get(COMPOSITE_RESOURCE_MAPPING) == 1,
        "Gunetter composite identification was not credited by the report",
    )
    check(
        report.counts.get(ModelExpectation.CONFIRMED_NON_MESH.value) == 1,
        "Watcher classification was not credited by the report",
    )

    print(
        "MODEL_IDENTIFICATION_REGRESSION: PASS "
        f"aliases={len(USER_IDENTIFIED_ALIASES)} "
        f"composites={len(USER_IDENTIFIED_COMPOSITES)}"
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: model_identification_regression.py -- ROMFS")

    run(Path(arguments[0]).resolve())

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.object_data import ObjectDataIndex
from smo_kingdom_importer.placement_classifier import (
    PlacementCategory,
    classify_stage_scenario,
)
from smo_kingdom_importer.stage_data import StageScenario, read_stage_layer


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def root_map_scenario(
    romfs_root: Path,
    stage_name: str,
    scenario_number: int,
) -> StageScenario:
    layer = read_stage_layer(
        romfs_root,
        stage_name,
        scenario_number,
        "Map",
    )
    return StageScenario(
        stage_name=stage_name,
        scenario_number=scenario_number,
        layers={"Map": layer},
        missing_layers=("Design", "Sound"),
    )


def classified_actor(
    romfs_root: Path,
    stage: StageScenario,
    actor_name: str,
):
    matches = [
        item
        for item in classify_stage_scenario(
            stage,
            ObjectDataIndex(romfs_root),
        )
        if item.placement.unit_config_name == actor_name
    ]
    check(len(matches) == 1, f"Expected one {actor_name}, found {len(matches)}")
    return matches[0]


def run(romfs_root: Path) -> None:
    city_pipes = [
        item
        for item in classify_stage_scenario(
            root_map_scenario(romfs_root, "CityWorldHomeStage", 2),
            ObjectDataIndex(romfs_root),
        )
        if item.placement.unit_config_name
        in {"Dokan", "DokanStageChange"}
    ]
    check(city_pipes, "Metro scenario 2 contained no representative pipes")
    check(
        all(
            item.category == PlacementCategory.GAMEPLAY
            for item in city_pipes
        ),
        "Dokan pipes were not classified as Gameplay Objects",
    )

    damaged = classified_actor(
        romfs_root,
        root_map_scenario(romfs_root, "WaterfallWorldHomeStage", 1),
        "ShineTowerRocket",
    )
    check(damaged.placement.identifier == "obj6", "Cascade scenario 1 did not select BeforeClear")
    check(damaged.resource.source_field == "ActorStateResourceRule", "Damaged Odyssey used an unsafe source")
    check(damaged.resource.archive_path is not None, "Damaged Odyssey has no archive")
    check(damaged.resource.archive_path.name == "ShineTowerDirty.szs", "Damaged Odyssey used the wrong archive")
    check(damaged.category == PlacementCategory.ENVIRONMENT, "Damaged Odyssey is not environment geometry")

    complete = classified_actor(
        romfs_root,
        root_map_scenario(romfs_root, "WaterfallWorldHomeStage", 2),
        "ShineTowerRocket",
    )
    check(complete.placement.identifier == "obj2093", "Cascade scenario 2 retained BeforeClear")
    check(complete.resource.archive_path is not None, "Complete Odyssey has no archive")
    check(complete.resource.archive_path.name == "ShineTower.szs", "Complete Odyssey used the wrong archive")

    peach_postgame = root_map_scenario(romfs_root, "PeachWorldHomeStage", 2)
    check(
        all(p.unit_config_name != "WorldTravelingPeach" for p in peach_postgame.placements),
        "Mushroom postgame scenario unexpectedly contains returned Peach",
    )
    peach_returned = root_map_scenario(romfs_root, "PeachWorldHomeStage", 3)
    peach = classified_actor(romfs_root, peach_returned, "WorldTravelingPeach")
    check(peach.resource.archive_path is not None, "Returned Peach has no archive")
    check(peach.resource.archive_path.name == "Peach.szs", "Returned Peach used the wrong archive")
    check(peach.placement.raw.get("TiaraWaitActionName") == "Wait", "Peach lost her runtime Tiara metadata")
    check(peach.category == PlacementCategory.CHARACTERS, "Returned Peach is not a character")

    ground = next(
        item
        for item in classify_stage_scenario(
            peach_returned,
            ObjectDataIndex(romfs_root),
        )
        if item.placement.unit_config_name.startswith("PeachWorldHomeGround")
    )
    check(ground.category != PlacementCategory.CHARACTERS, "Mushroom terrain was classified as Peach")

    print("DYNAMIC_ACTOR_RESOLUTION_REGRESSION: PASS")


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python "
            "dynamic_actor_resolution_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())
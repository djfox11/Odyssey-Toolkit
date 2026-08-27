from __future__ import annotations

from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon
from smo_kingdom_importer.stage_catalog import (
    NAMED_STAGE_GROUPS,
    discover_stage_catalogue,
)
from smo_kingdom_importer.stage_data import (
    read_stage_scenario,
    read_stage_scenario_numbers,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    registered = False

    try:
        addon.register()
        registered = True
        settings = bpy.context.scene.smo_settings
        settings.romfs_path = str(romfs_root)
        check(settings.worlds_loaded, f"Stage selector failed: {settings.load_error}")

        catalogue = discover_stage_catalogue(romfs_root / "StageData")
        map_count = len(tuple((romfs_root / "StageData").glob("*Map.szs")))
        translated_count = sum(
            (romfs_root / "StageData" / f"{stage_name}Map.szs").is_file()
            for _, entries in NAMED_STAGE_GROUPS
            for stage_name, _ in entries
        )
        check(len(catalogue) == map_count, "Not every Map archive was catalogued")
        check(
            sum(entry.translated for entry in catalogue) == translated_count,
            "Not every supplied noclip translation was retained",
        )
        check(
            len(addon._KINGDOM_ENUM_ITEMS) == map_count,
            "Blender stage enum does not match the ROMFS catalogue",
        )
        check(
            all(item[3] == 0 for item in addon._KINGDOM_ENUM_ITEMS),
            "The stage selector still contains thumbnail icons",
        )
        check(
            "kingdoms" not in addon._PREVIEW_COLLECTIONS,
            "A kingdom thumbnail preview collection was created",
        )

        labels = {item[0]: item[1] for item in addon._KINGDOM_ENUM_ITEMS}
        check(labels["CapWorldHomeStage"] == "Cap Kingdom", "Main stage label is wrong")
        check(
            labels["CapWorldTowerStage"] == "Cap Kingdom — Cap Tower",
            "Translated sublevel label is wrong",
        )
        check(
            labels["LakeWorld2DZone"] == "Unlisted Zones — LakeWorld2DZone",
            "Unlisted zone label is wrong",
        )
        cap_scenarios = {
            item[0]: item for item in addon._SCENARIO_ENUM_ITEMS
        }
        check(
            cap_scenarios["2"][1] == "Scenario 2 — Main story clear",
            "WorldList scenario tags changed",
        )
        scenario_previews = addon._PREVIEW_COLLECTIONS.get("scenarios")
        check(scenario_previews is not None, "Scenario previews were not loaded")
        check(
            "scenario::Main story clear::main_story_clear.png"
            in scenario_previews,
            "WorldList scenario icon was not retained",
        )

        representative_stages = (
            "CapWorldTowerStage",
            "SandWorldPyramid000Stage",
            "ForestWorldWoodsStage",
            "DemoOpeningStage",
            "HomeShipInsideStage",
            "LakeWorld2DZone",
            "MoonWorldWeddingRoom2Stage",
            "WorldMapStage",
        )

        for stage_name in representative_stages:
            numbers = read_stage_scenario_numbers(romfs_root, stage_name)
            check(numbers, f"{stage_name} has no scenarios")

        settings.kingdom = "CapWorldTowerStage"
        expected = tuple(
            str(number)
            for number in read_stage_scenario_numbers(
                romfs_root,
                "CapWorldTowerStage",
            )
        )
        actual = tuple(item[0] for item in addon._SCENARIO_ENUM_ITEMS)
        check(actual == expected, "Translated stage scenarios did not rebuild")
        cap_tower = read_stage_scenario(
            romfs_root,
            "CapWorldTowerStage",
            int(settings.scenario),
        )
        check(cap_tower.placements, "Translated standalone stage did not load")

        settings.kingdom = "LakeWorld2DZone"
        check(
            settings.kingdom == "LakeWorld2DZone",
            "Unlisted zone could not be selected",
        )
        check(not settings.scenario_error, "Unlisted zone scenarios failed to load")
        lake_zone = read_stage_scenario(
            romfs_root,
            "LakeWorld2DZone",
            int(settings.scenario),
        )
        check(lake_zone.placements, "Unlisted zone did not load standalone")

        previous_scenario = settings.scenario
        check(addon.load_worlds(settings), "Stage catalogue could not reload")
        check(
            settings.kingdom == "LakeWorld2DZone",
            "Reload did not preserve the selected stage",
        )
        check(
            settings.scenario == previous_scenario,
            "Reload did not preserve the selected scenario",
        )
        print(
            "STAGE_SELECTOR_ROMFS_REGRESSION: PASS "
            f"({len(catalogue)} stages, {translated_count} translated)"
        )
    finally:
        if registered:
            addon.unregister()


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python "
            "stage_selector_romfs_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())

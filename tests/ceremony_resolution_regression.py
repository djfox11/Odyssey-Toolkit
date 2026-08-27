from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.object_data import ObjectDataIndex
from smo_kingdom_importer.placement_classifier import classify_stage_scenario
from smo_kingdom_importer.stage_data import read_stage_scenario


EXPECTED_ARCHIVES = {
    "PaulineAtCeremony": ("CityMayorDress.szs", "CityMayorFace.szs"),
    "SessionMusicianBass": ("BandMan.szs",),
    "SessionMusicianDrum": ("BandMan.szs",),
    "SessionMusicianGuitar": ("BandMan.szs",),
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    for scenario_number in (1, 2):
        stage = read_stage_scenario(
            romfs_root,
            "Special2WorldHomeStage",
            scenario_number,
        )
        classified = classify_stage_scenario(stage, ObjectDataIndex(romfs_root))
        ceremony = {
            item.placement.unit_config_name: item
            for item in classified
            if item.placement.unit_config_name in EXPECTED_ARCHIVES
        }
        check(
            ceremony.keys() == EXPECTED_ARCHIVES.keys(),
            f"Scenario {scenario_number} ceremony actor set is incomplete",
        )

        for actor_name, archive_names in EXPECTED_ARCHIVES.items():
            item = ceremony[actor_name]
            resource = item.resource
            expected_source = (
                "CompositeActorResource"
                if len(archive_names) > 1
                else "ActorResourceAlias"
            )
            check(
                resource.source_field == expected_source,
                f"{actor_name} used {resource.source_field}",
            )
            resolved_names = tuple(
                component.archive_path.name
                for component in resource.model_resources
                if component.archive_path is not None
            )
            check(
                resolved_names == archive_names,
                f"{actor_name} resolved to {resolved_names}",
            )
            check(resource.has_model, f"{actor_name} archive has no BFRES")
            check(
                item.category.value == "CHARACTERS",
                f"{actor_name} is not classified as a character",
            )

    print("CEREMONY_RESOLUTION_REGRESSION: PASS")


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: blender --background --python ceremony_resolution_regression.py -- ROMFS")

    run(Path(arguments[0]).resolve())
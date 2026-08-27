from __future__ import annotations

from pathlib import Path
import sys
import time

import oead


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.stage_data import collect_placements, read_stage_layer
from smo_kingdom_importer.world_list import extract_file, read_szs, unwrap


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def raw_without_links(placement):
    return {
        key: value
        for key, value in placement.raw.items()
        if key != "Links"
    }


def link_ids(placement) -> dict[str, tuple[str, ...]]:
    raw_links = placement.raw.get("Links") or {}
    return {
        str(name): tuple(
            str(target.get("Id", "")).strip()
            for target in targets
            if isinstance(target, dict) and str(target.get("Id", "")).strip()
        )
        for name, targets in raw_links.items()
        if isinstance(targets, list)
    }


def placement_signature(placement):
    return (
        placement.identifier,
        placement.unit_config_name,
        placement.model_name,
        placement.category,
        placement.stage_layer,
        placement.placement_file_name,
        placement.layer_config_name,
        placement.translate,
        placement.rotate,
        placement.scale,
        placement.rotation_quaternion,
        placement.links,
        placement.unit_config,
        placement.is_link_destination,
        placement.is_root,
        placement.source_stage_name,
        placement.zone_path,
        raw_without_links(placement),
        link_ids(placement),
    )


def run(romfs_root: Path) -> None:
    stage_name = "CityWorldHomeStage"
    scenario_number = 2
    archive = read_szs(
        romfs_root / "StageData" / f"{stage_name}Map.szs"
    )
    byml_data = extract_file(archive, f"{stage_name}Map.byml")
    raw_root = oead.byml.from_binary(byml_data)

    started = time.perf_counter()
    reference_data = unwrap(raw_root[scenario_number - 1])
    reference = collect_placements(reference_data, "Map", stage_name)
    reference_seconds = time.perf_counter() - started

    started = time.perf_counter()
    actual = read_stage_layer(
        romfs_root,
        stage_name,
        scenario_number,
        "Map",
    )
    actual_seconds = time.perf_counter() - started

    expected_by_id = {
        placement.identifier: placement_signature(placement)
        for placement in reference
    }
    actual_by_id = {
        placement.identifier: placement_signature(placement)
        for placement in actual.placements
    }
    check(
        actual_by_id == expected_by_id,
        "Lazy StageData placement fields, metadata, or links changed",
    )
    check(
        len(actual.placements) == 2212,
        f"Metro scenario 2 placement count changed: {len(actual.placements)}",
    )
    check(
        sum(not placement.is_root for placement in actual.placements) == 386,
        "Metro scenario 2 linked-placement count changed",
    )
    check(
        isinstance(actual.scenario_data, dict),
        "StageLayer scenario_data is no longer a Python dictionary",
    )
    check(
        all(
            isinstance(value, (dict, list, str, int, float, bool, type(None)))
            for value in actual.scenario_data.values()
        ),
        "StageLayer scenario_data retained raw oead containers",
    )

    print(
        "STAGE_DATA_LAZY_REGRESSION: PASS "
        f"placements={len(actual.placements)} "
        f"lazy={actual_seconds:.3f}s "
        f"reference_selected={reference_seconds:.3f}s"
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python "
            "stage_data_lazy_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())
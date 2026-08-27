from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.model_category_registry import (
    resolved_model_categories,
    resolved_model_category,
)
from smo_kingdom_importer.object_data import ObjectResource
from smo_kingdom_importer.placement_classifier import (
    CATEGORY_OVERRIDES,
    IMPORT_CATEGORY_FILTERS,
    PlacementCategory,
    classify_placement,
    import_filter_group,
)
from smo_kingdom_importer.static_model_import import import_category_enabled


EXPECTED_COUNTS = {
    "CHARACTERS": 148,
    "COLLECTIBLES": 28,
    "ENVIRONMENT": 1517,
    "GAMEPLAY": 256,
    "TECHNICAL": 12,
    "UNCLASSIFIED": 6,
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _placement(unit_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        unit_config_name=unit_name,
        model_name=None,
        unit_config={"ParameterConfigName": ""},
        stage_layer="Map",
        category="ObjectList",
    )


def _resource(archive_name: str) -> ObjectResource:
    return ObjectResource(
        source_field="UnitConfigName",
        requested_name=archive_name,
        archive_path=Path("ObjectData") / f"{archive_name}.szs",
        bfres_files=(f"{archive_name}.bfres",),
    )


def run() -> None:
    categories = resolved_model_categories()
    counts = dict(sorted(Counter(categories.values()).items()))
    check(len(categories) == 1967, f"Unexpected registry size: {len(categories)}")
    check(counts == EXPECTED_COUNTS, f"Unexpected category counts: {counts}")
    check("ENEMIES" not in counts, "Enemies were not folded into Characters")

    examples = {
        "AirCurrent": PlacementCategory.ENVIRONMENT,
        "AmiiboNpc": PlacementCategory.CHARACTERS,
        "AirBubble": PlacementCategory.GAMEPLAY,
        "Coin": PlacementCategory.COLLECTIBLES,
        "DemoCamera": PlacementCategory.HELPERS,
        "CapSlotBase": PlacementCategory.UNKNOWN_MODEL,
    }

    for archive_name, expected in examples.items():
        check(
            resolved_model_category(archive_name.upper()) is not None,
            f"Case-insensitive lookup failed for {archive_name}",
        )
        actual = classify_placement(
            _placement(f"Neutral{archive_name}Actor"),
            _resource(archive_name),
        )
        check(
            actual == expected,
            f"{archive_name} classified as {actual}, expected {expected}",
        )

    override_name = "CapFlower"
    check(override_name in CATEGORY_OVERRIDES, "Override fixture is missing")
    check(
        classify_placement(
            _placement(override_name),
            _resource("AirCurrent"),
        )
        == PlacementCategory.GAMEPLAY,
        "Actor-specific override did not take priority over archive category",
    )

    filter_properties = {
        property_name
        for _, _, property_name in IMPORT_CATEGORY_FILTERS
    }
    check(len(filter_properties) == 8, "Category filter properties are incomplete")
    light = SimpleNamespace(
        placement=SimpleNamespace(unit_config_name="PrePassPointLight"),
        category=PlacementCategory.UNKNOWN_MODELLESS,
    )
    check(
        import_filter_group(light) == "TECHNICAL",
        "Placed lights are not controlled by the Technical filter",
    )

    settings = SimpleNamespace(
        include_environment=True,
        include_characters=False,
        include_gameplay=False,
        include_collectibles=False,
        include_effects=False,
        include_audio=False,
        include_technical=False,
        include_unclassified=False,
    )
    environment = SimpleNamespace(
        placement=SimpleNamespace(unit_config_name="AirCurrent"),
        category=PlacementCategory.ENVIRONMENT,
    )
    character = SimpleNamespace(
        placement=SimpleNamespace(unit_config_name="AmiiboNpc"),
        category=PlacementCategory.CHARACTERS,
    )
    check(
        import_category_enabled(settings, environment),
        "Enabled Environment category was filtered out",
    )
    check(
        not import_category_enabled(settings, character),
        "Disabled Characters category remained enabled",
    )
    check(
        not import_category_enabled(settings, light),
        "Placed light ignored the disabled Technical category",
    )
    print(
        "MODEL_CATEGORY_REGISTRY_REGRESSION: PASS "
        f"entries={len(categories)} categories={counts}"
    )


if __name__ == "__main__":
    run()
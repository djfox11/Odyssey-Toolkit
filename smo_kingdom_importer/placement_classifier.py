from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model_category_registry import resolved_model_category
from .model_expectation import (
    ModelExpectationAssessment,
    assess_model_expectation,
)
from .object_data import ObjectDataIndex, ObjectResource
from .performance import timed
from .stage_data import StagePlacement, StageScenario


class PlacementCategory(str, Enum):
    ENVIRONMENT = "ENVIRONMENT"
    CHARACTERS = "CHARACTERS"
    GAMEPLAY = "GAMEPLAY"
    COLLECTIBLES = "COLLECTIBLES"
    EFFECTS = "EFFECTS"
    AUDIO = "AUDIO"
    AREAS = "AREAS"
    CAMERAS = "CAMERAS"
    HELPERS = "HELPERS"
    DEBUG = "DEBUG"
    UNKNOWN_MODEL = "UNKNOWN_MODEL"
    UNKNOWN_MODELLESS = "UNKNOWN_MODELLESS"


CATEGORY_LABELS = {
    PlacementCategory.ENVIRONMENT: "Environment",
    PlacementCategory.CHARACTERS: "Characters and Enemies",
    PlacementCategory.GAMEPLAY: "Gameplay Objects",
    PlacementCategory.COLLECTIBLES: "Collectibles",
    PlacementCategory.EFFECTS: "Effects",
    PlacementCategory.AUDIO: "Audio",
    PlacementCategory.AREAS: "Areas and Volumes",
    PlacementCategory.CAMERAS: "Cameras",
    PlacementCategory.HELPERS: "Logic and Helpers",
    PlacementCategory.DEBUG: "Debug",
    PlacementCategory.UNKNOWN_MODEL: "Unknown With Models",
    PlacementCategory.UNKNOWN_MODELLESS: "Unknown Without Models",
}

CATEGORY_ORDER = tuple(PlacementCategory)

CATEGORY_OVERRIDES = {
    "CapFlower": PlacementCategory.GAMEPLAY,
    "CheckpointFlag": PlacementCategory.GAMEPLAY,
    "DokanMazeDirector": PlacementCategory.HELPERS,
    "PaulineAtCeremony": PlacementCategory.CHARACTERS,
    "ReactionObject": PlacementCategory.ENVIRONMENT,
    "RouteGuideArrow": PlacementCategory.HELPERS,
    "RouteGuideRailGround": PlacementCategory.HELPERS,
    "MarchingCubeBlockParts": PlacementCategory.ENVIRONMENT,
    "OceanWave": PlacementCategory.ENVIRONMENT,
    "SandWorldHomePyramidKai000": PlacementCategory.ENVIRONMENT,
    "SessionMusicianBass": PlacementCategory.CHARACTERS,
    "SessionMusicianDrum": PlacementCategory.CHARACTERS,
    "SessionMusicianGuitar": PlacementCategory.CHARACTERS,
    "ShineTowerRocket": PlacementCategory.ENVIRONMENT,
    "WorldWarpHole": PlacementCategory.GAMEPLAY,
}

RESOLVED_MODEL_CATEGORY_MAP = {
    "ENVIRONMENT": PlacementCategory.ENVIRONMENT,
    "CHARACTERS": PlacementCategory.CHARACTERS,
    "ENEMIES": PlacementCategory.CHARACTERS,
    "GAMEPLAY": PlacementCategory.GAMEPLAY,
    "COLLECTIBLES": PlacementCategory.COLLECTIBLES,
    "EFFECTS": PlacementCategory.EFFECTS,
    "LIGHTING": PlacementCategory.HELPERS,
    "AUDIO": PlacementCategory.AUDIO,
    "TECHNICAL": PlacementCategory.HELPERS,
}

IMPORT_CATEGORY_FILTERS = (
    ("ENVIRONMENT", "Environment", "include_environment"),
    ("CHARACTERS", "Characters", "include_characters"),
    ("GAMEPLAY", "Gameplay", "include_gameplay"),
    ("COLLECTIBLES", "Collectibles", "include_collectibles"),
    ("EFFECTS", "Effects", "include_effects"),
    ("AUDIO", "Audio", "include_audio"),
    ("TECHNICAL", "Technical", "include_technical"),
    ("UNCLASSIFIED", "Unclassified", "include_unclassified"),
)

_IMPORT_FILTER_BY_CATEGORY = {
    PlacementCategory.ENVIRONMENT: "ENVIRONMENT",
    PlacementCategory.CHARACTERS: "CHARACTERS",
    PlacementCategory.GAMEPLAY: "GAMEPLAY",
    PlacementCategory.COLLECTIBLES: "COLLECTIBLES",
    PlacementCategory.EFFECTS: "EFFECTS",
    PlacementCategory.AUDIO: "AUDIO",
    PlacementCategory.AREAS: "TECHNICAL",
    PlacementCategory.CAMERAS: "TECHNICAL",
    PlacementCategory.HELPERS: "TECHNICAL",
    PlacementCategory.DEBUG: "TECHNICAL",
    PlacementCategory.UNKNOWN_MODEL: "UNCLASSIFIED",
    PlacementCategory.UNKNOWN_MODELLESS: "UNCLASSIFIED",
}

_LOCAL_LIGHT_UNIT_NAMES = {
    "PrePassPointLight",
    "PrePassSpotLight",
    "PrePassLineLight",
}


def import_filter_group(classified: "ClassifiedPlacement") -> str:
    if classified.placement.unit_config_name in _LOCAL_LIGHT_UNIT_NAMES:
        return "TECHNICAL"

    return _IMPORT_FILTER_BY_CATEGORY[classified.category]

CHARACTER_PREFIXES = (
    "AmiiboNpc",
    "Bird",
    "Breeda",
    "Enemy",
    "Killer",
    "Koopa",
    "Kuribo",
    "Npc",
    "PlayerActor",
    "Pukupuku",
    "TRex",
    "Wanwan",
    "Yoshi",
)

PEACH_CHARACTER_PREFIX = "Peach"
PEACH_STAGE_PREFIX = "PeachWorld"

COLLECTIBLE_PREFIXES = (
    "Coin",
    "Shine",
)

GAMEPLAY_PREFIXES = (
    "Dokan",
)

EFFECT_PREFIXES = (
    "Effect",
    "PadRumble",
    "Ripple",
    "SePlay",
    "Vibration",
)

HELPER_TOKENS = (
    "AllDeadWatcher",
    "DemoRegister",
    "Generator",
    "GoalMark",
    "GroupView",
    "Information",
    "Rail",
    "RouteGuide",
    "StageSwitch",
    "StartObj",
    "Switch",
    "TalkMessage",
    "TransparentWall",
    "ViewCtrl",
    "Watcher",
)

ENVIRONMENT_TOKENS = (
    "BreakParts",
    "Fence",
    "Ground",
    "MapParts",
    "Plant",
    "Rock",
    "Sky",
    "Stone",
    "Tree",
)


@dataclass(slots=True, frozen=True)
class ClassifiedPlacement:
    placement: StagePlacement
    category: PlacementCategory
    resource: ObjectResource
    model_expectation: ModelExpectationAssessment


def _candidate_names(placement: StagePlacement) -> tuple[str, ...]:
    parameter_name = str(
        placement.unit_config.get("ParameterConfigName") or ""
    )
    return tuple(
        name
        for name in (
            placement.unit_config_name,
            placement.model_name or "",
            parameter_name,
        )
        if name
    )


def _starts_with_any(names: tuple[str, ...], prefixes: tuple[str, ...]) -> bool:
    return any(
        name.casefold().startswith(prefix.casefold())
        for name in names
        for prefix in prefixes
    )


def _contains_any(names: tuple[str, ...], tokens: tuple[str, ...]) -> bool:
    return any(
        token.casefold() in name.casefold()
        for name in names
        for token in tokens
    )


def classify_placement(
    placement: StagePlacement,
    resource: ObjectResource,
) -> PlacementCategory:
    override = CATEGORY_OVERRIDES.get(placement.unit_config_name)

    if override is not None:
        return override

    registry_category = resolved_model_category(resource.archive_path)

    if registry_category == "UNCLASSIFIED":
        return (
            PlacementCategory.UNKNOWN_MODEL
            if resource.has_model
            else PlacementCategory.UNKNOWN_MODELLESS
        )

    resolved_category = RESOLVED_MODEL_CATEGORY_MAP.get(
        registry_category or ""
    )

    if resolved_category is not None:
        return resolved_category

    names = _candidate_names(placement)

    if placement.stage_layer == "Sound":
        return PlacementCategory.AUDIO

    if placement.category == "DebugList":
        return PlacementCategory.DEBUG

    if _starts_with_any(names, COLLECTIBLE_PREFIXES):
        return PlacementCategory.COLLECTIBLES

    if _starts_with_any(names, GAMEPLAY_PREFIXES):
        return PlacementCategory.GAMEPLAY

    if placement.category == "AreaList":
        return PlacementCategory.AREAS

    if (
        placement.category == "ScenarioStartCameraList"
        or _contains_any(names, ("Camera",))
    ):
        return PlacementCategory.CAMERAS

    if _starts_with_any(names, CHARACTER_PREFIXES) or any(
        name.casefold().startswith(PEACH_CHARACTER_PREFIX.casefold())
        and not name.casefold().startswith(PEACH_STAGE_PREFIX.casefold())
        for name in names
    ):
        return PlacementCategory.CHARACTERS

    if placement.category == "CheckPointList":
        return PlacementCategory.GAMEPLAY

    if placement.category == "SkyList":
        return PlacementCategory.ENVIRONMENT

    if (
        placement.category
        in {
            "DemoObjList",
            "PlayerList",
            "PlayerStartInfoList",
            "SceneWatchObjList",
        }
        or _contains_any(names, HELPER_TOKENS)
    ):
        return PlacementCategory.HELPERS

    if resource.has_model and _contains_any(names, ENVIRONMENT_TOKENS):
        return PlacementCategory.ENVIRONMENT

    if _starts_with_any(names, EFFECT_PREFIXES):
        return PlacementCategory.EFFECTS

    if resource.has_model:
        return PlacementCategory.UNKNOWN_MODEL

    return PlacementCategory.UNKNOWN_MODELLESS


@timed("object_data_resolution")
def classify_stage_scenario(
    stage_scenario: StageScenario,
    object_data: ObjectDataIndex,
    *,
    include_suggestions: bool = False,
) -> list[ClassifiedPlacement]:
    placements = stage_scenario.placements
    object_data.learn_stage_placements(placements)
    classified: list[ClassifiedPlacement] = []

    for placement in placements:
        resource = object_data.resolve(
            placement,
            include_suggestions=include_suggestions,
        )
        category = classify_placement(placement, resource)
        parameter_name = str(
            placement.unit_config.get("ParameterConfigName") or ""
        ).strip()
        classified.append(
            ClassifiedPlacement(
                placement=placement,
                category=category,
                resource=resource,
                model_expectation=assess_model_expectation(
                    unit_config_name=placement.unit_config_name,
                    parameter_config_name=parameter_name,
                    explicit_model_name=placement.model_name or "",
                    stage_layers=(placement.stage_layer,),
                    placement_categories=(placement.category,),
                    import_category=category.value,
                    resource=resource,
                ),
            )
        )

    return classified

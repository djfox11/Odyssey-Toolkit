from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


class ModelExpectation(str, Enum):
    CONFIRMED_NON_MESH = "CONFIRMED_NON_MESH"
    RUNTIME_VISUAL = "RUNTIME_VISUAL"
    MODEL_EXPECTED = "MODEL_EXPECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True, frozen=True)
class ModelExpectationAssessment:
    expectation: ModelExpectation
    confidence: str
    reasons: tuple[str, ...]


_CONFIRMED_NON_MESH_CATEGORIES = frozenset(
    {
        "AreaList",
        "CheckPointList",
        "DebugList",
        "PlayerList",
        "PlayerStartInfoList",
        "RaceList",
        "ScenarioStartCameraList",
        "ZoneList",
    }
)
_CONFIRMED_NON_MESH_PREFIXES = (
    "MapIcon",
    "PadRumble",
    "Patrol",
    "PlayerStart",
    "PrePassLineLight",
    "PrePassPointLight",
    "PrePassSpotLight",
    "ProjectRaceCheckPoint",
    "RaceCheckPoint",
    "Rail",
    "SePlay",
    "StageSwitch",
    "Vibration",
)
_CONFIRMED_NON_MESH_TOKENS = (
    "BackHide",
    "Camera",
    "ClippingExpander",
    "Collision",
    "CoursePoint",
    "DebugWarpPoint",
    "DemoRegister",
    "Destination",
    "Director",
    "Dummy",
    "EyePoint",
    "ForceScroll",
    "GoalMark",
    "HintObj",
    "HintPos",
    "HomeAngleObj",
    "Initializer",
    "LookPos",
    "MeasureUnit",
    "MoveLimit",
    "MovePoint",
    "MoveRange",
    "Patrol",
    "PlayerResetPos",
    "PostureObj",
    "RailKeeper",
    "ReferencePosition",
    "ReflectGuide",
    "Requester",
    "RevivalPoint",
    "RotationCenter",
    "RouteKey",
    "ShadowMask",
    "ShopMark",
    "SnowVolume",
    "StartPoint",
    "StartPos",
    "Switch",
    "TalkMessageInfoPoint",
    "TalkPoint",
    "TargetPos",
    "TransparentWall",
    "Watcher",
    "WatchPoint",
    "Zone",
)
_CONFIRMED_NON_MESH_SUFFIXES = ("Ctrl",)
_RUNTIME_VISUAL_TOKENS = (
    "Barrier",
    "Coin2D",
    "CoinBlow",
    "CoinLead",
    "Collect2D",
    "Effect",
    "Emitter",
    "FixMapParts",
    "Generator",
    "Group",
    "GrowPlant",
    "Hole",
    "Holder",
    "MapParts",
    "OceanWave",
    "Particle",
    "Placement",
    "PlayerActor",
    "Ripple",
    "Spawner",
)
_VISUAL_CATEGORY_NAMES = frozenset(
    {
        "CHARACTERS",
        "COLLECTIBLES",
        "ENVIRONMENT",
        "UNKNOWN_MODEL",
    }
)
_VISUAL_NAME_PREFIXES = (
    "AmiiboNpc",
    "Bird",
    "Breeda",
    "Coin",
    "Enemy",
    "Killer",
    "Koopa",
    "Kuribo",
    "Npc",
    "Peach",
    "PlayerActor",
    "Pukupuku",
    "Shine",
    "TRex",
    "Wanwan",
    "Yoshi",
)
_VISUAL_NAME_TOKENS = (
    "BreakParts",
    "Building",
    "Fence",
    "Stone",
    "Tree",
)


def _text_values(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in values if str(value).strip())


def _contains_runtime_token(names: tuple[str, ...]) -> str | None:
    for name in names:
        folded = name.casefold()

        for token in _RUNTIME_VISUAL_TOKENS:
            if token.casefold() in folded:
                return token

    return None


def _visual_name_reason(names: tuple[str, ...]) -> str | None:
    for name in names:
        folded = name.casefold()

        for prefix in _VISUAL_NAME_PREFIXES:
            if folded.startswith(prefix.casefold()):
                return f"visual name prefix {prefix}"

        for token in _VISUAL_NAME_TOKENS:
            if token.casefold() in folded:
                return f"visual name token {token}"

    return None


def assess_model_expectation(
    *,
    unit_config_name: str,
    parameter_config_name: str = "",
    explicit_model_name: str = "",
    stage_layers: Iterable[str] = (),
    placement_categories: Iterable[str] = (),
    import_category: str = "",
    resource: Any | None = None,
    archive_metadata: Any | None = None,
) -> ModelExpectationAssessment:
    """Classify static-model expectations without changing resolution.

    This deliberately uses a small set of high-confidence signals. Unknown is
    preferable to incorrectly labelling a runtime actor as genuinely invisible.
    """

    names = _text_values(
        (unit_config_name, parameter_config_name, explicit_model_name)
    )
    layers = frozenset(_text_values(stage_layers))
    categories = frozenset(_text_values(placement_categories))
    reasons: list[str] = []

    if resource is not None and bool(getattr(resource, "has_model", False)):
        return ModelExpectationAssessment(
            ModelExpectation.MODEL_EXPECTED,
            "HIGH",
            ("resolved ObjectData archive contains BFRES",),
        )

    if explicit_model_name:
        return ModelExpectationAssessment(
            ModelExpectation.MODEL_EXPECTED,
            "HIGH",
            (f"StageData declares ModelName {explicit_model_name}",),
        )

    metadata = archive_metadata

    if metadata is None and resource is not None:
        metadata = resource

    init_models = tuple(getattr(metadata, "init_model_references", ()) or ())
    subactors = tuple(getattr(metadata, "sub_actor_model_names", ()) or ())
    byml_files = tuple(getattr(metadata, "byml_files", ()) or ())

    if init_models or subactors:
        if init_models:
            reasons.append(
                "ObjectData InitModel references " + ", ".join(init_models)
            )

        if subactors:
            reasons.append(
                "ObjectData InitSubActor references " + ", ".join(subactors)
            )

        return ModelExpectationAssessment(
            ModelExpectation.RUNTIME_VISUAL,
            "HIGH",
            tuple(reasons),
        )

    runtime_byml = next(
        (
            name
            for name in byml_files
            if (
                "initeffect" in name.casefold()
                or "initactor2d3d" in name.casefold()
                or name.casefold() == "pattern.byml"
                or "battlephase" in name.casefold()
            )
        ),
        None,
    )

    if runtime_byml is not None:
        return ModelExpectationAssessment(
            ModelExpectation.RUNTIME_VISUAL,
            "HIGH",
            (f"ObjectData runtime visual configuration {runtime_byml}",),
        )

    if layers == {"Sound"}:
        return ModelExpectationAssessment(
            ModelExpectation.CONFIRMED_NON_MESH,
            "HIGH",
            ("observed only in the Sound StageData layer",),
        )

    if categories and categories <= _CONFIRMED_NON_MESH_CATEGORIES:
        return ModelExpectationAssessment(
            ModelExpectation.CONFIRMED_NON_MESH,
            "HIGH",
            (
                "observed only in non-rendered placement lists: "
                + ", ".join(sorted(categories, key=str.casefold)),
            ),
        )

    for name in names:
        for prefix in _CONFIRMED_NON_MESH_PREFIXES:
            if name.casefold().startswith(prefix.casefold()):
                return ModelExpectationAssessment(
                    ModelExpectation.CONFIRMED_NON_MESH,
                    "HIGH",
                    (f"non-rendered actor prefix {prefix}",),
                )

        for token in _CONFIRMED_NON_MESH_TOKENS:
            if token.casefold() in name.casefold():
                return ModelExpectationAssessment(
                    ModelExpectation.CONFIRMED_NON_MESH,
                    "HIGH",
                    (f"non-rendered helper/controller token {token}",),
                )

        for suffix in _CONFIRMED_NON_MESH_SUFFIXES:
            if name.casefold().endswith(suffix.casefold()):
                return ModelExpectationAssessment(
                    ModelExpectation.CONFIRMED_NON_MESH,
                    "HIGH",
                    (f"non-rendered helper/controller suffix {suffix}",),
                )

    runtime_token = _contains_runtime_token(names)

    if runtime_token is not None:
        return ModelExpectationAssessment(
            ModelExpectation.RUNTIME_VISUAL,
            "MEDIUM",
            (f"runtime/composed actor token {runtime_token}",),
        )

    if import_category == "EFFECTS":
        return ModelExpectationAssessment(
            ModelExpectation.RUNTIME_VISUAL,
            "MEDIUM",
            ("placement is classified as an effect",),
        )

    if import_category in _VISUAL_CATEGORY_NAMES:
        return ModelExpectationAssessment(
            ModelExpectation.MODEL_EXPECTED,
            "MEDIUM",
            (f"visual placement category {import_category}",),
        )

    visual_reason = _visual_name_reason(names)

    if visual_reason is not None:
        return ModelExpectationAssessment(
            ModelExpectation.MODEL_EXPECTED,
            "MEDIUM",
            (visual_reason,),
        )

    if "SkyList" in categories:
        return ModelExpectationAssessment(
            ModelExpectation.MODEL_EXPECTED,
            "MEDIUM",
            ("observed in the visual SkyList",),
        )

    return ModelExpectationAssessment(
        ModelExpectation.UNKNOWN,
        "LOW",
        ("no reliable static-model or non-mesh evidence",),
    )

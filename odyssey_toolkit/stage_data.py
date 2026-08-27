from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import oead

from .performance import timed
from .world_list import extract_file, read_szs


STAGE_LAYERS = ("Map", "Design", "Sound")
GAME_UNIT_SCALE = 0.01
GameQuaternion = tuple[float, float, float, float]
_BYML_INTEGER_TYPES = (oead.S32, oead.U32, oead.S64, oead.U64)
_BYML_FLOAT_TYPES = (oead.F32, oead.F64)


@dataclass(slots=True, frozen=True)
class Vector3:
    x: float
    y: float
    z: float


@dataclass(slots=True, frozen=True)
class BlenderTransform:
    location: tuple[float, float, float]
    rotation_quaternion: tuple[float, float, float, float]
    scale: tuple[float, float, float]


@dataclass(slots=True, frozen=True)
class GameTransform:
    translate: Vector3
    rotation_quaternion: GameQuaternion
    scale: Vector3


@dataclass(slots=True)
class StagePlacement:
    identifier: str
    unit_config_name: str
    model_name: str | None
    category: str
    stage_layer: str
    placement_file_name: str
    layer_config_name: str
    translate: Vector3
    rotate: Vector3
    scale: Vector3
    rotation_quaternion: GameQuaternion
    links: dict[str, tuple[str, ...]]
    unit_config: dict[str, Any]
    is_link_destination: bool
    is_root: bool
    raw: dict[str, Any]
    source_stage_name: str = ""
    zone_path: tuple[str, ...] = ()


@dataclass(slots=True)
class StageLayer:
    name: str
    archive_path: Path
    byml_name: str
    scenario_data: dict[str, Any]
    placements: list[StagePlacement]


@dataclass(slots=True)
class StageScenario:
    stage_name: str
    scenario_number: int
    layers: dict[str, StageLayer]
    missing_layers: tuple[str, ...]
    zone_placements: list[StagePlacement] = field(default_factory=list)
    expanded_zones: tuple[str, ...] = ()

    @property
    def placements(self) -> list[StagePlacement]:
        placements = [
            placement
            for layer in self.layers.values()
            for placement in layer.placements
            if not is_zone_reference(placement)
        ] + self.zone_placements
        return _select_scenario_placements(
            placements,
            self.stage_name,
            self.scenario_number,
        )


_SCENARIO_KEY_MOVE_STATES = {
    (
        "SandWorldHomeStage",
        "SandWorldHomePyramidKai000",
    ): 3,
}


def _select_scenario_placements(
    placements: list[StagePlacement],
    stage_name: str,
    scenario_number: int,
) -> list[StagePlacement]:
    by_identifier = {
        placement.identifier: placement
        for placement in placements
    }
    key_move_targets = {
        identifier
        for placement in placements
        for identifier in placement.links.get("KeyMoveNext", ())
    }
    before_clear_targets = {
        identifier
        for placement in placements
        if placement.unit_config_name == "ShineTowerRocket"
        for identifier in placement.links.get("BeforeClear", ())
    }
    selected: list[StagePlacement] = []

    for placement in placements:
        if placement.unit_config_name == "ShineTowerRocket":
            if (
                scenario_number == 1
                and placement.is_root
                and placement.links.get("BeforeClear")
            ):
                continue

            if (
                scenario_number != 1
                and placement.identifier in before_clear_targets
            ):
                continue

        if (
            not placement.is_root
            and placement.identifier in key_move_targets
        ):
            continue

        raised_scenario = _SCENARIO_KEY_MOVE_STATES.get(
            (placement.source_stage_name, placement.unit_config_name)
        )

        if (
            placement.source_stage_name == stage_name
            and raised_scenario is not None
            and scenario_number >= raised_scenario
        ):
            target_ids = placement.links.get("KeyMoveNext", ())
            target = (
                by_identifier.get(target_ids[0])
                if target_ids
                else None
            )

            if target is not None:
                placement = replace(
                    placement,
                    translate=target.translate,
                    rotate=target.rotate,
                    scale=target.scale,
                    rotation_quaternion=target.rotation_quaternion,
                )

        selected.append(placement)

    return selected


def _is_byml_mapping(value: Any) -> bool:
    return isinstance(value, (dict, oead.byml.Hash))


def _is_byml_sequence(value: Any) -> bool:
    return isinstance(value, (list, oead.byml.Array))


def _mapping_get(
    value: dict[str, Any] | oead.byml.Hash,
    key: str,
    default: Any = None,
) -> Any:
    if isinstance(value, oead.byml.Hash):
        return value[key] if key in value else default

    return value.get(key, default)


def _convert_byml_value(value: Any) -> Any:
    value_type = type(value)

    if value_type is oead.byml.Array:
        return [_convert_byml_value(item) for item in value]

    if value_type is oead.byml.Hash:
        return {
            str(key): _convert_byml_value(item)
            for key, item in value.items()
        }

    if value_type in _BYML_INTEGER_TYPES:
        return int(value)

    if value_type in _BYML_FLOAT_TYPES:
        return float(value)

    if value_type is oead.Bytes:
        return bytes(value).hex()

    return value


def _convert_placement_record(
    record: dict[str, Any] | oead.byml.Hash,
) -> dict[str, Any]:
    if isinstance(record, dict):
        return record

    result = {
        str(key): _convert_byml_value(value)
        for key, value in record.items()
        if str(key) != "Links"
    }
    links = _mapping_get(record, "Links")

    if _is_byml_mapping(links):
        result["Links"] = {
            str(link_name): [
                {
                    "Id": _convert_byml_value(
                        _mapping_get(target, "Id", "")
                    )
                }
                for target in targets
                if _is_byml_mapping(target)
            ]
            for link_name, targets in links.items()
            if _is_byml_sequence(targets)
        }

    return result

def _vector3(value: Any, default: Vector3) -> Vector3:
    if not _is_byml_mapping(value):
        return default

    try:
        return Vector3(
            float(_mapping_get(value, "X", default.x)),
            float(_mapping_get(value, "Y", default.y)),
            float(_mapping_get(value, "Z", default.z)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid vector value: {value!r}") from exc

def _optional_string(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _link_identifiers(
    record: dict[str, Any] | oead.byml.Hash,
) -> dict[str, tuple[str, ...]]:
    links = _mapping_get(record, "Links")

    if not _is_byml_mapping(links):
        return {}

    result: dict[str, tuple[str, ...]] = {}

    for link_name, targets in links.items():
        if not _is_byml_sequence(targets):
            continue

        identifiers = []

        for target in targets:
            if not _is_byml_mapping(target):
                continue

            identifier = str(_mapping_get(target, "Id", "")).strip()

            if identifier:
                identifiers.append(identifier)

        if identifiers:
            result[str(link_name)] = tuple(identifiers)

    return result

def _normalise_quaternion(value: GameQuaternion) -> GameQuaternion:
    length = math.sqrt(sum(component * component for component in value))

    if length == 0.0:
        return (1.0, 0.0, 0.0, 0.0)

    return tuple(component / length for component in value)  # type: ignore[return-value]


def _game_rotation_quaternion(rotation_degrees: Vector3) -> GameQuaternion:
    half_x = math.radians(rotation_degrees.x) * 0.5
    half_y = math.radians(rotation_degrees.y) * 0.5
    half_z = math.radians(rotation_degrees.z) * 0.5
    sin_x, cos_x = math.sin(half_x), math.cos(half_x)
    sin_y, cos_y = math.sin(half_y), math.cos(half_y)
    sin_z, cos_z = math.sin(half_z), math.cos(half_z)

    return _normalise_quaternion(
        (
            cos_x * cos_y * cos_z + sin_x * sin_y * sin_z,
            sin_x * cos_y * cos_z - cos_x * sin_y * sin_z,
            cos_x * sin_y * cos_z + sin_x * cos_y * sin_z,
            cos_x * cos_y * sin_z - sin_x * sin_y * cos_z,
        )
    )


def _multiply_quaternions(
    left: GameQuaternion,
    right: GameQuaternion,
) -> GameQuaternion:
    left_w, left_x, left_y, left_z = left
    right_w, right_x, right_y, right_z = right
    return _normalise_quaternion(
        (
            left_w * right_w
            - left_x * right_x
            - left_y * right_y
            - left_z * right_z,
            left_w * right_x
            + left_x * right_w
            + left_y * right_z
            - left_z * right_y,
            left_w * right_y
            - left_x * right_z
            + left_y * right_w
            + left_z * right_x,
            left_w * right_z
            + left_x * right_y
            - left_y * right_x
            + left_z * right_w,
        )
    )


def _rotate_vector(
    rotation: GameQuaternion,
    value: Vector3,
) -> Vector3:
    w, x, y, z = rotation
    dot = x * value.x + y * value.y + z * value.z
    cross_x = y * value.z - z * value.y
    cross_y = z * value.x - x * value.z
    cross_z = x * value.y - y * value.x
    vector_length = x * x + y * y + z * z
    scalar = w * w - vector_length

    return Vector3(
        2.0 * dot * x + scalar * value.x + 2.0 * w * cross_x,
        2.0 * dot * y + scalar * value.y + 2.0 * w * cross_y,
        2.0 * dot * z + scalar * value.z + 2.0 * w * cross_z,
    )


def _quaternion_to_euler(rotation: GameQuaternion) -> Vector3:
    w, x, y, z = rotation
    sin_x = 2.0 * (w * x + y * z)
    cos_x = 1.0 - 2.0 * (x * x + y * y)
    sin_y = 2.0 * (w * y - z * x)
    sin_z = 2.0 * (w * z + x * y)
    cos_z = 1.0 - 2.0 * (y * y + z * z)

    return Vector3(
        math.degrees(math.atan2(sin_x, cos_x)),
        math.degrees(
            math.copysign(math.pi * 0.5, sin_y)
            if abs(sin_y) >= 1.0
            else math.asin(sin_y)
        ),
        math.degrees(math.atan2(sin_z, cos_z)),
    )


def _placement_transform(placement: StagePlacement) -> GameTransform:
    return GameTransform(
        translate=placement.translate,
        rotation_quaternion=placement.rotation_quaternion,
        scale=placement.scale,
    )


def _compose_game_transforms(
    parent: GameTransform,
    child: GameTransform,
) -> GameTransform:
    scaled_translate = Vector3(
        child.translate.x * parent.scale.x,
        child.translate.y * parent.scale.y,
        child.translate.z * parent.scale.z,
    )
    rotated_translate = _rotate_vector(
        parent.rotation_quaternion,
        scaled_translate,
    )
    return GameTransform(
        translate=Vector3(
            parent.translate.x + rotated_translate.x,
            parent.translate.y + rotated_translate.y,
            parent.translate.z + rotated_translate.z,
        ),
        rotation_quaternion=_multiply_quaternions(
            parent.rotation_quaternion,
            child.rotation_quaternion,
        ),
        scale=Vector3(
            parent.scale.x * child.scale.x,
            parent.scale.y * child.scale.y,
            parent.scale.z * child.scale.z,
        ),
    )


def _qualified_identifier(prefix: str, identifier: str) -> str:
    return f"{prefix}/{identifier}" if prefix else identifier


def _compose_zone_placement(
    placement: StagePlacement,
    parent_transform: GameTransform,
    identifier_prefix: str,
    zone_path: tuple[str, ...],
) -> StagePlacement:
    transform = _compose_game_transforms(
        parent_transform,
        _placement_transform(placement),
    )
    return replace(
        placement,
        identifier=_qualified_identifier(
            identifier_prefix,
            placement.identifier,
        ),
        translate=transform.translate,
        rotate=_quaternion_to_euler(transform.rotation_quaternion),
        scale=transform.scale,
        rotation_quaternion=transform.rotation_quaternion,
        links={
            name: tuple(
                _qualified_identifier(identifier_prefix, identifier)
                for identifier in identifiers
            )
            for name, identifiers in placement.links.items()
        },
        zone_path=zone_path,
    )


def is_zone_reference(placement: StagePlacement) -> bool:
    return placement.category.casefold() == "zonelist"


def _make_placement(
    record: dict[str, Any] | oead.byml.Hash,
    fallback_category: str,
    stage_layer: str,
    *,
    is_root: bool,
    source_stage_name: str = "",
) -> StagePlacement | None:
    identifier = str(_mapping_get(record, "Id", "")).strip()
    unit_config_name = str(
        _mapping_get(record, "UnitConfigName", "")
    ).strip()

    if not identifier or not unit_config_name:
        return None

    converted_record = _convert_placement_record(record)
    unit_config_value = converted_record.get("UnitConfig")
    unit_config = (
        dict(unit_config_value)
        if isinstance(unit_config_value, dict)
        else {}
    )
    category = str(unit_config.get("GenerateCategory") or fallback_category)
    zero = Vector3(0.0, 0.0, 0.0)
    translate = _vector3(converted_record.get("Translate"), zero)
    rotate = _vector3(converted_record.get("Rotate"), zero)
    scale = _vector3(
        converted_record.get("Scale"),
        Vector3(1.0, 1.0, 1.0),
    )

    return StagePlacement(
        identifier=identifier,
        unit_config_name=unit_config_name,
        model_name=_optional_string(converted_record.get("ModelName")),
        category=category,
        stage_layer=stage_layer,
        placement_file_name=str(
            converted_record.get("PlacementFileName", "")
        ),
        layer_config_name=str(
            converted_record.get("LayerConfigName", "")
        ),
        translate=translate,
        rotate=rotate,
        scale=scale,
        rotation_quaternion=_game_rotation_quaternion(rotate),
        links=_link_identifiers(converted_record),
        unit_config=unit_config,
        is_link_destination=bool(
            converted_record.get("IsLinkDest", False)
        ),
        is_root=is_root,
        raw=converted_record,
        source_stage_name=source_stage_name,
    )


@timed("placement_collection")
def _collect_placements(
    scenario_data: dict[str, Any] | oead.byml.Hash,
    stage_layer: str,
    source_stage_name: str = "",
) -> tuple[list[StagePlacement], dict[str, Any]]:
    roots: list[tuple[dict[str, Any] | oead.byml.Hash, str]] = []
    converted_scenario: dict[str, Any] = {}

    for category, values in scenario_data.items():
        category_name = str(category)

        if not _is_byml_sequence(values):
            converted_scenario[category_name] = _convert_byml_value(values)
            continue

        converted_scenario[category_name] = []

        for value in values:
            if _is_byml_mapping(value):
                roots.append((value, category_name))
            else:
                converted_scenario[category_name].append(
                    _convert_byml_value(value)
                )

    placements: dict[str, StagePlacement] = {}

    for record, category in roots:
        placement = _make_placement(
            record,
            category,
            stage_layer,
            is_root=True,
            source_stage_name=source_stage_name,
        )

        if placement is None:
            converted_scenario[category].append(
                _convert_placement_record(record)
            )
            continue

        placements.setdefault(placement.identifier, placement)
        converted_scenario[category].append(placement.raw)

    def visit_links(
        record: dict[str, Any] | oead.byml.Hash,
        fallback_category: str,
    ) -> None:
        links = _mapping_get(record, "Links")

        if not _is_byml_mapping(links):
            return

        for targets in links.values():
            if not _is_byml_sequence(targets):
                continue

            for target in targets:
                if not _is_byml_mapping(target):
                    continue

                identifier = str(_mapping_get(target, "Id", "")).strip()

                if not identifier or identifier in placements:
                    continue

                placement = _make_placement(
                    target,
                    fallback_category,
                    stage_layer,
                    is_root=False,
                    source_stage_name=source_stage_name,
                )

                if placement is None:
                    continue

                placements[placement.identifier] = placement
                visit_links(target, placement.category)

    for record, category in roots:
        visit_links(record, category)

    return list(placements.values()), converted_scenario


def collect_placements(
    scenario_data: dict[str, Any] | oead.byml.Hash,
    stage_layer: str,
    source_stage_name: str = "",
) -> list[StagePlacement]:
    placements, _ = _collect_placements(
        scenario_data,
        stage_layer,
        source_stage_name,
    )
    return placements

@timed("stage_data_byml_parse")
def _parse_stage_byml(byml_data: bytes) -> Any:
    return oead.byml.from_binary(byml_data)


def _read_stage_layer_document(
    romfs_path: Path,
    stage_name: str,
    stage_layer: str,
) -> tuple[Path, str, Any]:
    if stage_layer not in STAGE_LAYERS:
        raise ValueError(f"Unsupported stage layer: {stage_layer}")

    archive_name = f"{stage_name}{stage_layer}"
    archive_path = romfs_path / "StageData" / f"{archive_name}.szs"
    archive = read_szs(archive_path)
    byml_name = f"{archive_name}.byml"
    byml_data = extract_file(archive, byml_name)

    if byml_data[:2] not in {b"BY", b"YB"}:
        raise ValueError(
            f"{byml_name} does not appear to be BYML. "
            f"Found magic {byml_data[:2]!r}."
        )

    try:
        document = _parse_stage_byml(byml_data)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse {byml_name}: {exc}") from exc

    return archive_path, byml_name, document


def read_stage_scenario_numbers(
    romfs_path: Path,
    stage_name: str,
) -> tuple[int, ...]:
    _, byml_name, document = _read_stage_layer_document(
        romfs_path,
        stage_name,
        "Map",
    )

    if document is None:
        return (1,)

    if not _is_byml_sequence(document):
        raise TypeError(
            f"Expected {byml_name}'s root node to be an array or null, "
            f"but got {type(document).__name__}."
        )

    scenario_numbers = tuple(
        index
        for index, scenario_data in enumerate(document, start=1)
        if _is_byml_mapping(scenario_data)
    )

    if not scenario_numbers:
        raise RuntimeError(
            f"{byml_name} contained no importable scenario dictionaries."
        )

    return scenario_numbers


def read_stage_layer(
    romfs_path: Path,
    stage_name: str,
    scenario_number: int,
    stage_layer: str,
) -> StageLayer:
    if scenario_number < 1:
        raise ValueError("Scenario numbers start at 1.")

    archive_path, byml_name, document = _read_stage_layer_document(
        romfs_path,
        stage_name,
        stage_layer,
    )
    scenario_index = scenario_number - 1

    if document is None:
        scenario_data: Any = {}
    elif _is_byml_sequence(document):
        if scenario_index >= len(document):
            raise IndexError(
                f"{byml_name} has {len(document)} scenario entries; "
                f"scenario {scenario_number} is unavailable."
            )

        scenario_data = document[scenario_index]
    else:
        raise TypeError(
            f"Expected {byml_name}'s root node to be an array or null, "
            f"but got {type(document).__name__}."
        )

    if not _is_byml_mapping(scenario_data):
        raise TypeError(
            f"{byml_name} scenario {scenario_number} should be a dictionary, "
            f"but got {type(scenario_data).__name__}."
        )

    placements, converted_scenario = _collect_placements(
        scenario_data,
        stage_layer,
        source_stage_name=stage_name,
    )

    return StageLayer(
        name=stage_layer,
        archive_path=archive_path,
        byml_name=byml_name,
        scenario_data=converted_scenario,
        placements=placements,
    )


def _sky_resource_candidates(name: str) -> tuple[str, ...]:
    candidates = []

    if name.startswith("Cloud"):
        suffix = name[5:]
        candidates.append(f"Sky{suffix}")

        if suffix.endswith("Under"):
            candidates.append(f"Sky{suffix[:-len('Under')]}")

    if name.startswith("Distant"):
        suffix = name[7:]

        if suffix.endswith("Sun"):
            suffix = suffix[:-3]

        candidates.append(f"Sky{suffix}")

    return tuple(dict.fromkeys(candidates))


def _resolve_sky_resource_name(
    romfs_path: Path,
    candidate: str,
) -> str | None:
    if candidate.startswith("SkyWorld"):
        return None

    object_data = romfs_path / "ObjectData"
    archive_path = object_data / f"{candidate}.szs"

    if archive_path.is_file():
        return candidate

    prefix_matches = tuple(
        sorted(
            path.stem
            for path in object_data.glob(f"{candidate}*.szs")
            if path.is_file()
        )
    )

    if len(prefix_matches) == 1:
        return prefix_matches[0]

    return None


def _graphics_preset_sky_resources(
    romfs_path: Path,
    stage_name: str,
    scenario_number: int,
) -> tuple[str, ...]:
    # Imported lazily because stage_lighting imports StageData transform types.
    from .stage_lighting import read_stage_graphics_sky_name

    try:
        sky_name = read_stage_graphics_sky_name(
            romfs_path,
            stage_name,
            scenario_number,
        )
    except (FileNotFoundError, KeyError, RuntimeError, TypeError, ValueError):
        # Graphics metadata must never make an otherwise valid stage fail to
        # import. The existing home/parent sky fallback remains available.
        return ()

    if not sky_name or sky_name.startswith("SkyWorld"):
        return ()

    archive_path = romfs_path / "ObjectData" / f"{sky_name}.szs"

    if not archive_path.is_file():
        return ()

    return (sky_name,)


def _sky_resources_for_layers(
    romfs_path: Path,
    layers: dict[str, StageLayer],
) -> tuple[str, ...]:
    map_layer = layers.get("Map")

    if map_layer is None:
        return ()

    resources = []

    for placement in map_layer.placements:
        if placement.category != "SkyList":
            continue

        name = placement.unit_config_name
        candidates = []

        if name.startswith("Sky"):
            candidates.append(name)

        candidates.extend(_sky_resource_candidates(name))

        for candidate in candidates:
            resolved = _resolve_sky_resource_name(
                romfs_path,
                candidate,
            )

            if resolved is not None:
                resources.append(resolved)

    return tuple(dict.fromkeys(resources))


def _add_synthesised_sky_placements(
    romfs_path: Path,
    stage_name: str,
    layers: dict[str, StageLayer],
    fallback_resources: tuple[str, ...] = (),
    *,
    preferred_resources: tuple[str, ...] = (),
) -> tuple[str, ...]:
    map_layer = layers.get("Map")

    if map_layer is None:
        return ()

    existing_names = {
        placement.unit_config_name
        for placement in map_layer.placements
    }
    local_resources = _sky_resources_for_layers(romfs_path, layers)
    stage_resources = local_resources or preferred_resources
    resources = stage_resources or fallback_resources

    for candidate in resources:
        if candidate in existing_names:
            continue

        identifier = f"SMOSynthesisedSky_{candidate}"
        record = {
            "Id": identifier,
            "UnitConfigName": candidate,
            "UnitConfig": {
                "GenerateCategory": "SkyList",
                "ParameterConfigName": "Sky",
            },
            "SMOSynthesised": True,
            "SMOSkyInherited": not bool(stage_resources),
        }
        placement = _make_placement(
            record,
            "SkyList",
            "Map",
            is_root=True,
            source_stage_name=stage_name,
        )

        if placement is not None:
            map_layer.placements.append(placement)
            existing_names.add(candidate)

    return resources


def read_stage_scenario(
    romfs_path: Path,
    stage_name: str,
    scenario_number: int,
) -> StageScenario:
    layer_cache: dict[tuple[str, str], StageLayer | None] = {}

    def load_layer(
        source_stage_name: str,
        stage_layer: str,
        *,
        required: bool,
    ) -> StageLayer | None:
        key = (source_stage_name, stage_layer)
        archive_path = (
            romfs_path
            / "StageData"
            / f"{source_stage_name}{stage_layer}.szs"
        )

        if key in layer_cache:
            cached = layer_cache[key]

            if cached is not None or not required:
                return cached

        if not archive_path.is_file():
            layer_cache[key] = None

            if required:
                raise FileNotFoundError(f"Could not find {archive_path}.")

            return None

        try:
            layer = read_stage_layer(
                romfs_path,
                source_stage_name,
                scenario_number,
                stage_layer,
            )
        except IndexError:
            layer_cache[key] = None

            if required:
                raise

            return None

        layer_cache[key] = layer
        return layer

    layers: dict[str, StageLayer] = {}
    missing_layers: list[str] = []

    for stage_layer in STAGE_LAYERS:
        layer = load_layer(
            stage_name,
            stage_layer,
            required=stage_layer == "Map",
        )

        if layer is None:
            missing_layers.append(stage_layer)
        else:
            layers[stage_layer] = layer

    root_graphics_sky_resources = ()

    if not _sky_resources_for_layers(romfs_path, layers):
        root_graphics_sky_resources = _graphics_preset_sky_resources(
            romfs_path,
            stage_name,
            scenario_number,
        )
    root_sky_resources = _add_synthesised_sky_placements(
        romfs_path,
        stage_name,
        layers,
        preferred_resources=root_graphics_sky_resources,
    )

    if not root_sky_resources:
        from .stage_catalog import home_stage_for

        home_stage_name = home_stage_for(stage_name)

        if home_stage_name and home_stage_name != stage_name:
            home_map_layer = load_layer(
                home_stage_name,
                "Map",
                required=False,
            )

            if home_map_layer is not None:
                fallback_resources = _sky_resources_for_layers(
                    romfs_path,
                    {"Map": home_map_layer},
                )
                root_sky_resources = _add_synthesised_sky_placements(
                    romfs_path,
                    stage_name,
                    layers,
                    fallback_resources,
                )

    zone_placements: list[StagePlacement] = []
    expanded_zones: list[str] = []
    identity = GameTransform(
        translate=Vector3(0.0, 0.0, 0.0),
        rotation_quaternion=(1.0, 0.0, 0.0, 0.0),
        scale=Vector3(1.0, 1.0, 1.0),
    )

    def expand_zone(
        zone_reference: StagePlacement,
        parent_transform: GameTransform,
        identifier_prefix: str,
        zone_path: tuple[str, ...],
        active_stages: tuple[str, ...],
        inherited_sky_resources: tuple[str, ...],
    ) -> None:
        zone_name = zone_reference.unit_config_name

        if zone_name in active_stages:
            cycle = " -> ".join((*active_stages, zone_name))
            raise RuntimeError(f"StageData zone cycle detected: {cycle}")

        zone_transform = _compose_game_transforms(
            parent_transform,
            _placement_transform(zone_reference),
        )
        qualified_zone_identifier = _qualified_identifier(
            identifier_prefix,
            zone_reference.identifier,
        )
        current_zone_path = (*zone_path, zone_name)
        expanded_zones.append("/".join(current_zone_path))
        next_active_stages = (*active_stages, zone_name)

        zone_layers = {}

        for stage_layer in STAGE_LAYERS:
            layer = load_layer(
                zone_name,
                stage_layer,
                required=stage_layer == "Map",
            )

            if layer is not None:
                zone_layers[stage_layer] = replace(
                    layer,
                    placements=list(layer.placements),
                )

        zone_graphics_sky_resources = ()

        if not _sky_resources_for_layers(romfs_path, zone_layers):
            zone_graphics_sky_resources = _graphics_preset_sky_resources(
                romfs_path,
                zone_name,
                scenario_number,
            )
        zone_sky_resources = _add_synthesised_sky_placements(
            romfs_path,
            zone_name,
            zone_layers,
            inherited_sky_resources,
            preferred_resources=zone_graphics_sky_resources,
        )

        for stage_layer in STAGE_LAYERS:
            layer = zone_layers.get(stage_layer)

            if layer is None:
                continue

            for placement in layer.placements:
                if is_zone_reference(placement):
                    expand_zone(
                        placement,
                        zone_transform,
                        qualified_zone_identifier,
                        current_zone_path,
                        next_active_stages,
                        zone_sky_resources,
                    )
                    continue

                zone_placements.append(
                    _compose_zone_placement(
                        placement,
                        zone_transform,
                        qualified_zone_identifier,
                        current_zone_path,
                    )
                )

    @timed("zone_expansion")
    def expand_root_zones() -> None:
        for layer in layers.values():
            for placement in layer.placements:
                if is_zone_reference(placement):
                    expand_zone(
                        placement,
                        identity,
                        "",
                        (),
                        (stage_name,),
                        root_sky_resources,
                    )

    expand_root_zones()

    return StageScenario(
        stage_name=stage_name,
        scenario_number=scenario_number,
        layers=layers,
        missing_layers=tuple(missing_layers),
        zone_placements=zone_placements,
        expanded_zones=tuple(expanded_zones),
    )


def game_position_to_blender(position: Vector3) -> tuple[float, float, float]:
    return (
        position.x * GAME_UNIT_SCALE,
        -position.z * GAME_UNIT_SCALE,
        position.y * GAME_UNIT_SCALE,
    )


def game_quaternion_to_blender(
    rotation: GameQuaternion,
) -> tuple[float, float, float, float]:
    w, x, y, z = _normalise_quaternion(rotation)
    return (w, x, -z, y)


def game_rotation_to_blender(
    rotation_degrees: Vector3,
) -> tuple[float, float, float, float]:
    return game_quaternion_to_blender(
        _game_rotation_quaternion(rotation_degrees)
    )

def game_scale_to_blender(scale: Vector3) -> tuple[float, float, float]:
    return (scale.x, scale.z, scale.y)


def game_transform_to_blender(
    translate: Vector3,
    rotate_degrees: Vector3,
    scale: Vector3,
    rotation_quaternion: GameQuaternion | None = None,
) -> BlenderTransform:
    return BlenderTransform(
        location=game_position_to_blender(translate),
        rotation_quaternion=(
            game_rotation_to_blender(rotate_degrees)
            if rotation_quaternion is None
            else game_quaternion_to_blender(rotation_quaternion)
        ),
        scale=game_scale_to_blender(scale),
    )


def placement_model_transform_to_blender(
    placement: StagePlacement,
) -> BlenderTransform:
    display_rotate = _vector3(
        placement.unit_config.get("DisplayRotate"),
        Vector3(0.0, 0.0, 0.0),
    )
    display_transform = GameTransform(
        translate=_vector3(
            placement.unit_config.get("DisplayTranslate"),
            Vector3(0.0, 0.0, 0.0),
        ),
        rotation_quaternion=_game_rotation_quaternion(display_rotate),
        scale=_vector3(
            placement.unit_config.get("DisplayScale"),
            Vector3(1.0, 1.0, 1.0),
        ),
    )
    transform = _compose_game_transforms(
        _placement_transform(placement),
        display_transform,
    )
    return game_transform_to_blender(
        transform.translate,
        display_rotate,
        transform.scale,
        transform.rotation_quaternion,
    )

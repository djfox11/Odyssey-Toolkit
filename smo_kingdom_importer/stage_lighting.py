from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

import bpy
from mathutils import Vector
import oead

from .performance import timed
from .stage_data import (
    GAME_UNIT_SCALE,
    StagePlacement,
    game_transform_to_blender,
)
from .world_list import read_szs


LOCAL_LIGHT_TYPES = {
    "PrePassPointLight": "POINT",
    "PrePassSpotLight": "SPOT",
    "PrePassLineLight": "AREA",
}

GAME_LIGHT_RADIANCE_REFERENCE = 128.0
LOCAL_LIGHT_POWER_SCALE = 0.2
LINE_LIGHT_POWER_SCALE = 0.4


@dataclass(slots=True, frozen=True)
class StageLighting:
    stage_name: str
    scenario_number: int
    preset_name: str
    selected_area_name: str
    selected_suffix: str
    cube_map_unit_name: str
    sky_name: str
    sky_rotation: tuple[float, float, float]
    directional_color_raw: tuple[float, float, float]
    direction_param: tuple[float, float]
    sun_color: tuple[float, float, float]
    sun_energy: float
    world_color: tuple[float, float, float]
    world_radiance_raw: tuple[float, float, float]
    world_strength: float
    default_light_map_name: str
    exposure: float
    white_point: float

    def metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "stage_name": self.stage_name,
            "scenario": self.scenario_number,
            "preset_name": self.preset_name,
            "area_name": self.selected_area_name,
            "suffix": self.selected_suffix,
            "cube_map_unit_name": self.cube_map_unit_name,
            "sky_name": self.sky_name,
            "sky_rotation_degrees": self.sky_rotation,
            "directional_color_raw": self.directional_color_raw,
            "direction_param_radians": self.direction_param,
            "sun_color": self.sun_color,
            "sun_energy": self.sun_energy,
            "world_color": self.world_color,
            "world_radiance_raw": self.world_radiance_raw,
            "world_strength": self.world_strength,
            "radiance_reference": GAME_LIGHT_RADIANCE_REFERENCE,
            "default_light_map_name": self.default_light_map_name,
            "exposure": self.exposure,
            "white_point": self.white_point,
            "approximation": (
                "DirectionalLight radiance and accumulated enabled Default "
                "material-light lobes divided by the common game radiance "
                "reference; directional light-map shape is collapsed into "
                "a uniform Blender World"
            ),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.metadata(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

def _is_mapping(value: Any) -> bool:
    return isinstance(value, (dict, oead.byml.Hash))


def _is_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple, oead.byml.Array))


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, oead.byml.Hash):
        return value[key] if key in value else default

    if isinstance(value, dict):
        return value.get(key, default)

    return default


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _vector(
    value: Any,
    keys: tuple[str, ...],
    default: tuple[float, ...],
) -> tuple[float, ...]:
    if not _is_mapping(value):
        return default

    return tuple(
        _number(_get(value, key, fallback), fallback)
        for key, fallback in zip(keys, default, strict=True)
    )


def _archive_file(archive: oead.Sarc, name: str) -> bytes | None:
    entry = archive.get_file(name)
    return None if entry is None else bytes(entry.data)


def _parse_byml(data: bytes, name: str) -> Any:
    if data[:2] not in {b"BY", b"YB"}:
        raise ValueError(f"{name} is not BYML; found magic {data[:2]!r}.")

    try:
        return oead.byml.from_binary(data)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse {name}: {exc}") from exc


def _normalised_rgb(
    colour: tuple[float, float, float],
) -> tuple[float, float, float]:
    positive = tuple(max(0.0, component) for component in colour)
    maximum = max(positive, default=0.0)

    if maximum <= 0.0:
        return (1.0, 1.0, 1.0)

    return tuple(min(1.0, component / maximum) for component in positive)


def _select_graphics_area(
    document: Any,
    scenario_number: int,
) -> Any | None:
    entries = _get(document, "GraphicsAreaParamArray", ())

    if not _is_sequence(entries):
        return None

    wanted_suffix = f"Scenario{scenario_number}"
    candidates = [
        entry
        for entry in entries
        if _is_mapping(entry)
        and str(_get(entry, "PresetName", "")).strip()
        and str(_get(entry, "SuffixName", "")).strip()
        in {"", wanted_suffix}
    ]

    def score(entry: Any) -> tuple[int, int]:
        suffix = str(_get(entry, "SuffixName", "")).strip()
        area_name = str(_get(entry, "AreaName", "")).strip()
        suffix_score = (
            2 if suffix == wanted_suffix else 1 if not suffix else 0
        )
        area_score = (
            2 if area_name == "DefaultArea" else 1 if not area_name else 0
        )
        return suffix_score, area_score

    return max(candidates, key=score, default=None)


def _default_light_map_name(preset: Any) -> str:
    material_light = _get(preset, "MaterialLight", {})
    categories = _get(material_light, "MaterialLightCategory", ())

    if not _is_sequence(categories):
        return ""

    for category in categories:
        if (
            _is_mapping(category)
            and str(_get(category, "CategoryName", "")).strip() == "Default"
        ):
            return str(_get(category, "MapName", "")).strip()

    return ""


def _light_map_world_lighting(
    archive: oead.Sarc,
    light_map_name: str,
    fallback_color: tuple[float, float, float],
    fallback_radiance: tuple[float, float, float],
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    float,
]:
    if not light_map_name:
        return (
            fallback_color,
            fallback_radiance,
            max(fallback_radiance, default=0.0)
            / GAME_LIGHT_RADIANCE_REFERENCE,
        )

    data = _archive_file(archive, f"{light_map_name}.byml")

    if data is None:
        return (
            fallback_color,
            fallback_radiance,
            max(fallback_radiance, default=0.0)
            / GAME_LIGHT_RADIANCE_REFERENCE,
        )

    document = _parse_byml(data, f"{light_map_name}.byml")
    lights = _get(document, "LightArray", ())

    if not _is_sequence(lights):
        return (
            fallback_color,
            fallback_radiance,
            max(fallback_radiance, default=0.0)
            / GAME_LIGHT_RADIANCE_REFERENCE,
        )

    accumulated = [0.0, 0.0, 0.0]

    for light in lights[1:]:
        if not _is_mapping(light) or not bool(_get(light, "IsEnable", False)):
            continue

        intensity = max(0.0, _number(_get(light, "LightIntencity", 0.0)))
        colour = _vector(
            _get(light, "LightColor", {}),
            ("R", "G", "B"),
            (1.0, 1.0, 1.0),
        )
        for index, component in enumerate(colour):
            accumulated[index] += max(0.0, component) * intensity

    radiance = tuple(accumulated)
    peak = max(radiance, default=0.0)

    if peak <= 0.0:
        return (
            fallback_color,
            fallback_radiance,
            max(fallback_radiance, default=0.0)
            / GAME_LIGHT_RADIANCE_REFERENCE,
        )

    return (
        _normalised_rgb(radiance),
        radiance,
        peak / GAME_LIGHT_RADIANCE_REFERENCE,
    )

def _read_stage_graphics_area(
    romfs_root: Path,
    stage_name: str,
    scenario_number: int,
) -> Any | None:
    design_path = romfs_root / "StageData" / f"{stage_name}Design.szs"

    if not design_path.is_file():
        return None

    archive = read_szs(design_path)
    data = _archive_file(archive, "GraphicsArea.byml")

    if data is None:
        return None

    return _select_graphics_area(
        _parse_byml(data, "GraphicsArea.byml"),
        scenario_number,
    )


def read_stage_graphics_sky_name(
    romfs_root: Path,
    stage_name: str,
    scenario_number: int,
) -> str:
    """Return the exact sky resource named by this stage's graphics preset."""
    area = _read_stage_graphics_area(
        romfs_root,
        stage_name,
        scenario_number,
    )

    if area is None:
        return ""

    preset_name = str(_get(area, "PresetName", "")).strip()

    if not preset_name:
        return ""

    preset_archive = read_szs(
        romfs_root / "SystemData" / "GraphicsPreset.szs"
    )
    preset_data = _archive_file(preset_archive, f"{preset_name}.byml")

    if preset_data is None:
        return ""

    preset = _parse_byml(preset_data, f"{preset_name}.byml")
    return str(_get(_get(preset, "Sky", {}), "Name", "")).strip()


@timed("stage_lighting")
def read_stage_lighting(
    romfs_root: Path,
    stage_name: str,
    scenario_number: int,
) -> StageLighting | None:
    area = _read_stage_graphics_area(
        romfs_root,
        stage_name,
        scenario_number,
    )

    if area is None:
        from .stage_catalog import home_stage_for

        home_stage = home_stage_for(stage_name)

        if home_stage and home_stage != stage_name:
            area = _read_stage_graphics_area(
                romfs_root,
                home_stage,
                scenario_number,
            )

    if area is None:
        return None

    preset_name = str(_get(area, "PresetName", "")).strip()

    if not preset_name:
        return None

    preset_archive = read_szs(
        romfs_root / "SystemData" / "GraphicsPreset.szs"
    )
    preset_data = _archive_file(preset_archive, f"{preset_name}.byml")

    if preset_data is None:
        raise FileNotFoundError(
            f"Graphics preset {preset_name!r} was not found."
        )

    preset = _parse_byml(preset_data, f"{preset_name}.byml")
    directional = _get(preset, "DirectionalLight", {})
    raw_colour = _vector(
        _get(directional, "Color", {}),
        ("R", "G", "B"),
        (128.0, 128.0, 128.0),
    )
    direction_param = _vector(
        _get(directional, "DirectionParam", {}),
        ("X", "Y"),
        (0.0, math.pi * 0.25),
    )
    sun_colour = _normalised_rgb(raw_colour)
    peak = max(raw_colour, default=0.0)
    sun_energy = max(0.0, peak / GAME_LIGHT_RADIANCE_REFERENCE)
    default_light_map_name = _default_light_map_name(preset)
    world_colour = sun_colour
    world_radiance = tuple(
        component * peak * 0.25 for component in sun_colour
    )
    world_strength = (
        max(world_radiance, default=0.0)
        / GAME_LIGHT_RADIANCE_REFERENCE
    )
    light_map_path = romfs_root / "SystemData" / "LightMapList.szs"

    if light_map_path.is_file():
        world_colour, world_radiance, world_strength = (
            _light_map_world_lighting(
                read_szs(light_map_path),
                default_light_map_name,
                sun_colour,
                world_radiance,
            )
        )

    sky = _get(preset, "Sky", {})
    hdr = _get(preset, "HdrCompose", {})

    return StageLighting(
        stage_name=stage_name,
        scenario_number=scenario_number,
        preset_name=preset_name,
        selected_area_name=str(_get(area, "AreaName", "")).strip(),
        selected_suffix=str(_get(area, "SuffixName", "")).strip(),
        cube_map_unit_name=str(
            _get(area, "CubeMapUnitName", "")
        ).strip(),
        sky_name=str(_get(sky, "Name", "")).strip(),
        sky_rotation=_vector(
            _get(sky, "Rotate", {}),
            ("X", "Y", "Z"),
            (0.0, 0.0, 0.0),
        ),
        directional_color_raw=raw_colour,
        direction_param=direction_param,
        sun_color=sun_colour,
        sun_energy=sun_energy,
        world_color=world_colour,
        world_radiance_raw=world_radiance,
        world_strength=world_strength,
        default_light_map_name=default_light_map_name,
        exposure=_number(_get(hdr, "Exposure", 0.0)),
        white_point=_number(_get(hdr, "WhitePoint", 1.0), 1.0),
    )


def _sun_ray_direction(
    direction_param: tuple[float, float],
) -> tuple[float, float, float]:
    longitude, latitude = direction_param
    rho = -math.cos(latitude)
    game_x = math.sin(longitude) * rho
    game_y = -math.sin(latitude)
    game_z = math.cos(longitude) * rho
    return (game_x, -game_z, game_y)


def _set_generated_properties(
    obj: bpy.types.Object,
    *,
    representation: str,
) -> None:
    obj["smo_static_model_generated"] = True
    obj["smo_stage_lighting_generated"] = True
    obj["smo_representation"] = representation


def _persistent_identity(owner: Any, property_name: str) -> str:
    identity = str(owner.get(property_name, "")).strip()

    if not identity:
        identity = uuid4().hex
        owner[property_name] = identity

    return identity


def _stage_lighting_identity(
    scene: bpy.types.Scene,
    root: bpy.types.Object,
) -> str:
    scene_identity = _persistent_identity(
        scene,
        "smo_stage_lighting_scene_identity",
    )
    root_identity = _persistent_identity(
        root,
        "smo_stage_lighting_root_identity",
    )
    return f"{scene_identity}:{root_identity}"


def _generated_world_for_identity(
    identity: str,
) -> bpy.types.World | None:
    return next(
        (
            world
            for world in bpy.data.worlds
            if world.get("smo_stage_lighting_generated")
            and str(world.get("smo_stage_lighting_identity", ""))
            == identity
        ),
        None,
    )


def _world_for_stage_lighting(
    world_name: str,
    identity: str,
) -> bpy.types.World:
    world = _generated_world_for_identity(identity)

    if world is not None:
        return world

    named_world = bpy.data.worlds.get(world_name)

    if (
        named_world is not None
        and named_world.get("smo_stage_lighting_generated")
        and not str(named_world.get("smo_stage_lighting_identity", ""))
    ):
        # Adopt Worlds generated by releases before persistent lighting
        # identities were introduced, but never overwrite an untagged user
        # World which happens to use our display name.
        return named_world

    return bpy.data.worlds.new(world_name)


def _scene_global_suns(
    scene: bpy.types.Scene,
) -> tuple[bpy.types.Object, ...]:
    return tuple(
        obj
        for obj in scene.objects
        if obj.get("smo_global_stage_sun")
    )


def _world_sun(
    scene: bpy.types.Scene,
    world: bpy.types.World | None,
) -> bpy.types.Object | None:
    if world is None or not world.get("smo_stage_lighting_generated"):
        return None

    identity = str(world.get("smo_stage_lighting_identity", ""))
    stored_name = str(world.get("smo_stage_lighting_sun_object", ""))
    stored = scene.objects.get(stored_name) if stored_name else None

    if (
        stored is not None
        and stored.get("smo_global_stage_sun")
        and stored.type == "LIGHT"
        and stored.data.type == "SUN"
        and (
            not identity
            or str(stored.get("smo_stage_lighting_identity", ""))
            == identity
        )
    ):
        return stored

    if identity:
        matching = next(
            (
                obj
                for obj in _scene_global_suns(scene)
                if str(obj.get("smo_stage_lighting_identity", ""))
                == identity
                and obj.type == "LIGHT"
                and obj.data.type == "SUN"
            ),
            None,
        )

        if matching is not None:
            return matching

    preset_name = str(world.get("smo_graphics_preset", ""))
    return next(
        (
            obj
            for obj in _scene_global_suns(scene)
            if not obj.get("smo_stage_lighting_identity")
            and obj.type == "LIGHT"
            and obj.data.type == "SUN"
            and preset_name
            and obj.get("smo_graphics_preset") == preset_name
        ),
        None,
    )


def _activate_world_sun(
    scene: bpy.types.Scene,
    world: bpy.types.World | None,
) -> bpy.types.Object | None:
    active_sun = _world_sun(scene, world)

    for obj in _scene_global_suns(scene):
        enabled = obj == active_sun
        obj.hide_viewport = not enabled
        obj.hide_render = not enabled

    return active_sun


def _record_previous_world(
    scene: bpy.types.Scene,
    root: bpy.types.Object,
    generated_world: bpy.types.World,
) -> None:
    current_world = scene.world

    if current_world == generated_world:
        return

    if current_world is None:
        if "smo_previous_world" in root:
            del root["smo_previous_world"]

        root["smo_previous_world_identity"] = ""
        return

    root["smo_previous_world"] = current_world.name
    root["smo_previous_world_identity"] = (
        str(current_world.get("smo_stage_lighting_identity", ""))
        if current_world.get("smo_stage_lighting_generated")
        else ""
    )


@timed("stage_lighting")
def apply_global_stage_lighting(
    scene: bpy.types.Scene,
    collection: bpy.types.Collection,
    root: bpy.types.Object,
    lighting: StageLighting,
) -> bpy.types.Object:
    identity = _stage_lighting_identity(scene, root)
    world_name = (
        f"SMO World - {lighting.stage_name} S{lighting.scenario_number}"
    )
    world = _world_for_stage_lighting(world_name, identity)
    _record_previous_world(scene, root, world)
    world["smo_stage_lighting_generated"] = True
    world["smo_stage_lighting_identity"] = identity
    sun = _world_sun(scene, world)

    if sun is None:
        sun_data = bpy.data.lights.new(
            f"SMO Sun - {lighting.preset_name}",
            type="SUN",
        )
        sun = bpy.data.objects.new(sun_data.name, sun_data)
        collection.objects.link(sun)
    else:
        sun_data = sun.data

    sun_data.color = lighting.sun_color
    sun_data.energy = lighting.sun_energy
    sun_data.angle = math.radians(3.0)
    sun.parent = root
    direction = Vector(_sun_ray_direction(lighting.direction_param))
    sun.rotation_mode = "QUATERNION"
    sun.rotation_quaternion = direction.to_track_quat("-Z", "Y")
    sun.hide_viewport = False
    sun.hide_render = False
    _set_generated_properties(sun, representation="STAGE_SUN")
    sun["smo_global_stage_sun"] = True
    sun["smo_stage_lighting_identity"] = identity
    sun["smo_graphics_preset"] = lighting.preset_name
    sun["smo_direction_param"] = json.dumps(lighting.direction_param)
    sun["smo_direction_vector"] = json.dumps(tuple(direction))
    sun["smo_direction_is_approximate"] = False
    sun["smo_sun_energy"] = lighting.sun_energy
    for stale_key in (
        "smo_sun_energy_uncompensated",
        "smo_sun_energy_compensated",
        "smo_sun_strength_multiplier",
    ):
        if stale_key in sun:
            del sun[stale_key]

    world.use_nodes = True
    nodes = world.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputWorld")
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Color"].default_value = (*lighting.world_color, 1.0)
    background.inputs["Strength"].default_value = lighting.world_strength
    world.node_tree.links.new(
        background.outputs["Background"],
        output.inputs["Surface"],
    )
    world.color = lighting.world_color
    world["smo_stage_lighting_generated"] = True
    world["smo_stage_lighting_identity"] = identity
    world["smo_stage_lighting_sun_object"] = sun.name
    world["smo_graphics_preset"] = lighting.preset_name
    world["smo_lighting_metadata"] = lighting.to_json()
    root["smo_stage_lighting_world"] = world.name
    root["smo_stage_lighting_world_identity"] = identity
    root["smo_stage_lighting_sun"] = sun.name
    scene.world = world
    _activate_world_sun(scene, world)
    return sun

def restore_previous_stage_world(
    scene: bpy.types.Scene,
    root: bpy.types.Object,
) -> None:
    current_world = scene.world

    if (
        current_world is None
        or not current_world.get("smo_stage_lighting_generated")
    ):
        return

    expected_identity = str(
        root.get("smo_stage_lighting_world_identity", "")
    )
    current_identity = str(
        current_world.get("smo_stage_lighting_identity", "")
    )

    if expected_identity and current_identity != expected_identity:
        return

    previous_identity = str(
        root.get("smo_previous_world_identity", "")
    )
    previous_name = str(root.get("smo_previous_world", ""))
    previous_world = (
        _generated_world_for_identity(previous_identity)
        if previous_identity
        else bpy.data.worlds.get(previous_name)
    )
    scene.world = previous_world
    _activate_world_sun(scene, previous_world)


def _local_light_colour(
    placement: StagePlacement,
) -> tuple[tuple[float, float, float], float]:
    raw = (
        _number(placement.raw.get("ColorRed", 255.0), 255.0),
        _number(placement.raw.get("ColorGreen", 255.0), 255.0),
        _number(placement.raw.get("ColorBlue", 255.0), 255.0),
    )
    return _normalised_rgb(raw), max(raw, default=0.0)


@timed("stage_lighting")
def create_local_stage_light(
    collection: bpy.types.Collection,
    root: bpy.types.Object,
    placement: StagePlacement,
) -> bpy.types.Object | None:
    blender_type = LOCAL_LIGHT_TYPES.get(placement.unit_config_name)

    if blender_type is None:
        return None

    light_name = (
        f"{placement.identifier} {placement.unit_config_name} "
        f"[{placement.source_stage_name}]"
    )
    light = bpy.data.lights.new(light_name, type=blender_type)
    colour, peak = _local_light_colour(placement)
    light.color = colour
    light.energy = max(0.0, peak * LOCAL_LIGHT_POWER_SCALE)
    raw = placement.raw

    if blender_type == "POINT":
        radius = max(0.0, _number(raw.get("Radius", 0.0)))
        light.cutoff_distance = radius * GAME_UNIT_SCALE
        light.shadow_soft_size = min(
            0.5,
            max(0.01, radius * GAME_UNIT_SCALE * 0.05),
        )
    elif blender_type == "SPOT":
        length = max(0.0, _number(raw.get("SpotLightLength", 0.0)))
        light.cutoff_distance = length * GAME_UNIT_SCALE
        light.spot_size = math.radians(
            min(179.0, max(1.0, _number(raw.get("SpotLightDegree", 45.0))))
        )
        angle_damp = max(
            1.0,
            _number(raw.get("SpotLightAngleDamp", 1.0), 1.0),
        )
        light.spot_blend = min(1.0, max(0.0, 1.0 - 1.0 / angle_damp))
    else:
        length = max(0.0, _number(raw.get("LineLightLength", 0.0)))
        radius = max(0.0, _number(raw.get("Radius", 0.0)))
        light.shape = "RECTANGLE"
        light.size = max(0.01, length * GAME_UNIT_SCALE)
        light.size_y = max(0.01, radius * GAME_UNIT_SCALE * 2.0)
        light.energy = max(0.0, peak * LINE_LIGHT_POWER_SCALE)

    if hasattr(light, "use_shadow"):
        light.use_shadow = bool(raw.get("UseShadow", False))

    if hasattr(light, "specular_factor"):
        light.specular_factor = (
            1.0 if bool(raw.get("IsEnableSpecular", True)) else 0.0
        )

    obj = bpy.data.objects.new(light_name, light)
    collection.objects.link(obj)
    obj.parent = root
    transform = game_transform_to_blender(
        placement.translate,
        placement.rotate,
        placement.scale,
        placement.rotation_quaternion,
    )
    obj.location = transform.location
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = transform.rotation_quaternion
    obj.scale = transform.scale
    _set_generated_properties(obj, representation="STAGE_LOCAL_LIGHT")
    obj["smo_id"] = placement.identifier
    obj["smo_unit_config_name"] = placement.unit_config_name
    obj["smo_source_stage_name"] = placement.source_stage_name
    obj["smo_zone_path"] = json.dumps(placement.zone_path)
    obj["smo_stage_layer"] = placement.stage_layer
    obj["smo_light_energy"] = light.energy
    obj["smo_light_energy_scale"] = (
        LINE_LIGHT_POWER_SCALE
        if blender_type == "AREA"
        else LOCAL_LIGHT_POWER_SCALE
    )
    obj["smo_game_light_parameters"] = json.dumps(
        {
            key: value
            for key, value in raw.items()
            if key
            in {
                "ColorRed",
                "ColorGreen",
                "ColorBlue",
                "Radius",
                "PointLightDampPower",
                "SpotLightAngleDamp",
                "SpotLightDegree",
                "SpotLightLength",
                "LineLightLength",
                "LightDistDamp",
                "IsEnableSpecular",
                "IsIndirectIllumination",
                "UseShadow",
            }
        },
        sort_keys=True,
    )
    return obj

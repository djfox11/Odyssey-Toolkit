from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.stage_data import (
    game_position_to_blender,
    read_stage_layer,
)
from smo_kingdom_importer.stage_lighting import (
    LOCAL_LIGHT_TYPES,
    _sun_ray_direction,
    apply_global_stage_lighting,
    create_local_stage_light,
    read_stage_lighting,
    restore_previous_stage_world,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    city_night = read_stage_lighting(
        romfs_root,
        "CityWorldHomeStage",
        1,
    )
    city_day = read_stage_lighting(
        romfs_root,
        "CityWorldHomeStage",
        2,
    )
    city_late_night = read_stage_lighting(
        romfs_root,
        "CityWorldHomeStage",
        3,
    )
    trex = read_stage_lighting(
        romfs_root,
        "TrexBikeExStage",
        1,
    )
    ice_cave = read_stage_lighting(
        romfs_root,
        "SandWorldPressExStage",
        1,
    )
    check(city_night is not None, "Metro scenario 1 has no lighting")
    check(city_day is not None, "Metro scenario 2 has no lighting")
    check(city_late_night is not None, "Metro scenario 3 has no lighting")
    check(trex is not None, "T-Rex room has no lighting")
    check(ice_cave is not None, "Ice Cave has no lighting")
    check(
        city_day.preset_name == "\u90fd\u5e02\u9752\u7a7a",
        f"Metro scenario 2 selected {city_day.preset_name!r}",
    )
    check(
        city_day.selected_suffix == "Scenario2",
        f"Metro scenario 2 used suffix {city_day.selected_suffix!r}",
    )
    check(
        city_night.preset_name == "\u90fd\u5e02\u591c\u7a7a",
        f"Metro scenario 1 selected {city_night.preset_name!r}",
    )
    check(
        city_late_night.preset_name
        == "\u90fd\u5e02\u591c\u7a7a\u660e\u308b\u3081",
        f"Metro scenario 3 selected {city_late_night.preset_name!r}",
    )
    check(
        trex.preset_name
        == "\u90fd\u5e02EX\u6050\u7adc\u3068\u30d0\u30a4\u30af",
        f"T-Rex room selected {trex.preset_name!r}",
    )
    check(
        trex.directional_color_raw == (320.0, 287.0, 235.0),
        f"Unexpected T-Rex directional colour: {trex.directional_color_raw}",
    )
    check(
        trex.default_light_map_name
        == "\u90fd\u5e02EX\u30d0\u30a4\u30af\u3068\u6050\u7adc",
        f"Unexpected T-Rex light map: {trex.default_light_map_name!r}",
    )
    check(
        abs(ice_cave.sun_energy - (10.0 / 128.0)) < 1e-6,
        "Ice Cave Sun no longer follows the direct radiance conversion",
    )
    check(
        abs(
            ice_cave.world_strength
            - max(ice_cave.world_radiance_raw) / 128.0
        ) < 1e-6,
        "Ice Cave World strength diverged from its light-map radiance",
    )
    check(
        0.0 < ice_cave.world_strength < 0.25,
        "Ice Cave World unexpectedly retained the old minimum strength",
    )
    metadata = json.loads(trex.to_json())
    check(
        metadata["preset_name"] == trex.preset_name,
        "Lighting metadata lost its Unicode preset name",
    )
    cardinal_directions = (
        ((0.0, 0.0), (0.0, 1.0, 0.0)),
        ((math.pi * 0.5, 0.0), (-1.0, 0.0, 0.0)),
        ((0.0, math.pi * 0.5), (0.0, 0.0, -1.0)),
    )

    for parameters, expected in cardinal_directions:
        actual = _sun_ray_direction(parameters)
        check(
            all(
                abs(component - wanted) < 1e-6
                for component, wanted in zip(
                    actual,
                    expected,
                    strict=True,
                )
            ),
            (
                "Odyssey longitude/latitude direction conversion changed: "
                f"{parameters} produced {actual}, expected {expected}"
            ),
        )

    collection = bpy.data.collections.new("SMO Lighting Regression")
    bpy.context.scene.collection.children.link(collection)
    city_root = bpy.data.objects.new("City Lighting Root", None)
    trex_root = bpy.data.objects.new("T-Rex Lighting Root", None)
    collection.objects.link(city_root)
    collection.objects.link(trex_root)
    original_world = bpy.context.scene.world
    foreign_scene = bpy.data.scenes.new("SMO Lighting Foreign Scene")
    foreign_collection = bpy.data.collections.new(
        "SMO Lighting Foreign Collection"
    )
    foreign_scene.collection.children.link(foreign_collection)
    foreign_light = bpy.data.lights.new(
        "SMO Lighting Foreign Sun Data",
        type="SUN",
    )
    foreign_sun = bpy.data.objects.new(
        "SMO Lighting Foreign Sun",
        foreign_light,
    )
    foreign_sun["smo_global_stage_sun"] = True
    foreign_sun["smo_graphics_preset"] = city_day.preset_name
    foreign_collection.objects.link(foreign_sun)
    city_sun = apply_global_stage_lighting(
        bpy.context.scene,
        collection,
        city_root,
        city_day,
    )
    city_world = bpy.context.scene.world
    trex_sun = apply_global_stage_lighting(
        bpy.context.scene,
        collection,
        trex_root,
        trex,
    )
    check(city_sun.data.type == "SUN", "Metro global light is not a Sun")
    check(trex_sun.data.type == "SUN", "T-Rex global light is not a Sun")
    check(
        abs(trex_sun.data.energy - (320.0 / 128.0)) < 1e-6,
        "T-Rex Sun no longer follows the direct radiance conversion",
    )
    check(
        city_sun.hide_render and city_sun.hide_viewport,
        "The previous stage Sun remained active",
    )
    check(
        not trex_sun.hide_render and not trex_sun.hide_viewport,
        "The newest stage Sun was disabled",
    )
    check(
        not foreign_sun.hide_render and not foreign_sun.hide_viewport,
        "Importing in one scene disabled a Sun belonging to another scene",
    )
    expected_trex_direction = Vector(
        _sun_ray_direction(trex.direction_param)
    )
    actual_trex_direction = (
        trex_sun.rotation_quaternion @ Vector((0.0, 0.0, -1.0))
    )
    check(
        (actual_trex_direction - expected_trex_direction).length < 1e-6,
        "The Blender Sun -Z axis does not follow Odyssey's light rays",
    )
    check(
        expected_trex_direction.z < -0.75,
        (
            "The T-Rex preset should cast strongly downward in Blender; "
            f"got {tuple(expected_trex_direction)}"
        ),
    )
    check(
        not trex_sun.get("smo_direction_is_approximate", True),
        "The exact DirectionParam conversion is still marked approximate",
    )
    check(
        bpy.context.scene.world.get("smo_graphics_preset")
        == trex.preset_name,
        "The active World did not use the T-Rex preset",
    )
    world_background = next(
        node
        for node in bpy.context.scene.world.node_tree.nodes
        if node.type == "BACKGROUND"
    )
    check(
        abs(
            world_background.inputs["Strength"].default_value
            - trex.world_strength
        ) < 1e-6,
        "World strength does not match the light-map radiance",
    )
    check(
        abs(trex.world_strength - (74.1 / 128.0)) < 1e-6,
        "T-Rex World no longer includes its light-map intensity",
    )
    applied_metadata = json.loads(
        bpy.context.scene.world["smo_lighting_metadata"]
    )
    check(
        applied_metadata["schema_version"] == 3
        and applied_metadata["radiance_reference"] == 128.0
        and "sun_strength_multiplier" not in applied_metadata
        and "world_strength_multiplier" not in applied_metadata,
        "Direct lighting metadata is incomplete or retained multipliers",
    )
    check(
        len(bpy.context.scene.world.node_tree.nodes) == 2,
        "The generated World node tree is not concise",
    )
    restore_previous_stage_world(bpy.context.scene, trex_root)
    check(
        bpy.context.scene.world.get("smo_graphics_preset")
        == city_day.preset_name,
        "Disabling a re-import did not restore its previous SMO World",
    )
    check(
        not city_sun.hide_render and trex_sun.hide_render,
        "Restoring the previous World did not switch global Suns",
    )
    check(
        city_root.get("smo_previous_world")
        == (original_world.name if original_world is not None else None),
        "The original scene World was not recorded",
    )

    same_preset_root = bpy.data.objects.new(
        "City Duplicate-Preset Lighting Root",
        None,
    )
    collection.objects.link(same_preset_root)
    same_preset_sun = apply_global_stage_lighting(
        bpy.context.scene,
        collection,
        same_preset_root,
        city_day,
    )
    check(
        same_preset_sun is not city_sun,
        "Different import roots unexpectedly shared one Sun object",
    )
    restore_previous_stage_world(
        bpy.context.scene,
        same_preset_root,
    )
    active_scene_suns = [
        obj
        for obj in bpy.context.scene.objects
        if obj.get("smo_global_stage_sun") and not obj.hide_render
    ]
    check(
        bpy.context.scene.world is city_world,
        "Duplicate-preset restoration selected the wrong World",
    )
    check(
        active_scene_suns == [city_sun],
        (
            "Duplicate-preset restoration enabled the wrong Suns: "
            f"{[obj.name for obj in active_scene_suns]}"
        ),
    )

    colliding_name = (
        f"SMO World - {ice_cave.stage_name} S"
        f"{ice_cave.scenario_number}"
    )
    user_world = bpy.data.worlds.new(colliding_name)
    user_world.use_nodes = True
    user_world["smo_user_world_regression"] = True
    user_world.node_tree.nodes.new("ShaderNodeTexEnvironment")
    user_world_node_count = len(user_world.node_tree.nodes)
    bpy.context.scene.world = user_world
    ice_root = bpy.data.objects.new("Ice Cave Lighting Root", None)
    collection.objects.link(ice_root)
    ice_sun = apply_global_stage_lighting(
        bpy.context.scene,
        collection,
        ice_root,
        ice_cave,
    )
    ice_world = bpy.context.scene.world
    lighting_identity = str(
        ice_root["smo_stage_lighting_world_identity"]
    )
    check(
        ice_world is not user_world
        and ice_world.get("smo_stage_lighting_generated"),
        "An untagged user World name collision was reused",
    )
    check(
        user_world.get("smo_user_world_regression")
        and len(user_world.node_tree.nodes) == user_world_node_count
        and not user_world.get("smo_stage_lighting_generated"),
        "The colliding user World was modified",
    )
    check(
        ice_world.get("smo_stage_lighting_identity")
        == lighting_identity
        and ice_sun.get("smo_stage_lighting_identity")
        == lighting_identity,
        "The generated Sun and World do not share their root identity",
    )
    restore_previous_stage_world(bpy.context.scene, trex_root)
    check(
        bpy.context.scene.world is ice_world,
        "A different import root restored the active lighting",
    )
    restore_previous_stage_world(bpy.context.scene, ice_root)
    check(
        bpy.context.scene.world is user_world,
        "The colliding user World was not restored",
    )

    replacement_world = bpy.data.worlds.new(
        "SMO Lighting Replacement User World"
    )
    bpy.context.scene.world = replacement_world
    reapplied_ice_sun = apply_global_stage_lighting(
        bpy.context.scene,
        collection,
        ice_root,
        ice_cave,
    )
    check(
        reapplied_ice_sun is ice_sun,
        "Reapplying one root created a duplicate Sun",
    )
    check(
        ice_root.get("smo_previous_world") == replacement_world.name,
        "Reapplication retained a stale previous-World reference",
    )
    restore_previous_stage_world(bpy.context.scene, ice_root)
    check(
        bpy.context.scene.world is replacement_world,
        "Reapplication did not restore the latest previous World",
    )
    check(
        not foreign_sun.hide_render and not foreign_sun.hide_viewport,
        "Lighting restoration changed another scene's Sun",
    )

    design = read_stage_layer(
        romfs_root,
        "CityWorldHomeStage",
        3,
        "Design",
    )
    placements = [
        placement
        for placement in design.placements
        if placement.unit_config_name in LOCAL_LIGHT_TYPES
    ]
    counts = {
        unit_name: sum(
            placement.unit_config_name == unit_name
            for placement in placements
        )
        for unit_name in LOCAL_LIGHT_TYPES
    }
    check(
        counts
        == {
            "PrePassPointLight": 15,
            "PrePassSpotLight": 1,
            "PrePassLineLight": 2,
        },
        f"Metro scenario 3 local-light counts changed: {counts}",
    )
    local_objects = [
        create_local_stage_light(collection, city_root, placement)
        for placement in placements
    ]
    check(all(local_objects), "A supported local light was not converted")
    light_types = [
        obj.data.type for obj in local_objects if obj is not None
    ]
    check(light_types.count("POINT") == 15, "Point lights changed")
    check(light_types.count("SPOT") == 1, "Spot lights changed")
    check(light_types.count("AREA") == 2, "Line-light approximations changed")
    for placement, light_object in zip(
        placements,
        local_objects,
        strict=True,
    ):
        check(light_object is not None, "Local light object is missing")
        peak = max(
            float(placement.raw.get("ColorRed", 255.0)),
            float(placement.raw.get("ColorGreen", 255.0)),
            float(placement.raw.get("ColorBlue", 255.0)),
        )
        expected_scale = (
            0.4 if light_object.data.type == "AREA" else 0.2
        )
        check(
            abs(light_object.data.energy - peak * expected_scale) < 1e-6,
            "Placed-light power conversion changed",
        )
        check(
            abs(light_object["smo_light_energy_scale"] - expected_scale)
            < 1e-6,
            "Placed-light conversion metadata changed",
        )
    first_placement = placements[0]
    first_object = local_objects[0]
    expected_location = game_position_to_blender(first_placement.translate)
    check(
        first_object is not None
        and all(
            abs(actual - expected) < 1e-5
            for actual, expected in zip(
                first_object.location,
                expected_location,
                strict=True,
            )
        ),
        "Local-light coordinate conversion changed",
    )

    result = {
        "city_scenario_2_preset": city_day.preset_name,
        "trex_preset": trex.preset_name,
        "trex_sun_energy": trex.sun_energy,
        "trex_world_strength": trex.world_strength,
        "local_lights": counts,
    }
    print(
        "SMO_STAGE_LIGHTING_REGRESSION="
        + json.dumps(result, ensure_ascii=False, sort_keys=True)
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python "
            "stage_lighting_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())

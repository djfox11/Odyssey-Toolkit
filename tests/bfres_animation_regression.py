from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon
from smo_kingdom_importer.bfres_animation_import import (
    AnimationClip,
    _animation_sources,
    bfres_animation_enum_items,
    clear_bfres_animation_cache,
    import_bfres_animation,
)
from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.static_model_import import _create_armature_object
from smo_kingdom_importer.world_list import read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def action_fcurves(
    armature: bpy.types.Object,
    action: bpy.types.Action,
) -> tuple[object, ...]:
    if bpy.app.version >= (4, 4, 0):
        slot = armature.animation_data.action_slot
        bag = action.layers[0].strips[0].channelbag(slot)
        return tuple(bag.fcurves)

    return tuple(action.fcurves)


def node_tree_action_fcurves(
    node_tree: bpy.types.NodeTree,
    action: bpy.types.Action,
) -> tuple[object, ...]:
    if bpy.app.version >= (4, 4, 0):
        slot = node_tree.animation_data.action_slot
        bag = action.layers[0].strips[0].channelbag(slot)
        return tuple(bag.fcurves)

    return tuple(action.fcurves)


def run(romfs_root: Path) -> None:
    registered = False
    original_preferences = addon.get_addon_preferences

    try:
        addon.register()
        registered = True
        addon.get_addon_preferences = lambda _context=None: SimpleNamespace(
            apply_custom_normals=False,
            import_armatures=True,
            use_texture_cache=False,
            texture_cache_parent="",
            romfs_path=str(romfs_root),
        )
        archive_path = romfs_root / "ObjectData" / "Kuribo.szs"
        result = bpy.ops.smo.import_test_model(
            filepath=str(archive_path),
            use_selected_stage_textures=False,
        )
        check(result == {"FINISHED"}, f"Kuribo import returned {result}")
        armature = next(
            obj
            for obj in bpy.data.objects
            if obj.type == "ARMATURE" and obj.get("smo_armature_generated")
        )
        check(
            Path(armature["smo_source_archive"]) == archive_path,
            "Standalone armature lost its exact source archive",
        )
        check(
            armature["smo_source_bfres"] == "Kuribo.bfres",
            "Standalone armature lost its exact BFRES member",
        )
        check(
            all(
                "smo_rest_scale" in bone
                for bone in armature.data.bones
                if "smo_bone_index" in bone
            ),
            "Native FSKL rest transforms were not preserved on the armature",
        )
        rigged_meshes = tuple(
            obj
            for obj in bpy.data.objects
            if obj.type == "MESH"
            and str(obj.get("smo_armature") or "") == armature.name
        )
        check(rigged_meshes, "Kuribo import created no rigged mesh objects")
        check(
            any(obj.get("smo_visibility_bone") for obj in rigged_meshes),
            "Rigged meshes lost their BFRES base-bone visibility binding",
        )

        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        sources = _animation_sources(armature)
        check(len(sources) == 1, f"Expected one animation source, found {len(sources)}")
        check(
            len(sources[0].skeletal_animations) == 47,
            "Expected 47 Kuribo skeletal animations",
        )
        check(
            len(sources[0].visibility_animations) == 38,
            "Expected 38 Kuribo bone visibility animations",
        )
        check(
            len(sources[0].animations) == 55,
            f"Expected 55 Kuribo clips, found {len(sources[0].animations)}",
        )
        wait = next(
            animation
            for animation in sources[0].animations
            if animation.name == "Wait"
        )
        check(wait.frame_count == 118, "Wait frame count changed")
        check(wait.looping, "Wait loop flag changed")
        check(wait.euler_xyz, "Wait rotation mode changed")
        check(len(wait.bones) == 20, "Wait animated-bone count changed")
        check(
            sum(len(bone.curves) for bone in wait.bones) == 45,
            "Wait transform-curve count changed",
        )

        enum_items = bfres_animation_enum_items(
            bpy.context.scene,
            bpy.context,
        )
        check(len(enum_items) == 55, "Sidebar animation list is incomplete")
        wait_identifier = next(
            identifier
            for identifier, name, _description, _icon, _number in enum_items
            if name == "Wait"
        )
        bpy.context.scene.smo_bfres_animation = wait_identifier
        check(
            addon.SMO_OT_apply_bfres_animation.poll(bpy.context),
            "Use Animation is unavailable for the selected imported armature",
        )
        result = bpy.ops.smo.apply_bfres_animation()
        check(result == {"FINISHED"}, f"Native animation use returned {result}")
        action = armature.animation_data.action
        check(action is not None, "Native animation created no active Action")
        check(action.name == "Wait", f"Unexpected Action name {action.name!r}")
        check(action["smo_bfres_animation_import"], "Native Action metadata is missing")
        check(action["smo_source_animation"] == "Wait", "Animation source name changed")
        check(action["smo_frame_count"] == 119, "Action sample count changed")
        check(action["smo_animated_bone_count"] == 20, "Action bone count changed")
        check(action["smo_looping"], "Action loop metadata changed")
        check(bpy.context.scene.frame_start == 0, "Scene start frame changed")
        check(bpy.context.scene.frame_end == 118, "Scene end frame changed")
        check(bpy.context.scene.render.fps == 60, "Native playback is not 60 FPS")

        curves = action_fcurves(armature, action)
        check(len(curves) == 262, f"Expected 262 Action curves, found {len(curves)}")
        check(
            sum(curve.data_path.endswith(".scale") for curve in curves) == 117,
            "Native scale channels are missing",
        )
        transform_curves = tuple(
            curve
            for curve in curves
            if not curve.data_path.endswith('["smo_visible"]')
        )
        wait_visibility_curves = tuple(
            curve
            for curve in curves
            if curve.data_path.endswith('["smo_visible"]')
        )
        check(
            len(transform_curves) == 257,
            "Wait transform Action curve count changed",
        )
        check(
            len(wait_visibility_curves) == 5,
            "Wait bone visibility Action curve count changed",
        )
        check(
            all(
                point.interpolation == "LINEAR"
                for curve in transform_curves
                for point in curve.keyframe_points
            ),
            "Baked native samples are no longer linearly interpolated",
        )
        check(
            all(
                point.interpolation == "CONSTANT"
                for curve in wait_visibility_curves
                for point in curve.keyframe_points
            ),
            "Wait visibility channels are not step-interpolated",
        )
        check(
            action["smo_animated_visibility_bone_count"] == 5,
            "Wait visibility target count changed",
        )
        check(
            action["smo_visibility_mesh_count"] == 9,
            "Wait visibility mesh binding count changed",
        )
        scale_values = [
            float(point.co.y)
            for curve in curves
            if curve.data_path.endswith(".scale")
            for point in curve.keyframe_points
        ]
        check(
            any(abs(value - 1.0) > 1e-4 for value in scale_values),
            "Wait's native scale animation was flattened",
        )

        for frame in (0, 39, 42, 118):
            bpy.context.scene.frame_set(frame)
            check(
                all(
                    value == value
                    for bone in armature.pose.bones
                    for row in bone.matrix
                    for value in row
                ),
                f"Action produced a non-finite pose at frame {frame}",
            )

        eye_move = next(
            animation
            for animation in sources[0].animations
            if animation.name == "EyeMove"
        )
        check(
            len(eye_move.materials) == 1,
            "EyeMove lost its shader-parameter companion animation",
        )
        check(
            eye_move.materials[0].name == "EyeMove_fts",
            "EyeMove joined the wrong material animation",
        )
        eye_action = import_bfres_animation(
            armature,
            sources[0],
            eye_move,
        )
        check(
            eye_action["smo_has_material_animation"],
            "Material animation metadata is missing",
        )
        check(
            eye_action["smo_animated_material_count"] == 2,
            "EyeMove did not bind both eye materials",
        )
        check(
            eye_action["smo_animated_shader_parameter_count"] == 2,
            "EyeMove did not bind both tex_mtx0 parameters",
        )
        check(
            eye_action["smo_material_action_count"] == 2,
            "EyeMove did not create two material node-tree Actions",
        )
        animated_materials = tuple(
            material
            for material in bpy.data.materials
            if material.get("smo_material_animation_armature")
            == armature.name
            and material.node_tree is not None
            and material.node_tree.animation_data is not None
            and material.node_tree.animation_data.action is not None
        )
        check(
            len(animated_materials) == 2,
            f"Expected two localized eye materials, found "
            f"{len(animated_materials)}",
        )
        material_curves = tuple(
            curve
            for material in animated_materials
            for curve in node_tree_action_fcurves(
                material.node_tree,
                material.node_tree.animation_data.action,
            )
        )
        check(
            len(material_curves) == 12,
            f"Expected 12 TexSrt affine curves, found "
            f"{len(material_curves)}",
        )
        check(
            any(
                abs(
                    float(curve.keyframe_points[0].co.y)
                    - float(curve.keyframe_points[-1].co.y)
                )
                > 1e-6
                for curve in material_curves
            ),
            "EyeMove TexSrt animation was flattened",
        )

        shine_archive_path = romfs_root / "ObjectData" / "ShineTower.szs"
        shine_archive = read_szs(shine_archive_path)
        shine_entry = next(
            entry
            for entry in shine_archive.get_files()
            if entry.name == "ShineTower.bfres"
        )
        shine_models = read_static_bfres(
            bytes(shine_entry.data),
            include_rigging=True,
        )
        check(len(shine_models) == 1, "ShineTower model count changed")
        shine_model = shine_models[0]
        check(shine_model.skeleton is not None, "ShineTower has no test skeleton")
        shine_collection = bpy.data.collections.new(
            "SMO ShineTower Animation Regression"
        )
        bpy.context.scene.collection.children.link(shine_collection)
        shine_armature, _shine_bone_names = _create_armature_object(
            shine_collection,
            "ShineTower_Animation_Regression_Armature",
            shine_model.skeleton,
        )
        shine_armature["smo_armature_generated"] = True
        shine_armature["smo_source_archive"] = str(shine_archive_path)
        shine_armature["smo_source_bfres"] = shine_entry.name
        shine_armature["smo_source_model"] = shine_model.name
        shine_armature["smo_rig_key"] = "shine-tower-animation-regression"

        for source_material_name in ("WindowMT00", "WindowMT01"):
            color_mesh = bpy.data.meshes.new(
                f"SMO {source_material_name} Colour Animation Mesh"
            )
            color_mesh["smo_source_material_name"] = source_material_name
            color_object = bpy.data.objects.new(
                f"SMO {source_material_name} Colour Animation Object",
                color_mesh,
            )
            shine_collection.objects.link(color_object)
            color_object.parent = shine_armature
            color_object["smo_armature"] = shine_armature.name
            color_material = bpy.data.materials.new(
                f"SMO {source_material_name} Colour Animation Material"
            )
            color_material.use_nodes = True
            color_material["smo_source_material_name"] = (
                source_material_name
            )
            color_nodes = color_material.node_tree.nodes
            color_nodes.clear()

            for parameter_name, initial_value in (
                ("const_color3", (0.0, 0.0, 0.0, 0.0)),
                (
                    "base_color_mul_color",
                    (1.0, 1.0, 1.0, 1.0),
                ),
            ):
                node = color_nodes.new("ShaderNodeRGB")
                node.name = f"SMO Regression {parameter_name}"
                node.outputs["Color"].default_value = initial_value
                node["smo_shader_parameter"] = parameter_name
                node["smo_shader_parameter_binding"] = "COLOR_OUTPUT"

            color_mesh.materials.append(color_material)

        bpy.ops.object.select_all(action="DESELECT")
        shine_armature.select_set(True)
        bpy.context.view_layer.objects.active = shine_armature
        clear_bfres_animation_cache()
        shine_sources = _animation_sources(shine_armature)
        check(len(shine_sources) == 1, "ShineTower animation source is missing")
        check(
            len(shine_sources[0].skeletal_animations) == 26,
            "ShineTower skeletal animation count changed",
        )
        check(
            len(shine_sources[0].visibility_animations) == 24,
            "ShineTower bone visibility animation count changed",
        )
        check(
            len(shine_sources[0].animations) == 27,
            "ShineTower merged clip list lost skeletal or visibility clips",
        )
        shine_items = bfres_animation_enum_items(
            bpy.context.scene,
            bpy.context,
        )
        check(len(shine_items) == 27, "ShineTower sidebar list is incomplete")
        wait_world_map = next(
            animation
            for animation in shine_sources[0].animations
            if animation.name == "WaitWorldMap"
        )
        check(
            wait_world_map.skeletal is None
            and wait_world_map.visibility is not None,
            "ShineTower visibility-only clip was not retained",
        )
        wait_colour = next(
            animation
            for animation in shine_sources[0].animations
            if animation.name == "Wait"
        )
        check(
            len(wait_colour.materials) == 1
            and wait_colour.materials[0].name == "Wait_fcl",
            "ShineTower Wait lost its colour companion animation",
        )
        colour_action = import_bfres_animation(
            shine_armature,
            shine_sources[0],
            wait_colour,
        )
        check(
            colour_action["smo_animated_material_count"] == 2,
            "Wait_fcl did not bind both window materials",
        )
        check(
            colour_action["smo_animated_shader_parameter_count"] == 4,
            "Wait_fcl did not bind all four colour parameters",
        )
        check(
            colour_action["smo_skipped_shader_parameter_count"] == 0,
            "Wait_fcl skipped a translated colour parameter",
        )
        shine_animated_materials = tuple(
            material
            for material in bpy.data.materials
            if material.get("smo_material_animation_armature")
            == shine_armature.name
            and material.node_tree is not None
            and material.node_tree.animation_data is not None
            and material.node_tree.animation_data.action is not None
        )
        check(
            len(shine_animated_materials) == 2,
            "Wait_fcl did not create two localized material Actions",
        )
        colour_curves = tuple(
            curve
            for material in shine_animated_materials
            for curve in node_tree_action_fcurves(
                material.node_tree,
                material.node_tree.animation_data.action,
            )
        )
        check(
            len(colour_curves) == 16,
            f"Expected 16 RGBA curves, found {len(colour_curves)}",
        )
        colour_values = {
            round(float(point.co.y), 4)
            for curve in colour_curves
            for point in curve.keyframe_points
        }
        check(
            {0.1, 0.15, 0.19, 1.0, 2.0} <= colour_values,
            f"Wait_fcl constant colours were decoded incorrectly: "
            f"{sorted(colour_values)}",
        )

        scale_up_item = next(item for item in shine_items if item[1] == "ScaleUp")
        check(
            "3/4 bones on this rig" in scale_up_item[2],
            f"ScaleUp compatibility detail changed: {scale_up_item[2]!r}",
        )
        bpy.context.scene.smo_bfres_animation = scale_up_item[0]
        result = bpy.ops.smo.apply_bfres_animation()
        check(result == {"FINISHED"}, f"ShineTower ScaleUp returned {result}")
        shine_action = shine_armature.animation_data.action
        check(shine_action is not None, "ScaleUp created no active Action")
        check(
            shine_action["smo_animated_bone_count"] == 3,
            "ScaleUp did not apply all matching ShineTower tracks",
        )
        check(
            shine_action["smo_source_target_bone_count"] == 4,
            "ScaleUp source target count changed",
        )
        check(
            shine_action["smo_skipped_bone_count"] == 1,
            "ScaleUp missing companion track was not recorded",
        )
        check(
            json.loads(shine_action["smo_skipped_bones"]) == ["ShineNumber"],
            "ScaleUp skipped the wrong companion track",
        )
        shine_curves = action_fcurves(shine_armature, shine_action)
        check(
            len(shine_curves) == 39,
            f"Expected 39 partial-rig Action curves, found {len(shine_curves)}",
        )

        visibility_mesh = bpy.data.meshes.new(
            "SMO Visibility Regression Mesh"
        )
        visibility_object = bpy.data.objects.new(
            "SMO Visibility Regression Object",
            visibility_mesh,
        )
        shine_collection.objects.link(visibility_object)
        visibility_object.parent = shine_armature
        visibility_object["smo_armature"] = shine_armature.name
        visibility_object["smo_visibility_bone"] = "Tarap"
        crash_visibility = next(
            animation
            for animation in shine_sources[0].visibility_animations
            if animation.name == "DemoCrashHomeFall"
        )
        visibility_action = import_bfres_animation(
            shine_armature,
            shine_sources[0],
            AnimationClip(
                skeletal=None,
                visibility=crash_visibility,
            ),
        )
        check(
            visibility_action["smo_has_bone_visibility"],
            "Visibility Action metadata is missing",
        )
        check(
            visibility_action["smo_animated_visibility_bone_count"] == 4,
            "ShineTower visibility did not bind all four target bones",
        )
        check(
            visibility_action["smo_visibility_mesh_count"] == 1,
            "ShineTower visibility did not bind the controlled mesh",
        )
        visibility_curves = tuple(
            curve
            for curve in action_fcurves(
                shine_armature,
                visibility_action,
            )
            if curve.data_path.endswith('["smo_visible"]')
        )
        check(
            len(visibility_curves) == 4,
            f"Expected 4 visibility curves, found {len(visibility_curves)}",
        )
        check(
            all(
                point.interpolation == "CONSTANT"
                for curve in visibility_curves
                for point in curve.keyframe_points
            ),
            "Bone visibility channels are not step-interpolated",
        )
        bpy.context.scene.frame_set(0)
        bpy.context.view_layer.update()
        check(
            not visibility_object.hide_viewport
            and not visibility_object.hide_render,
            "Tarap mesh should be visible at frame 0",
        )
        bpy.context.scene.frame_set(1221)
        bpy.context.view_layer.update()
        check(
            visibility_object.hide_viewport
            and visibility_object.hide_render,
            "Tarap mesh did not hide at the FBVS transition",
        )

        mario_archive_path = romfs_root / "ObjectData" / "Mario.szs"
        mario_archive = read_szs(mario_archive_path)
        mario_entry = next(
            entry
            for entry in mario_archive.get_files()
            if entry.name == "Mario.bfres"
        )
        mario_models = read_static_bfres(
            bytes(mario_entry.data),
            include_rigging=True,
        )
        check(len(mario_models) == 1, "Mario model count changed")
        mario_model = mario_models[0]
        check(mario_model.skeleton is not None, "Mario has no test skeleton")
        mario_collection = bpy.data.collections.new(
            "SMO Mario External Animation Regression"
        )
        bpy.context.scene.collection.children.link(mario_collection)
        mario_armature, _mario_bone_names = _create_armature_object(
            mario_collection,
            "Mario_External_Animation_Regression_Armature",
            mario_model.skeleton,
        )
        mario_armature["smo_armature_generated"] = True
        mario_armature["smo_source_archive"] = str(mario_archive_path)
        mario_armature["smo_source_bfres"] = mario_entry.name
        mario_armature["smo_source_model"] = mario_model.name
        mario_armature["smo_rig_key"] = "mario-external-animation-regression"
        player_animation_path = (
            romfs_root / "ObjectData" / "PlayerAnimation.szs"
        )
        mario_armature.smo_bfres_animation_package = str(
            player_animation_path
        )
        check(
            not armature.smo_bfres_animation_package,
            "Animation package selection leaked to another armature",
        )
        bpy.ops.object.select_all(action="DESELECT")
        mario_armature.select_set(True)
        bpy.context.view_layer.objects.active = mario_armature
        clear_bfres_animation_cache()
        mario_sources = _animation_sources(mario_armature)
        check(len(mario_sources) == 1, "PlayerAnimation source is missing")
        check(
            mario_sources[0].archive_path == player_animation_path,
            "Per-armature package override was not used",
        )
        check(
            mario_sources[0].bfres_name == "PlayerAnimation.bfres",
            "External package retained the model's original BFRES filter",
        )
        check(
            len(mario_sources[0].skeletal_animations) == 511,
            "PlayerAnimation skeletal count changed",
        )
        check(
            len(mario_sources[0].visibility_animations) == 54,
            "PlayerAnimation visibility count changed",
        )
        check(
            len(mario_sources[0].animations) >= 500,
            "Mario rig rejected most external PlayerAnimation clips",
        )
        check(
            not any(
                animation.name in {"CapOff", "CapOn", "RaceManWait"}
                for animation in mario_sources[0].animations
            ),
            "External head-only visibility clips leaked onto Mario's body rig",
        )

        print(
            "BFRES_ANIMATION_REGRESSION: PASS "
            f"available={len(enum_items)} frames={wait.frame_count + 1} "
            f"bones={len(wait.bones)} curves={len(curves)} "
            f"scale_channels={sum(curve.data_path.endswith('.scale') for curve in curves)} "
            f"shine_tower_available={len(shine_items)} skipped_scale_up=1 "
            f"visibility_curves={len(visibility_curves)} "
            f"mario_external={len(mario_sources[0].animations)}"
        )
    finally:
        addon.get_addon_preferences = original_preferences

        if registered:
            addon.unregister()


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: bfres_animation_regression.py -- ROMFS")

    run(Path(arguments[0]).resolve())

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon
from smo_kingdom_importer.bfres_animation_import import (
    _animation_sources,
    clear_bfres_animation_cache,
    import_bfres_animation,
)
from smo_kingdom_importer.bfres_mesh import _Reader
from smo_kingdom_importer.world_list import read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


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
        archive_path = (
            romfs_root / "ObjectData" / "DemoHackFirstBackGround.szs"
        )
        result = bpy.ops.smo.import_test_model(
            filepath=str(archive_path),
            use_selected_stage_textures=False,
        )
        check(result == {"FINISHED"}, f"Model import returned {result}")
        armature = next(
            obj
            for obj in bpy.data.objects
            if obj.type == "ARMATURE"
            and obj.get("smo_armature_generated")
            and Path(str(obj.get("smo_source_archive"))) == archive_path
        )

        archive = read_szs(archive_path)
        bfres_entry = next(
            entry
            for entry in archive.get_files()
            if entry.name == "DemoHackFirstBackGround.bfres"
        )
        reader = _Reader(bytes(bfres_entry.data))
        check(reader.u16(190) == 3, "FSKA count changed")
        check(reader.u16(192) == 6, "FMAA count changed")
        check(reader.u16(194) == 3, "FBVS count changed")
        check(reader.u16(196) == 0, "Unexpected FSHA resources appeared")
        check(reader.u16(198) == 0, "Unexpected FSCN resources appeared")

        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        clear_bfres_animation_cache()
        sources = _animation_sources(armature)
        check(len(sources) == 1, f"Expected one source, found {len(sources)}")
        source = sources[0]
        check(len(source.skeletal_animations) == 3, "FSKA parsing changed")
        check(len(source.visibility_animations) == 3, "FBVS parsing changed")
        check(len(source.material_animations) == 6, "FMAA parsing changed")
        check(len(source.animations) == 4, "Merged clip inventory changed")

        clip = next(
            animation
            for animation in source.animations
            if animation.name == "DemoHackFirstBackGround"
        )
        check(
            clip.skeletal is None and clip.visibility is None,
            "Background clip unexpectedly gained bone tracks",
        )
        check(
            len(clip.materials) == 1
            and clip.materials[0].name == "DemoHackFirstBackGround_fts",
            "Background shader-parameter resource did not merge correctly",
        )
        targets = clip.materials[0].targets
        check(len(targets) == 8, "Background material target count changed")
        check(
            all(
                len(target.parameters) == 1
                and target.parameters[0].name == "tex_mtx0"
                for target in targets
            ),
            "Background FMAA no longer exclusively animates tex_mtx0",
        )

        action = import_bfres_animation(armature, source, clip)
        check(
            action["smo_animated_material_count"] == 8,
            "Background FMAA did not bind all eight materials",
        )
        check(
            action["smo_animated_shader_parameter_count"] == 8,
            "Background FMAA did not bind all eight tex_mtx0 parameters",
        )
        check(
            action["smo_skipped_shader_parameter_count"] == 0,
            "Background FMAA still skips tex_mtx0",
        )
        check(
            action["smo_material_action_count"] == 8,
            "Background FMAA did not create eight material Actions",
        )

        animated_materials = tuple(
            material
            for material in bpy.data.materials
            if material.get("smo_material_animation_armature") == armature.name
            and material.node_tree is not None
            and material.node_tree.animation_data is not None
            and material.node_tree.animation_data.action is not None
        )
        check(
            len(animated_materials) == 8,
            f"Expected eight animated materials, found {len(animated_materials)}",
        )
        curves = tuple(
            curve
            for material in animated_materials
            for curve in node_tree_action_fcurves(
                material.node_tree,
                material.node_tree.animation_data.action,
            )
        )
        check(len(curves) == 48, f"Expected 48 TexSrt curves, found {len(curves)}")
        check(
            any(
                abs(
                    float(curve.keyframe_points[0].co.y)
                    - float(curve.keyframe_points[-1].co.y)
                )
                > 1e-6
                for curve in curves
            ),
            "Background TexSrt animation was flattened",
        )
        check(
            all(
                any(
                    str(node.get("smo_shader_parameter") or "") == "tex_mtx0"
                    for node in material.node_tree.nodes
                )
                for material in animated_materials
            ),
            "Animated materials lack generated tex_mtx0 bindings",
        )

        print(
            "DEMO_HACK_FIRST_BACKGROUND_ANIMATION_REGRESSION: PASS "
            f"materials={len(animated_materials)} curves={len(curves)}"
        )
    finally:
        addon.get_addon_preferences = original_preferences
        if registered:
            addon.unregister()


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(arguments) != 1:
        raise SystemExit(
            "Usage: demo_hack_first_background_animation_regression.py -- ROMFS"
        )
    run(Path(arguments[0]).resolve())

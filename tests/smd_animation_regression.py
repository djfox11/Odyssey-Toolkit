from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

import bpy
from mathutils import Euler, Matrix


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon


BASIS = Matrix(
    (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
)
BASIS_INVERSE = BASIS.inverted()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def matrix_error(left: Matrix, right: Matrix) -> tuple[float, float]:
    translation = (left.translation - right.translation).length
    delta = left.to_quaternion().rotation_difference(right.to_quaternion())
    angle = abs(float(delta.angle)) % (2.0 * math.pi)
    rotation = min(angle, 2.0 * math.pi - angle)
    return translation, rotation


def source_local_matrix(
    bone: bpy.types.Bone,
) -> Matrix:
    parent_name = str(bone.get("smo_source_parent", "") or "")
    parent = bone.id_data.bones.get(parent_name)
    local = (
        parent.matrix_local.inverted_safe() @ bone.matrix_local
        if parent is not None
        else bone.matrix_local.copy()
    )
    source = BASIS_INVERSE @ local @ BASIS
    source.translation *= 100.0
    return source


def converted_local_matrix(source: Matrix) -> Matrix:
    converted = BASIS @ source @ BASIS_INVERSE
    converted.translation *= 0.01
    return converted


def smd_transform_line(bone_id: int, matrix: Matrix) -> str:
    euler = matrix.to_euler("XYZ")
    values = (*matrix.translation, *euler)
    return f"{bone_id} " + " ".join(f"{value:.9g}" for value in values)


def fixture_smd(
    armature: bpy.types.Object,
) -> tuple[str, dict[int, tuple[Matrix, ...]]]:
    bones = sorted(
        (
            bone
            for bone in armature.data.bones
            if "smo_bone_index" in bone
        ),
        key=lambda bone: int(bone["smo_bone_index"]),
    )
    ids = {bone.name: index for index, bone in enumerate(bones)}
    frame_zero = tuple(source_local_matrix(bone) for bone in bones)
    frame_one = [matrix.copy() for matrix in frame_zero]
    joint_root_index = ids["JointRoot"]
    head_index = ids["Head"]
    frame_one[joint_root_index].translation.y += 10.0
    frame_one[head_index] = (
        frame_one[head_index]
        @ Euler((0.0, 0.0, 0.15), "XYZ").to_matrix().to_4x4()
    )
    lines = ["version 1", "nodes"]

    for bone_id, bone in enumerate(bones):
        parent_name = str(bone.get("smo_source_parent", "") or "")
        parent_id = ids[parent_name] if parent_name else -1
        lines.append(f'{bone_id} "{bone.name}" {parent_id}')

    lines.extend(("end", "skeleton", "time 0"))
    lines.extend(
        smd_transform_line(bone_id, matrix)
        for bone_id, matrix in enumerate(frame_zero)
    )
    lines.append("time 1")
    lines.extend(
        smd_transform_line(bone_id, matrix)
        for bone_id, matrix in enumerate(frame_one)
    )
    lines.append("end")
    return "\n".join(lines) + "\n", {
        0: frame_zero,
        1: tuple(frame_one),
    }


def action_fcurves(
    armature: bpy.types.Object,
    action: bpy.types.Action,
) -> tuple[object, ...]:
    if bpy.app.version >= (4, 4, 0):
        slot = armature.animation_data.action_slot
        bag = action.layers[0].strips[0].channelbag(slot)
        return tuple(bag.fcurves)

    return tuple(action.fcurves)


def expected_pose(
    bones: tuple[bpy.types.Bone, ...],
    source_locals: tuple[Matrix, ...],
) -> dict[str, Matrix]:
    expected: dict[str, Matrix] = {}

    for bone, source in zip(bones, source_locals):
        local = converted_local_matrix(source)
        parent_name = str(bone.get("smo_source_parent", "") or "")
        expected[bone.name] = (
            expected[parent_name] @ local
            if parent_name
            else local
        )

    return expected


def run(romfs_root: Path) -> None:
    registered = False
    original_preferences = addon.get_addon_preferences
    fixture_path: Path | None = None

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
        result = bpy.ops.smo.import_test_model(
            filepath=str(romfs_root / "ObjectData" / "Kuribo.szs"),
            use_selected_stage_textures=False,
        )
        check(result == {"FINISHED"}, f"Kuribo import returned {result}")
        armature = next(
            obj
            for obj in bpy.data.objects
            if obj.type == "ARMATURE" and obj.get("smo_armature_generated")
        )
        bones = tuple(
            sorted(
                (
                    bone
                    for bone in armature.data.bones
                    if "smo_bone_index" in bone
                ),
                key=lambda bone: int(bone["smo_bone_index"]),
            )
        )
        fixture_text, source_frames = fixture_smd(armature)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".smd",
            delete=False,
        )

        try:
            handle.write(fixture_text)
            fixture_path = Path(handle.name)
        finally:
            handle.close()

        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        check(
            armature.data["smo_rest_matrix_revision"] == 4,
            "Corrected armature revision metadata is missing",
        )
        armature.data["smo_rest_matrix_revision"] = 1
        try:
            bpy.ops.smo.import_smd_animation(filepath=str(fixture_path))
        except RuntimeError as exc:
            check(
                "legacy incorrect rest-bone axes" in str(exc),
                f"Legacy armature import failed unexpectedly: {exc}",
            )
        else:
            raise AssertionError("Legacy armature import was not rejected")
        check(
            armature.animation_data is None
            or armature.animation_data.action is None,
            "Rejected legacy armature received an action",
        )
        armature.data["smo_rest_matrix_revision"] = 4
        result = bpy.ops.smo.import_smd_animation(filepath=str(fixture_path))
        check(result == {"FINISHED"}, f"SMD import returned {result}")
        action = armature.animation_data.action
        check(action is not None, "SMD import created no action")
        check(action["smo_animation_import"], "Action metadata is missing")
        check(action["smo_frame_count"] == 2, "Frame metadata changed")
        check(
            action["smo_animated_bone_count"] == len(bones) == 22,
            "Animated-bone metadata changed",
        )
        curves = action_fcurves(armature, action)
        check(len(curves) == len(bones) * 7, "Action channel count changed")
        check(
            all(
                point.interpolation == "LINEAR"
                for curve in curves
                for point in curve.keyframe_points
            ),
            "SMD samples are no longer linearly interpolated",
        )
        rotation_curves: dict[str, dict[int, object]] = {}

        for curve in curves:
            if curve.data_path.endswith(".rotation_quaternion"):
                rotation_curves.setdefault(curve.data_path, {})[
                    curve.array_index
                ] = curve

        for data_path, components in rotation_curves.items():
            frames = sorted(
                {
                    int(round(point.co.x))
                    for point in components[0].keyframe_points
                }
            )
            previous: tuple[float, ...] | None = None

            for frame in frames:
                current = tuple(
                    float(components[index].evaluate(frame))
                    for index in range(4)
                )

                if previous is not None:
                    check(
                        sum(a * b for a, b in zip(previous, current)) >= 0.0,
                        f"Quaternion sign discontinuity in {data_path}",
                    )

                previous = current

        maximum_translation_error = 0.0
        maximum_rotation_error = 0.0

        for frame, source_locals in source_frames.items():
            bpy.context.scene.frame_set(frame)
            expected = expected_pose(bones, source_locals)

            for pose_bone in armature.pose.bones:
                if "smo_bone_index" not in pose_bone.bone:
                    continue

                translation_error, rotation_error = matrix_error(
                    pose_bone.matrix,
                    expected[pose_bone.name],
                )
                maximum_translation_error = max(
                    maximum_translation_error,
                    translation_error,
                )
                maximum_rotation_error = max(
                    maximum_rotation_error,
                    rotation_error,
                )

        check(
            maximum_translation_error < 2e-5,
            f"Converted SMD position error is {maximum_translation_error}",
        )
        check(
            maximum_rotation_error < 2e-5,
            f"Converted SMD rotation error is {maximum_rotation_error}",
        )
        print(
            "SMD_ANIMATION_REGRESSION: PASS "
            f"bones={len(bones)} frames={len(source_frames)} "
            f"curves={len(curves)} "
            f"position_error={maximum_translation_error:.9f} "
            f"rotation_error={maximum_rotation_error:.9f}"
        )
    finally:
        addon.get_addon_preferences = original_preferences

        if fixture_path is not None and fixture_path.is_file():
            fixture_path.unlink()
        if registered:
            addon.unregister()


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: smd_animation_regression.py -- ROMFS")

    run(Path(arguments[0]).resolve())

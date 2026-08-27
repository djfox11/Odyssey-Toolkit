from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import shlex
from typing import Any

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from mathutils import Euler, Matrix


_GAME_TO_BLENDER_BASIS = Matrix(
    (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
)
_GAME_TO_BLENDER_BASIS_INVERSE = _GAME_TO_BLENDER_BASIS.inverted()
_TRANSLATION_SCALE = 0.01


class SMDAnimationError(ValueError):
    """An actionable Switch Toolbox SMD animation error."""


@dataclass(frozen=True, slots=True)
class SMDNode:
    bone_id: int
    name: str
    parent_id: int


@dataclass(frozen=True, slots=True)
class SMDAnimation:
    nodes: tuple[SMDNode, ...]
    frames: tuple[tuple[int, dict[int, Matrix]], ...]


def _parse_node(line: str, line_number: int) -> SMDNode:
    try:
        values = shlex.split(line, posix=True)
    except ValueError as exc:
        raise SMDAnimationError(
            f"Line {line_number}: invalid quoted bone name: {exc}"
        ) from exc

    if len(values) != 3:
        raise SMDAnimationError(
            f"Line {line_number}: expected a bone ID, quoted name and parent ID."
        )

    try:
        bone_id = int(values[0])
        parent_id = int(values[2])
    except ValueError as exc:
        raise SMDAnimationError(
            f"Line {line_number}: bone and parent IDs must be integers."
        ) from exc

    name = values[1].strip()

    if bone_id < 0 or not name:
        raise SMDAnimationError(
            f"Line {line_number}: bone IDs must be non-negative and names non-empty."
        )

    return SMDNode(bone_id, name, parent_id)


def _parse_transform(line: str, line_number: int) -> tuple[int, Matrix]:
    values = line.split()

    if len(values) != 7:
        raise SMDAnimationError(
            f"Line {line_number}: expected bone ID, XYZ position and XYZ rotation."
        )

    try:
        bone_id = int(values[0])
        components = tuple(float(value) for value in values[1:])
    except ValueError as exc:
        raise SMDAnimationError(
            f"Line {line_number}: transform values must be numeric."
        ) from exc

    if bone_id < 0 or not all(math.isfinite(value) for value in components):
        raise SMDAnimationError(
            f"Line {line_number}: transform values must be finite."
        )

    px, py, pz, rx, ry, rz = components
    source = Matrix.Translation((px, py, pz)) @ Euler(
        (rx, ry, rz),
        "XYZ",
    ).to_matrix().to_4x4()
    converted = (
        _GAME_TO_BLENDER_BASIS
        @ source
        @ _GAME_TO_BLENDER_BASIS_INVERSE
    )
    converted.translation *= _TRANSLATION_SCALE
    return bone_id, converted


def read_switch_toolbox_smd(path: Path) -> SMDAnimation:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SMDAnimationError(f"Could not read {path.name}: {exc}") from exc

    nodes: list[SMDNode] = []
    frames: list[tuple[int, dict[int, Matrix]]] = []
    section = ""
    current_time: int | None = None
    current_transforms: dict[int, Matrix] | None = None
    saw_version = False
    saw_geometry = False

    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()

        if not line or line.startswith(("#", ";", "//")):
            continue

        if not saw_version:
            if line != "version 1":
                raise SMDAnimationError(
                    f"Line {line_number}: expected SMD 'version 1'."
                )
            saw_version = True
            continue

        if line in {"nodes", "skeleton", "triangles", "vertexanimation"}:
            section = line
            saw_geometry = saw_geometry or section in {
                "triangles",
                "vertexanimation",
            }
            continue

        if line == "end":
            section = ""
            current_time = None
            current_transforms = None
            continue

        if section == "nodes":
            nodes.append(_parse_node(line, line_number))
            continue

        if section == "skeleton":
            if line.startswith("time "):
                values = line.split()

                if len(values) != 2:
                    raise SMDAnimationError(
                        f"Line {line_number}: invalid SMD time marker."
                    )

                try:
                    current_time = int(values[1])
                except ValueError as exc:
                    raise SMDAnimationError(
                        f"Line {line_number}: frame time must be an integer."
                    ) from exc

                if current_time < 0:
                    raise SMDAnimationError(
                        f"Line {line_number}: frame time must be non-negative."
                    )
                if frames and current_time <= frames[-1][0]:
                    raise SMDAnimationError(
                        f"Line {line_number}: frame times must increase."
                    )

                current_transforms = {}
                frames.append((current_time, current_transforms))
                continue

            if current_time is None or current_transforms is None:
                raise SMDAnimationError(
                    f"Line {line_number}: bone transform appears before a time marker."
                )

            bone_id, matrix = _parse_transform(line, line_number)

            if bone_id in current_transforms:
                raise SMDAnimationError(
                    f"Line {line_number}: bone {bone_id} is repeated in frame "
                    f"{current_time}."
                )

            current_transforms[bone_id] = matrix

    if not saw_version:
        raise SMDAnimationError("The file is empty or has no SMD version header.")
    if saw_geometry:
        raise SMDAnimationError(
            "This SMD contains model or vertex-animation data; export an "
            "animation-only SMD from Switch Toolbox."
        )
    if not nodes:
        raise SMDAnimationError("The SMD contains no bone nodes.")
    if not frames:
        raise SMDAnimationError("The SMD contains no skeletal animation frames.")

    node_ids = [node.bone_id for node in nodes]
    node_names = [node.name for node in nodes]

    if len(set(node_ids)) != len(node_ids):
        raise SMDAnimationError("The SMD contains duplicate bone IDs.")
    if len(set(node_names)) != len(node_names):
        raise SMDAnimationError("The SMD contains duplicate bone names.")

    id_set = set(node_ids)

    for node in nodes:
        if node.parent_id != -1 and node.parent_id not in id_set:
            raise SMDAnimationError(
                f"Bone {node.name!r} references missing parent ID {node.parent_id}."
            )

    animated_ids = set(frames[0][1])

    if not animated_ids:
        raise SMDAnimationError("The first SMD frame contains no bone transforms.")
    if not animated_ids <= id_set:
        missing = sorted(animated_ids - id_set)
        raise SMDAnimationError(
            f"The first frame references undefined bone IDs: {missing}."
        )

    for frame_time, transforms in frames[1:]:
        frame_ids = set(transforms)

        if frame_ids != animated_ids:
            missing = sorted(animated_ids - frame_ids)
            extra = sorted(frame_ids - animated_ids)
            raise SMDAnimationError(
                f"Frame {frame_time} has inconsistent animation tracks "
                f"(missing={missing}, extra={extra})."
            )

    return SMDAnimation(tuple(nodes), tuple(frames))


def _target_armature(context: bpy.types.Context) -> bpy.types.Object | None:
    active = getattr(context, "active_object", None)

    if active is None:
        return None
    if active.type == "ARMATURE":
        return active
    if active.type == "MESH":
        return active.find_armature()

    return None


def _validated_node_order(
    animation: SMDAnimation,
    armature: bpy.types.Object,
) -> tuple[tuple[SMDNode, bpy.types.PoseBone], ...]:
    if not armature.get("smo_armature_generated"):
        raise SMDAnimationError(
            "Select an armature generated by the SMO Kingdom Importer."
        )
    if int(armature.data.get("smo_rest_matrix_revision", 0)) < 2:
        raise SMDAnimationError(
            "This armature uses legacy incorrect rest-bone axes. Re-import "
            "the model with SMO Kingdom Importer 0.35.3 or newer, then "
            "import the animation again."
        )

    pose_bones = armature.pose.bones
    source_pose_bones = {
        bone.name: bone
        for bone in pose_bones
        if "smo_bone_index" in bone.bone
    }
    nodes_by_id = {node.bone_id: node for node in animation.nodes}
    source_names = {node.name for node in animation.nodes}
    target_names = set(source_pose_bones)

    if source_names != target_names:
        missing = sorted(target_names - source_names)
        extra = sorted(source_names - target_names)
        raise SMDAnimationError(
            "The SMD skeleton does not match the selected armature "
            f"(missing={missing}, extra={extra})."
        )

    ordered: list[tuple[SMDNode, bpy.types.PoseBone]] = []
    pending = {node.bone_id for node in animation.nodes}
    completed: set[int] = set()

    while pending:
        progressed = False

        for node in animation.nodes:
            if node.bone_id not in pending:
                continue
            if node.parent_id != -1 and node.parent_id not in completed:
                continue

            pose_bone = source_pose_bones[node.name]
            source_parent = (
                nodes_by_id[node.parent_id].name
                if node.parent_id != -1
                else None
            )
            target_parent = (
                str(pose_bone.bone.get("smo_source_parent", "") or "")
                or None
            )

            if source_parent != target_parent:
                raise SMDAnimationError(
                    f"Bone {node.name!r} has parent {source_parent!r} in the "
                    f"SMD but {target_parent!r} in the selected armature."
                )

            ordered.append((node, pose_bone))
            pending.remove(node.bone_id)
            completed.add(node.bone_id)
            progressed = True

        if not progressed:
            cycle_names = sorted(nodes_by_id[bone_id].name for bone_id in pending)
            raise SMDAnimationError(
                f"The SMD bone hierarchy contains a cycle: {cycle_names}."
            )

    return tuple(ordered)


def _new_action_channels(
    armature: bpy.types.Object,
    action_name: str,
) -> tuple[bpy.types.Action, Any, Any]:
    animation_data = armature.animation_data_create()
    action = bpy.data.actions.new(action_name)
    action.use_fake_user = True
    animation_data.action = action

    if bpy.app.version >= (4, 4, 0):
        slot = action.slots.new(id_type="OBJECT", name=armature.name)
        animation_data.action_slot = slot
        layer = action.layers.new(action_name)
        strip = layer.strips.new(type="KEYFRAME")
        channel_bag = strip.channelbag(slot, ensure=True)
        return action, channel_bag.fcurves, channel_bag.groups

    return action, action.fcurves, action.groups


def _write_curve(
    fcurves: Any,
    group: Any,
    data_path: str,
    index: int,
    values: tuple[tuple[int, float], ...],
) -> None:
    curve = fcurves.new(data_path=data_path, index=index)
    curve.group = group
    curve.keyframe_points.add(len(values))

    for point, (frame, value) in zip(curve.keyframe_points, values):
        point.co = (float(frame), value)
        point.interpolation = "LINEAR"

    curve.update()


def import_switch_toolbox_animation(
    armature: bpy.types.Object,
    animation: SMDAnimation,
    action_name: str,
) -> bpy.types.Action:
    ordered = _validated_node_order(animation, armature)
    pose_bones = armature.pose.bones
    animated_ids = set(animation.frames[0][1])
    animated = tuple(
        (node, pose_bone)
        for node, pose_bone in ordered
        if node.bone_id in animated_ids
    )
    original_basis = {
        pose_bone.name: pose_bone.matrix_basis.copy()
        for pose_bone in armature.pose.bones
    }
    previous_quaternions: dict[str, Any] = {}
    samples: dict[
        str,
        dict[str, list[list[tuple[int, float]]]],
    ] = {
        pose_bone.name: {
            "location": [[], [], []],
            "rotation_quaternion": [[], [], [], []],
        }
        for _node, pose_bone in animated
    }

    try:
        for frame_time, transforms in animation.frames:
            for pose_bone in armature.pose.bones:
                pose_bone.matrix_basis.identity()
                pose_bone.rotation_mode = "QUATERNION"

            for node, pose_bone in ordered:
                local = transforms.get(node.bone_id)

                if local is None:
                    continue

                source_parent_name = str(
                    pose_bone.bone.get("smo_source_parent", "") or ""
                )
                source_parent = pose_bones.get(source_parent_name)
                pose_bone.matrix = (
                    source_parent.matrix @ local
                    if source_parent is not None
                    else local
                )

            for _node, pose_bone in animated:
                bone_samples = samples[pose_bone.name]

                for index, value in enumerate(pose_bone.location):
                    bone_samples["location"][index].append(
                        (frame_time, float(value))
                    )
                rotation = pose_bone.rotation_quaternion.copy()
                previous_rotation = previous_quaternions.get(pose_bone.name)

                if (
                    previous_rotation is not None
                    and rotation.dot(previous_rotation) < 0.0
                ):
                    rotation.negate()

                previous_quaternions[pose_bone.name] = rotation

                for index, value in enumerate(rotation):
                    bone_samples["rotation_quaternion"][index].append(
                        (frame_time, float(value))
                    )
    except Exception:
        for pose_bone in armature.pose.bones:
            pose_bone.matrix_basis = original_basis[pose_bone.name]
        raise

    animation_data = armature.animation_data
    previous_action = animation_data.action if animation_data is not None else None
    previous_slot = (
        animation_data.action_slot
        if animation_data is not None and bpy.app.version >= (4, 4, 0)
        else None
    )

    try:
        action, fcurves, groups = _new_action_channels(armature, action_name)
    except Exception:
        for pose_bone in armature.pose.bones:
            pose_bone.matrix_basis = original_basis[pose_bone.name]
        raise

    try:
        for _node, pose_bone in animated:
            group = groups.new(name=pose_bone.name)
            escaped_name = bpy.utils.escape_identifier(pose_bone.name)
            base_path = f'pose.bones["{escaped_name}"].'
            bone_samples = samples[pose_bone.name]

            for data_name, component_samples in bone_samples.items():
                for index, values in enumerate(component_samples):
                    _write_curve(
                        fcurves,
                        group,
                        base_path + data_name,
                        index,
                        tuple(values),
                    )

        action["smo_animation_import"] = True
        action["smo_source_smd"] = str(action_name)
        action["smo_target_armature"] = armature.name
        action["smo_frame_count"] = len(animation.frames)
        action["smo_animated_bone_count"] = len(animated)
        action["smo_translation_scale"] = _TRANSLATION_SCALE
        action["smo_coordinate_basis"] = "(X, Y, Z) -> (X, -Z, Y)"
    except Exception:
        animation_data = armature.animation_data

        if animation_data is not None and animation_data.action is action:
            animation_data.action = previous_action

            if previous_action is not None and previous_slot is not None:
                animation_data.action_slot = previous_slot

        bpy.data.actions.remove(action)

        for pose_bone in armature.pose.bones:
            pose_bone.matrix_basis = original_basis[pose_bone.name]

        raise

    first_frame = animation.frames[0][0]
    last_frame = animation.frames[-1][0]
    scene = bpy.context.scene
    scene.frame_start = first_frame
    scene.frame_end = last_frame
    scene.frame_set(first_frame)
    return action


class SMO_OT_import_smd_animation(Operator):
    bl_idname = "smo.import_smd_animation"
    bl_label = "Import Switch Toolbox Animation"
    bl_description = (
        "Import a Switch Toolbox animation-only SMD onto the selected "
        "SMO-generated armature"
    )
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.smd", options={"HIDDEN"})

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        armature = _target_armature(context)

        if armature is None:
            cls.poll_message_set("Select an imported Odyssey armature or rigged mesh")
            return False
        if not armature.get("smo_armature_generated"):
            cls.poll_message_set(
                "The selected armature was not generated by this add-on"
            )
            return False

        return True

    def invoke(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        armature = _target_armature(context)

        if armature is None:
            self.report({"ERROR"}, "Select an imported Odyssey armature or rigged mesh.")
            return {"CANCELLED"}

        path = Path(bpy.path.abspath(self.filepath)).expanduser().resolve()

        if path.suffix.casefold() != ".smd":
            self.report({"ERROR"}, "Choose an animation-only .smd file.")
            return {"CANCELLED"}

        try:
            animation = read_switch_toolbox_smd(path)
            action = import_switch_toolbox_animation(
                armature,
                animation,
                path.stem,
            )
            action["smo_source_smd"] = str(path)
        except Exception as exc:
            print(f"[Odyssey Toolkit] SMD animation import failed: {exc}")
            self.report({"ERROR"}, f"Could not import SMD animation: {exc}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Imported {len(animation.frames)} frames for "
                f"{action['smo_animated_bone_count']} bones as {action.name}."
            ),
        )
        return {"FINISHED"}


def draw_smd_import_menu(
    self: bpy.types.Menu,
    context: bpy.types.Context,
) -> None:
    self.layout.operator(
        SMO_OT_import_smd_animation.bl_idname,
        text="Super Mario Odyssey Animation (.smd)",
    )

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import traceback
from typing import Any

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel
from mathutils import Euler, Matrix, Quaternion, Vector

from .bfres_animation import (
    BFRESAnimationError,
    BoneAnimation,
    BoneVisibilityAnimation,
    BoneVisibilityTarget,
    MaterialAnimation,
    MaterialParameterAnimation,
    SkeletalAnimation,
    read_bone_visibility_animations,
    read_material_animations,
    read_skeletal_animations,
)
from .smd_animation import (
    _GAME_TO_BLENDER_BASIS,
    _GAME_TO_BLENDER_BASIS_INVERSE,
    _TRANSLATION_SCALE,
    _new_action_channels,
    _target_armature,
    _write_curve,
)


@dataclass(frozen=True, slots=True)
class AnimationClip:
    skeletal: SkeletalAnimation | None
    visibility: BoneVisibilityAnimation | None
    materials: tuple[MaterialAnimation, ...] = ()

    @property
    def name(self) -> str:
        animation = self.skeletal or self.visibility

        if animation is not None:
            return animation.name
        if self.materials:
            return _material_clip_name(self.materials[0].name)

        raise BFRESAnimationError("Animation clip contains no resources.")

    @property
    def frame_count(self) -> int:
        return max(
            (
                animation.frame_count
                for animation in (
                    self.skeletal,
                    self.visibility,
                    *self.materials,
                )
                if animation is not None
            ),
            default=0,
        )

    @property
    def looping(self) -> bool:
        return any(
            animation.looping
            for animation in (
                self.skeletal,
                self.visibility,
                *self.materials,
            )
            if animation is not None
        )

    @property
    def euler_xyz(self) -> bool:
        return bool(self.skeletal and self.skeletal.euler_xyz)

    @property
    def bones(self) -> tuple[BoneAnimation, ...]:
        return self.skeletal.bones if self.skeletal is not None else ()


_SCALE_INHERITANCE_PROPERTY = "smo_bone_scale_inheritance"
_ACTIVE_SCALE_INHERITANCE_PROPERTY = "smo_active_scale_inheritance_action"
_VALID_SCALE_INHERITANCE_MODES = {"FULL", "NONE"}
_SCALE_COMPENSATION_HELPER_PROPERTY = "smo_scale_compensation_helper"
_SOURCE_PARENT_PROPERTY = "smo_source_parent"


def _apply_action_scale_inheritance(
    armature: bpy.types.Object,
    action: bpy.types.Action,
) -> bool:
    payload = str(action.get(_SCALE_INHERITANCE_PROPERTY, "") or "")

    if not payload or armature.type != "ARMATURE":
        return False

    marker = f"{action.name_full}:{action.as_pointer()}"

    if armature.get(_ACTIVE_SCALE_INHERITANCE_PROPERTY) == marker:
        return False

    try:
        modes = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False

    if not isinstance(modes, dict):
        return False

    changed = False

    for bone_name, mode in modes.items():
        if mode not in _VALID_SCALE_INHERITANCE_MODES:
            continue

        bone = armature.data.bones.get(str(bone_name))

        if bone is not None and bone.inherit_scale != mode:
            bone.inherit_scale = mode
            changed = True

    armature[_ACTIVE_SCALE_INHERITANCE_PROPERTY] = marker
    return changed


@persistent
def sync_active_action_scale_inheritance(
    _scene: bpy.types.Scene | None = None,
    _depsgraph: bpy.types.Depsgraph | None = None,
) -> None:
    for armature in bpy.data.objects:
        if armature.type != "ARMATURE" or armature.animation_data is None:
            continue

        action = armature.animation_data.action

        if action is not None:
            _apply_action_scale_inheritance(armature, action)


@dataclass(frozen=True, slots=True)
class AnimationSource:
    archive_path: Path
    bfres_name: str
    data: bytes
    skeletal_animations: tuple[SkeletalAnimation, ...]
    visibility_animations: tuple[BoneVisibilityAnimation, ...]
    animations: tuple[AnimationClip, ...]
    material_animations: tuple[MaterialAnimation, ...] = ()


_ANIMATION_SOURCE_CACHE: dict[
    tuple[
        str,
        int,
        int,
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ],
    tuple[AnimationSource, ...],
] = {}
_ENUM_ITEM_CACHE: dict[
    tuple[str, str],
    tuple[tuple[str, str, str, str, int], ...],
] = {}


def _source_archive_path(armature: bpy.types.Object) -> Path:
    override_path = str(
        getattr(armature, "smo_bfres_animation_package", "") or ""
    ).strip()
    raw_path = override_path or str(
        armature.get("smo_source_archive")
        or armature.get("smo_resource_archive")
        or ""
    )
    parent = armature.parent

    while not raw_path and parent is not None:
        raw_path = str(
            parent.get("smo_source_archive")
            or parent.get("smo_source_file")
            or parent.get("smo_resource_archive")
            or ""
        )
        parent = parent.parent

    if not raw_path:
        raise BFRESAnimationError(
            "The selected armature has no source archive metadata. Re-import "
            "the model with the current add-on version."
        )

    archive_path = Path(bpy.path.abspath(raw_path)).expanduser().resolve()

    if not archive_path.is_file():
        label = (
            "selected animation package"
            if override_path
            else "source archive"
        )
        raise BFRESAnimationError(
            f"The {label} no longer exists: {archive_path}"
        )

    return archive_path


def _source_bfres_names(armature: bpy.types.Object) -> tuple[str, ...]:
    if str(
        getattr(armature, "smo_bfres_animation_package", "") or ""
    ).strip():
        return ()

    exact_name = str(armature.get("smo_source_bfres") or "").strip()

    if exact_name:
        return (exact_name,)

    raw_names = armature.get("smo_bfres_files")

    if raw_names:
        try:
            names = tuple(str(name) for name in json.loads(str(raw_names)))
        except (TypeError, ValueError, json.JSONDecodeError):
            names = ()

        if names:
            return names

    return ()


def _material_mesh_objects(
    armature: bpy.types.Object,
) -> tuple[bpy.types.Object, ...]:
    return tuple(
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH"
        and (
            obj.parent is armature
            or str(obj.get("smo_armature") or "") == armature.name
        )
    )


def _source_material_names(armature: bpy.types.Object) -> set[str]:
    names = set()

    for obj in _material_mesh_objects(armature):
        mesh_name = str(
            obj.data.get("smo_source_material_name") or ""
        ).strip()
        if mesh_name:
            names.add(mesh_name)

        for slot in obj.material_slots:
            material = slot.material
            if material is None:
                continue
            material_name = str(
                material.get("smo_source_material_name") or ""
            ).strip()
            if material_name:
                names.add(material_name)

    return names


def _material_clip_name(name: str) -> str:
    lowered = name.casefold()
    for suffix in ("_fts", "_fcl", "_fst", "_fma"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _combine_animation_clips(
    skeletal_animations: tuple[SkeletalAnimation, ...],
    visibility_animations: tuple[BoneVisibilityAnimation, ...],
    material_animations: tuple[MaterialAnimation, ...],
    target_bones: set[str],
    target_materials: set[str],
) -> tuple[AnimationClip, ...]:
    skeletal_by_name = {
        animation.name: animation for animation in skeletal_animations
    }
    visibility_by_name = {
        animation.name: animation for animation in visibility_animations
    }
    materials_by_name: dict[str, list[MaterialAnimation]] = {}

    for animation in material_animations:
        if not any(target.parameters for target in animation.targets):
            continue
        materials_by_name.setdefault(
            _material_clip_name(animation.name), []
        ).append(animation)

    names = [animation.name for animation in skeletal_animations]
    names.extend(
        animation.name
        for animation in visibility_animations
        if animation.name not in skeletal_by_name
    )
    names.extend(
        name
        for name in materials_by_name
        if name not in skeletal_by_name and name not in visibility_by_name
    )
    clips = []

    for name in names:
        skeletal = skeletal_by_name.get(name)
        visibility = visibility_by_name.get(name)
        materials = tuple(materials_by_name.get(name, ()))
        transform_targets = (
            {bone.name for bone in skeletal.bones}
            if skeletal is not None
            else set()
        )
        visibility_targets = (
            {target.name for target in visibility.targets}
            if visibility is not None
            else set()
        )
        material_targets = {
            target.name
            for animation in materials
            for target in animation.targets
            if target.parameters
        }

        if (
            (transform_targets | visibility_targets) & target_bones
            or material_targets & target_materials
        ):
            clips.append(
                AnimationClip(
                    skeletal=skeletal,
                    visibility=visibility,
                    materials=materials,
                )
            )

    return tuple(clips)


def _animation_sources(armature: bpy.types.Object) -> tuple[AnimationSource, ...]:
    archive_path = _source_archive_path(armature)
    requested_names = _source_bfres_names(armature)
    stat = archive_path.stat()
    target_bones = {bone.name for bone in armature.data.bones}
    target_materials = _source_material_names(armature)
    cache_key = (
        str(archive_path).casefold(),
        stat.st_mtime_ns,
        stat.st_size,
        tuple(name.casefold() for name in requested_names),
        tuple(sorted(target_bones)),
        tuple(sorted(target_materials)),
    )
    cached = _ANIMATION_SOURCE_CACHE.get(cache_key)

    if cached is not None:
        return cached

    if archive_path.suffix.casefold() == ".bfres":
        candidates = ((archive_path.name, archive_path.read_bytes()),)
    elif archive_path.suffix.casefold() == ".szs":
        from .world_list import read_szs

        requested = {name.casefold() for name in requested_names}
        archive = read_szs(archive_path)
        candidates = tuple(
            (entry.name, bytes(entry.data))
            for entry in archive.get_files()
            if entry.name
            and Path(entry.name).suffix.casefold() == ".bfres"
            and (not requested or entry.name.casefold() in requested)
        )
    else:
        raise BFRESAnimationError(
            f"Unsupported armature source file: {archive_path.name}"
        )

    if not candidates:
        requested_text = ", ".join(requested_names) or "a BFRES file"
        raise BFRESAnimationError(
            f"{archive_path.name} does not contain {requested_text}."
        )

    sources = []

    for bfres_name, data in candidates:
        skeletal_animations = read_skeletal_animations(data)
        visibility_animations = read_bone_visibility_animations(data)
        material_animations = read_material_animations(data)
        animations = _combine_animation_clips(
            skeletal_animations,
            visibility_animations,
            material_animations,
            target_bones,
            target_materials,
        )

        if animations:
            sources.append(
                AnimationSource(
                    archive_path=archive_path,
                    bfres_name=bfres_name,
                    data=data,
                    skeletal_animations=skeletal_animations,
                    visibility_animations=visibility_animations,
                    material_animations=material_animations,
                    animations=animations,
                )
            )

    result = tuple(sources)
    _ANIMATION_SOURCE_CACHE[cache_key] = result
    return result


def _animation_identifier(source_index: int, animation_index: int) -> str:
    return f"{source_index}:{animation_index}"


def _selected_animation(
    armature: bpy.types.Object,
    identifier: str,
) -> tuple[AnimationSource, AnimationClip]:
    try:
        source_index, animation_index = (
            int(value) for value in identifier.split(":", 1)
        )
        source = _animation_sources(armature)[source_index]
        return source, source.animations[animation_index]
    except (ValueError, IndexError) as exc:
        raise BFRESAnimationError(
            "Choose an available animation from the list."
        ) from exc


def bfres_animation_enum_items(
    _scene: bpy.types.Scene,
    context: bpy.types.Context | None,
) -> tuple[tuple[str, str, str, str, int], ...]:
    armature = _target_armature(context) if context is not None else None

    if armature is None or not armature.get("smo_armature_generated"):
        return ()

    cache_key = (armature.name, str(armature.get("smo_rig_key") or ""))

    try:
        sources = _animation_sources(armature)
    except Exception:
        return ()

    items = []

    armature_names = {bone.name for bone in armature.data.bones}
    material_names = _source_material_names(armature)

    for source_index, source in enumerate(sources):
        for animation_index, animation in enumerate(source.animations):
            description_parts = [
                f"{animation.frame_count + 1} frames",
            ]

            if animation.skeletal is not None:
                target_names = {
                    bone.name for bone in animation.skeletal.bones
                }
                matching_count = len(target_names & armature_names)
                description_parts.append(
                    f"{matching_count}/{len(target_names)} bones on this rig"
                )

            if animation.visibility is not None:
                visibility_names = {
                    target.name for target in animation.visibility.targets
                }
                matching_visibility = len(
                    visibility_names & armature_names
                )
                description_parts.append(
                    f"{matching_visibility}/{len(visibility_names)} "
                    "visibility bones"
                )

            if animation.materials:
                target_names = {
                    target.name
                    for material_animation in animation.materials
                    for target in material_animation.targets
                    if target.parameters
                }
                parameter_count = sum(
                    len(target.parameters)
                    for material_animation in animation.materials
                    for target in material_animation.targets
                    if target.name in material_names
                )
                description_parts.append(
                    f"{len(target_names & material_names)}/"
                    f"{len(target_names)} materials, "
                    f"{parameter_count} shader parameters"
                )

            description = ", ".join(description_parts)

            if len(sources) > 1:
                description += f"; {source.bfres_name}"

            icon = (
                "FILE_TICK"
                if animation.looping
                else "ACTION"
                if animation.skeletal is not None
                else "HIDE_OFF"
                if animation.visibility is not None
                else "MATERIAL"
            )
            items.append(
                (
                    _animation_identifier(source_index, animation_index),
                    animation.name,
                    description,
                    icon,
                    len(items),
                )
            )

    result = tuple(items)
    _ENUM_ITEM_CACHE[cache_key] = result
    return result


def clear_bfres_animation_cache() -> None:
    _ANIMATION_SOURCE_CACHE.clear()
    _ENUM_ITEM_CACHE.clear()


def _rest_transform(
    pose_bone: bpy.types.PoseBone,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float],
    bool,
]:
    bone = pose_bone.bone
    raw_scale = bone.get("smo_rest_scale")
    raw_rotation = bone.get("smo_rest_rotation")
    raw_translation = bone.get("smo_rest_translation")

    if raw_scale is not None and raw_rotation is not None and raw_translation is not None:
        return (
            tuple(float(value) for value in raw_scale),
            tuple(float(value) for value in raw_rotation),
            tuple(float(value) for value in raw_translation),
            bool(bone.get("smo_rest_euler_xyz")),
        )

    local = (
        bone.parent.matrix_local.inverted_safe() @ bone.matrix_local
        if bone.parent is not None
        else bone.matrix_local.copy()
    )
    source = _GAME_TO_BLENDER_BASIS_INVERSE @ local @ _GAME_TO_BLENDER_BASIS
    source.translation /= _TRANSLATION_SCALE
    scale = tuple(float(value) for value in source.to_scale())
    euler_xyz = bool(int(bone.get("smo_fskl_flags", 0)) & (1 << 12))

    if euler_xyz:
        euler = source.to_euler("XYZ")
        rotation = (float(euler.x), float(euler.y), float(euler.z), 0.0)
    else:
        quaternion = source.to_quaternion().normalized()
        rotation = (
            float(quaternion.x),
            float(quaternion.y),
            float(quaternion.z),
            float(quaternion.w),
        )

    return (
        scale,
        rotation,
        tuple(float(value) for value in source.translation),
        euler_xyz,
    )


def _curve_value(
    bone_animation: BoneAnimation,
    data_name: str,
    frame: float,
    fallback: float,
) -> float:
    curve = bone_animation.curve(data_name)
    return curve.evaluate(frame) if curve is not None else fallback


def _sample_quaternion(
    bone_animation: BoneAnimation,
    frame: float,
    fallback: tuple[float, float, float, float],
) -> Quaternion:
    curves = tuple(
        bone_animation.curve(f"rotate_{component}")
        for component in "xyzw"
    )

    if all(curve is not None for curve in curves):
        typed_curves = tuple(curve for curve in curves if curve is not None)
        left_index, right_index = typed_curves[0].surrounding_key_indices(frame)
        left = Quaternion(
            (
                typed_curves[3].key_value(left_index),
                typed_curves[0].key_value(left_index),
                typed_curves[1].key_value(left_index),
                typed_curves[2].key_value(left_index),
            )
        ).normalized()

        if left_index == right_index:
            return left

        left_frame = typed_curves[0].frames[left_index]
        right_frame = typed_curves[0].frames[right_index]
        right = Quaternion(
            (
                typed_curves[3].key_value(right_index),
                typed_curves[0].key_value(right_index),
                typed_curves[1].key_value(right_index),
                typed_curves[2].key_value(right_index),
            )
        ).normalized()
        factor = (frame - left_frame) / (right_frame - left_frame)
        return left.slerp(right, factor)

    x, y, z, w = (
        _curve_value(bone_animation, f"rotate_{component}", frame, value)
        for component, value in zip("xyzw", fallback)
    )
    result = Quaternion((w, x, y, z))
    return result.normalized() if result.length_squared else Quaternion()


def _sample_local_matrix(
    bone_animation: BoneAnimation,
    frame: float,
    rest_transform: tuple[
        tuple[float, float, float],
        tuple[float, float, float, float],
        tuple[float, float, float],
        bool,
    ],
    euler_xyz: bool,
) -> tuple[Matrix, tuple[float, float, float]]:
    rest_scale, rest_rotation, rest_translation, _rest_euler = rest_transform
    scale_fallback = bone_animation.base_scale or (1.0, 1.0, 1.0)
    rotation_fallback = bone_animation.base_rotation or rest_rotation
    translation_fallback = bone_animation.base_translation or rest_translation
    scale = tuple(
        _curve_value(
            bone_animation,
            f"scale_{component}",
            frame,
            value,
        )
        for component, value in zip("xyz", scale_fallback)
    )
    translation = tuple(
        _curve_value(
            bone_animation,
            f"translate_{component}",
            frame,
            value,
        )
        for component, value in zip("xyz", translation_fallback)
    )

    if euler_xyz:
        rotation_values = tuple(
            _curve_value(
                bone_animation,
                f"rotate_{component}",
                frame,
                value,
            )
            for component, value in zip("xyz", rotation_fallback[:3])
        )
        rotation_matrix = Euler(rotation_values, "XYZ").to_matrix().to_4x4()
    else:
        rotation_matrix = _sample_quaternion(
            bone_animation,
            frame,
            rotation_fallback,
        ).to_matrix().to_4x4()

    source = (
        Matrix.Translation(Vector(translation))
        @ rotation_matrix
        @ Matrix.Diagonal((*scale, 1.0))
    )
    converted = (
        _GAME_TO_BLENDER_BASIS
        @ source
        @ _GAME_TO_BLENDER_BASIS_INVERSE
    )
    converted.translation *= _TRANSLATION_SCALE
    return converted, scale


def _hierarchy_order(
    armature: bpy.types.Object,
) -> tuple[bpy.types.PoseBone, ...]:
    remaining = set(armature.pose.bones)
    ordered = []

    while remaining:
        available = tuple(
            bone
            for bone in remaining
            if bone.parent is None or bone.parent not in remaining
        )

        if not available:
            raise BFRESAnimationError("The selected armature hierarchy has a cycle.")

        ordered.extend(available)
        remaining.difference_update(available)

    return tuple(ordered)


def _source_bone_order(
    armature: bpy.types.Object,
) -> tuple[bpy.types.PoseBone, ...]:
    source_bones = tuple(
        pose_bone
        for pose_bone in armature.pose.bones
        if "smo_bone_index" in pose_bone.bone
    )
    return tuple(
        sorted(
            source_bones,
            key=lambda pose_bone: int(pose_bone.bone["smo_bone_index"]),
        )
    )


def _scale_compensation_helpers(
    pose_bones: Any,
    source_order: tuple[bpy.types.PoseBone, ...],
) -> dict[str, bpy.types.PoseBone]:
    result = {}

    for pose_bone in source_order:
        parent_name = str(pose_bone.bone.get(_SOURCE_PARENT_PROPERTY, "") or "")

        if not parent_name:
            continue

        helper_name = str(
            pose_bone.bone.get(_SCALE_COMPENSATION_HELPER_PROPERTY, "") or ""
        )
        helper = pose_bones.get(helper_name)

        if (
            helper is None
            or not helper.bone.get(_SCALE_COMPENSATION_HELPER_PROPERTY)
            or helper.parent is None
            or helper.parent.name != parent_name
        ):
            raise BFRESAnimationError(
                "This armature lacks hierarchy-preserving BFRES scale "
                "compensation. Re-import the model with the current SMO "
                "Kingdom Importer."
            )

        result[pose_bone.name] = helper

    return result


_VISIBILITY_PROPERTY = "smo_visible"


def _visibility_keyframes(
    target: BoneVisibilityTarget,
) -> tuple[tuple[float, float], ...]:
    keyed_values = {0.0: float(target.base_visible)}

    if target.curve is not None:
        keyed_values.update(
            (float(frame), float(value))
            for frame, value in zip(
                target.curve.frames,
                target.curve.values,
            )
        )

    return tuple(sorted(keyed_values.items()))


def _write_visibility_curve(
    fcurves: Any,
    group: Any,
    pose_bone_name: str,
    values: tuple[tuple[float, float], ...],
) -> None:
    escaped_name = bpy.utils.escape_identifier(pose_bone_name)
    data_path = (
        f'pose.bones["{escaped_name}"]["{_VISIBILITY_PROPERTY}"]'
    )
    curve = fcurves.new(data_path=data_path, index=0)
    curve.group = group
    curve.keyframe_points.add(len(values))

    for point, (frame, value) in zip(curve.keyframe_points, values):
        point.co = (float(frame), float(value))
        point.interpolation = "CONSTANT"

    curve.update()


def _mesh_visibility_bone(
    obj: bpy.types.Object,
    armature: bpy.types.Object,
) -> str:
    explicit_name = str(obj.get("smo_visibility_bone") or "").strip()

    if explicit_name:
        return explicit_name

    mesh = obj.data if obj.type == "MESH" else None
    base_index = int(
        mesh.get("smo_base_bone_index", 0xFFFF)
        if mesh is not None
        else 0xFFFF
    )

    if 0 <= base_index < len(armature.data.bones):
        bone_name = armature.data.bones[base_index].name
        obj["smo_visibility_bone"] = bone_name
        return bone_name

    return ""


def _visibility_meshes(
    armature: bpy.types.Object,
) -> dict[str, tuple[bpy.types.Object, ...]]:
    by_bone: dict[str, list[bpy.types.Object]] = {}

    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if (
            obj.parent is not armature
            and str(obj.get("smo_armature") or "") != armature.name
        ):
            continue

        bone_name = _mesh_visibility_bone(obj, armature)

        if bone_name:
            by_bone.setdefault(bone_name, []).append(obj)

    return {
        bone_name: tuple(objects)
        for bone_name, objects in by_bone.items()
    }


def _install_visibility_drivers(
    armature: bpy.types.Object,
    bone_names: tuple[str, ...],
) -> int:
    controlled_objects = set()
    meshes_by_bone = _visibility_meshes(armature)

    for bone_name in bone_names:
        escaped_name = bpy.utils.escape_identifier(bone_name)
        target_path = (
            f'pose.bones["{escaped_name}"]["{_VISIBILITY_PROPERTY}"]'
        )

        for obj in meshes_by_bone.get(bone_name, ()):
            marker = str(
                obj.get("smo_visibility_driver_armature") or ""
            )
            animation_data = obj.animation_data
            existing_drivers = tuple(
                (
                    animation_data.drivers.find(property_name)
                    if animation_data is not None
                    else None
                )
                for property_name in ("hide_viewport", "hide_render")
            )

            if (
                marker != armature.name
                and any(driver is not None for driver in existing_drivers)
            ):
                continue

            for property_name, existing in zip(
                ("hide_viewport", "hide_render"),
                existing_drivers,
            ):
                if existing is not None:
                    obj.driver_remove(property_name)

                curve = obj.driver_add(property_name)
                driver = curve.driver
                driver.type = "SCRIPTED"
                driver.expression = "1.0 - vis"
                variable = driver.variables.new()
                variable.name = "vis"
                variable.type = "SINGLE_PROP"
                variable.targets[0].id = armature
                variable.targets[0].data_path = target_path

            obj["smo_visibility_driver_armature"] = armature.name
            controlled_objects.add(obj)

    return len(controlled_objects)


def _localized_materials(
    armature: bpy.types.Object,
    target_names: set[str],
) -> dict[str, tuple[bpy.types.Material, ...]]:
    localized_by_original: dict[bpy.types.Material, bpy.types.Material] = {}
    by_source: dict[str, list[bpy.types.Material]] = {}

    for obj in _material_mesh_objects(armature):
        mesh_source = str(
            obj.data.get("smo_source_material_name") or ""
        ).strip()

        for slot in obj.material_slots:
            original = slot.material
            if original is None:
                continue
            source_name = str(
                original.get("smo_source_material_name") or mesh_source
            ).strip()
            if source_name not in target_names:
                continue

            if (
                str(
                    original.get("smo_material_animation_armature") or ""
                )
                == armature.name
            ):
                localized = original
            else:
                localized = localized_by_original.get(original)
                if localized is None:
                    localized = original.copy()
                    localized.name = (
                        f"{original.name} [{armature.name} Animation]"
                    )
                    localized["smo_source_material_name"] = source_name
                    localized["smo_material_animation_armature"] = (
                        armature.name
                    )
                    localized.animation_data_clear()
                    if localized.node_tree is not None:
                        localized.node_tree.animation_data_clear()
                    localized_by_original[original] = localized

                slot.link = "OBJECT"
                slot.material = localized

            materials = by_source.setdefault(source_name, [])
            if localized not in materials:
                materials.append(localized)

    return {
        name: tuple(materials)
        for name, materials in by_source.items()
    }


def _material_parameter_metadata(
    material: bpy.types.Material,
    parameter_name: str,
) -> tuple[int | None, list[float | int]]:
    try:
        metadata = json.loads(
            str(material.get("smo_shader_parameters") or "{}")
        )
        parameter = metadata.get(parameter_name, {})
        type_id = int(parameter["type_id"])
        raw_value = parameter.get("value")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        type_id = None
        raw_value = None

    if isinstance(raw_value, list):
        values = [
            value
            for value in raw_value
            if isinstance(value, (bool, int, float))
        ]
    elif isinstance(raw_value, (bool, int, float)):
        values = [raw_value]
    else:
        values = []

    if not values and parameter_name.startswith("tex_mtx"):
        values = [0, 1.0, 1.0, 0.0, 0.0, 0.0]

    if not values and material.node_tree is not None:
        for node in material.node_tree.nodes:
            if str(node.get("smo_shader_parameter") or "") != parameter_name:
                continue
            binding = str(
                node.get("smo_shader_parameter_binding") or ""
            )
            if binding == "COLOR_OUTPUT":
                values = list(node.outputs["Color"].default_value)
            elif binding == "COLOR_INPUT_2":
                values = list(node.inputs[2].default_value)
            elif binding == "SCALAR_INPUT_1":
                values = [float(node.inputs[1].default_value)]
            if values:
                break

    return type_id, values


def _parameter_component_index(
    type_id: int | None,
    data_offset: int,
) -> int:
    if type_id is not None and 0 <= type_id <= 3:
        return data_offset
    return data_offset // 4


def _integer_parameter_component(
    type_id: int | None,
    data_offset: int,
) -> bool:
    return (
        type_id is not None
        and 0 <= type_id <= 11
    ) or (
        type_id in {30, 31}
        and data_offset == 0
    )


def _material_parameter_values(
    parameter: MaterialParameterAnimation,
    type_id: int | None,
    base_values: list[float | int],
    frame: float,
) -> list[float | int]:
    values = list(base_values)
    max_index = max(
        (
            _parameter_component_index(type_id, offset)
            for offset in parameter.data_offsets
        ),
        default=-1,
    )
    while len(values) <= max_index:
        values.append(0.0)

    for constant in parameter.constants:
        index = _parameter_component_index(
            type_id, constant.data_offset
        )
        values[index] = (
            constant.raw_value
            if _integer_parameter_component(
                type_id, constant.data_offset
            )
            else constant.float_value
        )

    for curve in parameter.curves:
        index = _parameter_component_index(type_id, curve.data_offset)
        value = curve.evaluate(frame)
        values[index] = (
            int(round(value))
            if _integer_parameter_component(type_id, curve.data_offset)
            else value
        )

    return values


def _node_parameter_curves(
    material: bpy.types.Material,
    parameter: MaterialParameterAnimation,
    frame_count: int,
) -> tuple[
    tuple[str, int, tuple[tuple[int, float], ...], str, str],
    ...,
]:
    node_tree = material.node_tree
    if node_tree is None:
        return ()

    type_id, base_values = _material_parameter_metadata(
        material, parameter.name
    )
    if (
        parameter.name.startswith("tex_mtx")
        and not any(
            str(node.get("smo_shader_parameter") or "") == parameter.name
            for node in node_tree.nodes
        )
    ):
        from .static_model_import import (
            _ensure_animated_texture_transform_binding,
        )

        _ensure_animated_texture_transform_binding(
            material,
            parameter.name,
        )
    interpolation = (
        "CONSTANT"
        if type_id is not None and 0 <= type_id <= 11
        else "LINEAR"
    )
    frames = tuple(range(frame_count + 1))
    values_by_frame = tuple(
        _material_parameter_values(
            parameter,
            type_id,
            base_values,
            float(frame),
        )
        for frame in frames
    )
    curves = []

    for node in node_tree.nodes:
        if str(node.get("smo_shader_parameter") or "") != parameter.name:
            continue

        binding = str(
            node.get("smo_shader_parameter_binding") or ""
        )
        if binding == "COLOR_OUTPUT":
            socket = node.outputs.get("Color")
            if socket is None:
                continue
            path = socket.path_from_id("default_value")
            component_count = min(
                4, max((len(values) for values in values_by_frame), default=0)
            )
            for index in range(component_count):
                curves.append(
                    (
                        path,
                        index,
                        tuple(
                            (frame, float(values[index]))
                            for frame, values in zip(frames, values_by_frame)
                        ),
                        parameter.name,
                        interpolation,
                    )
                )
        elif binding == "COLOR_INPUT_2":
            socket = node.inputs[2]
            path = socket.path_from_id("default_value")
            component_count = min(
                4, max((len(values) for values in values_by_frame), default=0)
            )
            for index in range(component_count):
                curves.append(
                    (
                        path,
                        index,
                        tuple(
                            (frame, float(values[index]))
                            for frame, values in zip(frames, values_by_frame)
                        ),
                        parameter.name,
                        interpolation,
                    )
                )
        elif binding == "SCALAR_INPUT_1":
            socket = node.inputs[1]
            path = socket.path_from_id("default_value")
            curves.append(
                (
                    path,
                    0,
                    tuple(
                        (frame, float(values[0]))
                        for frame, values in zip(frames, values_by_frame)
                        if values
                    ),
                    parameter.name,
                    interpolation,
                )
            )
        elif binding.startswith("TEXSRT_"):
            from .static_model_import import _tex_srt_affine_matrix

            matrices = tuple(
                _tex_srt_affine_matrix(tuple(values))
                for values in values_by_frame
            )
            if any(matrix is None for matrix in matrices):
                continue
            typed_matrices = tuple(
                matrix for matrix in matrices if matrix is not None
            )

            if binding == "TEXSRT_ROW_0":
                socket = node.inputs[1]
                path = socket.path_from_id("default_value")
                mappings = ((0, 0), (1, 1))
            elif binding == "TEXSRT_OFFSET_0":
                socket = node.inputs[1]
                path = socket.path_from_id("default_value")
                mappings = ((0, 2),)
            elif binding == "TEXSRT_ROW_1":
                socket = node.inputs[1]
                path = socket.path_from_id("default_value")
                mappings = ((0, 3), (1, 4))
            elif binding == "TEXSRT_OFFSET_1":
                socket = node.inputs[1]
                path = socket.path_from_id("default_value")
                mappings = ((0, 5),)
            else:
                continue

            for socket_index, matrix_index in mappings:
                curves.append(
                    (
                        path,
                        socket_index,
                        tuple(
                            (frame, float(matrix[matrix_index]))
                            for frame, matrix in zip(
                                frames, typed_matrices
                            )
                        ),
                        parameter.name,
                        "LINEAR",
                    )
                )

    return tuple(curves)


def _new_node_tree_action_channels(
    node_tree: bpy.types.NodeTree,
    action_name: str,
) -> tuple[bpy.types.Action, Any, Any]:
    animation_data = node_tree.animation_data_create()
    action = bpy.data.actions.new(action_name)
    action.use_fake_user = True
    animation_data.action = action

    if bpy.app.version >= (4, 4, 0):
        slot = action.slots.new(
            id_type="NODETREE",
            name=node_tree.name,
        )
        animation_data.action_slot = slot
        layer = action.layers.new(action_name)
        strip = layer.strips.new(type="KEYFRAME")
        channel_bag = strip.channelbag(slot, ensure=True)
        return action, channel_bag.fcurves, channel_bag.groups

    return action, action.fcurves, action.groups


def _write_material_curve(
    fcurves: Any,
    group: Any,
    data_path: str,
    index: int,
    values: tuple[tuple[int, float], ...],
    interpolation: str,
) -> None:
    curve = fcurves.new(data_path=data_path, index=index)
    curve.group = group
    curve.keyframe_points.add(len(values))

    for point, (frame, value) in zip(curve.keyframe_points, values):
        point.co = (float(frame), float(value))
        point.interpolation = interpolation

    curve.update()


def _apply_material_animations(
    armature: bpy.types.Object,
    source: AnimationSource,
    clip: AnimationClip,
) -> tuple[tuple[bpy.types.Action, ...], int, int, int]:
    target_names = {
        target.name
        for animation in clip.materials
        for target in animation.targets
        if target.parameters
    }
    materials_by_source = _localized_materials(
        armature, target_names
    )
    curve_data: dict[
        bpy.types.Material,
        dict[tuple[str, int], tuple[
            tuple[tuple[int, float], ...],
            str,
            str,
        ]],
    ] = {}
    matched_targets = set()
    bound_parameters = set()
    skipped_parameters = set()

    for animation in clip.materials:
        for target in animation.targets:
            materials = materials_by_source.get(target.name, ())
            if not materials:
                continue
            matched_targets.add(target.name)

            for material in materials:
                material_curves = curve_data.setdefault(material, {})

                for parameter in target.parameters:
                    bindings = _node_parameter_curves(
                        material,
                        parameter,
                        animation.frame_count,
                    )
                    if bindings:
                        bound_parameters.add(
                            (target.name, parameter.name)
                        )
                    else:
                        skipped_parameters.add(
                            (target.name, parameter.name)
                        )

                    for (
                        path,
                        index,
                        values,
                        group_name,
                        interpolation,
                    ) in bindings:
                        material_curves[(path, index)] = (
                            values,
                            group_name,
                            interpolation,
                        )

    actions = []

    for material, material_curves in curve_data.items():
        if not material_curves or material.node_tree is None:
            continue
        action_name = f"{clip.name} - {material.name}"
        action, fcurves, groups = _new_node_tree_action_channels(
            material.node_tree,
            action_name,
        )
        groups_by_name = {}

        for (
            path,
            index,
        ), (
            values,
            group_name,
            interpolation,
        ) in material_curves.items():
            group = groups_by_name.get(group_name)
            if group is None:
                group = groups.new(name=group_name)
                groups_by_name[group_name] = group
            _write_material_curve(
                fcurves,
                group,
                path,
                index,
                values,
                interpolation,
            )

        action["smo_animation_import"] = True
        action["smo_bfres_material_animation_import"] = True
        action["smo_source_archive"] = str(source.archive_path)
        action["smo_source_bfres"] = source.bfres_name
        action["smo_source_animation"] = clip.name
        action["smo_target_material"] = material.name
        action["smo_target_armature"] = armature.name
        action["smo_frame_count"] = clip.frame_count + 1
        action["smo_looping"] = clip.looping
        actions.append(action)

    return (
        tuple(actions),
        len(matched_targets),
        len(bound_parameters),
        len(skipped_parameters),
    )


def import_bfres_animation(
    armature: bpy.types.Object,
    source: AnimationSource,
    animation: AnimationClip,
) -> bpy.types.Action:
    if not armature.get("smo_armature_generated"):
        raise BFRESAnimationError(
            "Select an armature generated by the SMO Kingdom Importer."
        )

    skeletal = animation.skeletal
    visibility = animation.visibility
    material_target_names = {
        target.name
        for material_animation in animation.materials
        for target in material_animation.targets
        if target.parameters
    }
    matching_material_targets = (
        material_target_names & _source_material_names(armature)
    )

    pose_bones = armature.pose.bones
    skeletal_bones = skeletal.bones if skeletal is not None else ()
    missing = sorted(
        bone.name for bone in skeletal_bones if bone.name not in pose_bones
    )
    by_name = {
        bone.name: bone
        for bone in skeletal_bones
        if bone.name in pose_bones
    }
    visibility_targets = visibility.targets if visibility is not None else ()
    missing_visibility = sorted(
        target.name
        for target in visibility_targets
        if target.name not in pose_bones
    )
    visibility_by_name = {
        target.name: target
        for target in visibility_targets
        if target.name in pose_bones
    }

    if (
        by_name
        and int(armature.data.get("smo_rest_matrix_revision", 0)) < 4
    ):
        raise BFRESAnimationError(
            "This armature predates hierarchy-preserving BFRES scale "
            "compensation. Re-import the model with the current SMO "
            "Kingdom Importer."
        )

    if (
        not by_name
        and not visibility_by_name
        and not matching_material_targets
    ):
        raise BFRESAnimationError(
            f"Animation {animation.name!r} has no tracks for the selected "
            "model."
        )

    source_order = _source_bone_order(armature)
    animated = tuple(bone for bone in source_order if bone.name in by_name)
    scale_helpers = (
        _scale_compensation_helpers(pose_bones, source_order)
        if by_name
        else {}
    )
    original_inherit_scale = {
        pose_bone.name: pose_bone.bone.inherit_scale
        for pose_bone in armature.pose.bones
    }
    scale_inheritance_modes = {
        pose_bone.name: "FULL" for pose_bone in armature.pose.bones
    }
    for pose_bone in armature.pose.bones:
        pose_bone.bone.inherit_scale = "FULL"
    skeleton_segment_scale_compensate = bool(
        armature.data.get("smo_segment_scale_compensate", False)
    )
    rest_transforms = (
        {bone.name: _rest_transform(bone) for bone in source_order}
        if by_name
        else {}
    )
    original_basis = {
        bone.name: bone.matrix_basis.copy() for bone in armature.pose.bones
    }
    original_visibility = {
        bone.name: (
            float(bone[_VISIBILITY_PROPERTY])
            if _VISIBILITY_PROPERTY in bone
            else None
        )
        for bone in armature.pose.bones
    }

    for pose_bone in armature.pose.bones:
        if _VISIBILITY_PROPERTY in pose_bone:
            pose_bone[_VISIBILITY_PROPERTY] = 1.0

    for bone_name, target in visibility_by_name.items():
        pose_bone = pose_bones[bone_name]
        pose_bone[_VISIBILITY_PROPERTY] = float(target.base_visible)
        pose_bone.id_properties_ui(_VISIBILITY_PROPERTY).update(
            min=0.0,
            max=1.0,
            description=(
                "BFRES bone visibility; imported mesh visibility is driven "
                "by this value"
            ),
        )

    previous_quaternions: dict[str, Quaternion] = {}
    samples = {
        bone.name: {
            "location": [[], [], []],
            "rotation_quaternion": [[], [], [], []],
            "scale": [[], [], []],
        }
        for bone in animated
    }
    helper_samples = {
        scale_helpers[bone.name].name: [[], [], []]
        for bone in animated
        if bone.name in scale_helpers
    }

    try:
        if by_name and skeletal is not None:
            for frame in range(skeletal.frame_count + 1):
                local_scales = {
                    pose_bone.name: rest_transforms[pose_bone.name][0]
                    for pose_bone in source_order
                }

                for pose_bone in armature.pose.bones:
                    pose_bone.matrix_basis.identity()
                    pose_bone.rotation_mode = "QUATERNION"

                for pose_bone in source_order:
                    bone_animation = by_name.get(pose_bone.name)

                    if bone_animation is None:
                        continue

                    local, local_scale = _sample_local_matrix(
                        bone_animation,
                        float(frame),
                        rest_transforms[pose_bone.name],
                        skeletal.euler_xyz,
                    )
                    local_scales[pose_bone.name] = local_scale
                    parent_name = str(
                        pose_bone.bone.get(_SOURCE_PARENT_PROPERTY, "") or ""
                    )

                    if parent_name:
                        helper = scale_helpers[pose_bone.name]
                        correction_scale = (1.0, 1.0, 1.0)

                        if bone_animation.segment_scale_compensate:
                            parent_scale = tuple(
                                float(value)
                                for value in local_scales[parent_name]
                            )

                            if all(
                                abs(value) > 1e-12
                                for value in parent_scale
                            ):
                                correction_scale = tuple(
                                    1.0 / value for value in parent_scale
                                )

                        helper.scale = correction_scale
                        rest_relative = (
                            helper.bone.matrix_local.inverted_safe()
                            @ pose_bone.bone.matrix_local
                        )
                        basis = rest_relative.inverted_safe() @ local
                    else:
                        basis = (
                            pose_bone.bone.matrix_local.inverted_safe()
                            @ local
                        )

                    location, rotation, scale = basis.decompose()
                    pose_bone.location = location
                    pose_bone.rotation_quaternion = rotation
                    pose_bone.scale = scale

                for pose_bone in animated:
                    bone_samples = samples[pose_bone.name]

                    for index, value in enumerate(pose_bone.location):
                        bone_samples["location"][index].append(
                            (frame, float(value))
                        )
                    for index, value in enumerate(pose_bone.scale):
                        bone_samples["scale"][index].append(
                            (frame, float(value))
                        )

                    rotation = pose_bone.rotation_quaternion.copy()
                    previous = previous_quaternions.get(pose_bone.name)

                    if previous is not None and rotation.dot(previous) < 0.0:
                        rotation.negate()

                    previous_quaternions[pose_bone.name] = rotation

                    for index, value in enumerate(rotation):
                        bone_samples["rotation_quaternion"][index].append(
                            (frame, float(value))
                        )

                for helper_name, component_samples in helper_samples.items():
                    helper = pose_bones[helper_name]

                    for index, value in enumerate(helper.scale):
                        component_samples[index].append(
                            (frame, float(value))
                        )
    except Exception:
        for bone_name, inherit_scale in original_inherit_scale.items():
            pose_bones[bone_name].bone.inherit_scale = inherit_scale

        for pose_bone in armature.pose.bones:
            pose_bone.matrix_basis = original_basis[pose_bone.name]
            previous_visibility = original_visibility[pose_bone.name]

            if previous_visibility is None:
                if _VISIBILITY_PROPERTY in pose_bone:
                    del pose_bone[_VISIBILITY_PROPERTY]
            else:
                pose_bone[_VISIBILITY_PROPERTY] = previous_visibility
        raise

    previous_action = (
        armature.animation_data.action
        if armature.animation_data is not None
        else None
    )
    previous_slot = (
        armature.animation_data.action_slot
        if armature.animation_data is not None and bpy.app.version >= (4, 4, 0)
        else None
    )

    action = None
    material_actions: tuple[bpy.types.Action, ...] = ()

    try:
        action, fcurves, groups = _new_action_channels(
            armature,
            animation.name,
        )

        for pose_bone in animated:
            group = groups.new(name=pose_bone.name)
            escaped_name = bpy.utils.escape_identifier(pose_bone.name)
            base_path = f'pose.bones["{escaped_name}"].'

            for data_name, component_samples in samples[pose_bone.name].items():
                for index, values in enumerate(component_samples):
                    _write_curve(
                        fcurves,
                        group,
                        base_path + data_name,
                        index,
                        tuple(values),
                    )

        for helper_name, component_samples in helper_samples.items():
            group = groups.new(name=helper_name)
            escaped_name = bpy.utils.escape_identifier(helper_name)
            data_path = f'pose.bones["{escaped_name}"].scale'

            for index, values in enumerate(component_samples):
                _write_curve(
                    fcurves,
                    group,
                    data_path,
                    index,
                    tuple(values),
                )

        for bone_name, target in visibility_by_name.items():
            group = next(
                (
                    existing
                    for existing in groups
                    if existing.name == bone_name
                ),
                None,
            )

            if group is None:
                group = groups.new(name=bone_name)

            _write_visibility_curve(
                fcurves,
                group,
                bone_name,
                _visibility_keyframes(target),
            )

        visibility_mesh_count = _install_visibility_drivers(
            armature,
            tuple(visibility_by_name),
        )
        (
            material_actions,
            animated_material_count,
            animated_parameter_count,
            skipped_parameter_count,
        ) = _apply_material_animations(
            armature,
            source,
            animation,
        )

        if (
            not by_name
            and not visibility_by_name
            and not material_actions
        ):
            raise BFRESAnimationError(
                "The material tracks match this model, but none of its "
                "translated shader nodes expose animation bindings. "
                "Re-import the model with the current add-on version."
            )

        action["smo_animation_import"] = True
        action["smo_bfres_animation_import"] = True
        action["smo_source_archive"] = str(source.archive_path)
        action["smo_source_bfres"] = source.bfres_name
        action["smo_source_animation"] = animation.name
        action["smo_target_armature"] = armature.name
        action["smo_frame_count"] = animation.frame_count + 1
        action["smo_animated_bone_count"] = len(animated)
        action["smo_source_target_bone_count"] = len(skeletal_bones)
        action["smo_scale_compensation_helper_count"] = len(helper_samples)
        action["smo_skipped_bone_count"] = len(missing)
        action["smo_skipped_bones"] = json.dumps(missing)
        action["smo_has_bone_visibility"] = visibility is not None
        action["smo_visibility_target_count"] = len(visibility_targets)
        action["smo_animated_visibility_bone_count"] = len(
            visibility_by_name
        )
        action["smo_skipped_visibility_bone_count"] = len(
            missing_visibility
        )
        action["smo_skipped_visibility_bones"] = json.dumps(
            missing_visibility
        )
        action["smo_visibility_mesh_count"] = visibility_mesh_count
        action["smo_has_material_animation"] = bool(animation.materials)
        action["smo_material_action_count"] = len(material_actions)
        action["smo_animated_material_count"] = animated_material_count
        action["smo_animated_shader_parameter_count"] = (
            animated_parameter_count
        )
        action["smo_skipped_shader_parameter_count"] = (
            skipped_parameter_count
        )
        action["smo_looping"] = animation.looping
        action["smo_rotation_mode"] = (
            "EULER_XYZ"
            if skeletal is not None and skeletal.euler_xyz
            else "QUATERNION"
            if skeletal is not None
            else "NONE"
        )
        action["smo_translation_scale"] = _TRANSLATION_SCALE
        action["smo_coordinate_basis"] = "(X, Y, Z) -> (X, -Z, Y)"
        action["smo_segment_scale_compensate"] = (
            skeleton_segment_scale_compensate
        )
        action[_SCALE_INHERITANCE_PROPERTY] = json.dumps(
            scale_inheritance_modes,
            sort_keys=True,
            separators=(",", ":"),
        )
        action["smo_scale_inheritance_revision"] = 3
        _apply_action_scale_inheritance(armature, action)
    except Exception:
        for bone_name, inherit_scale in original_inherit_scale.items():
            pose_bones[bone_name].bone.inherit_scale = inherit_scale

        animation_data = armature.animation_data

        if (
            action is not None
            and animation_data is not None
            and animation_data.action is action
        ):
            animation_data.action = previous_action

            if previous_action is not None and previous_slot is not None:
                animation_data.action_slot = previous_slot

        if action is not None:
            bpy.data.actions.remove(action)

        for material_action in material_actions:
            if material_action.name in bpy.data.actions:
                bpy.data.actions.remove(material_action)

        for pose_bone in armature.pose.bones:
            pose_bone.matrix_basis = original_basis[pose_bone.name]
            previous_visibility = original_visibility[pose_bone.name]

            if previous_visibility is None:
                if _VISIBILITY_PROPERTY in pose_bone:
                    del pose_bone[_VISIBILITY_PROPERTY]
            else:
                pose_bone[_VISIBILITY_PROPERTY] = previous_visibility
        raise

    scene = bpy.context.scene
    scene.frame_start = 0
    scene.frame_end = animation.frame_count
    scene.render.fps = 60
    scene.render.fps_base = 1.0
    scene.frame_set(0)
    return action


class SMO_OT_refresh_bfres_animations(Operator):
    bl_idname = "smo.refresh_bfres_animations"
    bl_label = "Refresh BFRES Animations"
    bl_description = "Reload the animation list from the selected armature's BFRES"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        armature = _target_armature(context)
        return bool(armature and armature.get("smo_armature_generated"))

    def execute(self, context: bpy.types.Context) -> set[str]:
        armature = _target_armature(context)

        if armature is None:
            return {"CANCELLED"}

        clear_bfres_animation_cache()

        try:
            sources = _animation_sources(armature)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not read BFRES animations: {exc}")
            return {"CANCELLED"}

        count = sum(len(source.animations) for source in sources)
        self.report({"INFO"}, f"Found {count} compatible BFRES animation clips.")
        if context.area is not None:
            context.area.tag_redraw()
        return {"FINISHED"}


class SMO_OT_apply_bfres_animation(Operator):
    bl_idname = "smo.apply_bfres_animation"
    bl_label = "Use Animation"
    bl_description = "Create and activate a Blender Action from the selected BFRES animation"
    bl_options = {"REGISTER", "UNDO"}

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

        return bool(getattr(context.scene, "smo_bfres_animation", ""))

    def execute(self, context: bpy.types.Context) -> set[str]:
        armature = _target_armature(context)

        if armature is None:
            return {"CANCELLED"}

        try:
            source, animation = _selected_animation(
                armature,
                context.scene.smo_bfres_animation,
            )
            action = import_bfres_animation(armature, source, animation)
        except Exception as exc:
            print("[Odyssey Toolkit] BFRES animation import failed:")
            traceback.print_exc()
            self.report({"ERROR"}, f"Could not use BFRES animation: {exc}")
            return {"CANCELLED"}

        matching_count = int(action["smo_animated_bone_count"])
        skipped_count = int(action["smo_skipped_bone_count"])
        visibility_count = int(
            action["smo_animated_visibility_bone_count"]
        )
        visibility_mesh_count = int(action["smo_visibility_mesh_count"])
        material_count = int(action["smo_animated_material_count"])
        parameter_count = int(
            action["smo_animated_shader_parameter_count"]
        )
        skipped_parameter_count = int(
            action["smo_skipped_shader_parameter_count"]
        )
        message = (
            f"Created {action.name} with {animation.frame_count + 1} frames "
            f"for {matching_count} transform bones"
        )

        if visibility_count:
            message += (
                f" and {visibility_count} visibility bones driving "
                f"{visibility_mesh_count} meshes"
            )

        if material_count:
            message += (
                f" and {parameter_count} shader parameters on "
                f"{material_count} materials"
            )

        message += "."

        if skipped_count:
            message += f" Skipped {skipped_count} companion tracks."
        if skipped_parameter_count:
            message += (
                f" {skipped_parameter_count} shader parameters are not "
                "used by the translated Blender shader."
            )

        self.report({"INFO"}, message)
        return {"FINISHED"}


class SMO_PT_bfres_animations(Panel):
    bl_idname = "SMO_PT_bfres_animations"
    bl_label = "BFRES Animations"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Odyssey"
    bl_parent_id = "SMO_PT_models_and_animations"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        armature = _target_armature(context)

        if armature is None or not armature.get("smo_armature_generated"):
            layout.label(text="Select an imported Odyssey armature", icon="INFO")
            return

        header = layout.row(align=True)
        header.label(text=armature.name, icon="ARMATURE_DATA")
        header.operator(
            "smo.refresh_bfres_animations",
            text="",
            icon="FILE_REFRESH",
        )

        package = layout.box()
        package.prop(
            armature,
            "smo_bfres_animation_package",
            text="Animation Package",
        )
        package_path = str(
            getattr(armature, "smo_bfres_animation_package", "") or ""
        ).strip()
        package.label(
            text=(
                f"Using {Path(bpy.path.abspath(package_path)).name}"
                if package_path
                else "Using the model's original package"
            ),
            icon="PACKAGE",
        )

        try:
            sources = _animation_sources(armature)
        except Exception as exc:
            error = layout.row()
            error.alert = True
            error.label(text=str(exc), icon="ERROR")
            return

        count = sum(len(source.animations) for source in sources)
        skeletal_count = sum(
            animation.skeletal is not None
            for source in sources
            for animation in source.animations
        )
        visibility_count = sum(
            animation.visibility is not None
            for source in sources
            for animation in source.animations
        )
        material_count = sum(
            bool(animation.materials)
            for source in sources
            for animation in source.animations
        )

        if not count:
            layout.label(
                text="No compatible animation clips found",
                icon="INFO",
            )
            return

        layout.label(text=f"{count} available animation clips")
        layout.label(
            text=(
                f"{skeletal_count} skeletal, "
                f"{visibility_count} bone visibility, "
                f"{material_count} shader/colour"
            )
        )
        layout.prop(context.scene, "smo_bfres_animation", text="")
        identifier = str(context.scene.smo_bfres_animation)

        try:
            _source, animation = _selected_animation(armature, identifier)
        except BFRESAnimationError:
            animation = None

        if animation is not None:
            armature_names = {bone.name for bone in armature.data.bones}
            details = layout.row(align=True)
            details.label(text=f"{animation.frame_count + 1} frames")

            if animation.looping:
                details.label(text="Loops", icon="FILE_TICK")

            if animation.skeletal is not None:
                target_names = {
                    bone.name for bone in animation.skeletal.bones
                }
                matching_count = len(target_names & armature_names)
                skipped_count = len(target_names - armature_names)
                layout.label(
                    text=(
                        f"Skeletal: {matching_count}/"
                        f"{len(target_names)} bones"
                    ),
                    icon="ARMATURE_DATA",
                )

                if skipped_count:
                    layout.label(
                        text=(
                            f"{skipped_count} companion tracks are not on "
                            "this armature"
                        ),
                        icon="INFO",
                    )

            if animation.visibility is not None:
                visibility_names = {
                    target.name
                    for target in animation.visibility.targets
                }
                matching_visibility = len(
                    visibility_names & armature_names
                )
                layout.label(
                    text=(
                        f"Visibility: {matching_visibility}/"
                        f"{len(visibility_names)} bones"
                    ),
                    icon="HIDE_OFF",
                )

                meshes_by_visibility_bone = _visibility_meshes(
                    armature
                )

                if matching_visibility and not any(
                    meshes_by_visibility_bone.get(name)
                    for name in visibility_names
                ):
                    layout.label(
                        text=(
                            "No bound meshes found; re-import the model "
                            "with this version"
                        ),
                        icon="INFO",
                    )

        if animation is not None and animation.materials:
            model_material_names = _source_material_names(armature)
            material_target_names = {
                target.name
                for material_animation in animation.materials
                for target in material_animation.targets
                if target.parameters
            }
            matching_material_names = (
                material_target_names & model_material_names
            )
            parameter_count = sum(
                len(target.parameters)
                for material_animation in animation.materials
                for target in material_animation.targets
                if target.name in model_material_names
            )
            layout.label(
                text=(
                    f"Shader/colour: {len(matching_material_names)}/"
                    f"{len(material_target_names)} materials, "
                    f"{parameter_count} parameters"
                ),
                icon="MATERIAL",
            )

        use_row = layout.row()
        use_row.scale_y = 1.25
        use_row.operator("smo.apply_bfres_animation", icon="ACTION")


def _animation_package_updated(
    _armature: bpy.types.Object,
    context: bpy.types.Context,
) -> None:
    clear_bfres_animation_cache()

    if context.area is not None:
        context.area.tag_redraw()


def register_bfres_animation_properties() -> None:
    bpy.types.Object.smo_bfres_animation_package = StringProperty(
        name="Animation Package",
        description=(
            "Optional SZS or BFRES package to use for this armature's "
            "animations; leave blank to use the model's original package"
        ),
        subtype="FILE_PATH",
        update=_animation_package_updated,
    )
    bpy.types.Scene.smo_bfres_animation = EnumProperty(
        name="Animation",
        description=(
            "Skeletal, bone visibility, shader parameter, and colour "
            "animations compatible with the selected imported model"
        ),
        items=bfres_animation_enum_items,
    )

    if (
        sync_active_action_scale_inheritance
        not in bpy.app.handlers.depsgraph_update_post
    ):
        bpy.app.handlers.depsgraph_update_post.append(
            sync_active_action_scale_inheritance
        )


def unregister_bfres_animation_properties() -> None:
    if (
        sync_active_action_scale_inheritance
        in bpy.app.handlers.depsgraph_update_post
    ):
        bpy.app.handlers.depsgraph_update_post.remove(
            sync_active_action_scale_inheritance
        )

    if hasattr(bpy.types.Scene, "smo_bfres_animation"):
        del bpy.types.Scene.smo_bfres_animation
    if hasattr(bpy.types.Object, "smo_bfres_animation_package"):
        del bpy.types.Object.smo_bfres_animation_package

    clear_bfres_animation_cache()

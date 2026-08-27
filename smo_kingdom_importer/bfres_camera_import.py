from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import traceback
from typing import Any

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel
from mathutils import Quaternion, Vector

from .bfres_animation import (
    BFRESAnimationError,
    CameraAnimation,
    SceneAnimation,
    read_scene_animations,
)
from .smd_animation import _TRANSLATION_SCALE, _write_curve


@dataclass(frozen=True, slots=True)
class CameraAnimationSource:
    archive_path: Path
    bfres_name: str
    scene: SceneAnimation
    camera: CameraAnimation


_CAMERA_SOURCE_CACHE: dict[
    tuple[str, int, int], tuple[CameraAnimationSource, ...]
] = {}
_CAMERA_ENUM_CACHE: dict[
    tuple[str, int, int], tuple[tuple[str, str, str, str, int], ...]
] = {}


def clear_bfres_camera_cache() -> None:
    _CAMERA_SOURCE_CACHE.clear()
    _CAMERA_ENUM_CACHE.clear()


def _default_camera_package(scene: bpy.types.Scene) -> Path | None:
    settings = getattr(scene, "smo_settings", None)
    romfs_path = str(getattr(settings, "romfs_path", "") or "").strip()

    if not romfs_path:
        return None

    candidate = (
        Path(bpy.path.abspath(romfs_path)).expanduser()
        / "ObjectData"
        / "DemoCamera.szs"
    ).resolve()
    return candidate if candidate.is_file() else None


def _camera_package_path(scene: bpy.types.Scene) -> Path:
    raw_path = str(
        getattr(scene, "smo_bfres_camera_animation_package", "") or ""
    ).strip()
    path = (
        Path(bpy.path.abspath(raw_path)).expanduser().resolve()
        if raw_path
        else _default_camera_package(scene)
    )

    if path is None:
        raise BFRESAnimationError(
            "Choose DemoCamera.szs (or another BFRES/SZS camera package), "
            "or configure the SMO ROMFS path."
        )
    if not path.is_file():
        raise BFRESAnimationError(f"Camera animation package not found: {path}")
    if path.suffix.casefold() not in {".szs", ".bfres"}:
        raise BFRESAnimationError(
            f"Unsupported camera animation package: {path.name}"
        )

    return path


def _camera_sources(scene: bpy.types.Scene) -> tuple[CameraAnimationSource, ...]:
    package_path = _camera_package_path(scene)
    stat = package_path.stat()
    cache_key = (
        str(package_path).casefold(),
        stat.st_mtime_ns,
        stat.st_size,
    )
    cached = _CAMERA_SOURCE_CACHE.get(cache_key)

    if cached is not None:
        return cached

    if package_path.suffix.casefold() == ".bfres":
        candidates = ((package_path.name, package_path.read_bytes()),)
    else:
        from .world_list import read_szs

        archive = read_szs(package_path)
        candidates = tuple(
            (entry.name, bytes(entry.data))
            for entry in archive.get_files()
            if entry.name
            and Path(entry.name).suffix.casefold() == ".bfres"
        )

    sources = []

    for bfres_name, data in candidates:
        for scene_animation in read_scene_animations(data):
            for camera in scene_animation.cameras:
                sources.append(
                    CameraAnimationSource(
                        archive_path=package_path,
                        bfres_name=bfres_name,
                        scene=scene_animation,
                        camera=camera,
                    )
                )

    result = tuple(sources)
    _CAMERA_SOURCE_CACHE[cache_key] = result
    return result


def bfres_camera_enum_items(
    scene: bpy.types.Scene,
    _context: bpy.types.Context | None,
) -> tuple[tuple[str, str, str, str, int], ...]:
    try:
        path = _camera_package_path(scene)
        stat = path.stat()
        cache_key = (str(path).casefold(), stat.st_mtime_ns, stat.st_size)
        cached = _CAMERA_ENUM_CACHE.get(cache_key)

        if cached is not None:
            return cached

        sources = _camera_sources(scene)
    except Exception:
        return ()

    items = tuple(
        (
            str(index),
            source.scene.name,
            (
                f"{source.camera.name} from {source.bfres_name}; "
                f"{source.camera.frame_count + 1} frames"
            ),
            "CAMERA_DATA",
            index,
        )
        for index, source in enumerate(sources)
    )
    _CAMERA_ENUM_CACHE[cache_key] = items
    return items


def _selected_camera_source(
    scene: bpy.types.Scene,
) -> CameraAnimationSource:
    identifier = str(getattr(scene, "smo_bfres_camera_animation", "") or "")

    try:
        return _camera_sources(scene)[int(identifier)]
    except (ValueError, IndexError) as exc:
        raise BFRESAnimationError(
            "Choose an available BFRES camera animation."
        ) from exc


def _new_action_channels(
    target: bpy.types.ID,
    action_name: str,
    id_type: str,
) -> tuple[bpy.types.Action, Any, Any]:
    animation_data = target.animation_data_create()
    action = bpy.data.actions.new(action_name)
    action.use_fake_user = True
    animation_data.action = action

    if bpy.app.version >= (4, 4, 0):
        slot = action.slots.new(id_type=id_type, name=target.name)
        animation_data.action_slot = slot
        layer = action.layers.new(action_name)
        strip = layer.strips.new(type="KEYFRAME")
        channel_bag = strip.channelbag(slot, ensure=True)
        return action, channel_bag.fcurves, channel_bag.groups

    return action, action.fcurves, action.groups


def _game_vector(x: float, y: float, z: float) -> Vector:
    return Vector((x, -z, y)) * _TRANSLATION_SCALE


def _camera_rotation(
    position: Vector,
    target: Vector,
    twist: float,
) -> Quaternion:
    direction = target - position

    if direction.length_squared < 1e-16:
        raise BFRESAnimationError(
            "Camera position and look-at target coincide."
        )

    rotation = direction.normalized().to_track_quat("-Z", "Y")
    return rotation @ Quaternion((0.0, 0.0, 1.0), twist)


def _lens_from_vertical_fov(camera: bpy.types.Camera, fov: float) -> float:
    clamped = min(max(float(fov), 1e-5), math.pi - 1e-5)
    return camera.sensor_height / (2.0 * math.tan(clamped / 2.0))


def _write_scalar_curve(
    fcurves: Any,
    group: Any,
    data_path: str,
    values: list[tuple[int, float]],
) -> None:
    _write_curve(fcurves, group, data_path, 0, tuple(values))


def import_bfres_camera_animation(
    context: bpy.types.Context,
    source: CameraAnimationSource,
) -> tuple[bpy.types.Object, bpy.types.Action, bpy.types.Action]:
    scene = context.scene
    animation = source.camera

    if animation.euler_zxy:
        raise BFRESAnimationError(
            "This camera uses BFRES Euler-ZXY rotation, which is not present "
            "in Odyssey's DemoCamera package and is not yet supported."
        )

    active = context.view_layer.objects.active
    camera_object = active if active is not None and active.type == "CAMERA" else None

    if camera_object is None:
        camera_data = bpy.data.cameras.new(source.scene.name)
        camera_object = bpy.data.objects.new(source.scene.name, camera_data)
        context.collection.objects.link(camera_object)
    else:
        camera_data = camera_object.data

    camera_object.rotation_mode = "QUATERNION"
    camera_data.type = "PERSP" if animation.perspective else "ORTHO"
    camera_data.sensor_fit = "VERTICAL"

    object_samples = {
        "location": [[], [], []],
        "rotation_quaternion": [[], [], [], []],
    }
    data_samples = {
        "lens": [],
        "clip_start": [],
        "clip_end": [],
    }
    previous_rotation = None

    for frame in range(animation.frame_count + 1):
        position = _game_vector(
            animation.evaluate("position_x", frame),
            animation.evaluate("position_y", frame),
            animation.evaluate("position_z", frame),
        )
        target = _game_vector(
            animation.evaluate("rotation_x", frame),
            animation.evaluate("rotation_y", frame),
            animation.evaluate("rotation_z", frame),
        )
        twist = animation.evaluate("twist", frame)
        rotation = _camera_rotation(position, target, twist)

        if previous_rotation is not None and rotation.dot(previous_rotation) < 0.0:
            rotation.negate()

        previous_rotation = rotation.copy()

        for index, value in enumerate(position):
            object_samples["location"][index].append((frame, float(value)))
        for index, value in enumerate(rotation):
            object_samples["rotation_quaternion"][index].append(
                (frame, float(value))
            )

        fov = animation.evaluate("field_of_view", frame)
        data_samples["lens"].append(
            (frame, _lens_from_vertical_fov(camera_data, fov))
        )
        data_samples["clip_start"].append(
            (
                frame,
                max(
                    animation.evaluate("clip_near", frame)
                    * _TRANSLATION_SCALE,
                    1e-6,
                ),
            )
        )
        data_samples["clip_end"].append(
            (
                frame,
                max(
                    animation.evaluate("clip_far", frame)
                    * _TRANSLATION_SCALE,
                    1e-5,
                ),
            )
        )

    object_action, object_fcurves, object_groups = _new_action_channels(
        camera_object,
        source.scene.name,
        "OBJECT",
    )
    camera_action = None

    try:
        transform_group = object_groups.new(name="Camera Transform")

        for data_path, components in object_samples.items():
            for index, values in enumerate(components):
                _write_curve(
                    object_fcurves,
                    transform_group,
                    data_path,
                    index,
                    tuple(values),
                )

        camera_action, data_fcurves, data_groups = _new_action_channels(
            camera_data,
            f"{source.scene.name} Camera Data",
            "CAMERA",
        )
        optics_group = data_groups.new(name="Camera Optics")

        for data_path, values in data_samples.items():
            _write_scalar_curve(data_fcurves, optics_group, data_path, values)
    except Exception:
        if camera_action is not None:
            bpy.data.actions.remove(camera_action)
        bpy.data.actions.remove(object_action)
        raise

    aspect = animation.evaluate("aspect_ratio", 0.0)

    if math.isfinite(aspect) and aspect > 0.0:
        rendered_ratio = scene.render.resolution_x / max(
            scene.render.resolution_y,
            1,
        )
        if aspect >= rendered_ratio:
            scene.render.pixel_aspect_x = aspect / rendered_ratio
            scene.render.pixel_aspect_y = 1.0
        else:
            scene.render.pixel_aspect_x = 1.0
            scene.render.pixel_aspect_y = rendered_ratio / aspect

    scene.camera = camera_object
    scene.frame_start = 0
    scene.frame_end = animation.frame_count
    scene.render.fps = 60
    scene.render.fps_base = 1.0

    for action in (object_action, camera_action):
        action["smo_camera_animation_import"] = True
        action["smo_source_archive"] = str(source.archive_path)
        action["smo_source_bfres"] = source.bfres_name
        action["smo_source_scene_animation"] = source.scene.name
        action["smo_source_camera_animation"] = animation.name
        action["smo_frame_count"] = animation.frame_count + 1
        action["smo_looping"] = animation.looping
        action["smo_translation_scale"] = _TRANSLATION_SCALE
        action["smo_coordinate_basis"] = "(X, Y, Z) -> (X, -Z, Y)"
        action["smo_camera_rotation"] = "LOOK_AT_WITH_TWIST"
        action["smo_vertical_fov"] = True
        action["smo_aspect_ratio"] = float(aspect)

    scene.frame_set(0)
    camera_object.location = Vector(
        component[0][1] for component in object_samples["location"]
    )
    camera_object.rotation_quaternion = Quaternion(
        component[0][1]
        for component in object_samples["rotation_quaternion"]
    )
    camera_data.lens = data_samples["lens"][0][1]
    camera_data.clip_start = data_samples["clip_start"][0][1]
    camera_data.clip_end = data_samples["clip_end"][0][1]

    context.view_layer.objects.active = camera_object
    camera_object.select_set(True)
    return camera_object, object_action, camera_action


class SMO_OT_refresh_bfres_camera_animations(Operator):
    bl_idname = "smo.refresh_bfres_camera_animations"
    bl_label = "Refresh BFRES Camera Animations"
    bl_description = "Reload native BFRES camera animations from the package"

    def execute(self, context: bpy.types.Context) -> set[str]:
        clear_bfres_camera_cache()

        try:
            count = len(_camera_sources(context.scene))
        except Exception as exc:
            self.report({"ERROR"}, f"Could not read camera animations: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Found {count} BFRES camera animations.")

        if context.area is not None:
            context.area.tag_redraw()

        return {"FINISHED"}


class SMO_OT_apply_bfres_camera_animation(Operator):
    bl_idname = "smo.apply_bfres_camera_animation"
    bl_label = "Import Camera Animation"
    bl_description = (
        "Create or update the selected Blender camera from the native BFRES "
        "scene animation"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(
            getattr(context.scene, "smo_bfres_camera_animation", "")
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            source = _selected_camera_source(context.scene)
            camera_object, _object_action, _camera_action = (
                import_bfres_camera_animation(context, source)
            )
        except Exception as exc:
            print("[Odyssey Toolkit] BFRES camera import failed:")
            traceback.print_exc()
            self.report({"ERROR"}, f"Could not import camera animation: {exc}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Imported {source.scene.name} onto {camera_object.name} "
                f"({source.camera.frame_count + 1} frames)."
            ),
        )
        return {"FINISHED"}


class SMO_PT_bfres_camera_animations(Panel):
    bl_idname = "SMO_PT_bfres_camera_animations"
    bl_label = "BFRES Camera Animations"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Odyssey"
    bl_parent_id = "SMO_PT_models_and_animations"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        header = layout.row(align=True)
        active = context.view_layer.objects.active
        target = active if active is not None and active.type == "CAMERA" else None
        header.label(
            text=target.name if target is not None else "New Camera",
            icon="CAMERA_DATA",
        )
        header.operator(
            "smo.refresh_bfres_camera_animations",
            text="",
            icon="FILE_REFRESH",
        )

        package = layout.box()
        package.prop(
            scene,
            "smo_bfres_camera_animation_package",
            text="Camera Package",
        )

        raw_path = str(
            getattr(scene, "smo_bfres_camera_animation_package", "") or ""
        ).strip()
        default_path = _default_camera_package(scene)
        package.label(
            text=(
                f"Using {Path(bpy.path.abspath(raw_path)).name}"
                if raw_path
                else f"Using {default_path.name} from ROMFS"
                if default_path is not None
                else "Choose a camera package"
            ),
            icon="PACKAGE",
        )

        try:
            sources = _camera_sources(scene)
        except Exception as exc:
            error = layout.row()
            error.alert = True
            error.label(text=str(exc), icon="ERROR")
            return

        if not sources:
            layout.label(text="No BFRES camera animations found", icon="INFO")
            return

        layout.label(text=f"{len(sources)} available camera clips")
        layout.prop(scene, "smo_bfres_camera_animation", text="")

        try:
            source = _selected_camera_source(scene)
        except BFRESAnimationError:
            source = None

        if source is not None:
            details = layout.row(align=True)
            details.label(text=f"{source.camera.frame_count + 1} frames")

            if source.camera.looping:
                details.label(text="Loops", icon="FILE_TICK")

            layout.label(
                text=(
                    "Perspective, look-at + twist"
                    if source.camera.perspective
                    else "Orthographic, look-at + twist"
                ),
                icon="CAMERA_DATA",
            )

        use_row = layout.row()
        use_row.scale_y = 1.25
        use_row.operator(
            "smo.apply_bfres_camera_animation",
            icon="CAMERA_DATA",
        )


def _camera_package_updated(
    _scene: bpy.types.Scene,
    context: bpy.types.Context,
) -> None:
    clear_bfres_camera_cache()

    if context.area is not None:
        context.area.tag_redraw()


def register_bfres_camera_properties() -> None:
    bpy.types.Scene.smo_bfres_camera_animation_package = StringProperty(
        name="Camera Package",
        description=(
            "SZS or BFRES package containing native camera animations; "
            "leave blank to use ObjectData/DemoCamera.szs from the ROMFS"
        ),
        subtype="FILE_PATH",
        update=_camera_package_updated,
    )
    bpy.types.Scene.smo_bfres_camera_animation = EnumProperty(
        name="Camera Animation",
        description="Native BFRES scene camera animation to import",
        items=bfres_camera_enum_items,
    )


def unregister_bfres_camera_properties() -> None:
    if hasattr(bpy.types.Scene, "smo_bfres_camera_animation"):
        del bpy.types.Scene.smo_bfres_camera_animation
    if hasattr(bpy.types.Scene, "smo_bfres_camera_animation_package"):
        del bpy.types.Scene.smo_bfres_camera_animation_package

    clear_bfres_camera_cache()

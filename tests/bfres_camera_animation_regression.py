from __future__ import annotations

import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import smo_kingdom_importer as addon
from smo_kingdom_importer.bfres_camera_import import (
    _camera_sources,
    _game_vector,
    import_bfres_camera_animation,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def close(actual: float, expected: float, tolerance: float = 1e-5) -> None:
    check(
        math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance),
        f"{actual!r} != {expected!r}",
    )


def run(romfs_root: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon.register()

    try:
        scene = bpy.context.scene
        camera_package = romfs_root / "ObjectData" / "DemoCamera.szs"
        scene.smo_bfres_camera_animation_package = str(camera_package)
        sources = _camera_sources(scene)
        check(len(sources) == 51, f"Expected 51 camera clips, found {len(sources)}")
        opening = next(
            source for source in sources if source.scene.name == "DemoOpening01"
        )
        animation = opening.camera
        check(animation.name == "AnimCamera", "Unexpected camera resource name")
        check(animation.frame_count == 5408, "Opening camera frame count changed")
        check(animation.perspective, "Opening camera is no longer perspective")
        check(not animation.euler_zxy, "Opening camera unexpectedly uses Euler ZXY")
        check(len(animation.curves) == 8, "Opening camera curve count changed")
        close(animation.base_values[0], 10.0)
        close(animation.base_values[1], 100000.0)
        close(animation.base_values[2], 1.5)
        close(animation.base_values[3], 0.3980221450328827)

        camera_object, object_action, camera_action = (
            import_bfres_camera_animation(bpy.context, opening)
        )
        check(scene.camera is camera_object, "Imported camera was not activated")
        check(scene.frame_start == 0 and scene.frame_end == 5408, "Frame range wrong")
        check(scene.render.fps == 60, "Camera import did not select 60 FPS")
        check(object_action["smo_camera_animation_import"], "Object metadata missing")
        check(camera_action["smo_camera_animation_import"], "Data metadata missing")
        check(camera_object.rotation_mode == "QUATERNION", "Rotation mode wrong")
        check(camera_object.data.sensor_fit == "VERTICAL", "FOV axis is not vertical")

        rendered_ratio = (
            scene.render.resolution_x
            * scene.render.pixel_aspect_x
            / (
                scene.render.resolution_y
                * scene.render.pixel_aspect_y
            )
        )
        close(rendered_ratio, 1.5)

        for frame in (0, 2704, 5408):
            scene.frame_set(frame)
            expected_position = _game_vector(
                animation.evaluate("position_x", frame),
                animation.evaluate("position_y", frame),
                animation.evaluate("position_z", frame),
            )
            check(
                (camera_object.location - expected_position).length < 2e-4,
                f"Camera location is wrong at frame {frame}",
            )
            expected_target = _game_vector(
                animation.evaluate("rotation_x", frame),
                animation.evaluate("rotation_y", frame),
                animation.evaluate("rotation_z", frame),
            )
            expected_direction = (expected_target - expected_position).normalized()
            actual_direction = (
                camera_object.rotation_quaternion @ Vector((0.0, 0.0, -1.0))
            ).normalized()
            check(
                actual_direction.dot(expected_direction) > 0.99999,
                f"Camera look-at direction is wrong at frame {frame}",
            )
            close(
                camera_object.data.angle_y,
                animation.evaluate("field_of_view", frame),
                tolerance=2e-5,
            )
            close(
                camera_object.data.clip_start,
                animation.evaluate("clip_near", frame) * 0.01,
                tolerance=2e-5,
            )
            close(
                camera_object.data.clip_end,
                animation.evaluate("clip_far", frame) * 0.01,
                tolerance=2e-5,
            )

        print("BFRES_CAMERA_ANIMATION_REGRESSION: PASS")
    finally:
        addon.unregister()


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: bfres_camera_animation_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())

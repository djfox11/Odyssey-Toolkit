from __future__ import annotations

import math
import unittest

from pure_module_loader import load_toolkit_module


stage_data = load_toolkit_module("stage_data")


def assert_tuple_almost_equal(
    test: unittest.TestCase,
    actual: tuple[float, ...],
    expected: tuple[float, ...],
) -> None:
    test.assertEqual(len(actual), len(expected))
    for actual_value, expected_value in zip(actual, expected):
        test.assertAlmostEqual(actual_value, expected_value)


class StageCoordinateTests(unittest.TestCase):
    def test_game_coordinates_scale_and_axis_mapping(self) -> None:
        self.assertEqual(
            stage_data.game_position_to_blender(
                stage_data.Vector3(100.0, 200.0, 300.0)
            ),
            (1.0, -3.0, 2.0),
        )
        self.assertEqual(
            stage_data.game_scale_to_blender(
                stage_data.Vector3(2.0, 3.0, 4.0)
            ),
            (2.0, 4.0, 3.0),
        )

    def test_game_quaternion_is_normalised_and_axes_are_mapped(self) -> None:
        converted = stage_data.game_quaternion_to_blender(
            (2.0, 2.0, 4.0, 6.0)
        )
        magnitude = math.sqrt(60.0)
        assert_tuple_almost_equal(
            self,
            converted,
            (2.0 / magnitude, 2.0 / magnitude, -6.0 / magnitude, 4.0 / magnitude),
        )

    def test_game_euler_rotation_converts_to_blender_quaternion(self) -> None:
        converted = stage_data.game_rotation_to_blender(
            stage_data.Vector3(0.0, 0.0, 90.0)
        )
        root_half = math.sqrt(0.5)
        assert_tuple_almost_equal(
            self,
            converted,
            (root_half, 0.0, -root_half, 0.0),
        )


class NestedZoneTransformTests(unittest.TestCase):
    def test_two_nested_transforms_compose_translation_rotation_and_scale(self) -> None:
        identity = (1.0, 0.0, 0.0, 0.0)
        root = stage_data.GameTransform(
            translate=stage_data.Vector3(10.0, 0.0, 0.0),
            rotation_quaternion=stage_data._game_rotation_quaternion(
                stage_data.Vector3(0.0, 0.0, 90.0)
            ),
            scale=stage_data.Vector3(2.0, 2.0, 2.0),
        )
        zone = stage_data.GameTransform(
            translate=stage_data.Vector3(1.0, 0.0, 0.0),
            rotation_quaternion=stage_data._game_rotation_quaternion(
                stage_data.Vector3(0.0, 0.0, 90.0)
            ),
            scale=stage_data.Vector3(0.5, 0.5, 0.5),
        )
        actor = stage_data.GameTransform(
            translate=stage_data.Vector3(1.0, 0.0, 0.0),
            rotation_quaternion=identity,
            scale=stage_data.Vector3(1.0, 2.0, 3.0),
        )

        world_zone = stage_data._compose_game_transforms(root, zone)
        world_actor = stage_data._compose_game_transforms(world_zone, actor)

        assert_tuple_almost_equal(
            self,
            (world_zone.translate.x, world_zone.translate.y, world_zone.translate.z),
            (10.0, 2.0, 0.0),
        )
        assert_tuple_almost_equal(
            self,
            (world_actor.translate.x, world_actor.translate.y, world_actor.translate.z),
            (9.0, 2.0, 0.0),
        )
        assert_tuple_almost_equal(
            self,
            (world_actor.scale.x, world_actor.scale.y, world_actor.scale.z),
            (1.0, 2.0, 3.0),
        )
        assert_tuple_almost_equal(
            self,
            world_actor.rotation_quaternion,
            (0.0, 0.0, 0.0, 1.0),
        )


if __name__ == "__main__":
    unittest.main()

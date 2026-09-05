from __future__ import annotations

import unittest

from pure_module_loader import load_toolkit_module


bfres_animation = load_toolkit_module("bfres_animation")


def curve(
    curve_type: int,
    frames: tuple[float, ...],
    keys: tuple[tuple[float, ...], ...],
    *,
    scale: float = 1.0,
    offset: float = 0.0,
) -> object:
    return bfres_animation.AnimationCurve(
        data_offset=0x10,
        curve_type=curve_type,
        frames=frames,
        keys=keys,
        scale=scale,
        offset=offset,
        start_frame=frames[0],
        end_frame=frames[-1],
    )


class AnimationCurveTests(unittest.TestCase):
    def test_linear_curve_interpolates_scaled_and_offset_values(self) -> None:
        animation = curve(
            bfres_animation._CURVE_LINEAR,
            (0.0, 10.0),
            ((2.0, 0.0), (6.0, 0.0)),
            scale=2.0,
            offset=1.0,
        )
        self.assertEqual(animation.evaluate(-1.0), 5.0)
        self.assertEqual(animation.evaluate(5.0), 9.0)
        self.assertEqual(animation.evaluate(20.0), 13.0)

    def test_stepped_integer_and_boolean_curves_hold_left_key(self) -> None:
        integer = curve(
            bfres_animation._CURVE_STEP_INT,
            (0.0, 10.0),
            ((2.0,), (6.0,)),
            scale=100.0,
            offset=1.0,
        )
        boolean = curve(
            bfres_animation._CURVE_STEP_BOOL,
            (0.0, 10.0),
            ((0.0,), (1.0,)),
            offset=99.0,
        )
        self.assertEqual(integer.evaluate(9.999), 3.0)
        self.assertEqual(integer.evaluate(10.0), 7.0)
        self.assertEqual(boolean.evaluate(9.999), 0.0)
        self.assertEqual(boolean.evaluate(10.0), 1.0)

    def test_baked_float_curve_interpolates_between_samples(self) -> None:
        animation = curve(
            bfres_animation._CURVE_BAKED_FLOAT,
            (0.0, 1.0, 2.0),
            ((0.0,), (4.0,), (8.0,)),
        )
        self.assertEqual(animation.evaluate(1.5), 6.0)

    def test_baked_integer_and_boolean_curves_hold_samples(self) -> None:
        integer = curve(
            bfres_animation._CURVE_BAKED_INT,
            (0.0, 1.0),
            ((10.0,), (20.0,)),
            offset=2.0,
        )
        boolean = curve(
            bfres_animation._CURVE_BAKED_BOOL,
            (0.0, 1.0),
            ((0.0,), (1.0,)),
        )
        self.assertEqual(integer.evaluate(0.5), 12.0)
        self.assertEqual(boolean.evaluate(0.5), 0.0)

    def test_cubic_curve_uses_authored_tangents(self) -> None:
        animation = curve(
            bfres_animation._CURVE_CUBIC,
            (0.0, 10.0),
            ((0.0, 10.0, 0.0, 0.0), (10.0, 0.0, 0.0, 0.0)),
        )
        self.assertAlmostEqual(animation.evaluate(2.5), 2.5)
        self.assertAlmostEqual(animation.evaluate(5.0), 5.0)
        self.assertAlmostEqual(animation.evaluate(7.5), 7.5)


if __name__ == "__main__":
    unittest.main()

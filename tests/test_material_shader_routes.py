from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "odyssey_toolkit" / "static_model_import.py"


def load_route_symbols() -> dict[str, object]:
    wanted = {
        "_BLEND_COMPONENT_CHANNELS",
        "_ColorShaderRoute",
        "_apply_blend_channel",
        "_float4_parameter",
        "_shader_blend_coefficient_route",
        "_shader_blend_equation",
        "_shader_blend_route",
        "_shader_source_route",
    }
    module = ast.parse(
        SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(SOURCE_PATH),
    )
    body: list[ast.stmt] = [
        ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")],
            level=0,
        )
    ]

    for node in module.body:
        name = getattr(node, "name", None)
        if name in wanted:
            body.append(node)
            continue
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in wanted
            for target in node.targets
        ):
            body.append(node)

    extracted = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(extracted)
    namespace: dict[str, object] = {"Any": Any, "dataclass": dataclass}
    exec(compile(extracted, str(SOURCE_PATH), "exec"), namespace)
    return namespace


ROUTES = load_route_symbols()
shader_source_route = ROUTES["_shader_source_route"]
shader_blend_equation = ROUTES.get("_shader_blend_equation")


class LiquidMaterialRouteTests(unittest.TestCase):
    def resolve(
        self,
        source_code: int,
        options: dict[str, str],
        parameters: dict[str, object],
        textures: dict[str, str],
    ) -> Any:
        return shader_source_route(
            source_code,
            options,
            parameters,
            textures,
            {},
            set(),
        )

    def test_poison_blend_uses_authored_coefficient_map(self) -> None:
        route = self.resolve(
            80,
            {
                "enable_blend0": "1",
                "blend0_src": "60",
                "blend0_src_ch": "10",
                "blend0_dst": "61",
                "blend0_dst_ch": "10",
                "blend0_cof": "20",
                "blend0_cof_ch": "10",
                "blend0_cof_map": "10",
                "blend0_eq": "0",
                "blend0_post": "0",
            },
            {
                "const_color0": (0.21, 0.003, 0.13, 1.0),
                "const_color1": (0.10, 0.0, 0.09, 1.0),
                "base_color_mul_color": (1.0, 1.0, 1.0, 1.0),
            },
            {"_a0": "GroundPoisonPurple00_rep_alb"},
        )

        self.assertIsNotNone(route)
        self.assertEqual(route.equation, 0)
        self.assertEqual(route.coefficient.kind, "TEXTURE")
        self.assertEqual(
            route.coefficient.texture_name,
            "GroundPoisonPurple00_rep_alb",
        )

    def test_lava_emission_resolves_nested_coefficient_blend(self) -> None:
        route = self.resolve(
            82,
            {
                "enable_blend0": "1",
                "blend0_src": "50",
                "blend0_src_ch": "10",
                "blend0_dst": "51",
                "blend0_dst_ch": "10",
                "blend0_cof": "116",
                "blend0_cof_ch": "10",
                "blend0_eq": "2",
                "blend0_post": "0",
                "enable_blend1": "1",
                "blend1_src": "60",
                "blend1_src_ch": "10",
                "blend1_dst": "61",
                "blend1_dst_ch": "10",
                "blend1_cof": "20",
                "blend1_cof_ch": "10",
                "blend1_cof_map": "80",
                "blend1_eq": "1",
                "blend1_post": "0",
                "enable_blend2": "1",
                "blend2_src": "81",
                "blend2_src_ch": "10",
                "blend2_dst": "62",
                "blend2_dst_ch": "10",
                "blend2_cof": "116",
                "blend2_cof_ch": "10",
                "blend2_eq": "1",
                "blend2_post": "0",
                "sphere_const_color2": "2",
            },
            {
                "const_color0": (96.0, 4.0, 7.0, 1.0),
                "const_color1": (200.0, 12.0, 13.0, 1.0),
                "const_color2": (81.0, 4.0, 43.0, 1.0),
                "sphere_rate_color2": 4.5,
            },
            {"_u0": "LavaHotSide_ind", "_u1": "LavaHotSide_ind"},
        )

        self.assertIsNotNone(route)
        self.assertEqual(route.blend_index, 2)
        self.assertEqual(route.source.blend_index, 1)
        self.assertEqual(route.source.coefficient.blend_index, 0)
        self.assertEqual(getattr(route.destination, "sphere_type", None), 2)
        self.assertEqual(getattr(route.destination, "sphere_rate", None), 4.5)

    def test_equation_one_weights_destination_not_source(self) -> None:
        self.assertIsNotNone(shader_blend_equation)
        self.assertEqual(
            shader_blend_equation(1),
            "SOURCE_PLUS_DESTINATION_TIMES_COEFFICIENT",
        )

    def test_equation_two_multiplies_all_three_inputs(self) -> None:
        self.assertIsNotNone(shader_blend_equation)
        self.assertEqual(shader_blend_equation(2), "MULTIPLY_ALL")

    def test_hot_lava_saturating_post_operation_is_retained(self) -> None:
        route = self.resolve(
            80,
            {
                "enable_blend0": "1",
                "blend0_src": "52",
                "blend0_src_ch": "10",
                "blend0_dst": "51",
                "blend0_dst_ch": "10",
                "blend0_cof": "20",
                "blend0_cof_ch": "10",
                "blend0_cof_map": "50",
                "blend0_eq": "1",
                "blend0_post": "10",
            },
            {},
            {"_u0": "MaskA", "_u1": "MaskB", "_u2": "MaskC"},
        )

        self.assertIsNotNone(route)
        self.assertEqual(route.post, 10)
        self.assertEqual(route.source.texture_name, "MaskC")
        self.assertEqual(route.coefficient.texture_name, "MaskA")

    def test_normalize_post_operation_remains_unhandled(self) -> None:
        route = self.resolve(
            80,
            {
                "enable_blend0": "1",
                "blend0_src": "50",
                "blend0_src_ch": "10",
                "blend0_dst": "51",
                "blend0_dst_ch": "10",
                "blend0_cof": "116",
                "blend0_cof_ch": "10",
                "blend0_eq": "1",
                "blend0_post": "20",
            },
            {},
            {"_u0": "A", "_u1": "B"},
        )

        self.assertIsNone(route)


if __name__ == "__main__":
    unittest.main()

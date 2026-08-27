from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from shader_material_regression import blender_material, material
from smo_kingdom_importer.static_model_import import (
    _resolve_material_shader,
    _tex_srt_affine_matrix,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def close_tuple(
    actual: tuple[float, ...],
    expected: tuple[float, ...],
    tolerance: float = 1e-6,
) -> bool:
    return len(actual) == len(expected) and all(
        math.isclose(left, right, abs_tol=tolerance)
        for left, right in zip(actual, expected)
    )


def replace_option(
    options: tuple[tuple[str, str], ...],
    name: str,
    value: str,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (option_name, value if option_name == name else option_value)
        for option_name, option_value in options
    )


def run(romfs_root: Path) -> None:
    for mode in range(3):
        identity = _tex_srt_affine_matrix(
            (mode, 1.0, 1.0, 0.0, 0.0, 0.0)
        )
        check(identity is not None, f"TexSrt mode {mode} did not resolve")
        check(
            close_tuple(identity, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)),
            f"TexSrt mode {mode} identity changed: {identity}",
        )

    expected_maya = (
        2.165063509461097,
        -1.2499999999999998,
        0.5424682452694516,
        0.7499999999999999,
        1.299038105676658,
        0.02548094716167104,
    )
    maya = _tex_srt_affine_matrix(
        (0, 2.5, 1.5, math.pi / 6.0, 0.1, -0.2)
    )
    check(maya is not None and close_tuple(maya, expected_maya),
          f"Maya TexSrt conversion changed: {maya}")
    check(
        _tex_srt_affine_matrix((7, 1.0, 1.0, 0.0, 0.0, 0.0)) is None,
        "Unsupported TexSrt mode was translated speculatively",
    )

    glass = material(
        romfs_root,
        "CityWorldHomeBuilding000",
        "GlassBuilding1F00",
    )
    resolved_glass = _resolve_material_shader(glass)
    check(resolved_glass is not None, "Metro glass shader did not resolve")
    secondary = next(
        (
            (texture_name, route)
            for texture_name, route in resolved_glass.texture_coordinates
            if route.uv_index == 1 and "_u2" in route.shader_samplers
        ),
        None,
    )
    check(secondary is not None, "Metro glass uniform2 did not select _u1")
    secondary_texture, secondary_route = secondary
    check(secondary_route.fuv_index == 1,
          "Metro glass uniform2 did not select fuv1")
    check(secondary_route.matrix is None,
          "Metro glass fuv1 unexpectedly gained a transform")

    glass_material, _ = blender_material(glass, "shader-uv-glass")
    secondary_node = next(
        node
        for node in glass_material.node_tree.nodes
        if node.get("smo_texture_name") == secondary_texture
    )
    check(secondary_node.inputs["Vector"].is_linked,
          "Secondary-UV texture has no explicit coordinate link")
    uv_node = secondary_node.inputs["Vector"].links[0].from_node
    check(uv_node.bl_idname == "ShaderNodeUVMap",
          "Untransformed secondary UV created an unnecessary transform")
    check(uv_node.uv_map == "UVMap.001",
          f"Secondary texture selected {uv_node.uv_map!r}")
    coordinate_metadata = json.loads(
        glass_material["smo_texture_coordinates"]
    )
    check(
        coordinate_metadata[secondary_texture]["uv_layer"] == "UVMap.001",
        "Material UV provenance metadata is incorrect",
    )

    cage = material(romfs_root, "CageShine", "GlassMT00")
    resolved_cage = _resolve_material_shader(cage)
    check(resolved_cage is not None, "CageShine glass shader did not resolve")
    cage_transform = next(
        (
            (texture_name, route)
            for texture_name, route in resolved_cage.texture_coordinates
            if route.transform_parameter == "tex_mtx0"
        ),
        None,
    )
    check(cage_transform is not None,
          "CageShine tex_mtx0 did not reach a texture route")
    cage_texture, cage_route = cage_transform
    check(cage_route.fuv_index == 1 and cage_route.uv_index == 1,
          "CageShine tex_mtx0 did not use fuv1/_u1")
    check(
        cage_route.matrix is not None
        and close_tuple(
            cage_route.matrix,
            (2.0, 0.0, 0.0, 0.0, 1.0, 0.0),
        ),
        f"CageShine real TexSrt matrix is wrong: {cage_route.matrix}",
    )
    cage_material, _ = blender_material(cage, "shader-uv-cage")
    cage_node = next(
        node
        for node in cage_material.node_tree.nodes
        if node.get("smo_texture_name") == cage_texture
    )
    check(
        cage_node.inputs["Vector"].is_linked
        and cage_node.inputs["Vector"].links[0].from_node.bl_idname
        == "ShaderNodeCombineXYZ",
        "CageShine real TexSrt did not create an affine coordinate chain",
    )

    missing_uv_sets = list(glass.uv_sets)
    missing_uv_sets[1] = None
    missing_uv_mesh = replace(glass, uv_sets=tuple(missing_uv_sets))
    missing_uv = _resolve_material_shader(missing_uv_mesh)
    check(missing_uv is not None, "Missing-UV shader metadata was discarded")
    check(
        secondary_texture not in dict(missing_uv.texture_coordinates),
        "Missing secondary UV was connected speculatively",
    )
    check(
        any(
            texture_name == secondary_texture and "mesh has no _u1" in reason
            for texture_name, reason
            in missing_uv.unhandled_texture_coordinates
        ),
        "Missing secondary UV was not retained as unhandled metadata",
    )

    unsupported_shader = replace(
        glass.material_shader,
        shader_options=replace_option(
            glass.material_shader.shader_options,
            "uniform2_fuv_selector",
            "60",
        ),
    )
    unsupported = _resolve_material_shader(
        replace(glass, material_shader=unsupported_shader)
    )
    check(unsupported is not None, "Unsupported FUV shader was discarded")
    check(
        any(
            texture_name == secondary_texture
            and "unsupported uniform2_fuv_selector=60" in reason
            for texture_name, reason
            in unsupported.unhandled_texture_coordinates
        ),
        "Unsupported generated FUV was not retained as metadata",
    )

    metal = material(
        romfs_root,
        "CityWorldHomeBuilding000",
        "MetalWallMain00",
    )
    metal_shader = metal.material_shader
    check(metal_shader is not None, "Metro metal shader data is missing")
    transformed_parameters = tuple(
        replace(
            parameter,
            value=(0, 2.5, 1.5, math.pi / 6.0, 0.1, -0.2),
        )
        if parameter.name == "tex_mtx0"
        else parameter
        for parameter in metal_shader.parameters
    )
    transformed_mesh = replace(
        metal,
        material_shader=replace(
            metal_shader,
            parameters=transformed_parameters,
        ),
    )
    transformed = _resolve_material_shader(transformed_mesh)
    check(transformed is not None, "Synthetic transformed shader did not resolve")
    transformed_routes = tuple(
        route
        for _, route in transformed.texture_coordinates
        if route.transform_parameter == "tex_mtx0"
    )
    check(transformed_routes, "tex_mtx0 did not reach any texture route")
    check(
        all(
            route.matrix is not None
            and close_tuple(route.matrix, expected_maya)
            for route in transformed_routes
        ),
        "Resolved texture route has the wrong affine matrix",
    )

    transformed_material, _ = blender_material(
        transformed_mesh,
        "shader-uv-transform",
    )
    transformed_texture = transformed_routes[0].texture_name
    transformed_node = next(
        node
        for node in transformed_material.node_tree.nodes
        if node.get("smo_texture_name") == transformed_texture
    )
    check(transformed_node.inputs["Vector"].is_linked,
          "Transformed texture has no coordinate link")
    combine = transformed_node.inputs["Vector"].links[0].from_node
    check(
        combine.bl_idname == "ShaderNodeCombineXYZ",
        "TexSrt did not create an explicit affine coordinate chain",
    )
    add_x = combine.inputs["X"].links[0].from_node
    add_y = combine.inputs["Y"].links[0].from_node
    dot_x = add_x.inputs[0].links[0].from_node
    dot_y = add_y.inputs[0].links[0].from_node
    check(
        close_tuple(
            tuple(dot_x.inputs[1].default_value[:2]),
            expected_maya[:2],
        )
        and math.isclose(
            add_x.inputs[1].default_value,
            expected_maya[2],
            abs_tol=1e-6,
        )
        and close_tuple(
            tuple(dot_y.inputs[1].default_value[:2]),
            expected_maya[3:5],
        )
        and math.isclose(
            add_y.inputs[1].default_value,
            expected_maya[5],
            abs_tol=1e-6,
        ),
        "TexSrt affine coefficients were wired to the wrong node inputs",
    )
    check(
        not any(
            node.bl_idname == "ShaderNodeMapping"
            for node in transformed_material.node_tree.nodes
        ),
        "TexSrt depends on Blender Mapping-node operation order",
    )

    print(
        "SHADER_UV_TRANSFORM_REGRESSION: PASS "
        f"secondary_texture={secondary_texture} "
        f"transformed_routes={len(transformed_routes)}"
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: shader_uv_transform_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())

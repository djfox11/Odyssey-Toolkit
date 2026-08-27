from __future__ import annotations

from array import array
from dataclasses import dataclass
from hashlib import blake2s
import json
import math
import re
import time
import traceback
from pathlib import Path
from typing import Any

import bpy
from bpy.types import Operator
from mathutils import Matrix

from .performance import (
    PerformanceTimings,
    print_performance_summary,
    reset_active_timings,
    set_active_timings,
    timed,
)

from .placement_classifier import (
    IMPORT_CATEGORY_FILTERS,
    import_filter_group,
)
from .resource_rules import texture_archive_rule_names

try:
    import numpy as _numpy
except ImportError:
    _numpy = None


SourceKey = tuple[Path, str]
AssetContextKey = tuple[Path, str, tuple[Path, ...], bool]
ImageCacheKey = tuple[SourceKey, str, str, str]
TextureBinding = tuple[str, str, str, bpy.types.Image]


@dataclass(slots=True, frozen=True)
class MeshRigBinding:
    rig_key: str
    model_name: str
    armature_name: str
    skeleton: Any
    bone_weights: tuple[tuple[tuple[int, float], ...], ...]
    source_archive: Path | None = None
    source_bfres: str = ""

_OCEAN_WAVE_HALF_EXTENT_METRES = 2500.0
_SKY_HDR_RGBM_RANGE = 8.0
_CLOUD_DENSITY_SCALE = 8.0
_TEXTURE_ROLE_MARKERS = {
    "ALBEDO": ("_alb", ".alb", "_albedo", ".albedo"),
    "ROUGHNESS": ("_rgh", ".rgh", "_rough", ".rough"),
    "METALLIC": ("_mtl", ".mtl", "_metal", ".metal"),
    "NORMAL": ("_nrm", ".nrm", "_normal", ".normal", "_nor", ".nor"),
    "EMISSION": ("_emm", ".emm", "_emiss", ".emiss", "_emi", ".emi"),
}


def _identity_digest(*parts: object) -> str:
    value = "\0".join(
        str(part).replace("\\", "/").casefold()
        for part in parts
    )
    return blake2s(value.encode("utf-8"), digest_size=5).hexdigest()


def _asset_context_key(
    asset_key: SourceKey,
    shared_texture_paths: tuple[Path, ...],
    ignore_texture_alpha: bool = False,
) -> AssetContextKey:
    return (*asset_key, shared_texture_paths, ignore_texture_alpha)


def _stage_texture_archive_names(stage_name: str) -> tuple[str, ...]:
    names = [f"{stage_name}Texture.szs"]

    if stage_name.endswith("Stage"):
        names.append(f"{stage_name[:-len('Stage')]}Texture.szs")

    family_match = re.match(r"^([A-Za-z0-9]+World)", stage_name)

    if family_match is not None:
        names.append(f"{family_match.group(1)}HomeStageTexture.szs")

    return tuple(dict.fromkeys(names))


def _texture_role(texture_name: str, sampler_name: str = "") -> str:
    lowered = texture_name.casefold()

    for role, markers in _TEXTURE_ROLE_MARKERS.items():
        if any(marker in lowered for marker in markers):
            return role

    return {
        "_a0": "ALBEDO",
        "_n0": "NORMAL",
        "_e0": "EMISSION",
    }.get(sampler_name.casefold(), "UNASSIGNED")


@dataclass(slots=True, frozen=True)
class _ColorShaderRoute:
    source_code: int
    kind: str
    value: tuple[float, float, float, float] | None = None
    texture_name: str | None = None
    multiplier: tuple[float, float, float, float] | None = None
    channel: str | None = None
    child: _ColorShaderRoute | None = None
    blend_index: int | None = None
    equation: int | None = None
    source: _ColorShaderRoute | None = None
    destination: _ColorShaderRoute | None = None
    coefficient: _ColorShaderRoute | None = None


@dataclass(slots=True, frozen=True)
class _ScalarShaderRoute:
    source_code: int
    kind: str
    value: float | None = None
    texture_name: str | None = None
    channel: str | None = None
    color_route: _ColorShaderRoute | None = None
    invert: bool = False


@dataclass(slots=True, frozen=True)
class _TextureCoordinateRoute:
    texture_name: str
    shader_samplers: tuple[str, ...]
    fuv_index: int
    uv_index: int
    transform_parameter: str | None
    matrix: tuple[float, float, float, float, float, float] | None


@dataclass(slots=True, frozen=True)
class _ResolvedClothNoV:
    color: _ColorShaderRoute | None
    emission: _ColorShaderRoute | None
    mask: _ScalarShaderRoute | None
    reverse: bool
    emission_scale_type: int
    emission_scale: float
    peak_intensity: float
    peak_position: float
    peak_power: float
    slope: float
    tone_power: float
    uses_random_noise_mask: bool
    noise_mask_scale: float


@dataclass(slots=True, frozen=True)
class _ResolvedMaterialShader:
    shader_archive_name: str
    shading_model_name: str
    base_color_texture: str | None
    base_color_multiplier: tuple[float, float, float, float] | None
    base_color: _ColorShaderRoute | None
    emission: _ColorShaderRoute | None
    normal_texture: str | None
    roughness: _ScalarShaderRoute | None
    metallic: _ScalarShaderRoute | None
    alpha: _ScalarShaderRoute | None
    ao: _ScalarShaderRoute | None
    sss: _ScalarShaderRoute | None
    transmission: _ScalarShaderRoute | None
    refraction_eta: _ScalarShaderRoute | None
    refraction_color: _ColorShaderRoute | None
    transparent: bool
    alpha_mask_threshold: float | None
    display_face: str
    cloth_nov: _ResolvedClothNoV | None
    texture_extensions: tuple[tuple[str, str], ...]
    shader_options: tuple[tuple[str, str], ...]
    texture_coordinates: tuple[tuple[str, _TextureCoordinateRoute], ...]
    unhandled_texture_coordinates: tuple[tuple[str, str], ...]
    unhandled_outputs: tuple[tuple[str, str], ...]
    inactive_outputs: tuple[tuple[str, str], ...]


_COMPONENT_CHANNELS = {
    30: "Red",
    40: "Green",
    50: "Blue",
    60: "Alpha",
}
_INVERTED_COMPONENT_CHANNELS = {
    70: "Red",
    80: "Green",
    90: "Blue",
    100: "Alpha",
}

_BLEND_COMPONENT_CHANNELS = {
    20: "Red",
    30: "Green",
    40: "Blue",
    50: "Alpha",
}


def _float4_parameter(
    parameters: dict[str, object],
    name: str,
) -> tuple[float, float, float, float] | None:
    value = parameters.get(name)
    if not isinstance(value, tuple) or len(value) != 4:
        return None
    return tuple(float(component) for component in value)


def _shader_source_route(
    source_code: int,
    options: dict[str, str],
    parameters: dict[str, object],
    texture_lookup: dict[str, str],
    blend_cache: dict[int, _ColorShaderRoute | None],
    active_blends: set[int],
) -> _ColorShaderRoute | None:
    if source_code == 10:
        multiplier = _float4_parameter(
            parameters, "base_color_mul_color"
        ) or (1.0, 1.0, 1.0, 1.0)
        texture_name = texture_lookup.get("_a0")
        if texture_name is None:
            return _ColorShaderRoute(
                source_code,
                "CONSTANT",
                value=multiplier,
            )
        return _ColorShaderRoute(
            source_code,
            "TEXTURE",
            texture_name=texture_name,
            multiplier=multiplier,
        )

    if 50 <= source_code <= 54:
        uniform_index = source_code - 50
        texture_name = texture_lookup.get(f"_u{uniform_index}")
        if texture_name is None:
            return None
        return _ColorShaderRoute(
            source_code,
            "TEXTURE",
            texture_name=texture_name,
            multiplier=_float4_parameter(
                parameters, f"uniform{uniform_index}_mul_color"
            ),
        )

    if 60 <= source_code <= 63:
        value = _float4_parameter(
            parameters, f"const_color{source_code - 60}"
        )
        if value is not None:
            return _ColorShaderRoute(source_code, "CONSTANT", value=value)

    if 80 <= source_code <= 85:
        return _shader_blend_route(
            source_code - 80,
            options,
            parameters,
            texture_lookup,
            blend_cache,
            active_blends,
        )

    if source_code in {115, 116}:
        value = float(source_code - 115)
        return _ColorShaderRoute(
            source_code,
            "CONSTANT",
            value=(value, value, value, value),
        )

    return None


def _apply_blend_channel(
    route: _ColorShaderRoute,
    channel_code: int,
) -> _ColorShaderRoute | None:
    if channel_code == 10:
        return route
    channel = _BLEND_COMPONENT_CHANNELS.get(channel_code)
    if channel is None:
        return None
    return _ColorShaderRoute(
        route.source_code,
        "CHANNEL",
        channel=channel,
        child=route,
    )


def _shader_blend_coefficient_route(
    source_code: int,
    options: dict[str, str],
    parameters: dict[str, object],
    texture_lookup: dict[str, str],
    blend_cache: dict[int, _ColorShaderRoute | None],
    active_blends: set[int],
) -> _ColorShaderRoute | None:
    if 30 <= source_code <= 33:
        value = parameters.get(f"const_single{source_code - 30}")
        if isinstance(value, (float, int)):
            scalar = float(value)
            return _ColorShaderRoute(
                source_code,
                "CONSTANT",
                value=(scalar, scalar, scalar, scalar),
            )
        return None
    return _shader_source_route(
        source_code,
        options,
        parameters,
        texture_lookup,
        blend_cache,
        active_blends,
    )


def _shader_blend_route(
    blend_index: int,
    options: dict[str, str],
    parameters: dict[str, object],
    texture_lookup: dict[str, str],
    blend_cache: dict[int, _ColorShaderRoute | None],
    active_blends: set[int],
) -> _ColorShaderRoute | None:
    if blend_index in blend_cache:
        return blend_cache[blend_index]
    if blend_index in active_blends:
        return None
    if options.get(f"enable_blend{blend_index}") != "1":
        blend_cache[blend_index] = None
        return None

    try:
        equation = int(options[f"blend{blend_index}_eq"])
        post = int(options.get(f"blend{blend_index}_post", "0"))
        source_code = int(options[f"blend{blend_index}_src"])
        destination_code = int(options[f"blend{blend_index}_dst"])
        coefficient_code = int(options[f"blend{blend_index}_cof"])
        source_channel = int(
            options.get(f"blend{blend_index}_src_ch", "10")
        )
        destination_channel = int(
            options.get(f"blend{blend_index}_dst_ch", "10")
        )
        coefficient_channel = int(
            options.get(f"blend{blend_index}_cof_ch", "10")
        )
    except (KeyError, TypeError, ValueError):
        blend_cache[blend_index] = None
        return None

    if equation not in {0, 1, 2} or post != 0:
        blend_cache[blend_index] = None
        return None

    active_blends.add(blend_index)
    try:
        source = _shader_source_route(
            source_code,
            options,
            parameters,
            texture_lookup,
            blend_cache,
            active_blends,
        )
        destination = _shader_source_route(
            destination_code,
            options,
            parameters,
            texture_lookup,
            blend_cache,
            active_blends,
        )
        coefficient = _shader_blend_coefficient_route(
            coefficient_code,
            options,
            parameters,
            texture_lookup,
            blend_cache,
            active_blends,
        )
    finally:
        active_blends.remove(blend_index)

    if source is not None:
        source = _apply_blend_channel(source, source_channel)
    if destination is not None:
        destination = _apply_blend_channel(
            destination, destination_channel
        )
    if coefficient is not None:
        coefficient = _apply_blend_channel(
            coefficient, coefficient_channel
        )
    if source is None or destination is None or coefficient is None:
        blend_cache[blend_index] = None
        return None

    route = _ColorShaderRoute(
        80 + blend_index,
        "BLEND",
        blend_index=blend_index,
        equation=equation,
        source=source,
        destination=destination,
        coefficient=coefficient,
    )
    blend_cache[blend_index] = route
    return route


def _shader_texture_lookup(source: Any, shader: Any) -> dict[str, str]:
    original_bindings = tuple(getattr(shader, "texture_bindings", ()))
    textures = tuple(
        texture_name for texture_name, _ in original_bindings
    ) or tuple(getattr(source, "texture_names", ()))
    samplers = tuple(
        sampler_name for _, sampler_name in original_bindings
    ) or tuple(getattr(source, "texture_sampler_names", ()))
    texture_by_resource_sampler = {
        samplers[index].casefold(): texture_name
        for index, texture_name in enumerate(textures)
        if index < len(samplers) and samplers[index]
    }
    return {
        shader_sampler.casefold(): texture_by_resource_sampler[
            resource_sampler.casefold()
        ]
        for shader_sampler, resource_sampler in shader.sampler_assignments
        if resource_sampler.casefold() in texture_by_resource_sampler
    }


def _sampler_fuv_option(shader_sampler: str) -> str | None:
    sampler = shader_sampler.casefold()

    if sampler == "_a0":
        return "base_color_fuv_selector"
    if sampler == "_n0":
        return "normal_fuv_selector"

    match = re.fullmatch(r"_u([0-4])", sampler)
    if match is not None:
        return f"uniform{match.group(1)}_fuv_selector"

    return None


def _tex_srt_affine_matrix(
    value: object,
) -> tuple[float, float, float, float, float, float] | None:
    if not isinstance(value, tuple) or len(value) < 6:
        return None

    try:
        mode = int(value[0])
        scale_x, scale_y, rotation, translate_x, translate_y = (
            float(component) for component in value[1:6]
        )
    except (TypeError, ValueError):
        return None

    if mode not in {0, 1, 2} or not all(
        math.isfinite(component)
        for component in (
            scale_x,
            scale_y,
            rotation,
            translate_x,
            translate_y,
        )
    ):
        return None

    cosine = math.cos(rotation)
    sine = math.sin(rotation)
    scale_x_cosine = scale_x * cosine
    scale_x_sine = scale_x * sine
    scale_y_cosine = scale_y * cosine
    scale_y_sine = scale_y * sine

    if mode == 0:  # Maya
        values = (
            scale_x_cosine,
            -scale_y_sine,
            scale_x_sine,
            scale_y_cosine,
            -0.5 * (scale_x_cosine + scale_x_sine - scale_x)
            - scale_x * translate_x,
            -0.5 * (scale_y_cosine - scale_y_sine + scale_y)
            + scale_y * translate_y
            + 1.0,
        )
    elif mode == 1:  # 3ds Max
        values = (
            scale_x_cosine,
            -scale_y_sine,
            scale_x_sine,
            scale_y_cosine,
            -scale_x_cosine * (translate_x + 0.5)
            + scale_x_sine * (translate_y - 0.5)
            + 0.5,
            scale_y_sine * (translate_x + 0.5)
            + scale_y_cosine * (translate_y - 0.5)
            + 0.5,
        )
    else:  # Softimage
        values = (
            scale_x_cosine,
            scale_y_sine,
            -scale_x_sine,
            scale_y_cosine,
            scale_x_sine
            - scale_x_cosine * translate_x
            - scale_x_sine * translate_y,
            -scale_y_cosine
            - scale_y_sine * translate_x
            + scale_y_cosine * translate_y
            + 1.0,
        )

    # The matrix is column-major. Mesh UVs and image rows both convert from
    # Nintendo's top-left V convention, so conjugate it by V -> 1 - V.
    m00, m10, m01, m11, offset_x, offset_y = values
    matrix = (
        m00,
        -m01,
        m01 + offset_x,
        -m10,
        m11,
        1.0 - m11 - offset_y,
    )
    return matrix if all(math.isfinite(component) for component in matrix) else None


def _resolve_texture_coordinate_routes(
    source: Any,
    shader: Any,
    options: dict[str, str],
    texture_lookup: dict[str, str],
) -> tuple[
    tuple[tuple[str, _TextureCoordinateRoute], ...],
    tuple[tuple[str, str], ...],
]:
    attributes = {
        name.casefold(): value.casefold()
        for name, value in shader.attribute_assignments
    }
    parameters = {parameter.name: parameter for parameter in shader.parameters}
    uv_sets = tuple(getattr(source, "uv_sets", ()))
    routes: dict[str, _TextureCoordinateRoute] = {}
    unhandled: list[tuple[str, str]] = []
    conflicted_textures: set[str] = set()

    def reject(texture_name: str, sampler: str, reason: str) -> None:
        unhandled.append((texture_name, f"{sampler}: {reason}"))

    for shader_sampler, texture_name in sorted(texture_lookup.items()):
        option_name = _sampler_fuv_option(shader_sampler)
        if option_name is None:
            continue

        try:
            selected_fuv = int(options[option_name])
        except (KeyError, TypeError, ValueError):
            reject(texture_name, shader_sampler, f"missing {option_name}")
            continue

        if not 10 <= selected_fuv <= 13:
            reject(
                texture_name,
                shader_sampler,
                f"unsupported {option_name}={selected_fuv}",
            )
            continue

        fuv_index = selected_fuv - 10
        if options.get(f"enable_fuv{fuv_index}") == "0":
            reject(texture_name, shader_sampler, f"fuv{fuv_index} is disabled")
            continue

        try:
            shader_uv_index = int(options[f"fuv{fuv_index}_selector"])
        except (KeyError, TypeError, ValueError):
            reject(texture_name, shader_sampler, f"missing fuv{fuv_index}_selector")
            continue

        shader_attribute = f"_u{shader_uv_index}"
        resource_attribute = attributes.get(shader_attribute, shader_attribute)
        attribute_match = re.fullmatch(r"_u([0-7])", resource_attribute)
        if attribute_match is None:
            reject(texture_name, shader_sampler, f"unsupported UV attribute {resource_attribute!r}")
            continue

        uv_index = int(attribute_match.group(1))
        if uv_index >= len(uv_sets) or uv_sets[uv_index] is None:
            reject(texture_name, shader_sampler, f"mesh has no _u{uv_index} data")
            continue

        try:
            matrix_selector = int(options.get(f"fuv{fuv_index}_mtx", "0"))
        except (TypeError, ValueError):
            matrix_selector = -1

        transform_parameter = None
        matrix = None
        if matrix_selector != 0:
            if not 1 <= matrix_selector <= 4:
                reject(texture_name, shader_sampler, f"unsupported fuv{fuv_index}_mtx={matrix_selector}")
                continue

            transform_parameter = f"tex_mtx{matrix_selector - 1}"
            parameter = parameters.get(transform_parameter)
            if parameter is None or parameter.type_id not in {30, 31}:
                reject(texture_name, shader_sampler, f"missing {transform_parameter} TexSrt parameter")
                continue

            matrix = _tex_srt_affine_matrix(parameter.value)
            if matrix is None:
                reject(texture_name, shader_sampler, f"unsupported {transform_parameter} value")
                continue

        candidate = _TextureCoordinateRoute(
            texture_name=texture_name,
            shader_samplers=(shader_sampler,),
            fuv_index=fuv_index,
            uv_index=uv_index,
            transform_parameter=transform_parameter,
            matrix=matrix,
        )
        existing = routes.get(texture_name)

        if texture_name in conflicted_textures:
            continue
        if existing is None:
            routes[texture_name] = candidate
            continue
        if (
            existing.fuv_index == candidate.fuv_index
            and existing.uv_index == candidate.uv_index
            and existing.transform_parameter == candidate.transform_parameter
            and existing.matrix == candidate.matrix
        ):
            routes[texture_name] = _TextureCoordinateRoute(
                texture_name=texture_name,
                shader_samplers=existing.shader_samplers + (shader_sampler,),
                fuv_index=existing.fuv_index,
                uv_index=existing.uv_index,
                transform_parameter=existing.transform_parameter,
                matrix=existing.matrix,
            )
            continue

        routes.pop(texture_name, None)
        conflicted_textures.add(texture_name)
        reject(texture_name, shader_sampler, "texture is sampled through multiple coordinate routes")

    return tuple(sorted(routes.items())), tuple(unhandled)


def _shader_scalar_route(
    output_name: str,
    component_name: str,
    options: dict[str, str],
    parameters: dict[str, object],
    texture_lookup: dict[str, str],
    blend_cache: dict[int, _ColorShaderRoute | None],
    active_blends: set[int],
) -> _ScalarShaderRoute | None:
    try:
        source_code = int(options[output_name])
    except (KeyError, TypeError, ValueError):
        return None

    try:
        component_code = int(options.get(component_name, "30"))
    except (TypeError, ValueError):
        return None

    channel = _COMPONENT_CHANNELS.get(component_code)
    invert = False
    if channel is None:
        channel = _INVERTED_COMPONENT_CHANNELS.get(component_code)
        invert = channel is not None

    if source_code == 10 and channel is not None:
        texture_name = texture_lookup.get("_a0")
        if texture_name is not None:
            return _ScalarShaderRoute(
                source_code,
                "TEXTURE",
                texture_name=texture_name,
                channel=channel,
                invert=invert,
            )

    if 50 <= source_code <= 54 and channel is not None:
        texture_name = texture_lookup.get(f"_u{source_code - 50}")
        if texture_name is not None:
            return _ScalarShaderRoute(
                source_code,
                "TEXTURE",
                texture_name=texture_name,
                channel=channel,
                invert=invert,
            )

    if 60 <= source_code <= 63 and channel is not None:
        color = _float4_parameter(
            parameters, f"const_color{source_code - 60}"
        )
        if color is not None:
            component_index = {
                "Red": 0,
                "Green": 1,
                "Blue": 2,
                "Alpha": 3,
            }[channel]
            return _ScalarShaderRoute(
                source_code,
                "CONSTANT",
                value=color[component_index],
                invert=invert,
            )

    if 80 <= source_code <= 85 and channel is not None:
        color_route = _shader_blend_route(
            source_code - 80,
            options,
            parameters,
            texture_lookup,
            blend_cache,
            active_blends,
        )
        if color_route is not None:
            return _ScalarShaderRoute(
                source_code,
                "COLOR_ROUTE",
                channel=channel,
                color_route=color_route,
                invert=invert,
            )

    if 110 <= source_code <= 113:
        value = parameters.get(f"const_single{source_code - 110}")
        if isinstance(value, (float, int)):
            return _ScalarShaderRoute(source_code, "CONSTANT", float(value))

    if source_code in {115, 116}:
        return _ScalarShaderRoute(
            source_code,
            "CONSTANT",
            float(source_code - 115),
        )

    return None


def _finite_shader_float(
    parameters: dict[str, object],
    name: str,
    default: float,
) -> float:
    value = parameters.get(name, default)
    if not isinstance(value, (float, int)):
        return default
    result = float(value)
    return result if math.isfinite(result) else default


def _resolve_cloth_nov(
    options: dict[str, str],
    parameters: dict[str, object],
    texture_lookup: dict[str, str],
    blend_cache: dict[int, _ColorShaderRoute | None],
    active_blends: set[int],
) -> _ResolvedClothNoV | None:
    if options.get("enable_cloth_nov") != "1":
        return None

    def color_output(name: str) -> _ColorShaderRoute | None:
        try:
            source_code = int(options[name])
        except (KeyError, TypeError, ValueError):
            return None
        return _shader_source_route(
            source_code,
            options,
            parameters,
            texture_lookup,
            blend_cache,
            active_blends,
        )

    color = color_output("o_cloth_map")
    emission = color_output("o_cloth_emission_map")
    mask = _shader_scalar_route(
        "o_cloth_mask_map",
        "cloth_mask_component",
        options,
        parameters,
        texture_lookup,
        blend_cache,
        active_blends,
    )
    if color is None and emission is None:
        return None

    try:
        emission_scale_type = int(
            options.get("cloth_nov_emission_scale_type", "0")
        )
    except (TypeError, ValueError):
        emission_scale_type = 0

    return _ResolvedClothNoV(
        color=color,
        emission=emission,
        mask=mask,
        reverse=options.get("is_cloth_nov_reverse") == "1",
        emission_scale_type=emission_scale_type,
        emission_scale=max(
            0.0,
            _finite_shader_float(
                parameters, "cloth_nov_emission_scale0", 0.0
            ),
        ),
        peak_intensity=max(
            0.0,
            _finite_shader_float(
                parameters, "cloth_nov_peak_intensity0", 0.0
            ),
        ),
        peak_position=min(
            1.0,
            max(
                0.0,
                _finite_shader_float(
                    parameters, "cloth_nov_peak_pos0", 0.75
                ),
            ),
        ),
        peak_power=max(
            1.0e-4,
            _finite_shader_float(
                parameters, "cloth_nov_peak_pow0", 2.0
            ),
        ),
        slope=max(
            0.0,
            _finite_shader_float(parameters, "cloth_nov_slope0", 1.0),
        ),
        tone_power=max(
            1.0e-4,
            _finite_shader_float(
                parameters, "cloth_nov_tone_pow0", 1.0
            ),
        ),
        uses_random_noise_mask=(
            options.get("is_cloth_nov_use_rnd_noise_mask") == "1"
        ),
        noise_mask_scale=max(
            0.0,
            _finite_shader_float(
                parameters, "cloth_nov_noise_mask_scale0", 0.0
            ),
        ),
    )


def _resolve_material_shader(source: Any) -> _ResolvedMaterialShader | None:
    shader = getattr(source, "material_shader", None)

    if shader is None or (
        shader.shader_archive_name.casefold() != "alrendermaterial"
        or shader.shading_model_name.casefold() != "alrendermaterial"
    ):
        return None

    options = dict(shader.shader_options)
    parameters = {
        parameter.name: parameter.value for parameter in shader.parameters
    }
    texture_lookup = _shader_texture_lookup(source, shader)
    blend_cache: dict[int, _ColorShaderRoute | None] = {}
    active_blends: set[int] = set()
    texture_coordinates, unhandled_texture_coordinates = (
        _resolve_texture_coordinate_routes(source, shader, options, texture_lookup)
    )

    def output_route(name: str) -> _ColorShaderRoute | None:
        try:
            source_code = int(options[name])
        except (KeyError, TypeError, ValueError):
            return None
        return _shader_source_route(
            source_code,
            options,
            parameters,
            texture_lookup,
            blend_cache,
            active_blends,
        )

    base_color = output_route("o_base_color")
    emission_enabled = options.get("enable_emission") == "1"
    emission = output_route("o_emission") if emission_enabled else None
    base_color_texture = (
        base_color.texture_name
        if base_color is not None
        and base_color.kind == "TEXTURE"
        else None
    )
    base_color_multiplier = (
        base_color.multiplier
        if base_color is not None
        and base_color.kind == "TEXTURE"
        else None
    )
    normal_texture = (
        texture_lookup.get("_n0")
        if options.get("enable_normal") == "1"
        and options.get("o_normal") == "20"
        else None
    )
    roughness = _shader_scalar_route(
        "o_roughness",
        "roughness_component",
        options,
        parameters,
        texture_lookup,
        blend_cache,
        active_blends,
    )
    metallic = _shader_scalar_route(
        "o_metalness",
        "metalness_component",
        options,
        parameters,
        texture_lookup,
        blend_cache,
        active_blends,
    )
    alpha = _shader_scalar_route(
        "o_alpha",
        "alpha_component",
        options,
        parameters,
        texture_lookup,
        blend_cache,
        active_blends,
    )

    ao = (
        _shader_scalar_route(
            "o_ao", "ao_component", options, parameters,
            texture_lookup, blend_cache, active_blends,
        )
        if options.get("enable_ao") == "1"
        else None
    )
    sss = (
        _shader_scalar_route(
            "o_sss", "sss_component", options, parameters,
            texture_lookup, blend_cache, active_blends,
        )
        if options.get("enable_sss") == "1"
        else None
    )
    transparent_feature = (
        options.get("enable_transparent") == "1"
        or options.get("enable_translucent") == "1"
    )
    transmission = (
        _shader_scalar_route(
            "o_refract_rate", "refract_rate_component", options,
            parameters, texture_lookup, blend_cache, active_blends,
        )
        if transparent_feature
        else None
    )
    refraction_eta = (
        _shader_scalar_route(
            "o_refract_eta", "refract_eta_component", options,
            parameters, texture_lookup, blend_cache, active_blends,
        )
        if transparent_feature
        else None
    )
    refraction_color = (
        output_route("o_refract_color") if transparent_feature else None
    )

    render_infos = {
        info.name: info.values for info in shader.render_infos
    }
    display_face_values = render_infos.get("display_face", ())
    display_face = (
        str(display_face_values[0]).casefold()
        if display_face_values
        else "front"
    )
    try:
        render_type = int(options.get("cRenderType", "0"))
    except (TypeError, ValueError):
        render_type = 0
    forward_xlu_values = render_infos.get("forward_xlu", ())
    forward_xlu = (
        str(forward_xlu_values[0]).casefold()
        if forward_xlu_values
        else "opa"
    )
    transparent = (
        transparent_feature or render_type != 0 or forward_xlu != "opa"
    )
    alpha_mask_threshold = (
        _finite_shader_float(parameters, "alpha_test_value", 0.5)
        if options.get("enable_alphamask") == "1"
        else None
    )

    cloth_nov = _resolve_cloth_nov(
        options,
        parameters,
        texture_lookup,
        blend_cache,
        active_blends,
    )

    sampler_states = {sampler.name: sampler for sampler in shader.samplers}
    original_bindings = tuple(getattr(shader, "texture_bindings", ()))
    if not original_bindings:
        original_bindings = tuple(
            zip(
                getattr(source, "texture_names", ()),
                getattr(source, "texture_sampler_names", ()),
            )
        )
    resource_sampler_by_texture = dict(original_bindings)
    texture_extensions = []
    for texture_name, sampler_name in resource_sampler_by_texture.items():
        sampler = sampler_states.get(sampler_name)
        if sampler is None:
            continue
        if sampler.wrap_u != sampler.wrap_v:
            continue
        extension = {
            0: "REPEAT",
            1: "MIRROR",
        }.get(sampler.wrap_u, "EXTEND")
        texture_extensions.append((texture_name, extension))

    optional_output_enabled = {
        "o_base_color": options.get("enable_base_color") == "1",
        "o_normal": options.get("enable_normal") == "1",
        "o_emission": emission_enabled,
        "o_ao": options.get("enable_ao") == "1",
        "o_sss": options.get("enable_sss") == "1",
        "o_cloth_map": options.get("enable_cloth_nov") == "1",
        "o_cloth_emission_map": options.get("enable_cloth_nov") == "1",
        "o_cloth_mask_map": options.get("enable_cloth_nov") == "1",
        "o_refract_color": transparent_feature,
        "o_refract_rate": transparent_feature,
        "o_refract_eta": transparent_feature,
        "o_transparent_tex": options.get("enable_transparent") == "1",
        "o_structural_eta": options.get("enable_structural_color") == "1",
        "o_metal_flake_power": (
            options.get("metal_flake_emission_scale_type", "0") != "0"
        ),
    }
    inactive_outputs = tuple(
        (name, value)
        for name, value in shader.shader_options
        if name in optional_output_enabled
        and not optional_output_enabled[name]
    )
    inactive_output_names = {name for name, _ in inactive_outputs}
    handled_outputs = {
        "o_base_color" if base_color is not None else "",
        "o_normal" if normal_texture is not None else "",
        "o_emission" if emission is not None else "",
        "o_roughness" if roughness is not None else "",
        "o_metalness" if metallic is not None else "",
        "o_alpha" if alpha is not None else "",
        "o_ao" if ao is not None else "",
        "o_sss" if sss is not None else "",
        "o_refract_color" if refraction_color is not None else "",
        "o_refract_rate" if transmission is not None else "",
        "o_refract_eta" if refraction_eta is not None else "",
        (
            "o_cloth_map"
            if cloth_nov is not None and cloth_nov.color is not None
            else ""
        ),
        (
            "o_cloth_emission_map"
            if cloth_nov is not None and cloth_nov.emission is not None
            else ""
        ),
        (
            "o_cloth_mask_map"
            if cloth_nov is not None and cloth_nov.mask is not None
            else ""
        ),
        *inactive_output_names,
    }
    unhandled_outputs = tuple(
        (name, value)
        for name, value in shader.shader_options
        if name.startswith("o_") and name not in handled_outputs
    )
    return _ResolvedMaterialShader(
        shader_archive_name=shader.shader_archive_name,
        shading_model_name=shader.shading_model_name,
        base_color_texture=base_color_texture,
        base_color_multiplier=base_color_multiplier,
        base_color=base_color,
        emission=emission,
        normal_texture=normal_texture,
        roughness=roughness,
        metallic=metallic,
        alpha=alpha,
        ao=ao,
        sss=sss,
        transmission=transmission,
        refraction_eta=refraction_eta,
        refraction_color=refraction_color,
        transparent=transparent,
        alpha_mask_threshold=alpha_mask_threshold,
        display_face=display_face,
        cloth_nov=cloth_nov,
        texture_extensions=tuple(texture_extensions),
        shader_options=shader.shader_options,
        texture_coordinates=texture_coordinates,
        unhandled_texture_coordinates=unhandled_texture_coordinates,
        unhandled_outputs=unhandled_outputs,
        inactive_outputs=inactive_outputs,
    )


def _color_route_metadata(
    route: _ColorShaderRoute | None,
) -> dict[str, object] | None:
    if route is None:
        return None
    result: dict[str, object] = {
        "kind": route.kind,
        "source": route.source_code,
    }
    if route.value is not None:
        result["value"] = route.value
    if route.texture_name is not None:
        result["texture"] = route.texture_name
    if route.multiplier is not None:
        result["multiplier"] = route.multiplier
    if route.channel is not None:
        result["channel"] = route.channel
    if route.blend_index is not None:
        result["blend"] = route.blend_index
    if route.equation is not None:
        result["equation"] = route.equation
    if route.child is not None:
        result["child"] = _color_route_metadata(route.child)
    if route.source is not None:
        result["source_route"] = _color_route_metadata(route.source)
    if route.destination is not None:
        result["destination_route"] = _color_route_metadata(
            route.destination
        )
    if route.coefficient is not None:
        result["coefficient_route"] = _color_route_metadata(
            route.coefficient
        )
    return result


def _scalar_route_metadata(
    route: _ScalarShaderRoute | None,
) -> dict[str, object] | None:
    if route is None:
        return None
    return {
        "kind": route.kind,
        "source": route.source_code,
        "texture": route.texture_name,
        "channel": route.channel,
        "value": route.value,
        "color_route": _color_route_metadata(route.color_route),
        "invert": route.invert,
    }

def _cloth_nov_metadata(
    cloth_nov: _ResolvedClothNoV | None,
) -> dict[str, object] | None:
    if cloth_nov is None:
        return None
    return {
        "approximation": "masked NoV tone with angular peak modulation",
        "color": _color_route_metadata(cloth_nov.color),
        "emission": _color_route_metadata(cloth_nov.emission),
        "mask": _scalar_route_metadata(cloth_nov.mask),
        "reverse": cloth_nov.reverse,
        "emission_scale_type": cloth_nov.emission_scale_type,
        "emission_scale": cloth_nov.emission_scale,
        "peak_intensity": cloth_nov.peak_intensity,
        "peak_position": cloth_nov.peak_position,
        "peak_power": cloth_nov.peak_power,
        "slope": cloth_nov.slope,
        "tone_power": cloth_nov.tone_power,
        "uses_random_noise_mask": cloth_nov.uses_random_noise_mask,
        "noise_mask_scale": cloth_nov.noise_mask_scale,
    }


def _color_route_textures(
    route: _ColorShaderRoute | None,
) -> tuple[str, ...]:
    if route is None:
        return ()
    names = []
    if route.texture_name is not None:
        names.append(route.texture_name)
    for child in (
        route.child,
        route.source,
        route.destination,
        route.coefficient,
    ):
        names.extend(_color_route_textures(child))
    return tuple(dict.fromkeys(names))


def _shader_texture_role_overrides(
    resolved: _ResolvedMaterialShader | None,
) -> dict[str, str]:
    if resolved is None:
        return {}

    roles: dict[str, str] = {}
    if (
        resolved.base_color is not None
        and resolved.base_color.kind == "TEXTURE"
        and resolved.base_color.texture_name is not None
    ):
        roles[resolved.base_color.texture_name] = "ALBEDO"
    if resolved.normal_texture is not None:
        roles[resolved.normal_texture] = "NORMAL"
    if resolved.cloth_nov is not None:
        for route in (
            resolved.cloth_nov.color,
            resolved.cloth_nov.emission,
        ):
            for texture_name in _color_route_textures(route):
                roles.setdefault(texture_name, "EMISSION")

    if (
        resolved.emission is not None
        and resolved.emission.kind == "TEXTURE"
        and resolved.emission.texture_name is not None
        and _texture_role(resolved.emission.texture_name)
        in {"UNASSIGNED", "EMISSION"}
    ):
        roles.setdefault(resolved.emission.texture_name, "EMISSION")
    for route, role in (
        (resolved.roughness, "ROUGHNESS"),
        (resolved.metallic, "METALLIC"),
    ):
        if route is not None and route.texture_name is not None:
            roles.setdefault(route.texture_name, role)
    return roles

def _reconstruct_normal_blue_python(rgba8: bytes) -> bytes:
    reconstructed = bytearray(rgba8)

    for offset in range(0, len(reconstructed), 4):
        x = max(-1.0, min(1.0, (reconstructed[offset] - 128) / 127.0))
        y = max(-1.0, min(1.0, (reconstructed[offset + 1] - 128) / 127.0))
        z = math.sqrt(max(0.0, 1.0 - x * x - y * y))
        reconstructed[offset + 2] = round((z * 0.5 + 0.5) * 255.0)

    return bytes(reconstructed)


def _reconstruct_normal_blue_array(rgba: Any) -> None:
    xy = rgba[:, :2].astype(_numpy.float64)
    xy -= 128.0
    xy /= 127.0
    _numpy.clip(xy, -1.0, 1.0, out=xy)
    blue = 1.0 - _numpy.square(xy).sum(axis=1)
    _numpy.maximum(blue, 0.0, out=blue)
    _numpy.sqrt(blue, out=blue)
    blue *= 0.5
    blue += 0.5
    blue *= 255.0
    rgba[:, 2] = _numpy.rint(blue).astype(_numpy.uint8)


def _reconstruct_normal_blue(rgba8: bytes) -> bytes:
    if _numpy is None:
        return _reconstruct_normal_blue_python(rgba8)

    if len(rgba8) % 4:
        raise ValueError("RGBA texture data length must be divisible by four.")

    rgba = _numpy.frombuffer(rgba8, dtype=_numpy.uint8).reshape(-1, 4).copy()
    _reconstruct_normal_blue_array(rgba)
    return rgba.tobytes()


def _rgba8_to_blender_pixels_python(
    rgba8: bytes,
    width: int,
    height: int,
    reconstruct_normal_blue: bool,
) -> array:
    if reconstruct_normal_blue:
        rgba8 = _reconstruct_normal_blue_python(rgba8)

    values = tuple(index / 255.0 for index in range(256))
    pixels = array("f")
    row_size = width * 4

    for row in range(height - 1, -1, -1):
        start = row * row_size
        pixels.extend(
            values[value]
            for value in rgba8[start : start + row_size]
        )

    return pixels


def _rgba8_to_blender_pixels(
    rgba8: bytes,
    width: int,
    height: int,
    reconstruct_normal_blue: bool,
) -> Any:
    expected_size = width * height * 4

    if len(rgba8) != expected_size:
        raise ValueError(
            f"Expected {expected_size} RGBA bytes for {width}x{height}, "
            f"found {len(rgba8)}."
        )

    if _numpy is None:
        return _rgba8_to_blender_pixels_python(
            rgba8,
            width,
            height,
            reconstruct_normal_blue,
        )

    rgba = _numpy.frombuffer(rgba8, dtype=_numpy.uint8).reshape(
        height,
        width,
        4,
    )

    if reconstruct_normal_blue:
        rgba = rgba.copy()
        _reconstruct_normal_blue_array(rgba.reshape(-1, 4))

    flipped = _numpy.ascontiguousarray(rgba[::-1], dtype=_numpy.float32)
    flipped /= _numpy.float32(255.0)
    return flipped.reshape(-1)

def _set_specular_ior_level(shader: bpy.types.Node) -> None:
    specular = shader.inputs.get("Specular IOR Level")

    if specular is not None:
        specular.default_value = 0.2


def _set_material_transparency(
    material: bpy.types.Material,
    has_transparency: bool,
) -> None:
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"

    if hasattr(material, "blend_method"):
        material.blend_method = "HASHED" if has_transparency else "OPAQUE"


def _atmosphere_shader_kind(source: Any) -> str | None:
    shader = getattr(source, "material_shader", None)
    archive_name = str(
        getattr(shader, "shader_archive_name", "")
    ).casefold()
    shading_model = str(
        getattr(shader, "shading_model_name", "")
    ).casefold()
    names = {archive_name, shading_model}

    if "alrendersky" in names:
        return "SKY"
    if "alrendercloudlayer" in names:
        return "CLOUD"
    return None


def _shader_parameter_values(
    shader_parameters: tuple[Any, ...],
) -> dict[str, object]:
    return {
        str(parameter.name): parameter.value
        for parameter in shader_parameters
    }


def _shader_colour_parameter(
    parameters: dict[str, object],
    name: str,
    default: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    value = parameters.get(name)

    if not isinstance(value, (tuple, list)) or len(value) < 3:
        return default

    components = tuple(float(component) for component in value[:4])

    if len(components) == 3:
        return (*components, default[3])
    return components


def _shader_render_info_scalar(
    render_infos: tuple[Any, ...],
    name: str,
    default: float,
) -> float:
    for info in render_infos:
        if str(info.name) != name or not info.values:
            continue
        try:
            return float(info.values[0])
        except (TypeError, ValueError):
            return default
    return default


def _apply_atmosphere_shader(
    material: bpy.types.Material,
    output: Any,
    principled: Any,
    texture_nodes: dict[str, Any],
    texture_name: str,
    atmosphere_kind: str | None,
    shader_parameters: tuple[Any, ...],
    shader_options: tuple[tuple[str, str], ...],
    shader_render_infos: tuple[Any, ...],
) -> bool | None:
    if atmosphere_kind not in {"SKY", "CLOUD"}:
        return None

    texture = texture_nodes.get(texture_name)

    if texture is None:
        return None

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    _clear_input_links(material.node_tree, output.inputs["Surface"])
    parameters = _shader_parameter_values(shader_parameters)
    options = dict(shader_options)

    if atmosphere_kind == "SKY":
        sky_scale = _shader_colour_parameter(
            parameters,
            "uSkyScale",
            (1.0, 1.0, 1.0, 1.0),
        )
        alpha_colour = _shader_colour_parameter(
            parameters,
            "uAlphaColor",
            (1.0, 1.0, 1.0, 1.0),
        )
        tint = nodes.new("ShaderNodeMixRGB")
        tint.name = "SMO Sky Colour Scale"
        tint.label = "alRenderSky uSkyScale x uAlphaColor"
        tint.blend_type = "MULTIPLY"
        tint.inputs[0].default_value = 1.0
        tint.inputs[2].default_value = tuple(
            sky_scale[index] * alpha_colour[index]
            for index in range(4)
        )
        tint.location = (-80.0, 160.0)
        links.new(texture.outputs["Color"], tint.inputs[1])

        multiplier = nodes.new("ShaderNodeMath")
        multiplier.name = "SMO Sky HDR Multiplier"
        multiplier.label = "RGBM alpha x 8"
        multiplier.operation = "MULTIPLY"
        multiplier.inputs[1].default_value = (
            _SKY_HDR_RGBM_RANGE * sky_scale[3] * alpha_colour[3]
        )
        multiplier.location = (-80.0, -40.0)
        links.new(texture.outputs["Alpha"], multiplier.inputs[0])

        hdr_decode = nodes.new("ShaderNodeVectorMath")
        hdr_decode.name = "SMO Sky HDR Decode"
        hdr_decode.label = "Decode sky RGBM radiance"
        hdr_decode.operation = "SCALE"
        hdr_decode.location = (150.0, 140.0)
        links.new(tint.outputs["Color"], hdr_decode.inputs["Vector"])
        links.new(multiplier.outputs["Value"], hdr_decode.inputs["Scale"])

        emission = nodes.new("ShaderNodeEmission")
        emission.name = "SMO Sky Emission"
        emission.label = "Light-independent sky radiance"
        emission.location = (390.0, 120.0)
        links.new(hdr_decode.outputs["Vector"], emission.inputs["Color"])
        links.new(emission.outputs["Emission"], output.inputs["Surface"])

        nodes.remove(principled)
        material.use_backface_culling = False
        material["smo_atmosphere_shader"] = json.dumps(
            {
                "kind": "SKY",
                "source_shader": "alRenderSky",
                "surface": "Emission",
                "hdr_encoding": "RGBM",
                "hdr_alpha_range": _SKY_HDR_RGBM_RANGE,
                "alpha_as_opacity": False,
                "uSkyScale": sky_scale,
                "uAlphaColor": alpha_colour,
            },
            sort_keys=True,
        )
        return False

    albedo = _shader_colour_parameter(
        parameters,
        "albedo",
        (1.0, 1.0, 1.0, 1.0),
    )
    tint = nodes.new("ShaderNodeMixRGB")
    tint.name = "SMO Cloud Albedo"
    tint.label = "alRenderCloudLayer albedo"
    tint.blend_type = "MULTIPLY"
    tint.inputs[0].default_value = 1.0
    tint.inputs[2].default_value = albedo
    tint.location = (-120.0, 200.0)
    links.new(texture.outputs["Color"], tint.inputs[1])
    cloud_colour = tint.outputs["Color"]

    vertex_colour = None
    if options.get("cIsMultVertexColor") == "1":
        vertex_colour = nodes.new("ShaderNodeVertexColor")
        vertex_colour.name = "SMO Cloud Vertex Color"
        vertex_colour.label = "BFRES Color"
        vertex_colour.layer_name = "Color"
        vertex_colour.location = (-360.0, -180.0)
        vertex_multiply = nodes.new("ShaderNodeMixRGB")
        vertex_multiply.name = "SMO Cloud Vertex Colour Multiply"
        vertex_multiply.label = "Cloud colour x BFRES vertex colour"
        vertex_multiply.blend_type = "MULTIPLY"
        vertex_multiply.inputs[0].default_value = 1.0
        vertex_multiply.location = (100.0, 220.0)
        links.new(cloud_colour, vertex_multiply.inputs[1])
        links.new(vertex_colour.outputs["Color"], vertex_multiply.inputs[2])
        cloud_colour = vertex_multiply.outputs["Color"]

    normal_socket = None
    normal_input = principled.inputs.get("Normal")
    if normal_input is not None and normal_input.is_linked:
        normal_socket = normal_input.links[0].from_socket

    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.name = "SMO Cloud Diffuse"
    diffuse.label = "Normal-aware cloud scattering"
    diffuse.inputs["Roughness"].default_value = 1.0
    diffuse.location = (350.0, 240.0)
    links.new(cloud_colour, diffuse.inputs["Color"])
    if normal_socket is not None:
        links.new(normal_socket, diffuse.inputs["Normal"])

    emission = nodes.new("ShaderNodeEmission")
    emission.name = "SMO Cloud Ambient Emission"
    emission.label = "Wrapped atmospheric light"
    emission.location = (350.0, 40.0)
    links.new(cloud_colour, emission.inputs["Color"])

    wrap_coefficient = min(
        max(
            _shader_render_info_scalar(
                shader_render_infos,
                "wrap_coef",
                0.7,
            ),
            0.0,
        ),
        1.0,
    )
    lighting_mix = nodes.new("ShaderNodeMixShader")
    lighting_mix.name = "SMO Cloud Lighting Mix"
    lighting_mix.label = "Diffuse - wrapped ambient light"
    lighting_mix.inputs[0].default_value = wrap_coefficient
    lighting_mix.location = (590.0, 170.0)
    links.new(diffuse.outputs["BSDF"], lighting_mix.inputs[1])
    links.new(emission.outputs["Emission"], lighting_mix.inputs[2])

    density = nodes.new("ShaderNodeMath")
    density.name = "SMO Cloud Density Remap"
    density.label = "Volumetric density - surface coverage"
    density.operation = "MULTIPLY"
    density.use_clamp = True
    density.inputs[1].default_value = _CLOUD_DENSITY_SCALE
    density.location = (120.0, -180.0)
    links.new(texture.outputs["Alpha"], density.inputs[0])
    density_socket = density.outputs["Value"]

    if vertex_colour is not None:
        vertex_density = nodes.new("ShaderNodeMath")
        vertex_density.name = "SMO Cloud Vertex Density"
        vertex_density.label = "Density x vertex alpha"
        vertex_density.operation = "MULTIPLY"
        vertex_density.use_clamp = True
        vertex_density.location = (350.0, -180.0)
        links.new(density_socket, vertex_density.inputs[0])
        links.new(vertex_colour.outputs["Alpha"], vertex_density.inputs[1])
        density_socket = vertex_density.outputs["Value"]

    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.name = "SMO Cloud Transparent"
    transparent.location = (590.0, -80.0)

    surface_mix = nodes.new("ShaderNodeMixShader")
    surface_mix.name = "SMO Cloud Density Mix"
    surface_mix.label = "Cloud density coverage"
    surface_mix.location = (820.0, 100.0)
    links.new(density_socket, surface_mix.inputs[0])
    links.new(transparent.outputs["BSDF"], surface_mix.inputs[1])
    links.new(lighting_mix.outputs["Shader"], surface_mix.inputs[2])
    links.new(surface_mix.outputs["Shader"], output.inputs["Surface"])

    nodes.remove(principled)
    material.use_backface_culling = False
    material["smo_atmosphere_shader"] = json.dumps(
        {
            "kind": "CLOUD",
            "source_shader": "alRenderCloudLayer",
            "surface": "density-masked diffuse/emission mix",
            "alpha_as_opacity": False,
            "density_scale": _CLOUD_DENSITY_SCALE,
            "albedo": albedo,
            "vertex_colour": vertex_colour is not None,
            "wrap_coefficient": wrap_coefficient,
        },
        sort_keys=True,
    )
    return True


def _iter_collection_tree(collection: bpy.types.Collection) -> Any:
    yield collection

    for child in collection.children:
        yield from _iter_collection_tree(child)


def _remove_generated_objects(
    objects: Any,
) -> None:
    generated = tuple(set(objects))
    mesh_data = {
        obj.data for obj in generated if obj.type == "MESH"
    }
    light_data = {
        obj.data for obj in generated if obj.type == "LIGHT"
    }
    armature_data = {
        obj.data for obj in generated if obj.type == "ARMATURE"
    }

    for obj in generated:
        bpy.data.objects.remove(obj, do_unlink=True)

    for mesh in mesh_data:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)

    for light in light_data:
        if light.users == 0:
            bpy.data.lights.remove(light)

    for armature in armature_data:
        if armature.users == 0:
            bpy.data.armatures.remove(armature)


def _generated_objects(
    collection: bpy.types.Collection,
    root: bpy.types.Object,
) -> set[bpy.types.Object]:
    return {
        obj
        for child in _iter_collection_tree(collection)
        for obj in child.objects
        if obj != root and obj.get("smo_static_model_generated")
    }


def _rename_previous_generated(
    objects: Any,
) -> tuple[
    tuple[tuple[bpy.types.Object, str], ...],
    tuple[tuple[Any, str], ...],
]:
    generated = tuple(objects)
    object_names = tuple((obj, obj.name) for obj in generated)
    data = tuple(
        {
            obj.data
            for obj in generated
            if obj.type in {"MESH", "LIGHT", "ARMATURE"}
            and obj.data is not None
        }
    )
    data_names = tuple((item, item.name) for item in data)

    for index, (obj, _name) in enumerate(object_names):
        obj.name = f"__SMO Previous Object {index:05d}"

    for index, (item, _name) in enumerate(data_names):
        item.name = f"__SMO Previous Data {index:05d}"

    return object_names, data_names


def _restore_previous_generated_names(
    name_state: tuple[
        tuple[tuple[bpy.types.Object, str], ...],
        tuple[tuple[Any, str], ...],
    ],
) -> None:
    object_names, data_names = name_state

    for item, name in data_names:
        item.name = name

    for obj, name in object_names:
        obj.name = name


def _clear_previous_import(
    collection: bpy.types.Collection,
    root: bpy.types.Object,
) -> None:
    _remove_generated_objects(_generated_objects(collection, root))

    from . import remove_empty_import_collections

    remove_empty_import_collections(collection, collection.name)


def _placeholder_material(name: str) -> bpy.types.Material:
    material_name = f"SMO Placeholder - {name}"
    material = bpy.data.materials.get(material_name)

    if material is None:
        material = bpy.data.materials.new(material_name)
        material.diffuse_color = (0.32, 0.22, 0.14, 1.0)
        material.roughness = 0.8

    return material


def _create_solid_material(
    asset_name: str,
    material_name: str,
    base_color: tuple[float, float, float, float],
    identity: str,
    shader_material: _ResolvedMaterialShader | None = None,
) -> bpy.types.Material:
    name = f"SMO [{identity}] {asset_name} - {material_name}"
    material = bpy.data.materials.get(name)

    if material is None:
        material = bpy.data.materials.new(name)

    colour = tuple(float(value) for value in base_color)
    is_water = "water" in material_name.casefold()

    if is_water:
        colour = (*colour[:3], min(colour[3], 0.55))

    roughness = 0.25 if is_water else 0.8
    metallic = (
        0.75
        if any(
            token in material_name.casefold()
            for token in ("fence", "metal")
        )
        else 0.0
    )
    material.use_nodes = True
    material.diffuse_color = colour
    material.roughness = roughness
    material.metallic = metallic
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (300.0, 0.0)
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Base Color"].default_value = colour
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Alpha"].default_value = colour[3]
    _set_specular_ior_level(shader)
    material.node_tree.links.new(
        shader.outputs["BSDF"],
        output.inputs["Surface"],
    )

    translated_transparency = _apply_resolved_shader_material(
        material,
        shader,
        {},
        shader_material,
        allow_transparency=True,
    )
    _set_material_transparency(
        material,
        colour[3] < 1.0
        if translated_transparency is None
        else translated_transparency,
    )
    material["smo_base_color"] = colour
    material["smo_simplified_water"] = is_water
    return material


def _create_ocean_wave_mesh() -> bpy.types.Mesh:
    identity = _identity_digest(
        "OceanWave",
        _OCEAN_WAVE_HALF_EXTENT_METRES,
    )
    mesh_name = f"OceanWave - Procedural Ocean [{identity}]"
    mesh = bpy.data.meshes.get(mesh_name)

    if mesh is not None:
        return mesh

    material = _create_solid_material(
        "OceanWave",
        "OceanWater",
        (0.02, 0.22, 0.34, 0.55),
        identity,
    )
    extent = _OCEAN_WAVE_HALF_EXTENT_METRES
    mesh = bpy.data.meshes.new(mesh_name)
    mesh.from_pydata(
        (
            (-extent, -extent, 0.0),
            (extent, -extent, 0.0),
            (extent, extent, 0.0),
            (-extent, extent, 0.0),
        ),
        (),
        ((0, 1, 2), (0, 2, 3)),
    )
    mesh.materials.append(material)
    mesh["smo_procedural_ocean"] = True
    mesh["smo_ocean_extent_metres"] = extent * 2.0
    mesh.update(calc_edges=True)
    return mesh


def _validated_vertex_array(source: Any) -> Any:
    vertices = _numpy.asarray(source.vertices, dtype=_numpy.float64)

    if vertices.ndim != 2 or vertices.shape[1:] != (3,):
        raise ValueError(
            "Mesh vertices must be a two-dimensional array of XYZ values."
        )

    if not _numpy.isfinite(vertices).all():
        invalid = int(
            _numpy.flatnonzero(~_numpy.isfinite(vertices).all(axis=1))[0]
        )
        raise ValueError(f"vertex {invalid} contains a non-finite value")

    return vertices


def _validated_triangle_array(source: Any, vertex_count: int) -> Any:
    raw_triangles = _numpy.asarray(source.triangles)

    if raw_triangles.size == 0:
        return _numpy.empty((0, 3), dtype=_numpy.int32)

    if raw_triangles.ndim != 2 or raw_triangles.shape[1:] != (3,):
        raise ValueError(
            "Mesh triangles must be a two-dimensional array of index triples."
        )

    if not _numpy.issubdtype(raw_triangles.dtype, _numpy.number):
        raise ValueError("Mesh triangle indices must be numeric.")

    if not _numpy.isfinite(raw_triangles).all():
        raise ValueError("Mesh triangle indices contain a non-finite value.")

    triangles64 = raw_triangles.astype(_numpy.int64)

    if not _numpy.equal(raw_triangles, triangles64).all():
        raise ValueError("Mesh triangle indices must be integers.")

    minimum = int(triangles64.min())
    maximum = int(triangles64.max())

    if minimum < 0 or maximum >= vertex_count:
        raise ValueError(
            f"Mesh triangle index range {minimum}..{maximum} is outside "
            f"0..{vertex_count - 1}."
        )

    if maximum > 2_147_483_647:
        raise ValueError("Mesh triangle indices exceed Blender's integer range.")

    return triangles64.astype(_numpy.int32)


def _create_mesh_geometry(mesh: bpy.types.Mesh, source: Any) -> None:
    if _numpy is None:
        vertices = tuple(
            (
                vertex[0] * 0.01,
                -vertex[2] * 0.01,
                vertex[1] * 0.01,
            )
            for vertex in source.vertices
        )
        mesh.from_pydata(vertices, (), source.triangles)
        return

    source_vertices = _validated_vertex_array(source)
    triangles = _validated_triangle_array(source, len(source_vertices))
    vertices = _numpy.empty_like(source_vertices)
    vertices[:, 0] = source_vertices[:, 0] * 0.01
    vertices[:, 1] = source_vertices[:, 2] * -0.01
    vertices[:, 2] = source_vertices[:, 1] * 0.01
    triangle_count = len(triangles)

    mesh.vertices.add(len(vertices))

    if len(vertices):
        mesh.vertices.foreach_set("co", vertices.reshape(-1))

    mesh.loops.add(triangle_count * 3)

    if triangle_count:
        mesh.loops.foreach_set("vertex_index", triangles.reshape(-1))
        mesh.polygons.add(triangle_count)
        mesh.polygons.foreach_set(
            "loop_start",
            _numpy.arange(0, triangle_count * 3, 3, dtype=_numpy.int32),
        )
        mesh.polygons.foreach_set(
            "loop_total",
            _numpy.full(triangle_count, 3, dtype=_numpy.int32),
        )


def _mesh_loop_vertex_indices(mesh: bpy.types.Mesh) -> Any:
    indices = _numpy.empty(len(mesh.loops), dtype=_numpy.int32)

    if len(indices):
        mesh.loops.foreach_get("vertex_index", indices)

    return indices


def _set_mesh_uvs(
    mesh: bpy.types.Mesh,
    source_uvs: Any,
    name: str = "UVMap",
    *,
    loop_vertex_indices: Any = None,
) -> None:
    uv_layer = mesh.uv_layers.new(name=name)

    if _numpy is None:
        for loop in mesh.loops:
            u, v = source_uvs[loop.vertex_index]
            uv_layer.data[loop.index].uv = (u, 1.0 - v)

        return

    uvs = _numpy.asarray(source_uvs, dtype=_numpy.float64)

    if uvs.ndim != 2 or uvs.shape != (len(mesh.vertices), 2):
        raise ValueError(
            f"Expected {len(mesh.vertices)} UV pairs for {name!r}, "
            f"found shape {uvs.shape}."
        )

    if not _numpy.isfinite(uvs).all():
        raise ValueError(f"Mesh UV layer {name!r} contains a non-finite value.")

    if loop_vertex_indices is None:
        loop_vertex_indices = _mesh_loop_vertex_indices(mesh)

    loop_uvs = uvs[loop_vertex_indices].copy()
    loop_uvs[:, 1] = 1.0 - loop_uvs[:, 1]

    if len(loop_uvs):
        uv_layer.data.foreach_set("uv", loop_uvs.reshape(-1))


def _set_mesh_colours(
    mesh: bpy.types.Mesh,
    name: str,
    colours: Any,
) -> None:
    colour_attribute = mesh.color_attributes.new(
        name=name,
        type="FLOAT_COLOR",
        domain="POINT",
    )

    if _numpy is None:
        values = array(
            "f",
            (
                float(component)
                for colour in colours
                for component in colour
            ),
        )
    else:
        values = _numpy.asarray(colours, dtype=_numpy.float32)

        if values.ndim != 2 or values.shape != (len(mesh.vertices), 4):
            raise ValueError(
                f"Expected {len(mesh.vertices)} RGBA colours for {name!r}, "
                f"found shape {values.shape}."
            )

        if not _numpy.isfinite(values).all():
            raise ValueError(f"Vertex colour set {name!r} is non-finite.")

        values = values.reshape(-1)

    colour_attribute.data.foreach_set("color", values)


def _populate_mesh_data(
    mesh: bpy.types.Mesh,
    source: Any,
    material: bpy.types.Material,
    apply_custom_normals: bool = False,
) -> bpy.types.Mesh:
    _create_mesh_geometry(mesh, source)
    mesh.validate(verbose=False)
    mesh.update(calc_edges=True)
    mesh.materials.append(material)

    uv_sets = tuple(getattr(source, "uv_sets", ()))

    if not uv_sets:
        legacy_uvs = getattr(source, "uvs", None)
        uv_sets = (legacy_uvs,) if legacy_uvs is not None else ()

    loop_vertex_indices = None

    if _numpy is not None and (
        any(source_uvs is not None for source_uvs in uv_sets)
        or (
            apply_custom_normals
            and source.normals is not None
        )
    ):
        loop_vertex_indices = _mesh_loop_vertex_indices(mesh)

    first_uv_layer_index = None

    for index, source_uvs in enumerate(uv_sets):
        if source_uvs is None:
            continue

        layer_name = "UVMap" if index == 0 else f"UVMap.{index:03d}"
        _set_mesh_uvs(
            mesh,
            source_uvs,
            layer_name,
            loop_vertex_indices=loop_vertex_indices,
        )

        if first_uv_layer_index is None:
            first_uv_layer_index = len(mesh.uv_layers) - 1

    if first_uv_layer_index is not None:
        mesh.uv_layers.active_index = first_uv_layer_index

        for index, uv_layer in enumerate(mesh.uv_layers):
            uv_layer.active_render = index == first_uv_layer_index

        mesh["smo_uv_layers"] = ",".join(
            uv_layer.name for uv_layer in mesh.uv_layers
        )

    colour_names = ("Color", "Color1", "Color2", "Color3")

    for name, colours in zip(
        colour_names,
        getattr(source, "colour_sets", ()),
    ):
        if colours is None:
            continue

        if len(colours) != len(mesh.vertices):
            print(
                "[Odyssey Toolkit] Ignoring vertex colour set "
                f"{name!r} on {mesh.name!r}: expected "
                f"{len(mesh.vertices)} values, found {len(colours)}."
            )
            continue

        _set_mesh_colours(mesh, name, colours)

    if source.normals is not None:
        if len(mesh.polygons):
            if _numpy is None:
                for polygon in mesh.polygons:
                    polygon.use_smooth = True
            else:
                mesh.polygons.foreach_set(
                    "use_smooth",
                    _numpy.ones(len(mesh.polygons), dtype=_numpy.bool_),
                )

        mesh["smo_source_normal_count"] = len(source.normals)

        if apply_custom_normals:
            try:
                _apply_custom_normals(
                    mesh,
                    source.normals,
                    loop_vertex_indices=loop_vertex_indices,
                )
                mesh["smo_custom_normals"] = "APPLIED"
            except Exception as exc:
                mesh["smo_custom_normals"] = f"FAILED: {exc}"
                print(
                    "[Odyssey Toolkit] Could not apply custom normals "
                    f"to {mesh.name!r}:"
                )
                traceback.print_exc()
        else:
            mesh["smo_custom_normals"] = "DISABLED"

    mesh["smo_vertex_colour_sets"] = len(mesh.color_attributes)
    mesh.update()
    return mesh


def _source_mesh_display_name(source: Any, asset_name: str) -> str:
    archive_name = str(asset_name).strip() or "Asset"
    source_name = str(source.name).strip() or "Mesh"
    material_name = str(source.material_name).strip() or "Material"
    suffix = f"_{material_name}"
    mesh_material_name = (
        source_name
        if source_name.casefold().endswith(suffix.casefold())
        else f"{source_name}{suffix}"
    )
    archive_prefix = f"{archive_name}_"
    return (
        mesh_material_name
        if mesh_material_name.casefold().startswith(
            archive_prefix.casefold()
        )
        else f"{archive_prefix}{mesh_material_name}"
    )


@timed("blender_mesh_creation")
def _create_mesh_data(
    source: Any,
    asset_name: str,
    material: bpy.types.Material,
    apply_custom_normals: bool = False,
) -> bpy.types.Mesh:
    display_name = _source_mesh_display_name(source, asset_name)
    mesh = bpy.data.meshes.new(display_name)
    mesh["smo_display_name"] = display_name
    mesh["smo_source_mesh_name"] = str(source.name)
    mesh["smo_source_material_name"] = str(source.material_name)
    mesh["smo_base_bone_index"] = int(
        getattr(source, "base_bone_index", 0xFFFF)
    )

    try:
        return _populate_mesh_data(
            mesh,
            source,
            material,
            apply_custom_normals,
        )
    except Exception:
        if (
            mesh.users == 0
            and bpy.data.meshes.get(mesh.name) is mesh
        ):
            bpy.data.meshes.remove(mesh)
        raise


def _model_has_deformable_skeleton(model: Any) -> bool:
    skeleton = getattr(model, "skeleton", None)

    if skeleton is None or len(skeleton.bones) <= 1:
        return False

    return any(
        any(weights for weights in mesh.bone_weights)
        for mesh in model.meshes
    )


def _bone_matrix_to_blender(source_matrix: Any) -> Matrix:
    source = Matrix(source_matrix)
    basis = Matrix(
        (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 0.0, -1.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )
    )
    converted = basis @ source @ basis.inverted()
    converted.translation *= 0.01
    location, rotation, scale = converted.decompose()

    if not all(
        math.isfinite(value)
        for value in (*location, *rotation, *scale)
    ):
        raise ValueError("FSKL bone matrix contains non-finite values.")

    rest_matrix = rotation.to_matrix().to_4x4()
    rest_matrix.translation = location
    return rest_matrix


def _bone_display_lengths(
    skeleton: Any,
    matrices: tuple[Matrix, ...],
) -> tuple[float, ...]:
    child_indices: list[list[int]] = [
        [] for _bone in skeleton.bones
    ]

    for index, bone in enumerate(skeleton.bones):
        if 0 <= bone.parent_index < len(skeleton.bones):
            child_indices[bone.parent_index].append(index)

    lengths = []

    for index, bone in enumerate(skeleton.bones):
        head = matrices[index].translation
        child_distances = tuple(
            (matrices[child].translation - head).length
            for child in child_indices[index]
            if (matrices[child].translation - head).length > 1e-6
        )

        if child_distances:
            length = min(child_distances)
        elif 0 <= bone.parent_index < len(skeleton.bones):
            parent_distance = (
                head - matrices[bone.parent_index].translation
            ).length
            length = parent_distance * 0.25
        else:
            length = 0.05

        lengths.append(max(0.01, float(length)))

    return tuple(lengths)


@timed("blender_armature_creation")
def _create_armature_object(
    collection: bpy.types.Collection,
    display_name: str,
    skeleton: Any,
    *,
    armature_data: bpy.types.Armature | None = None,
) -> tuple[bpy.types.Object, tuple[str, ...]]:
    if skeleton is None or not skeleton.bones:
        raise ValueError("Cannot create an armature without FSKL bones.")

    created_data = armature_data is None

    if armature_data is None:
        armature_data = bpy.data.armatures.new(display_name)
        armature_data.display_type = "STICK"

    armature_object = bpy.data.objects.new(display_name, armature_data)
    armature_object_name = armature_object.name
    armature_object.show_in_front = True
    collection.objects.link(armature_object)

    if not created_data:
        source_bones = sorted(
            (
                bone
                for bone in armature_data.bones
                if "smo_bone_index" in bone
            ),
            key=lambda bone: int(bone["smo_bone_index"]),
        )
        return armature_object, tuple(bone.name for bone in source_bones)

    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    previous_selected = tuple(bpy.context.selected_objects)
    bone_names: list[str] = []

    try:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        for selected in tuple(bpy.context.selected_objects):
            selected.select_set(False)

        armature_object.select_set(True)
        view_layer.objects.active = armature_object
        bpy.ops.object.mode_set(mode="EDIT")
        matrices = tuple(
            _bone_matrix_to_blender(bone.model_matrix)
            for bone in skeleton.bones
        )
        lengths = _bone_display_lengths(skeleton, matrices)
        edit_bones = []

        for index, (bone, matrix, length) in enumerate(
            zip(skeleton.bones, matrices, lengths)
        ):
            name = bone.name.strip() or f"Bone_{index:03d}"
            edit_bone = armature_data.edit_bones.new(name)
            # A new EditBone is initially zero-length, so Blender cannot apply
            # its matrix orientation until a non-zero length exists.
            edit_bone.length = length
            edit_bone.matrix = matrix
            edit_bone.use_connect = False
            edit_bones.append(edit_bone)
            bone_names.append(edit_bone.name)

        for index, bone in enumerate(skeleton.bones):
            if bone.parent_index == -1:
                continue

            if not 0 <= bone.parent_index < len(edit_bones):
                raise ValueError(
                    f"FSKL bone {bone.name!r} references invalid parent "
                    f"{bone.parent_index}."
                )

            source_parent = edit_bones[bone.parent_index]
            source_child = edit_bones[index]
            helper = armature_data.edit_bones.new(
                f"__SMO_SSC__{source_child.name}"
            )
            helper.length = max(0.01, source_parent.length * 0.25)
            helper.matrix = source_parent.matrix.copy()
            helper.use_connect = False
            helper.parent = source_parent
            source_child.use_connect = False
            source_child.parent = helper

        bpy.ops.object.mode_set(mode="OBJECT")

        for index, (source_bone, bone_name) in enumerate(
            zip(skeleton.bones, bone_names)
        ):
            blender_bone = armature_data.bones[bone_name]
            blender_bone["smo_bone_index"] = index
            blender_bone["smo_source_name"] = source_bone.name
            blender_bone["smo_fskl_flags"] = int(source_bone.flags)
            blender_bone["smo_rest_scale"] = tuple(source_bone.scale)
            blender_bone["smo_rest_rotation"] = tuple(source_bone.rotation)
            blender_bone["smo_rest_translation"] = tuple(source_bone.position)
            blender_bone["smo_rest_euler_xyz"] = bool(
                source_bone.flags & (1 << 12)
            )
            blender_bone["smo_source_parent"] = (
                bone_names[source_bone.parent_index]
                if source_bone.parent_index >= 0
                else ""
            )

            if source_bone.parent_index >= 0:
                helper_name = blender_bone.parent.name
                helper_bone = armature_data.bones[helper_name]
                helper_bone.use_deform = False
                helper_bone.hide = True
                helper_bone["smo_scale_compensation_helper"] = True
                helper_bone["smo_scale_compensation_child"] = bone_name
                helper_bone["smo_scale_compensation_parent"] = bone_names[
                    source_bone.parent_index
                ]
                blender_bone["smo_scale_compensation_helper"] = helper_name

        armature_data["smo_fskl_bone_count"] = len(skeleton.bones)
        armature_data["smo_fskl_smooth_matrix_count"] = int(
            skeleton.smooth_matrix_count
        )
        armature_data["smo_segment_scale_compensate"] = bool(
            skeleton.segment_scale_compensate
        )
        armature_data["smo_rest_matrix_revision"] = 4
    except Exception:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        bpy.data.objects.remove(armature_object, do_unlink=True)

        if armature_data.users == 0:
            bpy.data.armatures.remove(armature_data)

        raise
    finally:
        if armature_object_name in bpy.data.objects:
            bpy.data.objects[armature_object_name].select_set(False)

        for selected in previous_selected:
            if selected.name in bpy.data.objects:
                selected.select_set(True)

        if (
            previous_active is not None
            and previous_active.name in bpy.data.objects
        ):
            view_layer.objects.active = previous_active

    return armature_object, tuple(bone_names)


@timed("blender_skin_binding")
def _apply_skin_binding(
    obj: bpy.types.Object,
    armature_object: bpy.types.Object,
    binding: MeshRigBinding,
    bone_names: tuple[str, ...],
) -> None:
    mesh = obj.data

    if len(binding.bone_weights) != len(mesh.vertices):
        raise ValueError(
            f"Rig binding has {len(binding.bone_weights)} vertices; "
            f"mesh has {len(mesh.vertices)}."
        )

    if len(bone_names) != len(binding.skeleton.bones):
        raise ValueError(
            "Blender armature bone count differs from the FSKL skeleton."
        )

    groups = tuple(obj.vertex_groups.new(name=name) for name in bone_names)
    applied_key = str(mesh.get("smo_skin_binding_key", ""))

    if applied_key and applied_key != binding.rig_key:
        raise ValueError(
            "Shared mesh already contains weights for another skeleton."
        )

    if not applied_key:
        assignments: list[dict[float, list[int]]] = [
            {} for _bone_name in bone_names
        ]

        for vertex_index, influences in enumerate(binding.bone_weights):
            for bone_index, weight in influences:
                if not 0 <= bone_index < len(groups):
                    raise ValueError(
                        f"Vertex {vertex_index} references missing bone "
                        f"{bone_index}."
                    )

                numeric_weight = float(weight)

                if not math.isfinite(numeric_weight) or numeric_weight <= 0.0:
                    raise ValueError(
                        f"Vertex {vertex_index} has invalid bone weight "
                        f"{numeric_weight!r}."
                    )

                assignments[bone_index].setdefault(
                    numeric_weight,
                    [],
                ).append(vertex_index)

        for bone_index, weights_to_vertices in enumerate(assignments):
            group = groups[bone_index]

            for weight, vertex_indices in weights_to_vertices.items():
                group.add(vertex_indices, weight, "REPLACE")

        mesh["smo_skin_binding_key"] = binding.rig_key
        mesh["smo_skin_influence_max"] = max(
            (len(weights) for weights in binding.bone_weights),
            default=0,
        )

    modifier = obj.modifiers.new(name="SMO Armature", type="ARMATURE")
    modifier.object = armature_object
    modifier.use_vertex_groups = True
    obj.parent = armature_object
    obj["smo_rigged"] = True
    obj["smo_source_model"] = binding.model_name
    obj["smo_armature"] = armature_object.name

    base_bone_index = int(mesh.get("smo_base_bone_index", 0xFFFF))

    if 0 <= base_bone_index < len(bone_names):
        obj["smo_visibility_bone"] = bone_names[base_bone_index]


def _apply_custom_normals_python(
    mesh: bpy.types.Mesh,
    source_normals: Any,
) -> None:
    converted = []

    for index, source_normal in enumerate(source_normals):
        normal = (
            float(source_normal[0]),
            -float(source_normal[2]),
            float(source_normal[1]),
        )

        if not all(math.isfinite(component) for component in normal):
            raise ValueError(f"normal {index} contains a non-finite value")

        length_squared = sum(component * component for component in normal)

        if length_squared <= 1e-12:
            raise ValueError(f"normal {index} has zero length")

        inverse_length = 1.0 / math.sqrt(length_squared)
        converted.append(
            tuple(component * inverse_length for component in normal)
        )

    loop_normals = tuple(
        converted[loop.vertex_index]
        for loop in mesh.loops
    )
    mesh.normals_split_custom_set(loop_normals)


def _apply_custom_normals(
    mesh: bpy.types.Mesh,
    source_normals: Any,
    *,
    loop_vertex_indices: Any = None,
) -> None:
    if len(source_normals) != len(mesh.vertices):
        raise ValueError(
            f"expected {len(mesh.vertices)} source normals, "
            f"found {len(source_normals)}"
        )

    if _numpy is None:
        _apply_custom_normals_python(mesh, source_normals)
        return

    normals = _numpy.asarray(source_normals, dtype=_numpy.float64)

    if normals.ndim != 2 or normals.shape != (len(mesh.vertices), 3):
        raise ValueError(
            f"expected {len(mesh.vertices)} XYZ source normals, "
            f"found shape {normals.shape}"
        )

    converted = _numpy.empty_like(normals)
    converted[:, 0] = normals[:, 0]
    converted[:, 1] = -normals[:, 2]
    converted[:, 2] = normals[:, 1]
    finite_rows = _numpy.isfinite(converted).all(axis=1)

    if not finite_rows.all():
        index = int(_numpy.flatnonzero(~finite_rows)[0])
        raise ValueError(f"normal {index} contains a non-finite value")

    length_squared = _numpy.square(converted).sum(axis=1)
    zero_rows = length_squared <= 1e-12

    if zero_rows.any():
        index = int(_numpy.flatnonzero(zero_rows)[0])
        raise ValueError(f"normal {index} has zero length")

    converted /= _numpy.sqrt(length_squared)[:, None]

    if loop_vertex_indices is None:
        loop_vertex_indices = _mesh_loop_vertex_indices(mesh)

    loop_normals = converted[loop_vertex_indices]

    if len(loop_normals) != len(mesh.loops):
        raise ValueError(
            f"expected {len(mesh.loops)} loop normals, "
            f"built {len(loop_normals)}"
        )

    mesh.normals_split_custom_set(loop_normals)

@timed("blender_image_creation")
def _create_texture_image(
    decoded: Any,
    source_key: SourceKey,
    colour_space: str,
    texture_role: str | None = None,
) -> bpy.types.Image:
    inferred_role = _texture_role(decoded.name)
    role = texture_role or inferred_role
    is_normal = role == "NORMAL"
    identity_parts = (*source_key, decoded.name)
    suffix = ""

    if role != inferred_role:
        identity_parts = (*identity_parts, f"role:{role}")
        suffix = f" - {role.title()}"

    if colour_space != "sRGB":
        identity_parts = (*identity_parts, colour_space)
        suffix += " - Data"

    identity = _identity_digest(*identity_parts)
    format_name = f"0x{decoded.format_value:04X}"
    pixel_source_digest = blake2s(
        decoded.rgba8,
        digest_size=12,
    ).hexdigest()
    image_name = (
        f"SMO [{identity}] {source_key[0].stem} - {decoded.name}{suffix}"
    )
    image = bpy.data.images.get(image_name)
    alpha_mode = (
        "STRAIGHT" if role == "ALBEDO" else "CHANNEL_PACKED"
    )

    if (
        image is not None
        and tuple(image.size) == (decoded.width, decoded.height)
        and image.get("smo_image_identity") == identity
        and image.get("smo_texture_name") == decoded.name
        and image.get("smo_texture_format") == format_name
        and image.get("smo_colour_space") == colour_space
        and image.get("smo_texture_role") == role
        and bool(image.get("smo_normal_blue_reconstructed")) == is_normal
        and image.get("smo_pixel_source_digest") == pixel_source_digest
        and bool(image.get("smo_pixels_packed"))
        and image.packed_file is not None
    ):
        image.colorspace_settings.name = colour_space
        image.alpha_mode = alpha_mode
        return image

    if (
        image is not None
        and image.get("smo_image_identity") not in {None, identity}
    ):
        image = None

    if image is None:
        image = bpy.data.images.new(
            image_name,
            width=decoded.width,
            height=decoded.height,
            alpha=True,
        )
    elif tuple(image.size) != (decoded.width, decoded.height):
        image.scale(decoded.width, decoded.height)

    pixels = _rgba8_to_blender_pixels(
        decoded.rgba8,
        decoded.width,
        decoded.height,
        is_normal,
    )

    image.colorspace_settings.name = colour_space
    image.alpha_mode = alpha_mode
    image.pixels.foreach_set(pixels)
    image["smo_image_identity"] = identity
    image["smo_texture_name"] = decoded.name
    image["smo_texture_role"] = role
    image["smo_texture_format"] = format_name
    image["smo_colour_space"] = colour_space
    image["smo_normal_blue_reconstructed"] = is_normal
    image["smo_pixel_source_digest"] = pixel_source_digest
    image["smo_pixel_upload_count"] = (
        int(image.get("smo_pixel_upload_count", 0)) + 1
    )
    image.update()
    image.pack()
    image["smo_pixels_packed"] = True
    return image


def _create_albedo_image(
    decoded: Any,
    source_key: SourceKey,
) -> bpy.types.Image:
    return _create_texture_image(
        decoded,
        source_key,
        "sRGB",
        "ALBEDO",
    )


def _clear_input_links(node_tree: Any, input_socket: Any) -> None:
    for link in tuple(input_socket.links):
        node_tree.links.remove(link)


def _connect_normal_texture(
    material: bpy.types.Material,
    shader: Any,
    texture_node: Any,
    *,
    node_name: str,
    label: str,
    location: tuple[float, float],
) -> Any | None:
    normal_input = shader.inputs.get("Normal")
    if normal_input is None:
        return None

    normal_map = next(
        (
            link.from_node
            for link in normal_input.links
            if link.from_node.bl_idname == "ShaderNodeNormalMap"
            and link.from_node.inputs["Color"].is_linked
            and link.from_node.inputs["Color"].links[0].from_node
            == texture_node
        ),
        None,
    )
    if normal_map is None:
        _clear_input_links(material.node_tree, normal_input)
        normal_map = material.node_tree.nodes.new("ShaderNodeNormalMap")
        material.node_tree.links.new(
            texture_node.outputs["Color"], normal_map.inputs["Color"]
        )
        material.node_tree.links.new(
            normal_map.outputs["Normal"], normal_input
        )

    normal_map.name = node_name
    normal_map.label = label
    normal_map.location = location
    return normal_map


def _shader_texture_channel(
    material: bpy.types.Material,
    texture_node: Any,
    channel: str,
    label: str,
    y: float,
) -> Any:
    if channel == "Alpha":
        return texture_node.outputs["Alpha"]

    role = str(texture_node.get("smo_texture_role") or "")
    if channel == "Red" and role in {"ROUGHNESS", "METALLIC"}:
        # Odyssey's dedicated BC4 scalar maps are replicated to RGB by
        # the decoder, so Blender's colour-to-value conversion is exact.
        return texture_node.outputs["Color"]

    texture_name = str(
        texture_node.get("smo_texture_name") or texture_node.name
    )
    node_name = f"SMO Components - {texture_name}"
    separate = material.node_tree.nodes.get(node_name)
    if separate is None:
        separate = material.node_tree.nodes.new("ShaderNodeSeparateColor")
        separate.name = node_name
        separate.label = f"FMAT component channels: {label}"
        separate.mode = "RGB"
        separate.location = (-300.0, y)
        material.node_tree.links.new(
            texture_node.outputs["Color"], separate.inputs["Color"]
        )
    return separate.outputs[channel]


def _build_color_route_component(
    material: bpy.types.Material,
    texture_nodes: dict[str, Any],
    route: _ColorShaderRoute,
    channel: str,
    cache: dict[tuple[_ColorShaderRoute, str], Any],
    y: float,
) -> Any | None:
    key = (route, channel)
    if key in cache:
        return cache[key]

    channel_index = {
        "Red": 0,
        "Green": 1,
        "Blue": 2,
        "Alpha": 3,
    }[channel]
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    if route.kind == "CONSTANT" and route.value is not None:
        node = nodes.new("ShaderNodeValue")
        node.name = f"SMO Constant {route.source_code} {channel}"
        node.label = f"FMAT constant {route.source_code}: {channel}"
        node.location = (-700.0, y)
        node.outputs[0].default_value = route.value[channel_index]
        cache[key] = node.outputs[0]
        return node.outputs[0]

    if route.kind == "TEXTURE" and route.texture_name is not None:
        texture_node = texture_nodes.get(route.texture_name)
        if texture_node is None:
            return None
        socket = _shader_texture_channel(
            material,
            texture_node,
            channel,
            f"Source {route.source_code}",
            y,
        )
        multiplier = route.multiplier
        factor = 1.0 if multiplier is None else multiplier[channel_index]
        if math.isclose(factor, 1.0, abs_tol=1e-7):
            cache[key] = socket
            return socket
        multiply = nodes.new("ShaderNodeMath")
        multiply.name = (
            f"SMO Source {route.source_code} {channel} Multiplier"
        )
        multiply.label = f"FMAT source {route.source_code} multiplier"
        multiply.operation = "MULTIPLY"
        multiply.use_clamp = False
        multiply.inputs[1].default_value = factor
        multiply.location = (-480.0, y)
        links.new(socket, multiply.inputs[0])
        cache[key] = multiply.outputs[0]
        return multiply.outputs[0]

    if (
        route.kind == "CHANNEL"
        and route.child is not None
        and route.channel is not None
    ):
        socket = _build_color_route_component(
            material,
            texture_nodes,
            route.child,
            route.channel,
            cache,
            y,
        )
        if socket is not None:
            cache[key] = socket
        return socket

    if (
        route.kind != "BLEND"
        or route.equation not in {0, 1, 2}
        or route.source is None
        or route.destination is None
        or route.coefficient is None
    ):
        return None

    source = _build_color_route_component(
        material, texture_nodes, route.source, channel, cache, y + 50.0
    )
    destination = _build_color_route_component(
        material,
        texture_nodes,
        route.destination,
        channel,
        cache,
        y,
    )
    coefficient = _build_color_route_component(
        material,
        texture_nodes,
        route.coefficient,
        channel,
        cache,
        y - 50.0,
    )
    if source is None or destination is None or coefficient is None:
        return None

    prefix = f"SMO Blend {route.blend_index} {channel}"
    if route.equation == 0:
        subtract = nodes.new("ShaderNodeMath")
        subtract.name = f"{prefix} Source Minus Destination"
        subtract.operation = "SUBTRACT"
        subtract.use_clamp = False
        links.new(source, subtract.inputs[0])
        links.new(destination, subtract.inputs[1])
        multiply = nodes.new("ShaderNodeMath")
        multiply.name = f"{prefix} Lerp Coefficient"
        multiply.operation = "MULTIPLY"
        multiply.use_clamp = False
        links.new(subtract.outputs[0], multiply.inputs[0])
        links.new(coefficient, multiply.inputs[1])
        result = nodes.new("ShaderNodeMath")
        result.name = f"{prefix} Lerp"
        result.label = "FMAT blend: mix(dst, src, coefficient)"
        result.operation = "ADD"
        result.use_clamp = False
        links.new(destination, result.inputs[0])
        links.new(multiply.outputs[0], result.inputs[1])
    elif route.equation == 1:
        multiply = nodes.new("ShaderNodeMath")
        multiply.name = f"{prefix} Source Times Coefficient"
        multiply.operation = "MULTIPLY"
        multiply.use_clamp = False
        links.new(source, multiply.inputs[0])
        links.new(coefficient, multiply.inputs[1])
        result = nodes.new("ShaderNodeMath")
        result.name = f"{prefix} Multiply Add"
        result.label = "FMAT blend: src * coefficient + dst"
        result.operation = "ADD"
        result.use_clamp = False
        links.new(multiply.outputs[0], result.inputs[0])
        links.new(destination, result.inputs[1])
    else:
        result = nodes.new("ShaderNodeMath")
        result.name = f"{prefix} Multiply"
        result.label = "FMAT blend: src * dst"
        result.operation = "MULTIPLY"
        result.use_clamp = False
        links.new(source, result.inputs[0])
        links.new(destination, result.inputs[1])

    result.location = (-220.0, y)
    cache[key] = result.outputs[0]
    return result.outputs[0]


def _shader_color_parameter_name(source_code: int) -> str | None:
    if source_code == 10:
        return "base_color_mul_color"
    if 30 <= source_code <= 33:
        return f"const_single{source_code - 30}"
    if 50 <= source_code <= 54:
        return f"uniform{source_code - 50}_mul_color"
    if 60 <= source_code <= 63:
        return f"const_color{source_code - 60}"
    return None


def _tag_shader_parameter_node(
    node: Any,
    parameter_name: str | None,
    binding: str,
) -> None:
    if parameter_name:
        node["smo_shader_parameter"] = parameter_name
        node["smo_shader_parameter_binding"] = binding


def _build_color_shader_route(
    material: bpy.types.Material,
    texture_nodes: dict[str, Any],
    route: _ColorShaderRoute,
    cache: dict[object, Any],
    y: float,
) -> Any | None:
    cached = cache.get(route)
    if cached is not None:
        return cached

    nodes = material.node_tree.nodes
    links = material.node_tree.links

    if route.kind == "CONSTANT" and route.value is not None:
        node = nodes.new("ShaderNodeRGB")
        node.name = f"SMO Constant {route.source_code} Color"
        node.label = f"FMAT constant {route.source_code}"
        node.location = (-700.0, y)
        node.outputs["Color"].default_value = route.value
        _tag_shader_parameter_node(
            node,
            _shader_color_parameter_name(route.source_code),
            "COLOR_OUTPUT",
        )
        cache[route] = node.outputs["Color"]
        return node.outputs["Color"]

    if route.kind == "TEXTURE" and route.texture_name is not None:
        texture_node = texture_nodes.get(route.texture_name)
        if texture_node is None:
            return None
        socket = texture_node.outputs["Color"]
        multiplier = route.multiplier
        if multiplier is None or all(
            math.isclose(value, 1.0, abs_tol=1e-7)
            for value in multiplier[:3]
        ):
            cache[route] = socket
            return socket
        multiply = nodes.new("ShaderNodeMixRGB")
        multiply.name = f"SMO Source {route.source_code} Color Multiplier"
        multiply.label = f"FMAT source {route.source_code} multiplier"
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        multiply.inputs[2].default_value = multiplier
        _tag_shader_parameter_node(
            multiply,
            _shader_color_parameter_name(route.source_code),
            "COLOR_INPUT_2",
        )
        multiply.location = (-480.0, y)
        links.new(socket, multiply.inputs[1])
        cache[route] = multiply.outputs["Color"]
        return multiply.outputs["Color"]

    if (
        route.kind == "CHANNEL"
        and route.child is not None
        and route.channel is not None
    ):
        component = _build_color_route_component(
            material,
            texture_nodes,
            route.child,
            route.channel,
            cache,
            y,
        )
        if component is None:
            return None
        combine = nodes.new("ShaderNodeCombineColor")
        combine.name = f"SMO Source {route.source_code} {route.channel}"
        combine.label = f"FMAT {route.channel} channel"
        combine.location = (-260.0, y)
        for input_name in ("Red", "Green", "Blue"):
            links.new(component, combine.inputs[input_name])
        cache[route] = combine.outputs["Color"]
        return combine.outputs["Color"]

    if (
        route.kind != "BLEND"
        or route.equation not in {0, 1, 2}
        or route.source is None
        or route.destination is None
        or route.coefficient is None
    ):
        return None

    source = _build_color_shader_route(
        material, texture_nodes, route.source, cache, y + 80.0
    )
    destination = _build_color_shader_route(
        material, texture_nodes, route.destination, cache, y
    )
    coefficient = _build_color_shader_route(
        material, texture_nodes, route.coefficient, cache, y - 80.0
    )
    if source is None or destination is None or coefficient is None:
        return None

    prefix = f"SMO Blend {route.blend_index}"
    if route.equation == 0:
        subtract = nodes.new("ShaderNodeVectorMath")
        subtract.name = f"{prefix} Source Minus Destination"
        subtract.operation = "SUBTRACT"
        links.new(source, subtract.inputs[0])
        links.new(destination, subtract.inputs[1])
        multiply = nodes.new("ShaderNodeVectorMath")
        multiply.name = f"{prefix} Lerp Coefficient"
        multiply.operation = "MULTIPLY"
        links.new(subtract.outputs[0], multiply.inputs[0])
        links.new(coefficient, multiply.inputs[1])
        result = nodes.new("ShaderNodeVectorMath")
        result.name = f"{prefix} Lerp"
        result.label = "FMAT blend: mix(dst, src, coefficient)"
        result.operation = "ADD"
        links.new(destination, result.inputs[0])
        links.new(multiply.outputs[0], result.inputs[1])
    elif route.equation == 1:
        multiply = nodes.new("ShaderNodeVectorMath")
        multiply.name = f"{prefix} Source Times Coefficient"
        multiply.operation = "MULTIPLY"
        links.new(source, multiply.inputs[0])
        links.new(coefficient, multiply.inputs[1])
        result = nodes.new("ShaderNodeVectorMath")
        result.name = f"{prefix} Multiply Add"
        result.label = "FMAT blend: src * coefficient + dst"
        result.operation = "ADD"
        links.new(multiply.outputs[0], result.inputs[0])
        links.new(destination, result.inputs[1])
    else:
        result = nodes.new("ShaderNodeVectorMath")
        result.name = f"{prefix} Multiply"
        result.label = "FMAT blend: src * dst"
        result.operation = "MULTIPLY"
        links.new(source, result.inputs[0])
        links.new(destination, result.inputs[1])

    result.location = (-120.0, y)
    cache[route] = result.outputs[0]
    return result.outputs[0]

def _invert_scalar_route_socket(
    material: bpy.types.Material,
    socket: Any,
    route: _ScalarShaderRoute,
    *,
    name: str,
    label: str,
    y: float,
) -> Any:
    if not route.invert:
        return socket

    invert = material.node_tree.nodes.new("ShaderNodeMath")
    invert.name = name
    invert.label = label
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    invert.location = (-180.0, y)
    material.node_tree.links.new(socket, invert.inputs[1])
    return invert.outputs[0]


def _apply_scalar_shader_route(
    material: bpy.types.Material,
    shader: Any,
    texture_nodes: dict[str, Any],
    route: _ScalarShaderRoute | None,
    color_cache: dict[tuple[_ColorShaderRoute, str], Any],
    input_name: str,
    label: str,
    y: float,
) -> bool:
    if route is None:
        return False

    shader_input = shader.inputs.get(input_name)
    if shader_input is None:
        return False

    if route.kind == "CONSTANT" and route.value is not None:
        value = 1.0 - route.value if route.invert else route.value
        _clear_input_links(material.node_tree, shader_input)
        shader_input.default_value = value
        if input_name == "Roughness":
            material.roughness = value
        elif input_name == "Metallic":
            material.metallic = value
        return True

    if (
        route.kind == "COLOR_ROUTE"
        and route.color_route is not None
        and route.channel is not None
    ):
        socket = _build_color_route_component(
            material,
            texture_nodes,
            route.color_route,
            route.channel,
            color_cache,
            y,
        )
        if socket is None:
            return False
        socket = _invert_scalar_route_socket(
            material,
            socket,
            route,
            name=f"SMO {label} Component Invert",
            label="FMAT inverted component",
            y=y,
        )
        _clear_input_links(material.node_tree, shader_input)
        material.node_tree.links.new(socket, shader_input)
        return True

    texture_node = texture_nodes.get(route.texture_name or "")
    if route.kind != "TEXTURE" or texture_node is None or route.channel is None:
        return False

    socket = _shader_texture_channel(
        material, texture_node, route.channel, label, y
    )
    socket = _invert_scalar_route_socket(
        material,
        socket,
        route,
        name=f"SMO {label} Component Invert",
        label="FMAT inverted component",
        y=y,
    )
    _clear_input_links(material.node_tree, shader_input)
    material.node_tree.links.new(socket, shader_input)
    return True


def _texture_coordinate_metadata(
    route: _TextureCoordinateRoute,
) -> dict[str, object]:
    return {
        "shader_samplers": route.shader_samplers,
        "fuv": route.fuv_index,
        "uv": route.uv_index,
        "uv_layer": (
            "UVMap" if route.uv_index == 0 else f"UVMap.{route.uv_index:03d}"
        ),
        "tex_mtx": route.transform_parameter,
        "matrix": route.matrix,
    }


def _is_identity_affine(
    matrix: tuple[float, float, float, float, float, float],
) -> bool:
    return all(
        math.isclose(actual, expected, abs_tol=1e-7)
        for actual, expected in zip(
            matrix,
            (1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
        )
    )


def _build_texture_coordinate_socket(
    material: bpy.types.Material,
    route: _TextureCoordinateRoute,
    cache: dict[
        tuple[
            int,
            str | None,
            tuple[float, float, float, float, float, float] | None,
        ],
        Any,
    ],
    y: float,
) -> Any:
    matrix = route.matrix
    if (
        matrix is not None
        and route.transform_parameter is None
        and _is_identity_affine(matrix)
    ):
        matrix = None

    key = (route.uv_index, route.transform_parameter, matrix)
    cached = cache.get(key)
    if cached is not None:
        return cached

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    layer_name = (
        "UVMap" if route.uv_index == 0 else f"UVMap.{route.uv_index:03d}"
    )
    uv_map = nodes.new("ShaderNodeUVMap")
    uv_map.name = f"SMO {layer_name} Coordinates"
    uv_map.label = f"FMAT _u{route.uv_index}"
    uv_map.uv_map = layer_name
    uv_map.location = (-1320.0, y)
    socket = uv_map.outputs["UV"]

    if matrix is None:
        cache[key] = socket
        return socket

    m00, m01, offset_x, m10, m11, offset_y = matrix
    dot_x = nodes.new("ShaderNodeVectorMath")
    dot_x.name = f"SMO {layer_name} Transform X"
    dot_x.label = "FMAT TexSrt X"
    dot_x.operation = "DOT_PRODUCT"
    dot_x.inputs[1].default_value = (m00, m01, 0.0)
    _tag_shader_parameter_node(
        dot_x, route.transform_parameter, "TEXSRT_ROW_0"
    )
    dot_x.location = (-1080.0, y + 50.0)
    links.new(socket, dot_x.inputs[0])

    add_x = nodes.new("ShaderNodeMath")
    add_x.name = f"SMO {layer_name} Offset X"
    add_x.operation = "ADD"
    add_x.use_clamp = False
    add_x.inputs[1].default_value = offset_x
    _tag_shader_parameter_node(
        add_x, route.transform_parameter, "TEXSRT_OFFSET_0"
    )
    add_x.location = (-840.0, y + 50.0)
    links.new(dot_x.outputs["Value"], add_x.inputs[0])

    dot_y = nodes.new("ShaderNodeVectorMath")
    dot_y.name = f"SMO {layer_name} Transform Y"
    dot_y.label = "FMAT TexSrt Y"
    dot_y.operation = "DOT_PRODUCT"
    dot_y.inputs[1].default_value = (m10, m11, 0.0)
    _tag_shader_parameter_node(
        dot_y, route.transform_parameter, "TEXSRT_ROW_1"
    )
    dot_y.location = (-1080.0, y - 50.0)
    links.new(socket, dot_y.inputs[0])

    add_y = nodes.new("ShaderNodeMath")
    add_y.name = f"SMO {layer_name} Offset Y"
    add_y.operation = "ADD"
    add_y.use_clamp = False
    add_y.inputs[1].default_value = offset_y
    _tag_shader_parameter_node(
        add_y, route.transform_parameter, "TEXSRT_OFFSET_1"
    )
    add_y.location = (-840.0, y - 50.0)
    links.new(dot_y.outputs["Value"], add_y.inputs[0])

    combine = nodes.new("ShaderNodeCombineXYZ")
    combine.name = f"SMO {layer_name} TexSrt"
    combine.label = f"FMAT {route.transform_parameter}"
    combine.location = (-600.0, y)
    links.new(add_x.outputs[0], combine.inputs["X"])
    links.new(add_y.outputs[0], combine.inputs["Y"])
    socket = combine.outputs["Vector"]
    cache[key] = socket
    return socket


def _apply_texture_coordinate_routes(
    material: bpy.types.Material,
    texture_nodes: dict[str, Any],
    resolved: _ResolvedMaterialShader,
) -> None:
    cache: dict[
        tuple[
            int,
            str | None,
            tuple[float, float, float, float, float, float] | None,
        ],
        Any,
    ] = {}

    for index, (texture_name, route) in enumerate(
        resolved.texture_coordinates
    ):
        texture_node = texture_nodes.get(texture_name)
        if texture_node is None:
            continue

        coordinate = _build_texture_coordinate_socket(
            material,
            route,
            cache,
            760.0 - index * 180.0,
        )
        vector_input = texture_node.inputs.get("Vector")
        if vector_input is None:
            continue

        _clear_input_links(material.node_tree, vector_input)
        material.node_tree.links.new(coordinate, vector_input)
        texture_node["smo_fuv_index"] = route.fuv_index
        texture_node["smo_uv_index"] = route.uv_index
        texture_node["smo_uv_layer"] = (
            "UVMap"
            if route.uv_index == 0
            else f"UVMap.{route.uv_index:03d}"
        )
        texture_node["smo_tex_mtx"] = route.transform_parameter or ""
        if route.matrix is not None:
            texture_node["smo_uv_transform"] = route.matrix


def _ensure_animated_texture_transform_binding(
    material: bpy.types.Material,
    parameter_name: str,
) -> bool:
    node_tree = material.node_tree
    if node_tree is None:
        return False
    if any(
        str(node.get("smo_shader_parameter") or "") == parameter_name
        for node in node_tree.nodes
    ):
        return True

    match = re.fullmatch(r"tex_mtx([0-3])", parameter_name)
    if match is None:
        return False
    fuv_index = int(match.group(1))

    try:
        parameter_metadata = json.loads(
            str(material.get("smo_shader_parameters") or "{}")
        ).get(parameter_name, {})
        raw_value = parameter_metadata.get("value")
        coordinate_metadata = json.loads(
            str(material.get("smo_texture_coordinates") or "{}")
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False

    if not isinstance(raw_value, list):
        return False
    matrix = _tex_srt_affine_matrix(tuple(raw_value))
    if matrix is None or not isinstance(coordinate_metadata, dict):
        return False

    texture_nodes = {
        str(node.get("smo_texture_name") or ""): node
        for node in node_tree.nodes
        if str(node.get("smo_texture_name") or "")
    }
    cache: dict[
        tuple[
            int,
            str | None,
            tuple[float, float, float, float, float, float] | None,
        ],
        Any,
    ] = {}
    created = False

    for index, (texture_name, metadata) in enumerate(
        sorted(coordinate_metadata.items())
    ):
        if not isinstance(metadata, dict):
            continue
        try:
            route_fuv = int(metadata.get("fuv", -1))
            uv_index = int(metadata.get("uv", -1))
        except (TypeError, ValueError):
            continue
        if (
            route_fuv != fuv_index
            or uv_index < 0
            or metadata.get("tex_mtx")
        ):
            continue

        texture_node = texture_nodes.get(texture_name)
        if texture_node is None:
            continue
        vector_input = texture_node.inputs.get("Vector")
        if vector_input is None:
            continue

        shader_samplers = metadata.get("shader_samplers")
        if not isinstance(shader_samplers, list):
            shader_samplers = []
        route = _TextureCoordinateRoute(
            texture_name=texture_name,
            shader_samplers=tuple(str(value) for value in shader_samplers),
            fuv_index=route_fuv,
            uv_index=uv_index,
            transform_parameter=parameter_name,
            matrix=matrix,
        )
        coordinate = _build_texture_coordinate_socket(
            material,
            route,
            cache,
            760.0 - index * 180.0,
        )
        _clear_input_links(node_tree, vector_input)
        node_tree.links.new(coordinate, vector_input)
        texture_node["smo_tex_mtx"] = parameter_name
        texture_node["smo_uv_transform"] = matrix
        metadata["tex_mtx"] = parameter_name
        metadata["matrix"] = matrix
        created = True

    if created:
        material["smo_texture_coordinates"] = json.dumps(
            coordinate_metadata,
            sort_keys=True,
        )
        try:
            shader_routes = json.loads(
                str(material.get("smo_shader_routes") or "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            shader_routes = {}
        if isinstance(shader_routes, dict):
            shader_routes["texture_coordinates"] = coordinate_metadata
            material["smo_shader_routes"] = json.dumps(
                shader_routes,
                sort_keys=True,
            )

    return created


def _build_scalar_route_socket(
    material: bpy.types.Material,
    texture_nodes: dict[str, Any],
    route: _ScalarShaderRoute | None,
    color_cache: dict[object, Any],
    *,
    name: str,
    label: str,
    y: float,
) -> Any | None:
    if route is None:
        return None

    nodes = material.node_tree.nodes
    if route.kind == "CONSTANT" and route.value is not None:
        node = nodes.new("ShaderNodeValue")
        node.name = name
        node.label = label
        node.location = (-760.0, y)
        node.outputs[0].default_value = (
            1.0 - route.value if route.invert else route.value
        )
        return node.outputs[0]

    if (
        route.kind == "COLOR_ROUTE"
        and route.color_route is not None
        and route.channel is not None
    ):
        socket = _build_color_route_component(
            material,
            texture_nodes,
            route.color_route,
            route.channel,
            color_cache,
            y,
        )
        if socket is None:
            return None
        return _invert_scalar_route_socket(
            material,
            socket,
            route,
            name=f"{name} Invert",
            label="FMAT inverted component",
            y=y,
        )

    texture_node = texture_nodes.get(route.texture_name or "")
    if (
        route.kind == "TEXTURE"
        and texture_node is not None
        and route.channel is not None
    ):
        socket = _shader_texture_channel(
            material,
            texture_node,
            route.channel,
            label,
            y,
        )
        return _invert_scalar_route_socket(
            material,
            socket,
            route,
            name=f"{name} Invert",
            label="FMAT inverted component",
            y=y,
        )
    return None


def _apply_ambient_occlusion(
    material: bpy.types.Material,
    shader: Any,
    texture_nodes: dict[str, Any],
    route: _ScalarShaderRoute | None,
    color_cache: dict[object, Any],
) -> bool:
    base_input = shader.inputs.get("Base Color")
    ao_socket = _build_scalar_route_socket(
        material,
        texture_nodes,
        route,
        color_cache,
        name="SMO Ambient Occlusion",
        label="FMAT o_ao",
        y=-260.0,
    )
    if base_input is None or ao_socket is None:
        return False

    previous = (
        base_input.links[0].from_socket if base_input.links else None
    )
    previous_value = tuple(base_input.default_value)
    _clear_input_links(material.node_tree, base_input)

    multiply = material.node_tree.nodes.new("ShaderNodeMixRGB")
    multiply.name = "SMO Ambient Occlusion Multiply"
    multiply.label = "FMAT ambient occlusion"
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1.0
    multiply.inputs[1].default_value = previous_value
    multiply.location = (20.0, 80.0)
    if previous is not None:
        material.node_tree.links.new(previous, multiply.inputs[1])
    material.node_tree.links.new(ao_socket, multiply.inputs[2])
    material.node_tree.links.new(multiply.outputs["Color"], base_input)
    return True


def _build_cloth_nov_factor(
    material: bpy.types.Material,
    texture_nodes: dict[str, Any],
    cloth_nov: _ResolvedClothNoV,
    color_cache: dict[object, Any],
) -> Any:
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    layer_weight = nodes.new("ShaderNodeLayerWeight")
    layer_weight.name = "SMO Cloth Fresnel"
    layer_weight.label = "Blender Facing (1 - NoV)"
    layer_weight.location = (-1050.0, -900.0)
    layer_weight.inputs["Blend"].default_value = 0.5
    nov_socket = layer_weight.outputs["Facing"]

    # Blender Layer Weight Facing is zero head-on and one at grazing
    # angles: it is 1 - NoV. Odyssey's reverse flag requests that
    # Fresnel polarity directly; otherwise convert it back to NoV.
    if not cloth_nov.reverse:
        convert = nodes.new("ShaderNodeMath")
        convert.name = "SMO Cloth NoV"
        convert.label = "Convert Blender Facing to NoV"
        convert.operation = "SUBTRACT"
        convert.inputs[0].default_value = 1.0
        convert.location = (-850.0, -900.0)
        links.new(nov_socket, convert.inputs[1])
        nov_socket = convert.outputs[0]

    tone_scale = nodes.new("ShaderNodeMath")
    tone_scale.name = "SMO Cloth NoV Slope"
    tone_scale.label = "FMAT cloth_nov_slope0"
    tone_scale.operation = "MULTIPLY"
    tone_scale.use_clamp = True
    tone_scale.inputs[1].default_value = cloth_nov.slope
    _tag_shader_parameter_node(
        tone_scale, "cloth_nov_slope0", "SCALAR_INPUT_1"
    )
    tone_scale.location = (-650.0, -840.0)
    links.new(nov_socket, tone_scale.inputs[0])

    tone = nodes.new("ShaderNodeMath")
    tone.name = "SMO Cloth NoV Tone"
    tone.label = "FMAT cloth_nov_tone_pow0"
    tone.operation = "POWER"
    tone.inputs[1].default_value = cloth_nov.tone_power
    _tag_shader_parameter_node(
        tone, "cloth_nov_tone_pow0", "SCALAR_INPUT_1"
    )
    tone.location = (-450.0, -820.0)
    links.new(tone_scale.outputs[0], tone.inputs[0])

    peak_offset = nodes.new("ShaderNodeMath")
    peak_offset.name = "SMO Cloth NoV Peak Position"
    peak_offset.label = "FMAT cloth_nov_peak_pos0"
    peak_offset.operation = "SUBTRACT"
    peak_offset.inputs[1].default_value = cloth_nov.peak_position
    _tag_shader_parameter_node(
        peak_offset, "cloth_nov_peak_pos0", "SCALAR_INPUT_1"
    )
    peak_offset.location = (-650.0, -1010.0)
    links.new(nov_socket, peak_offset.inputs[0])

    peak_distance = nodes.new("ShaderNodeMath")
    peak_distance.name = "SMO Cloth NoV Peak Distance"
    peak_distance.operation = "ABSOLUTE"
    peak_distance.location = (-470.0, -1010.0)
    links.new(peak_offset.outputs[0], peak_distance.inputs[0])

    peak_shape = nodes.new("ShaderNodeMath")
    peak_shape.name = "SMO Cloth NoV Peak Shape"
    peak_shape.label = "FMAT cloth_nov_peak_pow0"
    peak_shape.operation = "SUBTRACT"
    peak_shape.use_clamp = True
    peak_shape.inputs[0].default_value = 1.0
    peak_shape.location = (-290.0, -1010.0)
    links.new(peak_distance.outputs[0], peak_shape.inputs[1])

    peak_power = nodes.new("ShaderNodeMath")
    peak_power.name = "SMO Cloth NoV Peak Power"
    peak_power.operation = "POWER"
    peak_power.inputs[1].default_value = cloth_nov.peak_power
    _tag_shader_parameter_node(
        peak_power, "cloth_nov_peak_pow0", "SCALAR_INPUT_1"
    )
    peak_power.location = (-110.0, -1010.0)
    links.new(peak_shape.outputs[0], peak_power.inputs[0])

    peak = nodes.new("ShaderNodeMath")
    peak.name = "SMO Cloth NoV Peak"
    peak.label = "FMAT cloth_nov_peak_intensity0"
    peak.operation = "MULTIPLY"
    peak.inputs[1].default_value = cloth_nov.peak_intensity
    _tag_shader_parameter_node(
        peak, "cloth_nov_peak_intensity0", "SCALAR_INPUT_1"
    )
    peak.location = (70.0, -1010.0)
    links.new(peak_power.outputs[0], peak.inputs[0])

    peak_boost = nodes.new("ShaderNodeMath")
    peak_boost.name = "SMO Cloth NoV Peak Modulation"
    peak_boost.label = "Peak modulates the main NoV tone"
    peak_boost.operation = "ADD"
    peak_boost.inputs[0].default_value = 1.0
    peak_boost.location = (250.0, -1010.0)
    links.new(peak.outputs[0], peak_boost.inputs[1])

    combined = nodes.new("ShaderNodeMath")
    combined.name = "SMO Cloth NoV Factor"
    combined.label = "Approximate masked FMAT cloth NoV curve"
    combined.operation = "MULTIPLY"
    combined.use_clamp = True
    combined.location = (440.0, -900.0)
    links.new(tone.outputs[0], combined.inputs[0])
    links.new(peak_boost.outputs[0], combined.inputs[1])
    factor_socket = combined.outputs[0]

    mask_socket = _build_scalar_route_socket(
        material,
        texture_nodes,
        cloth_nov.mask,
        color_cache,
        name="SMO Cloth NoV Mask Component",
        label="FMAT o_cloth_mask_map",
        y=-1120.0,
    )
    if mask_socket is not None:
        masked = nodes.new("ShaderNodeMath")
        masked.name = "SMO Cloth NoV Mask"
        masked.label = "FMAT cloth_mask_component"
        masked.operation = "MULTIPLY"
        masked.use_clamp = True
        masked.location = (630.0, -900.0)
        links.new(factor_socket, masked.inputs[0])
        links.new(mask_socket, masked.inputs[1])
        factor_socket = masked.outputs[0]

    return factor_socket


def _mix_cloth_color_into_input(
    material: bpy.types.Material,
    input_socket: Any,
    cloth_color: Any,
    factor: Any,
) -> None:
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    previous = (
        input_socket.links[0].from_socket
        if input_socket.links
        else None
    )
    previous_value = tuple(input_socket.default_value)
    _clear_input_links(material.node_tree, input_socket)

    mix = nodes.new("ShaderNodeMixRGB")
    mix.name = "SMO Cloth NoV Base Mix"
    mix.label = "Approximate FMAT o_cloth_map"
    mix.blend_type = "MIX"
    mix.location = (-20.0, 300.0)
    mix.inputs[1].default_value = previous_value
    links.new(factor, mix.inputs[0])
    if previous is not None:
        links.new(previous, mix.inputs[1])
    links.new(cloth_color, mix.inputs[2])
    links.new(mix.outputs["Color"], input_socket)


def _add_cloth_emission(
    material: bpy.types.Material,
    emission_input: Any,
    cloth_emission: Any,
    factor: Any,
    scale: float,
) -> None:
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    previous = (
        emission_input.links[0].from_socket
        if emission_input.links
        else None
    )
    # Principled's unlinked Emission Color can be white while its
    # Emission Strength is zero. Once this branch enables strength 1,
    # carrying that dormant default forward would make the entire
    # material self-illuminated. An unlinked prior emission is black.
    previous_value = (
        tuple(emission_input.default_value)
        if previous is not None
        else (0.0, 0.0, 0.0, 1.0)
    )
    _clear_input_links(material.node_tree, emission_input)

    scaled_factor = nodes.new("ShaderNodeMath")
    scaled_factor.name = "SMO Cloth NoV Emission Scale"
    scaled_factor.label = "FMAT cloth_nov_emission_scale0"
    scaled_factor.operation = "MULTIPLY"
    scaled_factor.inputs[1].default_value = scale
    _tag_shader_parameter_node(
        scaled_factor, "cloth_nov_emission_scale0", "SCALAR_INPUT_1"
    )
    scaled_factor.location = (450.0, -1080.0)
    links.new(factor, scaled_factor.inputs[0])

    factor_color = nodes.new("ShaderNodeCombineColor")
    factor_color.name = "SMO Cloth NoV Emission Factor"
    factor_color.location = (640.0, -1080.0)
    for channel in ("Red", "Green", "Blue"):
        links.new(scaled_factor.outputs[0], factor_color.inputs[channel])

    multiply = nodes.new("ShaderNodeMixRGB")
    multiply.name = "SMO Cloth NoV Emission Multiply"
    multiply.label = "Approximate FMAT o_cloth_emission_map"
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1.0
    multiply.location = (820.0, -650.0)
    links.new(cloth_emission, multiply.inputs[1])
    links.new(factor_color.outputs["Color"], multiply.inputs[2])

    add = nodes.new("ShaderNodeMixRGB")
    add.name = "SMO Cloth NoV Emission Add"
    add.label = "Add cloth NoV emission"
    add.blend_type = "ADD"
    add.inputs[0].default_value = 1.0
    add.inputs[1].default_value = previous_value
    add.location = (1020.0, -450.0)
    if previous is not None:
        links.new(previous, add.inputs[1])
    links.new(multiply.outputs["Color"], add.inputs[2])
    links.new(add.outputs["Color"], emission_input)


def _apply_cloth_nov(
    material: bpy.types.Material,
    shader: Any,
    texture_nodes: dict[str, Any],
    cloth_nov: _ResolvedClothNoV | None,
    color_cache: dict[object, Any],
) -> None:
    if cloth_nov is None:
        return

    cloth_color = (
        _build_color_shader_route(
            material,
            texture_nodes,
            cloth_nov.color,
            color_cache,
            -760.0,
        )
        if cloth_nov.color is not None
        else None
    )
    cloth_emission = (
        _build_color_shader_route(
            material,
            texture_nodes,
            cloth_nov.emission,
            color_cache,
            -940.0,
        )
        if cloth_nov.emission is not None
        else None
    )
    if cloth_color is None and cloth_emission is None:
        return

    factor = _build_cloth_nov_factor(
        material,
        texture_nodes,
        cloth_nov,
        color_cache,
    )
    base_input = shader.inputs.get("Base Color")
    if cloth_color is not None and base_input is not None:
        _mix_cloth_color_into_input(
            material,
            base_input,
            cloth_color,
            factor,
        )

    emission_input = (
        shader.inputs.get("Emission Color")
        or shader.inputs.get("Emission")
    )
    if (
        cloth_emission is not None
        and emission_input is not None
        and cloth_nov.emission_scale > 0.0
    ):
        _add_cloth_emission(
            material,
            emission_input,
            cloth_emission,
            factor,
            cloth_nov.emission_scale,
        )
        emission_strength = shader.inputs.get("Emission Strength")
        if emission_strength is not None:
            emission_strength.default_value = 1.0


def _apply_resolved_shader_material(
    material: bpy.types.Material,
    shader: Any,
    texture_nodes: dict[str, Any],
    resolved: _ResolvedMaterialShader | None,
    *,
    allow_transparency: bool,
    apply_cloth_nov_approximation: bool,
) -> bool | None:
    if resolved is None:
        return None
    material.use_backface_culling = resolved.display_face in {
        "front",
        "back",
    }
    material["smo_display_face"] = resolved.display_face

    _apply_texture_coordinate_routes(material, texture_nodes, resolved)
    extensions = dict(resolved.texture_extensions)
    for texture_name, texture_node in texture_nodes.items():
        extension = extensions.get(texture_name)
        if extension is not None:
            texture_node.extension = extension

    color_cache: dict[object, Any] = {}
    base_texture = texture_nodes.get(resolved.base_color_texture or "")
    if base_texture is not None:
        base_input = shader.inputs.get("Base Color")
        if base_input is not None:
            _clear_input_links(material.node_tree, base_input)
            multiplier = resolved.base_color_multiplier
            if multiplier is None or all(
                math.isclose(value, 1.0, abs_tol=1e-7)
                for value in multiplier
            ):
                material.node_tree.links.new(
                    base_texture.outputs["Color"], base_input
                )
            else:
                multiply = material.node_tree.nodes.new("ShaderNodeMixRGB")
                multiply.name = "SMO Base Color Multiplier"
                multiply.label = "FMAT base_color_mul_color"
                multiply.blend_type = "MULTIPLY"
                multiply.inputs[0].default_value = 1.0
                multiply.inputs[2].default_value = multiplier
                _tag_shader_parameter_node(
                    multiply,
                    "base_color_mul_color",
                    "COLOR_INPUT_2",
                )
                multiply.location = (-80.0, 180.0)
                material.node_tree.links.new(
                    base_texture.outputs["Color"], multiply.inputs[1]
                )
                material.node_tree.links.new(
                    multiply.outputs["Color"], base_input
                )
    elif resolved.base_color is not None:
        base_input = shader.inputs.get("Base Color")
        base_socket = _build_color_shader_route(
            material,
            texture_nodes,
            resolved.base_color,
            color_cache,
            180.0,
        )
        if base_input is not None and base_socket is not None:
            _clear_input_links(material.node_tree, base_input)
            material.node_tree.links.new(base_socket, base_input)

    emission_input = (
        shader.inputs.get("Emission Color")
        or shader.inputs.get("Emission")
    )
    if resolved.emission is not None and emission_input is not None:
        emission_socket = _build_color_shader_route(
            material,
            texture_nodes,
            resolved.emission,
            color_cache,
            -620.0,
        )
        if emission_socket is not None:
            _clear_input_links(material.node_tree, emission_input)
            material.node_tree.links.new(emission_socket, emission_input)
            emission_strength = shader.inputs.get("Emission Strength")
            if emission_strength is not None:
                emission_strength.default_value = 1.0

    _apply_ambient_occlusion(
        material,
        shader,
        texture_nodes,
        resolved.ao,
        color_cache,
    )

    if apply_cloth_nov_approximation:
        _apply_cloth_nov(
            material,
            shader,
            texture_nodes,
            resolved.cloth_nov,
            color_cache,
        )

    normal_texture = texture_nodes.get(resolved.normal_texture or "")
    if normal_texture is not None:
        _connect_normal_texture(
            material,
            shader,
            normal_texture,
            node_name="SMO Shader Normal Map",
            label=f"FMAT Normal: {resolved.normal_texture}",
            location=(-80.0, -360.0),
        )

    _apply_scalar_shader_route(
        material, shader, texture_nodes, resolved.roughness, color_cache,
        "Roughness", "Roughness", 0.0,
    )
    _apply_scalar_shader_route(
        material, shader, texture_nodes, resolved.metallic, color_cache,
        "Metallic", "Metallic", -180.0,
    )

    _apply_scalar_shader_route(
        material, shader, texture_nodes, resolved.sss, color_cache,
        "Subsurface Weight", "Subsurface", -360.0,
    )
    if allow_transparency:
        _apply_scalar_shader_route(
            material, shader, texture_nodes, resolved.transmission,
            color_cache, "Transmission Weight", "Transmission", -450.0,
        )

    alpha_transparency = allow_transparency and resolved.transparent
    if allow_transparency and resolved.alpha is not None:
        if _apply_scalar_shader_route(
            material, shader, texture_nodes, resolved.alpha, color_cache,
            "Alpha", "Alpha", -540.0,
        ):
            alpha_transparency = alpha_transparency or (
                resolved.alpha.kind in {"TEXTURE", "COLOR_ROUTE"}
                or (
                    resolved.alpha.value is not None
                    and resolved.alpha.value < 1.0
                )
            )
    if allow_transparency and resolved.alpha_mask_threshold is not None:
        alpha_transparency = True

    material["smo_shader_translation"] = "alRenderMaterial"
    material["smo_shader_archive"] = resolved.shader_archive_name
    material["smo_shading_model"] = resolved.shading_model_name
    material["smo_shader_options"] = json.dumps(
        dict(resolved.shader_options), sort_keys=True
    )
    material["smo_cloth_nov"] = json.dumps(
        _cloth_nov_metadata(resolved.cloth_nov),
        sort_keys=True,
    )
    material["smo_cloth_nov_approximation_enabled"] = bool(
        apply_cloth_nov_approximation and resolved.cloth_nov is not None
    )
    material["smo_shader_unhandled_outputs"] = json.dumps(
        dict(resolved.unhandled_outputs), sort_keys=True
    )
    material["smo_shader_inactive_outputs"] = json.dumps(
        dict(resolved.inactive_outputs), sort_keys=True
    )
    material["smo_shader_transparency"] = json.dumps(
        {
            "enabled": resolved.transparent,
            "alpha_mask_threshold": resolved.alpha_mask_threshold,
            "display_face": resolved.display_face,
            "transmission": _scalar_route_metadata(resolved.transmission),
            "refraction_eta": _scalar_route_metadata(resolved.refraction_eta),
            "refraction_color": _color_route_metadata(resolved.refraction_color),
        },
        sort_keys=True,
    )
    material["smo_texture_coordinates"] = json.dumps(
        {
            texture_name: _texture_coordinate_metadata(route)
            for texture_name, route in resolved.texture_coordinates
        },
        sort_keys=True,
    )
    material["smo_shader_unhandled_texture_coordinates"] = json.dumps(
        [
            {"texture": texture_name, "reason": reason}
            for texture_name, reason
            in resolved.unhandled_texture_coordinates
        ],
        sort_keys=True,
    )
    material["smo_shader_routes"] = json.dumps(
        {
            "base_color": _color_route_metadata(resolved.base_color),
            "emission": _color_route_metadata(resolved.emission),
            "normal": resolved.normal_texture,
            "roughness": _scalar_route_metadata(resolved.roughness),
            "metallic": _scalar_route_metadata(resolved.metallic),
            "alpha": _scalar_route_metadata(resolved.alpha),
            "ao": _scalar_route_metadata(resolved.ao),
            "sss": _scalar_route_metadata(resolved.sss),
            "transmission": _scalar_route_metadata(resolved.transmission),
            "refraction_eta": _scalar_route_metadata(resolved.refraction_eta),
            "refraction_color": _color_route_metadata(resolved.refraction_color),
            "cloth_nov": _cloth_nov_metadata(resolved.cloth_nov),
            "texture_coordinates": {
                texture_name: _texture_coordinate_metadata(route)
                for texture_name, route in resolved.texture_coordinates
            },
            "unhandled_texture_coordinates": (
                resolved.unhandled_texture_coordinates
            ),
        },
        sort_keys=True,
    )
    return alpha_transparency

def _json_shader_parameter_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, tuple):
        return [_json_shader_parameter_value(component) for component in value]
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return repr(value)


def _create_albedo_material(
    asset_name: str,
    material_name: str,
    texture_name: str,
    image: bpy.types.Image,
    has_transparency: bool,
    identity: str,
    *,
    is_fallback: bool = False,
    candidate_index: int = 0,
    texture_bindings: tuple[TextureBinding, ...] = (),
    ignore_texture_alpha: bool = False,
    atmosphere_kind: str | None = None,
    shader_material: _ResolvedMaterialShader | None = None,
    shader_parameters: tuple[Any, ...] = (),
    shader_options: tuple[tuple[str, str], ...] = (),
    shader_render_infos: tuple[Any, ...] = (),
    apply_cloth_nov_approximation: bool = False,
) -> bpy.types.Material:
    name = f"SMO [{identity}] {asset_name} - {material_name}"
    material = bpy.data.materials.get(name)

    if material is None:
        material = bpy.data.materials.new(name)

    material.use_nodes = True
    material["smo_source_material_name"] = material_name
    material["smo_shader_parameters"] = json.dumps(
        {
            parameter.name: {
                "type_id": int(parameter.type_id),
                "type_name": str(parameter.type_name),
                "value": _json_shader_parameter_value(parameter.value),
            }
            for parameter in shader_parameters
        },
        sort_keys=True,
    )
    material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    material.roughness = 0.8
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (420.0, 0.0)
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.location = (120.0, 0.0)
    shader.inputs["Roughness"].default_value = 0.8
    shader.inputs["Alpha"].default_value = 1.0
    _set_specular_ior_level(shader)
    texture = nodes.new("ShaderNodeTexImage")
    texture.name = "SMO Base Color"
    texture.label = f"Base Color: {texture_name}"
    texture.location = (-320.0, 180.0)
    texture.image = image
    texture["smo_texture_name"] = texture_name
    texture["smo_texture_role"] = "DISPLAY"
    texture_nodes = {texture_name: texture}
    material.node_tree.links.new(
        texture.outputs["Color"],
        shader.inputs["Base Color"],
    )
    material.node_tree.links.new(
        shader.outputs["BSDF"],
        output.inputs["Surface"],
    )

    if has_transparency and not ignore_texture_alpha:
        material.node_tree.links.new(
            texture.outputs["Alpha"],
            shader.inputs["Alpha"],
        )

    connected_roles = set()
    display_role = next(
        (
            role
            for role, binding_name, _, _ in texture_bindings
            if binding_name == texture_name
        ),
        _texture_role(texture_name),
    )

    if display_role == "ALBEDO":
        connected_roles.add("ALBEDO")
        texture["smo_texture_role"] = "ALBEDO"

    role_inputs = {
        "ALBEDO": "Base Color",
        "ROUGHNESS": "Roughness",
        "METALLIC": "Metallic",
        "EMISSION": "Emission Color",
    }
    role_locations = {
        "ALBEDO": 180.0,
        "ROUGHNESS": 0.0,
        "METALLIC": -180.0,
        "NORMAL": -360.0,
        "EMISSION": -540.0,
        "UNASSIGNED": -720.0,
    }
    loaded_texture_names = []
    connected_textures: dict[str, str] = {}
    extra_index = 0

    primary_samplers = {
        "ALBEDO": "_a0",
        "NORMAL": "_n0",
        "EMISSION": "_e0",
    }
    preferred_role_bindings: dict[str, str] = {}

    for role, binding_name, sampler_name, _ in texture_bindings:
        current = preferred_role_bindings.get(role)

        if current is None or (
            sampler_name.casefold() == primary_samplers.get(role)
        ):
            preferred_role_bindings[role] = binding_name

    for role, binding_name, sampler_name, binding_image in texture_bindings:
        loaded_texture_names.append(binding_name)

        if role == "ALBEDO" and binding_name == texture_name:
            connected_textures[role] = binding_name
            continue

        node = nodes.new("ShaderNodeTexImage")
        node.name = f"SMO {role.title()} - {binding_name}"
        node.label = f"{role.title()}: {binding_name}"
        node.location = (
            -520.0 - 240.0 * extra_index,
            role_locations[role],
        )
        node.image = binding_image
        node["smo_texture_name"] = binding_name
        node["smo_texture_role"] = role
        node["smo_sampler_name"] = sampler_name
        texture_nodes.setdefault(binding_name, node)

        if (
            role in connected_roles
            or role == "UNASSIGNED"
            or preferred_role_bindings.get(role) != binding_name
        ):
            extra_index += 1
            continue

        if role == "NORMAL":
            _connect_normal_texture(
                material,
                shader,
                node,
                node_name=f"SMO Normal Map - {binding_name}",
                label=f"Normal Map: {binding_name}",
                location=(-140.0, -360.0),
            )
        else:
            shader_input = shader.inputs.get(role_inputs[role])

            if shader_input is None:
                extra_index += 1
                continue

            material.node_tree.links.new(
                node.outputs["Color"],
                shader_input,
            )

            if role == "EMISSION":
                strength = shader.inputs.get("Emission Strength")

                if strength is not None:
                    strength.default_value = 1.0

        connected_roles.add(role)
        connected_textures[role] = binding_name
        extra_index += 1

    translated_transparency = _apply_resolved_shader_material(
        material,
        shader,
        texture_nodes,
        shader_material,
        allow_transparency=not ignore_texture_alpha,
        apply_cloth_nov_approximation=apply_cloth_nov_approximation,
    )
    effective_transparency = (
        has_transparency and not ignore_texture_alpha
        if translated_transparency is None
        else translated_transparency
    )

    atmosphere_transparency = _apply_atmosphere_shader(
        material,
        output,
        shader,
        texture_nodes,
        texture_name,
        atmosphere_kind,
        shader_parameters,
        shader_options,
        shader_render_infos,
    )
    if atmosphere_transparency is not None:
        effective_transparency = atmosphere_transparency
    if shader_material is not None:
        if (
            shader_material.base_color is not None
            and shader_material.base_color.kind == "TEXTURE"
            and shader_material.base_color.texture_name in texture_nodes
        ):
            connected_textures["ALBEDO"] = (
                shader_material.base_color.texture_name
            )
        else:
            connected_textures.pop("ALBEDO", None)
        if (
            shader_material.emission is not None
            and shader_material.emission.kind == "TEXTURE"
            and shader_material.emission.texture_name in texture_nodes
        ):
            connected_textures["EMISSION"] = (
                shader_material.emission.texture_name
            )
        elif shader_material.emission is not None:
            connected_textures.pop("EMISSION", None)
        if (
            shader_material.normal_texture is not None
            and shader_material.normal_texture in texture_nodes
        ):
            connected_textures["NORMAL"] = shader_material.normal_texture
        for route, role in (
            (shader_material.roughness, "ROUGHNESS"),
            (shader_material.metallic, "METALLIC"),
        ):
            if route is None:
                continue
            if (
                route.kind == "TEXTURE"
                and route.texture_name in texture_nodes
            ):
                connected_textures[role] = route.texture_name
            else:
                connected_textures.pop(role, None)
    _set_material_transparency(material, effective_transparency)
    material["smo_display_texture"] = texture_name
    material["smo_display_texture_fallback"] = is_fallback
    material["smo_display_texture_index"] = candidate_index
    material["smo_albedo_texture"] = texture_name
    material["smo_texture_alpha_ignored"] = ignore_texture_alpha
    material["smo_loaded_textures"] = json.dumps(loaded_texture_names)
    material["smo_connected_textures"] = json.dumps(
        connected_textures,
        sort_keys=True,
    )
    return material


def _preferred_bfres_name(resource: Any) -> str | None:
    requested_name = (resource.requested_name or "").casefold()

    if requested_name:
        matching_name = next(
            (
                name
                for name in resource.bfres_files
                if Path(name).stem.casefold() == requested_name
            ),
            None,
        )

        if matching_name is not None:
            return matching_name

    return resource.bfres_files[0] if resource.bfres_files else None


def _set_placement_properties(
    obj: bpy.types.Object,
    classified: Any,
    representation: str,
    fallback_reason: str = "",
) -> None:
    placement = classified.placement
    resource = classified.resource
    obj["smo_static_model_generated"] = True
    obj["smo_representation"] = representation
    obj["smo_id"] = placement.identifier
    obj["smo_unit_config_name"] = placement.unit_config_name
    obj["smo_parameter_config_name"] = str(
        placement.unit_config.get("ParameterConfigName") or ""
    )
    obj["smo_model_name"] = placement.model_name or ""
    obj["smo_import_category"] = classified.category.value
    obj["smo_stage_layer"] = placement.stage_layer
    obj["smo_source_stage_name"] = placement.source_stage_name
    obj["smo_synthesised_sky"] = bool(
        placement.raw.get("SMOSynthesised", False)
    )
    obj["smo_zone_path"] = json.dumps(placement.zone_path)
    obj["smo_resource_source_field"] = resource.source_field or ""
    obj["smo_resource_archive"] = str(resource.archive_path or "")
    obj["smo_bfres_files"] = json.dumps(resource.bfres_files)
    obj["smo_resource_components"] = json.dumps(
        [
            {
                "archive": str(component.archive_path or ""),
                "requested_name": component.requested_name or "",
                "bfres_files": list(component.bfres_files),
            }
            for component in resource.model_resources
        ],
        sort_keys=True,
    )
    obj["smo_fallback_reason"] = fallback_reason

    if fallback_reason:
        assessment = classified.model_expectation
        obj["smo_model_expectation"] = assessment.expectation.value
        obj["smo_model_expectation_confidence"] = assessment.confidence
        obj["smo_model_expectation_reasons"] = json.dumps(
            assessment.reasons
        )


def _fallback_reason(
    resource: Any,
    bfres_name: str | None,
    asset_errors: dict[tuple[Path, str], str],
) -> str:
    if resource.archive_path is None:
        return "No matching ObjectData archive"

    if bfres_name is None:
        return "ObjectData archive contains no BFRES"

    error = asset_errors.get((resource.archive_path, bfres_name))

    if error:
        return error

    return "BFRES contains no supported static meshes"


def _apply_placement_transform(obj: bpy.types.Object, placement: Any) -> None:
    from .stage_data import placement_model_transform_to_blender

    transform = placement_model_transform_to_blender(placement)
    obj.location = transform.location
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = transform.rotation_quaternion
    obj.scale = transform.scale


_CATEGORY_SETTING_BY_FILTER_GROUP = {
    "ENVIRONMENT": "include_environment",
    "CHARACTERS": "include_characters",
    "GAMEPLAY": "include_gameplay",
    "COLLECTIBLES": "include_collectibles",
    "EFFECTS": "include_effects",
    "AUDIO": "include_audio",
    "TECHNICAL": "include_technical",
    "UNCLASSIFIED": "include_unclassified",
}


def import_category_enabled(settings: Any, classified: Any) -> bool:
    group = import_filter_group(classified)
    property_name = _CATEGORY_SETTING_BY_FILTER_GROUP[group]
    return bool(getattr(settings, property_name, True))

_CATEGORY_IMPORT_ORDER = {
    "ENVIRONMENT": 0,
    "UNKNOWN_MODEL": 1,
    "GAMEPLAY": 2,
    "CHARACTERS": 3,
    "COLLECTIBLES": 4,
    "EFFECTS": 5,
    "AREAS": 6,
    "CAMERAS": 7,
    "HELPERS": 8,
    "AUDIO": 9,
    "DEBUG": 10,
    "UNKNOWN_MODELLESS": 11,
}
_STAGE_LAYER_ORDER = {"Map": 0, "Design": 1, "Sound": 2}
_STATIC_IMPORT_RUNNING = False
_STATIC_IMPORT_CANCEL_REQUESTED = False
_STATIC_IMPORT_PROGRESS = (0, 0, "")


def static_import_is_running() -> bool:
    return _STATIC_IMPORT_RUNNING


def static_import_status() -> tuple[int, int, str]:
    return _STATIC_IMPORT_PROGRESS


def request_static_import_cancel() -> bool:
    global _STATIC_IMPORT_CANCEL_REQUESTED

    if not _STATIC_IMPORT_RUNNING:
        return False

    _STATIC_IMPORT_CANCEL_REQUESTED = True
    return True


class SMO_OT_import_static_models(Operator):
    bl_idname = "smo.import_static_models"
    bl_label = "Import Stage"
    bl_description = (
        "Progressively import supported BFRES models with cube fallbacks; "
        "press Esc to cancel"
    )
    bl_options = {"REGISTER", "UNDO"}

    _timer: Any = None

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import actor_registry_report_is_running, has_valid_selection

        settings = getattr(context.scene, "smo_settings", None)
        return (
            not static_import_is_running()
            and not actor_registry_report_is_running()
            and has_valid_selection(settings)
        )

    def _queue_sort_key(self, classified: Any) -> tuple[int, int, int]:
        placement = classified.placement
        zone_key = "/".join(placement.zone_path)
        source_order = (
            0
            if not zone_key
            else self._zone_order.get(zone_key, len(self._zone_order)) + 1
        )
        return (
            source_order,
            _CATEGORY_IMPORT_ORDER.get(classified.category.value, 99),
            _STAGE_LAYER_ORDER.get(placement.stage_layer, 99),
        )

    def _redraw_viewports(self, context: bpy.types.Context) -> None:
        screen = getattr(context, "screen", None)

        if screen is None:
            return

        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    def _set_status(
        self,
        context: bpy.types.Context,
        classified: Any,
    ) -> None:
        global _STATIC_IMPORT_PROGRESS

        placement = classified.placement
        asset_name = placement.model_name or placement.unit_config_name
        source_name = (
            placement.zone_path[-1]
            if placement.zone_path
            else "Main stage"
        )
        message = (
            f"SMO {self._queue_index + 1}/{len(self._queue)}"
            f" - {source_name} - {asset_name} - Esc to cancel"
        )
        _STATIC_IMPORT_PROGRESS = (
            self._queue_index,
            len(self._queue),
            f"{source_name} - {asset_name}",
        )
        workspace = getattr(context, "workspace", None)

        if workspace is not None:
            workspace.status_text_set(message)

    def _stop_modal(self, context: bpy.types.Context) -> None:
        global _STATIC_IMPORT_CANCEL_REQUESTED
        global _STATIC_IMPORT_PROGRESS
        global _STATIC_IMPORT_RUNNING

        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

        context.window_manager.progress_end()
        workspace = getattr(context, "workspace", None)

        if workspace is not None:
            workspace.status_text_set(None)

        _STATIC_IMPORT_CANCEL_REQUESTED = False
        _STATIC_IMPORT_PROGRESS = (0, 0, "")
        _STATIC_IMPORT_RUNNING = False
        self._redraw_viewports(context)

    def _load_texture_archive(
        self,
        source_key: tuple[Path, str],
        bfres_data: bytes,
    ) -> Any:
        from .bntx_texture import BntxTextureArchive

        if source_key not in self._texture_archive_cache:
            try:
                self._texture_archive_cache[source_key] = (
                    BntxTextureArchive.from_bfres(bfres_data)
                )
            except Exception as exc:
                self._texture_archive_cache[source_key] = None
                error_key = f"{source_key[0].name}/{source_key[1]}"
                self._texture_errors[error_key] = str(exc)
                print(
                    "[Odyssey Toolkit] Failed to read textures from "
                    f"{error_key}:"
                )
                traceback.print_exc()

        return self._texture_archive_cache[source_key]

    def _decode_texture(
        self,
        source_key: SourceKey,
        texture_archive: Any,
        texture_name: str,
    ) -> Any:
        persistent_cache = getattr(
            self,
            "_persistent_texture_cache",
            None,
        )

        if persistent_cache is not None:
            cached = persistent_cache.load(source_key, texture_name)

            if cached is not None:
                return cached

        decoded = texture_archive.decode(texture_name)

        if persistent_cache is not None:
            persistent_cache.store(source_key, decoded)

        return decoded

    def _shared_texture_paths(
        self,
        classified: Any,
        resource: Any | None = None,
    ) -> tuple[Path, ...]:
        placement = classified.placement
        resource = resource or classified.resource
        names = []
        cache_key = (
            resource.archive_path,
            placement.source_stage_name,
            self._stage_name,
        )
        path_cache = getattr(self, "_shared_texture_path_cache", None)

        if path_cache is None:
            path_cache = {}
            self._shared_texture_path_cache = path_cache
        elif cache_key in path_cache:
            return path_cache[cache_key]

        if resource.archive_path is not None:
            resource_name = resource.archive_path.stem
            names.append(f"{resource_name}Texture.szs")
            names.extend(texture_archive_rule_names(resource_name))
            names.extend(_stage_texture_archive_names(resource_name))

        names.extend(
            _stage_texture_archive_names(placement.source_stage_name)
        )
        names.extend(_stage_texture_archive_names(self._stage_name))
        paths = []

        for name in names:
            path = self._object_data_dir / name

            if path.is_file() and path not in paths:
                paths.append(path)

        path_cache[cache_key] = tuple(paths)
        return path_cache[cache_key]

    def _read_shared_texture_archive(
        self,
        archive_path: Path,
    ) -> tuple[tuple[Path, str], Any] | None:
        from .world_list import extract_file, read_szs

        archive = self._archive_cache.get(archive_path)

        if archive is None:
            print(
                "[Odyssey Toolkit] Loading shared textures from "
                f"{archive_path.name}",
                flush=True,
            )
            archive = read_szs(archive_path)
            self._archive_cache[archive_path] = archive

        bfres_files = tuple(
            entry.name
            for entry in archive.get_files()
            if Path(entry.name).suffix.casefold() == ".bfres"
        )
        bfres_name = next(
            (
                name
                for name in bfres_files
                if Path(name).stem.casefold()
                == archive_path.stem.casefold()
            ),
            bfres_files[0] if bfres_files else None,
        )

        if bfres_name is None:
            return None

        source_key = (archive_path, bfres_name)
        texture_archive = self._texture_archive_cache.get(source_key, ...)

        if texture_archive is ...:
            bfres_data = bytes(extract_file(archive, bfres_name))
            texture_archive = self._load_texture_archive(
                source_key,
                bfres_data,
            )

        if texture_archive is None:
            return None

        return source_key, texture_archive

    def _load_shared_texture_archive(
        self,
        archive_path: Path,
    ) -> tuple[tuple[Path, str], Any] | None:
        cache = getattr(self, "_shared_texture_archive_cache", None)

        if cache is None:
            cache = {}
            self._shared_texture_archive_cache = cache
        elif archive_path in cache:
            return cache[archive_path]

        try:
            result = (
                SMO_OT_import_static_models._read_shared_texture_archive(
                    self,
                    archive_path,
                )
            )
        except Exception as exc:
            result = None
            error_key = f"{archive_path.name}/shared textures"
            self._texture_errors[error_key] = str(exc)
            print(
                "[Odyssey Toolkit] Ignoring unreadable optional "
                f"texture archive {archive_path.name}:"
            )
            traceback.print_exc()

        cache[archive_path] = result
        return result

    def _material_for_mesh(
        self,
        source: Any,
        asset_key: SourceKey,
        bfres_data: bytes,
        shared_texture_paths: tuple[Path, ...],
        asset_name: str,
        *,
        ignore_texture_alpha: bool = False,
    ) -> bpy.types.Material:
        display_textures = tuple(getattr(source, "texture_names", ()))
        sampler_names = tuple(
            getattr(source, "texture_sampler_names", ())
        )
        sampler_by_texture = {
            texture_name: sampler_names[index]
            for index, texture_name in enumerate(display_textures)
            if index < len(sampler_names)
        }

        shader_material = _resolve_material_shader(source)

        source_material_shader = getattr(source, "material_shader", None)
        atmosphere_kind = _atmosphere_shader_kind(source)
        if atmosphere_kind is not None:
            ignore_texture_alpha = True
        shader_role_overrides = _shader_texture_role_overrides(
            shader_material
        )

        if not display_textures and source.albedo_texture_name is not None:
            display_textures = (source.albedo_texture_name,)

        if not display_textures:
            if source.base_color is None:
                return _placeholder_material(source.material_name)

            material_key = (
                "solid",
                asset_key,
                source.material_name,
                source.base_color,
                shader_material,
            )

            if material_key not in self._material_cache:
                identity = _identity_digest(*material_key)
                self._material_cache[material_key] = (
                    _create_solid_material(
                        asset_name,
                        source.material_name,
                        source.base_color,
                        identity,
                        shader_material=shader_material,
                    )
                )

            return self._material_cache[material_key]

        candidates = []
        embedded = self._load_texture_archive(asset_key, bfres_data)

        if embedded is not None:
            candidates.append((asset_key, embedded))

        for archive_path in shared_texture_paths:
            shared = self._load_shared_texture_archive(archive_path)

            if shared is not None:
                candidates.append(shared)

        texture_bindings: list[TextureBinding] = []
        binding_keys = []
        missing_textures = set()
        base_binding = None

        for candidate_index, texture_name in enumerate(display_textures):
            loaded = None

            for source_key, texture_archive in candidates:
                if texture_name not in texture_archive.names:
                    continue

                decoded_key = (source_key, texture_name)

                if decoded_key not in self._decoded_texture_cache:
                    try:
                        self._decoded_texture_cache[decoded_key] = (
                            SMO_OT_import_static_models._decode_texture(
                                self,
                                source_key,
                                texture_archive,
                                texture_name,
                            )
                        )
                    except Exception as exc:
                        self._decoded_texture_cache[decoded_key] = None
                        error_key = (
                            f"{source_key[0].name}/{source_key[1]}/"
                            f"{texture_name}"
                        )
                        self._texture_errors[error_key] = str(exc)
                        print(
                            "[Odyssey Toolkit] Failed to decode texture "
                            f"{error_key}:"
                        )
                        traceback.print_exc()

                decoded = self._decoded_texture_cache[decoded_key]

                if decoded is None:
                    continue

                role = shader_role_overrides.get(
                    texture_name,
                    _texture_role(
                        texture_name,
                        sampler_by_texture.get(texture_name, ""),
                    ),
                )
                colour_space = (
                    "sRGB"
                    if role in {"ALBEDO", "EMISSION"}
                    else "Non-Color"
                )
                image_key = (
                    source_key,
                    texture_name,
                    colour_space,
                    role,
                )

                if image_key not in self._image_cache:
                    try:
                        self._image_cache[image_key] = _create_texture_image(
                            decoded,
                            source_key,
                            colour_space,
                            role,
                        )
                    except Exception as exc:
                        self._image_cache[image_key] = None
                        error_key = (
                            f"{source_key[0].name}/{source_key[1]}/"
                            f"{texture_name}/{colour_space}"
                        )
                        self._texture_errors[error_key] = str(exc)
                        print(
                            "[Odyssey Toolkit] Failed to create image "
                            f"{error_key}:"
                        )
                        traceback.print_exc()

                image = self._image_cache[image_key]

                if image is None:
                    continue

                loaded = (
                    role,
                    source_key,
                    decoded,
                    image,
                    colour_space,
                )
                break

            if loaded is None:
                missing_textures.add(texture_name)
                continue

            role, source_key, decoded, image, colour_space = loaded
            texture_bindings.append(
                (
                    role,
                    texture_name,
                    sampler_by_texture.get(texture_name, ""),
                    image,
                )
            )
            binding_keys.append(
                (role, source_key, texture_name, colour_space)
            )

            if base_binding is not None:
                continue

            base_image = image

            if colour_space != "sRGB":
                base_image_key = (
                    source_key,
                    texture_name,
                    "sRGB",
                    role,
                )

                if base_image_key not in self._image_cache:
                    try:
                        self._image_cache[base_image_key] = (
                            _create_texture_image(
                                decoded,
                                source_key,
                                "sRGB",
                                role,
                            )
                        )
                    except Exception as exc:
                        self._image_cache[base_image_key] = None
                        error_key = (
                            f"{source_key[0].name}/{source_key[1]}/"
                            f"{texture_name}/sRGB"
                        )
                        self._texture_errors[error_key] = str(exc)
                        print(
                            "[Odyssey Toolkit] Failed to create display "
                            f"image {error_key}:"
                        )
                        traceback.print_exc()

                base_image = self._image_cache[base_image_key]

            if base_image is not None:
                base_binding = (
                    candidate_index,
                    texture_name,
                    source_key,
                    base_image,
                    decoded.has_transparency if role == "ALBEDO" else False,
                )

        self._missing_albedo_textures.update(missing_textures)

        if base_binding is None:
            self._missing_albedo_textures.update(display_textures)
            return _placeholder_material(source.material_name)

        (
            candidate_index,
            texture_name,
            source_key,
            image,
            has_transparency,
        ) = base_binding
        is_fallback = texture_name != source.albedo_texture_name
        apply_cloth_nov_approximation = bool(
            getattr(self, "_experimental_cloth_nov", False)
        )
        material_key = (
            "textured",
            asset_key,
            source.material_name,
            source_key,
            texture_name,
            is_fallback,
            tuple(binding_keys),
            shader_material,
            ignore_texture_alpha,
            atmosphere_kind,
            apply_cloth_nov_approximation,
        )

        if material_key not in self._material_cache:
            identity = _identity_digest(*material_key)
            self._material_cache[material_key] = _create_albedo_material(
                asset_name,
                source.material_name,
                texture_name,
                image,
                has_transparency,
                identity,
                is_fallback=is_fallback,
                candidate_index=candidate_index,
                texture_bindings=tuple(texture_bindings),
                atmosphere_kind=atmosphere_kind,
                ignore_texture_alpha=ignore_texture_alpha,
                shader_material=shader_material,
                apply_cloth_nov_approximation=(
                    apply_cloth_nov_approximation
                ),
                shader_parameters=(
                    tuple(material_shader.parameters)
                    if (
                        material_shader := getattr(
                            source, "material_shader", None
                        )
                    )
                    is not None
                    else ()
                ),
                shader_options=(
                    tuple(source_material_shader.shader_options)
                    if source_material_shader is not None
                    else ()
                ),
                shader_render_infos=(
                    tuple(source_material_shader.render_infos)
                    if source_material_shader is not None
                    else ()
                ),
            )

        if is_fallback:
            self._fallback_display_textures.add(
                f"{source.material_name}: {texture_name}"
            )

        return self._material_cache[material_key]

    def _meshes_for_resource(
        self,
        classified: Any,
        resource: Any,
    ) -> tuple[bpy.types.Mesh, ...]:
        from .bfres_mesh import read_static_bfres
        from .world_list import extract_file, read_szs

        archive_path = resource.archive_path
        bfres_name = _preferred_bfres_name(resource)

        if archive_path is None or bfres_name is None:
            return ()

        asset_key = (archive_path, bfres_name)
        shared_texture_paths = self._shared_texture_paths(
            classified,
            resource,
        )
        ignore_texture_alpha = classified.placement.category == "SkyList"
        asset_context_key = _asset_context_key(
            asset_key,
            shared_texture_paths,
            ignore_texture_alpha,
        )

        if asset_context_key not in self._asset_cache:
            try:
                print(
                    "[Odyssey Toolkit] Decoding "
                    f"{bfres_name} "
                    f"({self._queue_index + 1}/{len(self._queue)})",
                    flush=True,
                )
                archive = self._archive_cache.get(archive_path)

                if archive is None:
                    archive = read_szs(archive_path)
                    self._archive_cache[archive_path] = archive

                bfres_data = bytes(extract_file(archive, bfres_name))
                models = read_static_bfres(
                    bfres_data,
                    include_rigging=self._import_armatures,
                )
                asset_name = Path(bfres_name).stem
                created_meshes = []

                for model_index, model in enumerate(models):
                    rig_key = _identity_digest(
                        *asset_context_key,
                        model_index,
                        model.name,
                    )
                    has_armature = (
                        self._import_armatures
                        and _model_has_deformable_skeleton(model)
                    )
                    armature_name = (
                        f"{archive_path.stem}_{model.name}_Armature"
                    )

                    for source_mesh in model.meshes:
                        material = self._material_for_mesh(
                            source_mesh,
                            asset_key,
                            bfres_data,
                            shared_texture_paths,
                            asset_name,
                            ignore_texture_alpha=ignore_texture_alpha,
                        )
                        created_mesh = _create_mesh_data(
                            source_mesh,
                            archive_path.stem,
                            material,
                            self._apply_custom_normals,
                        )
                        created_meshes.append(created_mesh)

                        if has_armature and source_mesh.bone_weights:
                            self._mesh_rig_bindings[created_mesh.name] = (
                                MeshRigBinding(
                                    rig_key=rig_key,
                                    model_name=model.name,
                                    armature_name=armature_name,
                                    source_archive=archive_path,
                                    source_bfres=bfres_name,
                                    skeleton=model.skeleton,
                                    bone_weights=source_mesh.bone_weights,
                                )
                            )

                self._asset_cache[asset_context_key] = tuple(created_meshes)
            except NotImplementedError as exc:
                self._asset_cache[asset_context_key] = None
                self._asset_errors[asset_key] = str(exc)
                print(
                    "[Odyssey Toolkit] Static model "
                    f"unsupported: {bfres_name}: {exc}"
                )
            except Exception as exc:
                self._asset_cache[asset_context_key] = None
                self._asset_errors[asset_key] = str(exc)
                print(
                    "[Odyssey Toolkit] Failed to decode "
                    f"static model {bfres_name}:"
                )
                traceback.print_exc()

        return self._asset_cache[asset_context_key] or ()

    def _armature_for_binding(
        self,
        collection: bpy.types.Collection,
        binding: MeshRigBinding,
    ) -> tuple[bpy.types.Object, tuple[str, ...]]:
        cached = self._armature_data_cache.get(binding.rig_key)

        if cached is None:
            armature_object, bone_names = _create_armature_object(
                collection,
                binding.armature_name,
                binding.skeleton,
            )
            self._armature_data_cache[binding.rig_key] = (
                armature_object.data,
                bone_names,
            )
            return armature_object, bone_names

        armature_data, bone_names = cached
        armature_object, reused_names = _create_armature_object(
            collection,
            binding.armature_name,
            binding.skeleton,
            armature_data=armature_data,
        )

        if reused_names != bone_names:
            bpy.data.objects.remove(armature_object, do_unlink=True)
            raise ValueError(
                "Cached Blender armature changed its FSKL bone ordering."
            )

        return armature_object, bone_names


    def _process_placement(self, classified: Any) -> None:
        from . import get_or_create_import_group_collection

        placement = classified.placement
        resource = classified.resource

        if getattr(self, "_import_stage_lighting", False):
            try:
                from .stage_lighting import create_local_stage_light

                local_light = create_local_stage_light(
                    self._lighting_collection,
                    self._root,
                    placement,
                )

                if local_light is not None:
                    self._local_light_count += 1
                    return
            except Exception as exc:
                self._lighting_errors[placement.identifier] = str(exc)

                if len(self._lighting_errors) == 1:
                    print(
                        "[Odyssey Toolkit] A local stage light could "
                        f"not be created: {exc}"
                    )

        group_collection = get_or_create_import_group_collection(
            self._collection,
            classified.category,
            self._group_scope,
        )
        source_meshes: tuple[bpy.types.Mesh, ...] = ()
        representation = "STATIC_MODEL"

        if placement.unit_config_name == "OceanWave":
            source_meshes = (_create_ocean_wave_mesh(),)
            representation = "PROCEDURAL_OCEAN"
        elif resource.model_resources:
            created_meshes: list[bpy.types.Mesh] = []

            for component in resource.model_resources:
                created_meshes.extend(
                    self._meshes_for_resource(classified, component)
                )

            source_meshes = tuple(created_meshes)

            if len(resource.model_resources) > 1:
                representation = "COMPOSITE_MODEL"

        if source_meshes:
            rig_bindings = getattr(self, "_mesh_rig_bindings", {})
            rig_groups: dict[
                str,
                tuple[MeshRigBinding, list[bpy.types.Mesh]],
            ] = {}
            static_meshes = []

            for source_mesh in source_meshes:
                binding = rig_bindings.get(source_mesh.name)

                if binding is None:
                    static_meshes.append(source_mesh)
                    continue

                existing = rig_groups.get(binding.rig_key)

                if existing is None:
                    rig_groups[binding.rig_key] = (
                        binding,
                        [source_mesh],
                    )
                else:
                    existing[1].append(source_mesh)

            def create_static_object(
                source_mesh: bpy.types.Mesh,
                object_representation: str,
            ) -> bpy.types.Object:
                object_name = str(
                    source_mesh.get("smo_display_name", source_mesh.name)
                )
                obj = bpy.data.objects.new(object_name, source_mesh)
                group_collection.objects.link(obj)
                obj.parent = self._root
                _apply_placement_transform(obj, placement)
                _set_placement_properties(
                    obj,
                    classified,
                    object_representation,
                )
                obj["smo_procedural_ocean"] = (
                    object_representation == "PROCEDURAL_OCEAN"
                )
                self._mesh_object_count += 1
                return obj

            for source_mesh in static_meshes:
                create_static_object(source_mesh, representation)

            for binding, rig_meshes in rig_groups.values():
                created_rig_objects = []
                successful_rig_objects = []
                armature_object = None
                armature_counted = False

                try:
                    armature_object, bone_names = (
                        self._armature_for_binding(
                            group_collection,
                            binding,
                        )
                    )
                    armature_object.parent = self._root
                    _apply_placement_transform(
                        armature_object,
                        placement,
                    )
                    rig_representation = (
                        "COMPOSITE_RIGGED_MODEL"
                        if representation == "COMPOSITE_MODEL"
                        else "RIGGED_MODEL"
                    )
                    _set_placement_properties(
                        armature_object,
                        classified,
                        rig_representation,
                    )
                    armature_object["smo_armature_generated"] = True
                    armature_object["smo_rig_key"] = binding.rig_key
                    if binding.source_archive is not None:
                        armature_object["smo_source_archive"] = str(
                            binding.source_archive
                        )
                    if binding.source_bfres:
                        armature_object["smo_source_bfres"] = (
                            binding.source_bfres
                        )
                    armature_object["smo_source_model"] = (
                        binding.model_name
                    )
                    armature_object["smo_bone_count"] = len(bone_names)
                    self._armature_object_count += 1
                    armature_counted = True

                    for source_mesh in rig_meshes:
                        object_name = str(
                            source_mesh.get(
                                "smo_display_name",
                                source_mesh.name,
                            )
                        )
                        obj = bpy.data.objects.new(
                            object_name,
                            source_mesh,
                        )
                        group_collection.objects.link(obj)
                        created_rig_objects.append(obj)
                        _apply_skin_binding(
                            obj,
                            armature_object,
                            rig_bindings[source_mesh.name],
                            bone_names,
                        )
                        _set_placement_properties(
                            obj,
                            classified,
                            rig_representation,
                        )
                        obj["smo_procedural_ocean"] = False
                        successful_rig_objects.append(obj)
                        self._mesh_object_count += 1
                        self._rigged_mesh_object_count += 1
                except Exception as exc:
                    first_rig_error = (
                        binding.rig_key not in self._rig_errors
                    )
                    self._rig_errors.setdefault(
                        binding.rig_key,
                        str(exc),
                    )

                    self._mesh_object_count -= len(
                        successful_rig_objects
                    )
                    self._rigged_mesh_object_count -= len(
                        successful_rig_objects
                    )

                    for obj in created_rig_objects:
                        if obj.name in bpy.data.objects:
                            bpy.data.objects.remove(obj, do_unlink=True)

                    if (
                        armature_object is not None
                        and armature_object.name in bpy.data.objects
                    ):
                        if armature_counted:
                            self._armature_object_count -= 1
                        bpy.data.objects.remove(
                            armature_object,
                            do_unlink=True,
                        )

                    cached_armature = self._armature_data_cache.get(
                        binding.rig_key
                    )

                    if (
                        cached_armature is not None
                        and cached_armature[0].users == 0
                    ):
                        bpy.data.armatures.remove(cached_armature[0])
                        del self._armature_data_cache[binding.rig_key]

                    if first_rig_error:
                        print(
                            "[Odyssey Toolkit] Armature creation failed; "
                            "using static bind pose for "
                            f"{binding.model_name}: {exc}"
                        )

                    for source_mesh in rig_meshes:
                        create_static_object(source_mesh, representation)

            if representation == "PROCEDURAL_OCEAN":
                self._procedural_ocean_count += 1
            else:
                self._model_placement_count += 1
            return

        bfres_name = _preferred_bfres_name(resource)
        fallback = bpy.data.objects.new(
            (
                f"{placement.identifier} "
                f"{placement.unit_config_name} "
                f"[{placement.stage_layer}]"
            ),
            None,
        )
        fallback.empty_display_type = "CUBE"
        fallback.empty_display_size = 0.5
        group_collection.objects.link(fallback)
        fallback.parent = self._root
        _apply_placement_transform(fallback, placement)
        _set_placement_properties(
            fallback,
            classified,
            "CUBE_FALLBACK",
            _fallback_reason(
                resource,
                bfres_name,
                self._asset_errors,
            ),
        )
        self._fallback_count += 1
        expectation_name = classified.model_expectation.expectation.value
        self._model_expectation_counts[expectation_name] = (
            self._model_expectation_counts.get(expectation_name, 0) + 1
        )
    def _write_root_metadata(self) -> None:
        self._root["smo_display_name"] = self._display_name
        self._root["smo_stage_name"] = self._stage_name
        self._root["smo_scenario"] = self._scenario_number
        created_meshes = tuple(
            mesh
            for meshes in self._asset_cache.values()
            if meshes is not None
            for mesh in meshes
        )
        self._root["smo_custom_normals_enabled"] = (
            self._apply_custom_normals
        )
        self._root["smo_cloth_nov_approximation_enabled"] = (
            self._experimental_cloth_nov
        )
        self._root["smo_armatures_enabled"] = self._import_armatures
        self._root["smo_armature_object_count"] = (
            self._armature_object_count
        )
        self._root["smo_rigged_mesh_object_count"] = (
            self._rigged_mesh_object_count
        )
        self._root["smo_rig_errors"] = json.dumps(
            self._rig_errors,
            sort_keys=True,
        )
        self._root["smo_custom_normal_mesh_count"] = sum(
            mesh.get("smo_custom_normals") == "APPLIED"
            for mesh in created_meshes
        )
        self._root["smo_custom_normal_failure_count"] = sum(
            str(mesh.get("smo_custom_normals", "")).startswith("FAILED:")
            for mesh in created_meshes
        )
        self._root["smo_vertex_colour_set_count"] = sum(
            len(mesh.color_attributes)
            for mesh in created_meshes
        )

        for legacy_key in ("smo_import_preset",):
            if legacy_key in self._root:
                del self._root[legacy_key]

        self._root["smo_available_placement_count"] = (
            len(self._queue) + self._category_filtered_count
        )
        self._root["smo_filtered_placement_count"] = (
            self._category_filtered_count
        )
        self._root["smo_import_categories"] = json.dumps(
            self._selected_import_categories
        )
        self._root["smo_placement_count"] = len(self._queue)
        self._root["smo_static_model_placement_count"] = (
            self._model_placement_count
        )
        self._root["smo_static_mesh_object_count"] = (
            self._mesh_object_count
        )
        self._root["smo_procedural_ocean_count"] = (
            self._procedural_ocean_count
        )
        self._root["smo_cube_fallback_count"] = self._fallback_count
        self._root["smo_model_expectation_counts"] = json.dumps(
            self._model_expectation_counts,
            sort_keys=True,
        )
        self._root["smo_supported_asset_count"] = sum(
            bool(meshes) for meshes in self._asset_cache.values()
        )
        self._root["smo_unsupported_asset_count"] = len(
            self._asset_errors
        )
        texture_image_count = sum(
            image is not None for image in self._image_cache.values()
        )
        self._root["smo_texture_image_count"] = texture_image_count
        self._root["smo_albedo_image_count"] = texture_image_count
        self._root["smo_textured_material_count"] = len(
            self._material_cache
        )
        missing_textures = sorted(
            self._missing_albedo_textures,
            key=str.casefold,
        )
        self._root["smo_missing_textures"] = json.dumps(missing_textures)
        self._root["smo_missing_albedo_textures"] = json.dumps(
            missing_textures
        )
        self._root["smo_fallback_display_textures"] = json.dumps(
            sorted(self._fallback_display_textures, key=str.casefold)
        )
        self._root["smo_texture_errors"] = json.dumps(
            self._texture_errors,
            sort_keys=True,
        )
        persistent_cache = getattr(
            self,
            "_persistent_texture_cache",
            None,
        )

        if persistent_cache is None:
            from .texture_cache import disabled_cache_payload

            cache_payload = disabled_cache_payload(
                self._texture_cache_directory
            )
        else:
            cache_payload = persistent_cache.payload()

        self._root["smo_texture_cache"] = json.dumps(
            cache_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._root["smo_texture_cache_enabled"] = cache_payload["enabled"]
        self._root["smo_texture_cache_hits"] = cache_payload["hits"]
        self._root["smo_texture_cache_misses"] = cache_payload["misses"]
        self._root["smo_texture_cache_writes"] = cache_payload["writes"]
        self._root["smo_texture_cache_errors"] = cache_payload["errors"]
        self._root["smo_unsupported_assets"] = json.dumps(
            {
                f"{path.name}/{name}": error
                for (path, name), error in sorted(
                    self._asset_errors.items(),
                    key=lambda item: (
                        item[0][0].name.casefold(),
                        item[0][1].casefold(),
                    ),
                )
            },
            sort_keys=True,
        )
        self._root["smo_missing_stage_layers"] = json.dumps(
            self._stage_scenario.missing_layers
        )
        self._root["smo_expanded_zone_count"] = len(
            self._stage_scenario.expanded_zones
        )
        self._root["smo_expanded_zones"] = json.dumps(
            self._stage_scenario.expanded_zones
        )
        self._root["smo_stage_lighting_enabled"] = (
            self._import_stage_lighting
        )
        self._root["smo_stage_lighting_preset"] = (
            self._stage_lighting.preset_name
            if self._stage_lighting is not None
            else ""
        )
        self._root["smo_stage_lighting"] = (
            self._stage_lighting.to_json()
            if self._stage_lighting is not None
            else "{}"
        )
        for stale_key in (
            "smo_sun_strength_multiplier",
            "smo_world_strength_multiplier",
        ):
            if stale_key in self._root:
                del self._root[stale_key]
        self._root["smo_local_light_count"] = self._local_light_count
        self._root["smo_stage_lighting_errors"] = json.dumps(
            self._lighting_errors,
            sort_keys=True,
        )
        self._root["smo_performance_timings"] = self._timings.to_json()
        self._root["smo_performance_total_seconds"] = (
            self._timings.seconds("import_total")
        )
        self._root["smo_import_status"] = "FINISHED"

        for key in (
            "smo_import_message",
            "smo_last_reimport_status",
            "smo_last_reimport_message",
        ):
            if key in self._root:
                del self._root[key]

    def _commit_previous_import(self) -> None:
        _remove_generated_objects(self._previous_generated)
        from . import remove_empty_import_collections

        remove_empty_import_collections(
            self._collection,
            self._collection.name,
        )
        self._previous_generated = set()
        self._previous_name_state = ((), ())
        self._replacement_committed = True

    def _finish_import(
        self,
        context: bpy.types.Context,
    ) -> set[str]:
        self._commit_previous_import()

        if self._stage_lighting is not None:
            try:
                from .stage_lighting import apply_global_stage_lighting

                apply_global_stage_lighting(
                    context.scene,
                    self._lighting_collection,
                    self._root,
                    self._stage_lighting,
                )
            except Exception as exc:
                self._lighting_errors["global"] = str(exc)
                print(
                    "[Odyssey Toolkit] Global stage lighting could "
                    f"not be created: {exc}"
                )
        else:
            lighting_keys = (
                "smo_stage_lighting_world",
                "smo_stage_lighting_world_identity",
                "smo_stage_lighting_sun",
            )

            if any(key in self._root for key in lighting_keys):
                from .stage_lighting import restore_previous_stage_world

                restore_previous_stage_world(context.scene, self._root)

            for key in lighting_keys:
                if key in self._root:
                    del self._root[key]

        self._timings.add(
            "import_total",
            time.perf_counter() - self._import_started_at,
        )
        self._write_root_metadata()
        print_performance_summary(self._timings)

        for obj in context.selected_objects:
            obj.select_set(False)

        self._root.select_set(True)
        context.view_layer.objects.active = self._root
        self._stop_modal(context)

        if not self._import_stage_lighting:
            lighting_summary = "lighting disabled"
        elif self._stage_lighting is None:
            lighting_summary = "lighting unavailable"
        else:
            lighting_summary = (
                f"lighting {self._stage_lighting.preset_name} applied"
            )

        warning_count = (
            len(self._texture_errors)
            + len(self._lighting_errors)
            + int(self._root["smo_custom_normal_failure_count"])
        )
        warning_summary = (
            f"; {warning_count} decode/lighting warnings on the import root"
            if warning_count
            else ""
        )
        self.report(
            {"WARNING"} if warning_count else {"INFO"},
            (
                f"Imported {self._model_placement_count} model placements "
                f"({self._category_filtered_count} excluded by category) "
                f"as {self._mesh_object_count} mesh objects, "
                f"{self._procedural_ocean_count} procedural oceans and "
                f"{self._fallback_count} cube fallbacks in "
                f"{self._timings.seconds('import_total'):.2f}s; "
                f"{sum(image is not None for image in self._image_cache.values())} "
                "texture images; "
                f"{self._root['smo_vertex_colour_set_count']} vertex "
                "colour sets; "
                f"{self._root['smo_custom_normal_mesh_count']} meshes "
                "with custom normals; "
                f"{self._local_light_count} local lights; "
                f"{lighting_summary}{warning_summary}."
            ),
        )
        return {"FINISHED"}

    def _discard_partial_import(
        self,
        context: bpy.types.Context,
        status: str,
        message: str,
    ) -> None:
        rollback_preserved = (
            self._had_previous_result
            and not self._replacement_committed
        )

        try:
            if self._replacement_committed:
                _clear_previous_import(self._collection, self._root)

                try:
                    from .stage_lighting import restore_previous_stage_world

                    restore_previous_stage_world(context.scene, self._root)
                except Exception:
                    print(
                        "[Odyssey Toolkit] Could not restore the previous "
                        "World while cleaning up an incomplete import:"
                    )
                    traceback.print_exc()
            else:
                current_generated = _generated_objects(
                    self._collection,
                    self._root,
                )
                _remove_generated_objects(
                    current_generated.difference(self._previous_generated)
                )

                for meshes in getattr(self, "_asset_cache", {}).values():
                    if not meshes:
                        continue

                    for mesh in meshes:
                        if (
                            mesh.users == 0
                            and bpy.data.meshes.get(mesh.name) is mesh
                        ):
                            bpy.data.meshes.remove(mesh)

                from . import remove_empty_import_collections

                remove_empty_import_collections(
                    self._collection,
                    self._collection.name,
                )
                _restore_previous_generated_names(
                    self._previous_name_state,
                )
        finally:
            if rollback_preserved:
                self._root["smo_last_reimport_status"] = status
                self._root["smo_last_reimport_message"] = message
            else:
                self._root["smo_import_status"] = status
                self._root["smo_import_message"] = message

                for key in (
                    "smo_available_placement_count",
                    "smo_filtered_placement_count",
                    "smo_placement_count",
                    "smo_static_model_placement_count",
                    "smo_static_mesh_object_count",
                    "smo_procedural_ocean_count",
                    "smo_cube_fallback_count",
                    "smo_texture_image_count",
                    "smo_albedo_image_count",
                    "smo_local_light_count",
                ):
                    self._root[key] = 0

                self._root["smo_stage_lighting_enabled"] = False
                self._root["smo_stage_lighting_preset"] = ""
                self._root["smo_stage_lighting"] = "{}"
                self._root["smo_missing_textures"] = "[]"
                self._root["smo_missing_albedo_textures"] = "[]"
                self._root["smo_fallback_display_textures"] = "[]"
                self._root["smo_texture_errors"] = "{}"
                self._root["smo_unsupported_assets"] = "{}"
                self._root["smo_model_expectation_counts"] = "{}"
                self._root["smo_import_categories"] = "[]"

                if not self._timings.seconds("import_total"):
                    self._timings.add(
                        "import_total",
                        time.perf_counter() - self._import_started_at,
                    )

                self._root["smo_performance_timings"] = (
                    self._timings.to_json()
                )
                self._root["smo_performance_total_seconds"] = (
                    self._timings.seconds("import_total")
                )

            self._stop_modal(context)

    def _cancel_import(
        self,
        context: bpy.types.Context,
        message: str,
    ) -> set[str]:
        self._discard_partial_import(
            context,
            "CANCELLED",
            message,
        )

        self.report({"WARNING"}, message)
        return {"CANCELLED"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _STATIC_IMPORT_CANCEL_REQUESTED
        global _STATIC_IMPORT_PROGRESS
        global _STATIC_IMPORT_RUNNING

        _STATIC_IMPORT_CANCEL_REQUESTED = False
        _STATIC_IMPORT_PROGRESS = (0, 0, "Preparing stage data")
        _STATIC_IMPORT_RUNNING = True
        self._previous_import_cleared = False
        self._previous_generated: set[bpy.types.Object] = set()
        self._previous_name_state = ((), ())
        self._had_previous_result = False
        self._replacement_committed = False
        self._timings = PerformanceTimings()
        self._import_started_at = time.perf_counter()
        preparation_started = self._import_started_at
        timing_token = set_active_timings(self._timings)

        try:
            from . import _WORLD_BY_STAGE, resolve_romfs_root
            from .object_data import get_object_data_index
            from .placement_classifier import classify_stage_scenario
            from .stage_data import read_stage_scenario
            from .stage_lighting import read_stage_lighting

            settings = context.scene.smo_settings
            from . import get_addon_preferences

            preferences = get_addon_preferences(context)
            self._apply_custom_normals = bool(
                getattr(preferences, "apply_custom_normals", False)
            )
            self._import_armatures = bool(
                getattr(preferences, "import_armatures", False)
            )
            self._experimental_cloth_nov = bool(
                getattr(preferences, "experimental_cloth_nov", False)
            )
            from . import texture_cache_directory
            from .texture_cache import PersistentTextureCache

            self._texture_cache_directory = texture_cache_directory(
                preferences
            )
            self._persistent_texture_cache = (
                PersistentTextureCache(self._texture_cache_directory)
                if bool(getattr(preferences, "use_texture_cache", False))
                else None
            )
            self._import_stage_lighting = bool(
                getattr(settings, "import_stage_lighting", True)
            )


            world = _WORLD_BY_STAGE[settings.kingdom]
            self._scenario_number = int(settings.scenario)
            self._display_name = str(world["_display_name"])
            self._stage_name = str(world["Name"])
            self._group_scope = (
                f"{self._display_name} S{self._scenario_number}"
            )
            romfs_root = resolve_romfs_root(settings.romfs_path)
            self._object_data_dir = romfs_root / "ObjectData"
            self._lighting_errors: dict[str, str] = {}
            self._stage_scenario = read_stage_scenario(
                romfs_root,
                self._stage_name,
                self._scenario_number,
            )
            self._stage_lighting = None

            if self._import_stage_lighting:
                try:
                    self._stage_lighting = read_stage_lighting(
                        romfs_root,
                        self._stage_name,
                        self._scenario_number,
                    )
                except Exception as exc:
                    self._lighting_errors["preset"] = str(exc)
                    print(
                        "[Odyssey Toolkit] Stage lighting preset "
                        f"could not be read: {exc}"
                    )

            classified_placements = classify_stage_scenario(
                self._stage_scenario,
                get_object_data_index(romfs_root),
            )
            self._zone_order = {
                zone_path: index
                for index, zone_path in enumerate(
                    self._stage_scenario.expanded_zones
                )
            }
            self._selected_import_categories = [
                group_name
                for group_name, _, property_name in IMPORT_CATEGORY_FILTERS
                if bool(getattr(settings, property_name, True))
            ]
            self._queue = [
                classified
                for classified in classified_placements
                if import_category_enabled(settings, classified)
            ]
            self._category_filtered_count = (
                len(classified_placements) - len(self._queue)
            )
            self._queue.sort(key=self._queue_sort_key)
            collection_name = (
                f"SMO - {self._display_name} - "
                f"Scenario {self._scenario_number} - Static Models"
            )
            from . import get_or_create_scene_collection

            self._collection = get_or_create_scene_collection(
                context.scene,
                collection_name,
            )

            root_name = (
                f"{self._stage_name}_Scenario"
                f"{self._scenario_number}_StaticModels"
            )
            self._root = self._collection.objects.get(root_name)

            if self._root is None:
                self._root = bpy.data.objects.new(root_name, None)
                self._root.empty_display_type = "PLAIN_AXES"
                self._root.empty_display_size = 5.0
                self._collection.objects.link(self._root)

            self._previous_generated = _generated_objects(
                self._collection,
                self._root,
            )
            self._had_previous_result = bool(self._previous_generated) or (
                self._root.get("smo_import_status") == "FINISHED"
            )
            self._previous_name_state = _rename_previous_generated(
                self._previous_generated,
            )
            self._previous_import_cleared = True
            self._lighting_collection = self._collection

            if self._import_stage_lighting:
                from . import get_or_create_named_import_collection

                self._lighting_collection = (
                    get_or_create_named_import_collection(
                        self._collection,
                        "Lighting",
                        "LIGHTING",
                    )
                )
                self._lighting_collection.color_tag = "COLOR_05"

            self._archive_cache: dict[Path, Any] = {}
            self._texture_archive_cache: dict[
                tuple[Path, str],
                Any,
            ] = {}
            self._decoded_texture_cache: dict[
                tuple[tuple[Path, str], str],
                Any | None,
            ] = {}
            self._image_cache: dict[ImageCacheKey, bpy.types.Image | None] = {}
            self._material_cache: dict[Any, bpy.types.Material] = {}
            self._missing_albedo_textures: set[str] = set()
            self._fallback_display_textures: set[str] = set()
            self._texture_errors: dict[str, str] = {}
            self._asset_cache: dict[
                AssetContextKey,
                tuple[bpy.types.Mesh, ...] | None,
            ] = {}
            self._asset_errors: dict[SourceKey, str] = {}
            self._mesh_rig_bindings: dict[str, MeshRigBinding] = {}
            self._armature_data_cache: dict[
                str,
                tuple[bpy.types.Armature, tuple[str, ...]],
            ] = {}
            self._rig_errors: dict[str, str] = {}
            self._queue_index = 0
            self._model_placement_count = 0
            self._mesh_object_count = 0
            self._armature_object_count = 0
            self._rigged_mesh_object_count = 0
            self._procedural_ocean_count = 0
            self._fallback_count = 0
            self._model_expectation_counts: dict[str, int] = {}
            self._local_light_count = 0
            context.window_manager.progress_begin(
                0,
                max(1, len(self._queue)),
            )
            self._timer = context.window_manager.event_timer_add(
                0.02,
                window=context.window,
            )
            context.window_manager.modal_handler_add(self)
            workspace = getattr(context, "workspace", None)

            if workspace is not None:
                workspace.status_text_set(
                    f"SMO preparing {len(self._queue)} placements "
                    "- Esc to cancel"
                )

            self._redraw_viewports(context)
            self._timings.add(
                "preparation_total",
                time.perf_counter() - preparation_started,
            )
            return {"RUNNING_MODAL"}

        except Exception as exc:
            if "preparation_total" not in self._timings.totals_seconds:
                self._timings.add(
                    "preparation_total",
                    time.perf_counter() - preparation_started,
                )

            if self._previous_import_cleared:
                self._discard_partial_import(
                    context,
                    "FAILED",
                    str(exc),
                )
            else:
                _STATIC_IMPORT_CANCEL_REQUESTED = False
                _STATIC_IMPORT_PROGRESS = (0, 0, "")
                _STATIC_IMPORT_RUNNING = False

            print("[Odyssey Toolkit] Failed to prepare static import:")
            traceback.print_exc()
            self.report({"ERROR"}, f"Could not import static models: {exc}")
            return {"CANCELLED"}
        finally:
            reset_active_timings(timing_token)

    def modal(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        if event.type == "ESC" or _STATIC_IMPORT_CANCEL_REQUESTED:
            return self._cancel_import(
                context,
                (
                    f"Cancelled after {self._queue_index} of "
                    f"{len(self._queue)} placements."
                ),
            )

        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        timing_token = set_active_timings(self._timings)

        try:
            deadline = time.perf_counter() + 0.05

            while self._queue_index < len(self._queue):
                classified = self._queue[self._queue_index]
                self._set_status(context, classified)
                self._process_placement(classified)
                self._queue_index += 1
                context.window_manager.progress_update(self._queue_index)

                if time.perf_counter() >= deadline:
                    break

            self._redraw_viewports(context)

            if self._queue_index >= len(self._queue):
                return self._finish_import(context)

            return {"RUNNING_MODAL"}

        except Exception as exc:
            print("[Odyssey Toolkit] Progressive import failed:")
            traceback.print_exc()

            self._discard_partial_import(
                context,
                "FAILED",
                str(exc),
            )

            self.report({"ERROR"}, f"Could not import static models: {exc}")
            return {"CANCELLED"}
        finally:
            reset_active_timings(timing_token)

    def cancel(self, context: bpy.types.Context) -> None:
        if not static_import_is_running():
            return

        self._discard_partial_import(
            context,
            "CANCELLED",
            "Import cancelled by Blender.",
        )

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import math
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import StaticMesh, read_static_bfres
from smo_kingdom_importer.world_list import extract_file, read_szs
from smo_kingdom_importer.static_model_import import (
    _create_albedo_material,
    _resolve_material_shader,
    _shader_texture_role_overrides,
    _atmosphere_shader_kind,
    _texture_role,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def asset_meshes(romfs_root: Path, asset_name: str) -> tuple[StaticMesh, ...]:
    archive = read_szs(romfs_root / "ObjectData" / f"{asset_name}.szs")
    bfres_name = next(
        entry.name
        for entry in archive.get_files()
        if Path(entry.name).suffix.casefold() == ".bfres"
    )
    models = read_static_bfres(bytes(extract_file(archive, bfres_name)))
    return tuple(mesh for model in models for mesh in model.meshes)


def material(
    romfs_root: Path,
    asset_name: str,
    material_name: str,
) -> StaticMesh:
    return next(
        mesh
        for mesh in asset_meshes(romfs_root, asset_name)
        if mesh.material_name == material_name
    )


def describe(mesh: StaticMesh) -> None:
    shader = mesh.material_shader
    check(shader is not None, f"{mesh.material_name} has no FMAT shader data")
    options = dict(shader.shader_options)
    check("o_roughness" in options, "Roughness output option was not parsed")
    check("o_metalness" in options, "Metalness output option was not parsed")
    check(shader.render_infos, "Render infos were not parsed")
    check(shader.samplers, "Sampler states were not parsed")
    check(shader.texture_bindings, "Original texture-slot bindings were not retained")
    check(
        any(parameter.type_id == 12 for parameter in shader.parameters),
        "Scalar Float shader parameters were not parsed",
    )
    check(
        any(parameter.type_id in {30, 31} for parameter in shader.parameters),
        "Texture-transform shader parameters were not parsed",
    )
    check(
        all(info.type_id in {0, 1, 2} for info in shader.render_infos),
        "A render-info type was decoded incorrectly",
    )


def blender_material(
    mesh: StaticMesh,
    identity: str,
    *,
    apply_cloth_nov_approximation: bool = False,
):
    resolved = _resolve_material_shader(mesh)
    check(resolved is not None, f"{mesh.material_name} did not resolve")
    role_overrides = _shader_texture_role_overrides(resolved)
    images = {
        texture_name: bpy.data.images.new(
            f"{identity}-{texture_name}", width=1, height=1, alpha=True
        )
        for texture_name in mesh.texture_names
    }
    if not images:
        images["__shader_placeholder__"] = bpy.data.images.new(
            f"{identity}-placeholder", width=1, height=1, alpha=True
        )
    bindings = tuple(
        (
            role_overrides.get(
                texture_name,
                _texture_role(texture_name, sampler_name),
            ),
            texture_name,
            sampler_name,
            images[texture_name],
        )
        for texture_name, sampler_name in zip(
            mesh.texture_names,
            mesh.texture_sampler_names,
        )
    )
    base_name = (
        mesh.albedo_texture_name
        or (mesh.texture_names[0] if mesh.texture_names else "__shader_placeholder__")
    )
    material_value = _create_albedo_material(
        "ShaderRegression",
        mesh.material_name,
        base_name,
        images[base_name],
        True,
        identity,
        texture_bindings=bindings,
        shader_material=resolved,
        apply_cloth_nov_approximation=apply_cloth_nov_approximation,
    )
    shader_node = next(
        node
        for node in material_value.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    return material_value, shader_node


def atmospheric_blender_material(
    mesh: StaticMesh,
    asset_name: str,
    atmosphere_kind: str,
    identity: str,
):
    resolved = _resolve_material_shader(mesh)
    shader = mesh.material_shader
    images = {
        texture_name: bpy.data.images.new(
            f"{identity}-{texture_name}", width=1, height=1, alpha=True
        )
        for texture_name in mesh.texture_names
    }
    bindings = tuple(
        (
            _texture_role(texture_name, sampler_name),
            texture_name,
            sampler_name,
            images[texture_name],
        )
        for texture_name, sampler_name in zip(
            mesh.texture_names,
            mesh.texture_sampler_names,
        )
    )
    base_name = mesh.albedo_texture_name or mesh.texture_names[0]
    return _create_albedo_material(
        asset_name,
        mesh.material_name,
        base_name,
        images[base_name],
        True,
        identity,
        texture_bindings=bindings,
        ignore_texture_alpha=True,
        atmosphere_kind=atmosphere_kind,
        shader_material=resolved,
        shader_parameters=(
            tuple(shader.parameters) if shader is not None else ()
        ),
        shader_options=(
            tuple(shader.shader_options) if shader is not None else ()
        ),
        shader_render_infos=(
            tuple(shader.render_infos) if shader is not None else ()
        ),
    )


def check_atmospheric_translation(
    sky_mesh: StaticMesh,
    cloud_mesh: StaticMesh,
) -> None:
    for mesh, asset_name, kind in (
        (sky_mesh, "SkyForestDayLight", "SKY"),
        (cloud_mesh, "CloudForestDayLight", "CLOUD"),
    ):
        check(
            _atmosphere_shader_kind(mesh) == kind,
            f"{asset_name} was not classified as {kind}",
        )
        material_value = atmospheric_blender_material(
            mesh,
            asset_name,
            kind,
            f"shader-atmosphere-{kind.casefold()}",
        )
        output = next(
            node
            for node in material_value.node_tree.nodes
            if node.bl_idname == "ShaderNodeOutputMaterial"
        )
        check(output.inputs["Surface"].is_linked,
              f"{kind} material surface is unlinked")
        surface_node = output.inputs["Surface"].links[0].from_node
        check(
            surface_node.bl_idname != "ShaderNodeBsdfPrincipled",
            f"{kind} atmosphere still terminates in Principled BSDF",
        )
        check(
            not any(
                link.from_socket.name == "Alpha"
                and link.to_node.name not in {
                    "SMO Sky HDR Multiplier",
                    "SMO Cloud Density Remap",
                }
                for link in material_value.node_tree.links
                if link.from_node.bl_idname == "ShaderNodeTexImage"
            ),
            f"{kind} atmosphere still uses raw texture alpha",
        )
        check(
            not any(
                node.bl_idname == "ShaderNodeBsdfPrincipled"
                for node in material_value.node_tree.nodes
            ),
            f"{kind} atmosphere retained a Principled BSDF",
        )
        metadata = json.loads(material_value["smo_atmosphere_shader"])
        check(metadata["kind"] == kind,
              f"{kind} atmosphere metadata is missing")

        if kind == "SKY":
            check(
                surface_node.name == "SMO Sky Emission",
                "Sky does not terminate in its emission shader",
            )
            hdr_decode = material_value.node_tree.nodes.get(
                "SMO Sky HDR Decode"
            )
            hdr_multiplier = material_value.node_tree.nodes.get(
                "SMO Sky HDR Multiplier"
            )
            check(
                hdr_decode is not None and hdr_multiplier is not None,
                "Sky RGBM/HDR decoding nodes are missing",
            )
            check(
                hdr_multiplier.inputs[0].is_linked
                and hdr_multiplier.inputs[0].links[0].from_socket.name
                == "Alpha",
                "Sky alpha is not retained as the HDR multiplier",
            )
            check(
                not metadata["alpha_as_opacity"],
                "Sky HDR alpha is still marked as opacity",
            )
        else:
            density = material_value.node_tree.nodes.get(
                "SMO Cloud Density Remap"
            )
            lighting = material_value.node_tree.nodes.get(
                "SMO Cloud Lighting Mix"
            )
            diffuse = material_value.node_tree.nodes.get(
                "SMO Cloud Diffuse"
            )
            check(
                surface_node.name == "SMO Cloud Density Mix",
                "Cloud does not terminate in its density surface mix",
            )
            check(
                density is not None
                and density.inputs[0].is_linked
                and density.inputs[0].links[0].from_socket.name == "Alpha"
                and math.isclose(
                    density.inputs[1].default_value,
                    8.0,
                    abs_tol=1e-6,
                ),
                "Cloud alpha was not remapped from density to coverage",
            )
            check(
                lighting is not None
                and math.isclose(
                    lighting.inputs[0].default_value,
                    0.7,
                    abs_tol=1e-6,
                ),
                "Cloud wrap-light coefficient was not retained",
            )
            check(
                diffuse is not None and diffuse.inputs["Normal"].is_linked,
                "Cloud normal map is not connected to cloud scattering",
            )
            check(
                material_value.node_tree.nodes.get(
                    "SMO Cloud Vertex Colour Multiply"
                )
                is not None,
                "Cloud vertex-colour modulation is missing",
            )


def check_blender_translation(meshes: tuple[StaticMesh, ...]) -> None:
    car_material, car_shader = blender_material(meshes[0], "shader-car")
    check(
        not any(
            node.bl_idname == "ShaderNodeSeparateColor"
            for node in car_material.node_tree.nodes
        ),
        "Car dedicated scalar maps created unnecessary component nodes",
    )
    check(car_shader.inputs["Roughness"].is_linked, "Car roughness is unlinked")
    check(car_shader.inputs["Metallic"].is_linked, "Car metallic is unlinked")
    roughness_texture = car_shader.inputs["Roughness"].links[0].from_node
    metallic_texture = car_shader.inputs["Metallic"].links[0].from_node
    check(
        roughness_texture.get("smo_texture_name") == "CarBody_rgh",
        "Car roughness did not route through CarBody_rgh",
    )
    check(
        metallic_texture.get("smo_texture_name") == "CarBody_mtl",
        "Car metallic did not route through CarBody_mtl",
    )
    check(
        car_material.node_tree.nodes.get("SMO Base Color Multiplier") is not None,
        "Car base-colour multiplier was not created",
    )
    normal_maps = [
        node
        for node in car_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeNormalMap"
    ]
    check(
        len(normal_maps) == 1,
        "Car material created unexpected Normal Map nodes: "
        + f"{[(node.name, node.inputs['Color'].links[0].from_node.name) for node in normal_maps]}",
    )
    normal_source = normal_maps[0].inputs["Color"].links[0].from_node
    check(
        normal_source.get("smo_texture_name") == "CarBody_nrm",
        "The consolidated Normal Map uses the wrong texture",
    )

    city_material, city_shader = blender_material(meshes[1], "shader-city")
    check(not city_shader.inputs["Roughness"].is_linked, "City constant roughness linked")
    check(not city_shader.inputs["Metallic"].is_linked, "City constant metallic linked")
    city_emission = (
        city_shader.inputs.get("Emission Color")
        or city_shader.inputs.get("Emission")
    )
    check(
        city_emission is not None and not city_emission.is_linked,
        "Disabled City emission was translated",
    )
    check(
        math.isclose(city_shader.inputs["Roughness"].default_value, 0.3, abs_tol=1e-6),
        "City roughness constant was not applied",
    )
    check(
        math.isclose(city_shader.inputs["Metallic"].default_value, 0.7, abs_tol=1e-6),
        "City metallic constant was not applied",
    )
    check(
        city_material["smo_shader_translation"] == "alRenderMaterial",
        "Shader translation metadata is missing",
    )

    gold_material, _ = blender_material(meshes[2], "shader-gold")
    check(
        gold_material.node_tree.nodes.get("SMO Base Color Multiplier") is not None,
        "Gold material colour multiplier was not created",
    )

    _, bubble_shader = blender_material(meshes[3], "shader-bubble")
    check(
        math.isclose(bubble_shader.inputs["Roughness"].default_value, 0.0),
        "LifeBubble literal-zero roughness was not applied",
    )
    check(
        math.isclose(bubble_shader.inputs["Metallic"].default_value, 0.3, abs_tol=1e-6),
        "LifeBubble metallic constant was not applied",
    )
    check(
        math.isclose(bubble_shader.inputs["Alpha"].default_value, 0.78, abs_tol=1e-6),
        "LifeBubble alpha constant was not applied",
    )

def check_shader_translation(meshes: tuple[StaticMesh, ...]) -> None:
    brake_material, brake_shader = blender_material(
        meshes[4], "shader-brake"
    )
    brake_emission = (
        brake_shader.inputs.get("Emission Color")
        or brake_shader.inputs.get("Emission")
    )
    check(brake_emission is not None, "Principled emission input is missing")
    check(brake_emission.is_linked, "Brake-light emission blend is unlinked")
    check(
        brake_material.node_tree.nodes.get(
            "SMO Blend 0 Multiply Add"
        ) is not None,
        "Blend equation 1 was not translated as multiply-add",
    )
    brake_hdr = brake_material.node_tree.nodes.get("SMO Constant 60 Color")
    check(brake_hdr is not None, "Brake-light const_color0 node is missing")
    check(
        math.isclose(brake_hdr.outputs["Color"].default_value[0], 10.0),
        "Brake-light HDR const_color0 was clamped",
    )
    check(
        not brake_shader.inputs["Alpha"].is_linked
        and math.isclose(brake_shader.inputs["Alpha"].default_value, 1.0),
        "Emission const_color alpha leaked into Principled alpha",
    )
    brake_routes = json.loads(brake_material["smo_shader_routes"])
    check(
        brake_routes["emission"]["equation"] == 1,
        "Brake-light blend metadata is incomplete",
    )

    sky_material, sky_shader = blender_material(meshes[5], "shader-sky-eye")
    sky_emission = (
        sky_shader.inputs.get("Emission Color")
        or sky_shader.inputs.get("Emission")
    )
    check(sky_emission is not None and sky_emission.is_linked,
          "Direct const_color emission is unlinked")
    sky_hdr = sky_material.node_tree.nodes.get("SMO Constant 60 Color")
    check(sky_hdr is not None, "Direct const_color0 node is missing")
    check(
        math.isclose(sky_hdr.outputs["Color"].default_value[0], 8.9, abs_tol=1e-5),
        "Direct HDR const_color0 was clamped or altered",
    )

    metal_material, metal_shader = blender_material(
        meshes[6], "shader-metal"
    )
    metal_emission = (
        metal_shader.inputs.get("Emission Color")
        or metal_shader.inputs.get("Emission")
    )
    check(metal_shader.inputs["Base Color"].is_linked,
          "Nested equation-2 base colour is unlinked")
    check(metal_emission is not None and metal_emission.is_linked,
          "Equation-2 emission is unlinked")
    for blend_index in (0, 1):
        check(
            metal_material.node_tree.nodes.get(
                f"SMO Blend {blend_index} Multiply"
            ) is not None,
            f"Blend equation 2 was not translated for blend{blend_index}",
        )

    figure_shader = meshes[7].material_shader
    check(figure_shader is not None, "Figure Walker shader data is missing")
    figure_options = tuple(
        (name, "80" if name == "o_emission" else value)
        for name, value in figure_shader.shader_options
    )
    figure_mesh = replace(
        meshes[7],
        material_shader=replace(
            figure_shader,
            shader_options=figure_options,
        ),
    )
    figure_material, figure_principled = blender_material(
        figure_mesh, "shader-lerp"
    )
    figure_emission = (
        figure_principled.inputs.get("Emission Color")
        or figure_principled.inputs.get("Emission")
    )
    check(figure_emission is not None and figure_emission.is_linked,
          "Equation-0 interpolation is unlinked")
    check(
        figure_material.node_tree.nodes.get(
            "SMO Blend 0 Lerp"
        ) is not None,
        "Blend equation 0 was not translated as mix(dst, src, coefficient)",
    )

    unsupported_options = tuple(
        (name, "7" if name == "blend0_eq" else value)
        for name, value in figure_options
    )
    unsupported_mesh = replace(
        meshes[7],
        material_shader=replace(
            figure_shader,
            shader_options=unsupported_options,
        ),
    )
    unsupported = _resolve_material_shader(unsupported_mesh)
    check(unsupported is not None, "Unsupported shader did not resolve metadata")
    check(unsupported.emission is None,
          "Unsupported blend equation was translated speculatively")
    check(
        dict(unsupported.unhandled_outputs).get("o_emission") == "80",
        "Unsupported blend output was not retained as fallback metadata",
    )

def check_cloth_nov_translation(
    peach_dress: StaticMesh,
    mario_body: StaticMesh,
    mario_hair: StaticMesh,
) -> None:
    peach_resolved = _resolve_material_shader(peach_dress)
    check(peach_resolved is not None, "Peach DressMT did not resolve")
    cloth_nov = peach_resolved.cloth_nov
    check(cloth_nov is not None, "Enabled Peach cloth NoV was not resolved")
    check(cloth_nov.reverse, "Peach cloth NoV reverse flag was lost")
    check(
        math.isclose(cloth_nov.peak_position, 0.5, abs_tol=1e-6),
        "Peach cloth NoV peak position was not retained",
    )
    check(
        math.isclose(cloth_nov.peak_power, 1.58, abs_tol=1e-5),
        "Peach cloth NoV peak power was not retained",
    )
    check(
        math.isclose(cloth_nov.tone_power, 2.4, abs_tol=1e-5),
        "Peach cloth NoV tone power was not retained",
    )
    check(
        math.isclose(cloth_nov.emission_scale, 1.7, abs_tol=1e-5),
        "Peach cloth NoV emission scale was not retained",
    )

    default_peach_material, _ = blender_material(
        peach_dress, "shader-peach-cloth-nov-default"
    )
    check(
        default_peach_material.node_tree.nodes.get("SMO Cloth Fresnel")
        is None,
        "Cloth NoV approximation was enabled by default",
    )
    check(
        json.loads(default_peach_material["smo_cloth_nov"]) is not None,
        "Disabled cloth approximation discarded parsed metadata",
    )
    check(
        not default_peach_material[
            "smo_cloth_nov_approximation_enabled"
        ],
        "Disabled cloth approximation recorded an enabled state",
    )

    peach_material, peach_shader = blender_material(
        peach_dress,
        "shader-peach-cloth-nov",
        apply_cloth_nov_approximation=True,
    )
    base_source = peach_shader.inputs["Base Color"].links[0].from_node
    emission_input = (
        peach_shader.inputs.get("Emission Color")
        or peach_shader.inputs.get("Emission")
    )
    check(
        base_source.name == "SMO Cloth NoV Base Mix",
        "Peach cloth colour was not mixed into Principled Base Color",
    )
    check(
        emission_input is not None
        and emission_input.is_linked
        and emission_input.links[0].from_node.name
        == "SMO Cloth NoV Emission Add",
        "Peach cloth NoV emission was not added to Principled Emission",
    )
    for node_name in (
        "SMO Cloth Fresnel",
        "SMO Cloth NoV Slope",
        "SMO Cloth NoV Tone",
        "SMO Cloth NoV Peak Position",
        "SMO Cloth NoV Peak Power",
        "SMO Cloth NoV Peak",
        "SMO Cloth NoV Factor",
        "SMO Cloth NoV Mask",
    ):
        check(
            peach_material.node_tree.nodes.get(node_name) is not None,
            f"Peach cloth NoV node is missing: {node_name}",
        )
    check(
        peach_material.node_tree.nodes.get("SMO Cloth NoV") is None,
        "Reversed Peach cloth NoV was inverted away from Fresnel polarity",
    )
    emission_add = peach_material.node_tree.nodes.get(
        "SMO Cloth NoV Emission Add"
    )
    check(emission_add is not None, "Peach cloth emission add node is missing")
    check(
        all(
            math.isclose(component, 0.0, abs_tol=1e-7)
            for component in emission_add.inputs[1].default_value[:3]
        ),
        "Dormant Principled white emission leaked into cloth NoV",
    )
    metadata = json.loads(peach_material["smo_cloth_nov"])
    check(
        peach_material["smo_cloth_nov_approximation_enabled"],
        "Enabled cloth approximation did not record its material state",
    )
    check(
        metadata["approximation"]
        == "masked NoV tone with angular peak modulation"
        and metadata["reverse"]
        and math.isclose(metadata["emission_scale"], 1.7, abs_tol=1e-5),
        "Peach cloth NoV material metadata is incomplete",
    )

    mario_resolved = _resolve_material_shader(mario_body)
    check(mario_resolved is not None, "Mario BodyMT did not resolve")
    check(
        mario_resolved.cloth_nov is None,
        "Disabled Mario cloth NoV defaults created an effect",
    )
    mario_material, mario_principled = blender_material(
        mario_body, "shader-mario-disabled-cloth-nov"
    )
    check(
        mario_material.node_tree.nodes.get("SMO Cloth Fresnel") is None,
        "Disabled Mario cloth NoV created Blender nodes",
    )
    check(
        json.loads(mario_material["smo_cloth_nov"]) is None,
        "Disabled Mario cloth NoV metadata is not null",
    )
    sss_input = mario_principled.inputs.get("Subsurface Weight")
    check(
        getattr(mario_resolved, "sss", None) is not None,
        "Enabled Mario SSS route was not resolved",
    )
    check(
        sss_input is not None and sss_input.is_linked,
        "Enabled Mario SSS route was not connected to Principled",
    )

    mario_shader = mario_body.material_shader
    check(mario_shader is not None, "Mario BodyMT shader data is missing")
    ao_options = tuple(
        (
            name,
            "1" if name == "enable_ao"
            else "52" if name == "o_ao"
            else value,
        )
        for name, value in mario_shader.shader_options
    )
    ao_mesh = replace(
        mario_body,
        material_shader=replace(mario_shader, shader_options=ao_options),
    )
    ao_resolved = _resolve_material_shader(ao_mesh)
    check(
        ao_resolved is not None
        and getattr(ao_resolved, "ao", None) is not None,
        "Enabled AO route was not resolved",
    )
    ao_material, _ = blender_material(ao_mesh, "shader-mario-ao")
    check(
        ao_material.node_tree.nodes.get("SMO Ambient Occlusion Multiply")
        is not None,
        "Enabled AO did not modulate the Blender base colour",
    )


    hair_resolved = _resolve_material_shader(mario_hair)
    check(hair_resolved is not None, "MarioFace HairMT did not resolve")
    hair_cloth = hair_resolved.cloth_nov
    check(hair_cloth is not None, "MarioFace HairMT cloth NoV was lost")
    hair_mask = hair_cloth.mask
    check(hair_mask is not None, "MarioFace HairMT mask code 70 was dropped")
    check(
        hair_mask.kind == "TEXTURE"
        and hair_mask.texture_name == "MarioHairFace_rgh"
        and hair_mask.channel == "Red"
        and hair_mask.invert,
        "MarioFace HairMT did not resolve component 70 as inverted red",
    )

    hair_material, _ = blender_material(
        mario_hair, "shader-mario-hair-cloth-nov"
    )
    check(
        hair_material.node_tree.nodes.get(
            "SMO Cloth NoV Mask Component Invert"
        )
        is not None,
        "MarioFace HairMT inverted mask node is missing",
    )
    factor = hair_material.node_tree.nodes.get("SMO Cloth NoV Factor")
    check(
        factor is not None and factor.operation == "MULTIPLY",
        "Cloth NoV peak is still added as an independent lobe",
    )
    check(
        hair_material.node_tree.nodes.get(
            "SMO Cloth NoV Peak Modulation"
        )
        is not None,
        "Cloth NoV angular peak does not modulate the main tone",
    )
    hair_metadata = json.loads(hair_material["smo_cloth_nov"])
    check(
        hair_metadata["mask"]["texture"] == "MarioHairFace_rgh"
        and hair_metadata["mask"]["channel"] == "Red"
        and hair_metadata["mask"]["invert"],
        "MarioFace HairMT mask metadata is incomplete",
    )

    hair_shader = mario_hair.material_shader
    check(hair_shader is not None, "MarioFace HairMT shader data is missing")
    green_options = tuple(
        (name, "80" if name == "cloth_mask_component" else value)
        for name, value in hair_shader.shader_options
    )
    green_mesh = replace(
        mario_hair,
        material_shader=replace(hair_shader, shader_options=green_options),
    )
    green_material, _ = blender_material(
        green_mesh, "shader-mario-hair-green-mask"
    )
    green_invert = green_material.node_tree.nodes.get(
        "SMO Cloth NoV Mask Component Invert"
    )
    check(green_invert is not None, "Synthetic green mask invert is missing")
    green_link = green_invert.inputs[1].links[0]
    check(
        green_link.from_node.bl_idname == "ShaderNodeSeparateColor"
        and green_link.from_socket.name == "Green",
        "Green component selector was converted to luminance instead of Green",
    )



def check_transparency_translation(car_glass: StaticMesh) -> None:
    resolved = _resolve_material_shader(car_glass)
    check(resolved is not None, "Car GlassMT did not resolve")
    check(
        getattr(resolved, "transmission", None) is not None,
        "Enabled Car glass refraction rate was not resolved",
    )
    inactive = dict(getattr(resolved, "inactive_outputs", ()))
    check(
        {"o_ao", "o_sss", "o_cloth_map"}.issubset(inactive),
        "Disabled shader outputs were not separated from active gaps",
    )

    glass_material, glass_shader = blender_material(
        car_glass, "shader-car-glass"
    )
    transmission = glass_shader.inputs.get("Transmission Weight")
    check(
        transmission is not None
        and (transmission.is_linked or transmission.default_value > 0.99),
        "Car glass transmission was not connected to Principled",
    )
    check(
        glass_material.surface_render_method == "DITHERED",
        "Transparent FMAT render state did not enable Blender transparency",
    )
    check(
        glass_material.use_backface_culling,
        "FMAT display_face=front did not enable back-face culling",
    )

def run(romfs_root: Path) -> None:
    cases = (
        ("Car", "BodyMT"),
        ("CityWorldHomeBuilding000", "MetalWallMain00"),
        ("PeachWorldVase", "GoldDeco00"),
        ("AirBubble", "LifeBubble_Mat"),
        ("Car", "BrakeLightMT"),
        ("SkyWorldHomeVisibleSwitchParts001", "EyeEmm01"),
        ("Mario64MetalHandL", "BodyMT"),
        ("FigureWalkerStartPoint", "lambert1"),
        ("Peach", "DressMT"),
        ("Mario", "BodyMT"),
        ("MarioFace", "HairMT"),
        ("Car", "GlassMT"),
        ("SkyForestDayLight", "SkyMatUnder"),
        ("CloudForestDayLight", "G_CloudLayerMiddleMat05"),
    )
    meshes = tuple(material(romfs_root, *case) for case in cases)

    for mesh in meshes[:4]:
        describe(mesh)

    check_blender_translation(meshes)
    check_shader_translation(meshes)
    check_cloth_nov_translation(meshes[8], meshes[9], meshes[10])
    check_transparency_translation(meshes[11])
    check_atmospheric_translation(meshes[12], meshes[13])

    car = meshes[0].material_shader
    check(car is not None, "Car BodyMT shader data is missing")
    car_options = dict(car.shader_options)
    check(car.shader_archive_name == "alRenderMaterial", "Wrong shader archive")
    check(car_options["o_roughness"] == "50", "Car roughness route changed")
    check(car_options["o_metalness"] == "51", "Car metalness route changed")
    print("SHADER_MATERIAL_REGRESSION: PASS")


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python shader_material_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())

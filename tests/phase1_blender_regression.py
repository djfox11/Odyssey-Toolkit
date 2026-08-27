from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon
import smo_kingdom_importer.static_model_import as static_model_import
from smo_kingdom_importer.bfres_mesh import (
    _ordered_display_textures,
    _select_albedo_texture,
)
from smo_kingdom_importer.bntx_texture import _decode_block
from smo_kingdom_importer.static_model_import import (
    _apply_custom_normals,
    _asset_context_key,
    _create_albedo_image,
    _create_albedo_material,
    _create_mesh_data,
    _create_texture_image,
    _create_ocean_wave_mesh,
    _generated_objects,
    _identity_digest,
    _remove_generated_objects,
    _reconstruct_normal_blue,
    _rename_previous_generated,
    _rgba8_to_blender_pixels,
    _texture_role,
    SMO_OT_import_static_models,
)
from smo_kingdom_importer.placement_classifier import PlacementCategory


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    registered = False

    try:
        addon.register()
        registered = True
        normal_preference = addon.SMOAddonPreferences.bl_rna.properties[
            "apply_custom_normals"
        ]
        check(
            not normal_preference.default,
            "Experimental custom normals are not opt-in",
        )
        armature_preference = addon.SMOAddonPreferences.bl_rna.properties[
            "import_armatures"
        ]
        check(
            not armature_preference.default,
            "Experimental armatures are not opt-in",
        )
        check(
            "sun_strength_multiplier"
            not in addon.SMOProperties.bl_rna.properties,
            "Obsolete Sun strength multiplier is still registered",
        )
        check(
            "world_strength_multiplier"
            not in addon.SMOProperties.bl_rna.properties,
            "Obsolete World strength multiplier is still registered",
        )
        check(len(addon._KINGDOM_ENUM_ITEMS) == 1, "Initial kingdom enum is invalid")
        check(len(addon._SCENARIO_ENUM_ITEMS) == 1, "Initial scenario enum is invalid")
        addon.unregister()
        registered = False
        check(len(addon._KINGDOM_ENUM_ITEMS) == 1, "Unregister emptied kingdom enum")
        check(len(addon._SCENARIO_ENUM_ITEMS) == 1, "Unregister emptied scenario enum")
        addon.register()
        registered = True
        check(len(addon._KINGDOM_ENUM_ITEMS) == 1, "Second register lost kingdom enum")
        check(len(addon._SCENARIO_ENUM_ITEMS) == 1, "Second register lost scenario enum")

        scene_a = bpy.context.scene
        scene_b = bpy.data.scenes.new("Phase1 Scene B")
        shared_a = addon.get_or_create_scene_collection(scene_a, "Phase1 Shared")
        shared_b = addon.get_or_create_scene_collection(scene_b, "Phase1 Shared")
        check(shared_a is shared_b, "Collection datablock was duplicated")
        check(scene_a.collection.children.get(shared_a.name) is shared_a, "Scene A is not linked")
        check(scene_b.collection.children.get(shared_a.name) is shared_a, "Scene B is not linked")

        transaction_collection = bpy.data.collections.new(
            "Phase1 Transaction"
        )
        scene_a.collection.children.link(transaction_collection)
        transaction_root = bpy.data.objects.new(
            "Phase1 Transaction Root",
            None,
        )
        transaction_collection.objects.link(transaction_root)
        transaction_root["smo_import_status"] = "FINISHED"
        old_mesh = bpy.data.meshes.new("Phase1 Previous Mesh")
        old_mesh.from_pydata(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            (),
            ((0, 1, 2),),
        )
        old_object = bpy.data.objects.new("Phase1 Previous Object", old_mesh)
        old_object["smo_static_model_generated"] = True
        transaction_collection.objects.link(old_object)
        previous = _generated_objects(
            transaction_collection,
            transaction_root,
        )
        previous_names = _rename_previous_generated(previous)
        new_object = bpy.data.objects.new("Phase1 Replacement Object", None)
        new_object["smo_static_model_generated"] = True
        transaction_collection.objects.link(new_object)
        modal_stopped = []
        transaction_harness = SimpleNamespace(
            _collection=transaction_collection,
            _root=transaction_root,
            _previous_generated=previous,
            _previous_name_state=previous_names,
            _had_previous_result=True,
            _replacement_committed=False,
            _asset_cache={},
            _stop_modal=lambda _context: modal_stopped.append(True),
        )
        SMO_OT_import_static_models._discard_partial_import(
            transaction_harness,
            SimpleNamespace(scene=scene_a),
            "CANCELLED",
            "Regression cancellation",
        )
        check(
            bpy.data.objects.get("Phase1 Previous Object") is old_object,
            "Cancelled reimport did not restore the previous object",
        )
        check(
            bpy.data.objects.get("Phase1 Replacement Object") is None,
            "Cancelled reimport retained its partial replacement",
        )
        check(
            transaction_root["smo_import_status"] == "FINISHED"
            and transaction_root["smo_last_reimport_status"] == "CANCELLED",
            "Cancelled reimport overwrote successful root metadata",
        )
        check(modal_stopped == [True], "Transactional rollback did not stop modal state")
        _remove_generated_objects((old_object,))
        bpy.data.objects.remove(transaction_root, do_unlink=True)
        bpy.data.collections.remove(transaction_collection)

        generated_root = bpy.data.collections.new("Phase1 Generated Root")
        scene_a.collection.children.link(generated_root)
        legacy_lighting = bpy.data.collections.new("Lighting - Phase1")
        legacy_lighting["smo_import_generated"] = True
        generated_root.children.link(legacy_lighting)
        lighting = addon.get_or_create_named_import_collection(
            generated_root,
            "Lighting",
            "LIGHTING",
        )
        check(
            lighting is legacy_lighting and lighting.name == "Lighting",
            "Legacy scoped Lighting collection was not migrated",
        )
        environment = addon.get_or_create_import_group_collection(
            generated_root,
            PlacementCategory.ENVIRONMENT,
            "Ignored Scope",
        )
        effects = addon.get_or_create_import_group_collection(
            generated_root,
            PlacementCategory.EFFECTS,
            "Ignored Scope",
        )
        extras = next(
            child
            for child in generated_root.children
            if child.get("smo_import_group_key") == "EXTRAS"
        )
        check(
            environment.name == "Environment",
            "Environment group name is verbose",
        )
        check(extras.name == "Extras", "Extras group name is verbose")
        check(effects.name == "Effects", "Effects group name is verbose")
        lighting_name = lighting.name
        environment_name = environment.name
        extras_name = extras.name
        effects_name = effects.name
        addon.remove_empty_import_collections(generated_root, generated_root.name)
        check(
            bpy.data.collections.get(lighting_name) is None,
            "Empty lighting datablock remained",
        )
        check(
            bpy.data.collections.get(environment_name) is None,
            "Empty environment datablock remained",
        )
        check(generated_root.children.get(extras_name) is None, "Empty generated group remained")
        check(bpy.data.collections.get(extras_name) is None, "Empty extras datablock remained")
        check(bpy.data.collections.get(effects_name) is None, "Empty child group remained")

        check(
            _select_albedo_texture(
                ("CarBody_nrm", "CarBody_alb", "CarBody_rgh"),
                ("_a0", "_a1", "_a2"),
            ) == "CarBody_alb",
            "Named albedo did not take priority over _a0",
        )
        check(
            _select_albedo_texture(("CarBody_nrm",), ("_a0",)) is None,
            "Normal map was accepted as colour",
        )
        check(
            _select_albedo_texture(("CloudForestDayLight_dns",), ("_a0",))
            == "CloudForestDayLight_dns",
            "Valid _a0 fallback was rejected",
        )
        check(
            _select_albedo_texture(("Net00.alb",), ("_a2",)) == "Net00.alb",
            "Dot-style albedo name was not recognised",
        )
        check(
            _ordered_display_textures(
                ("Water00_nrm", "Water00_rgh"),
                ("_n0", "_r0"),
            ) == ("Water00_nrm", "Water00_rgh"),
            "Non-colour FMAT textures were not retained in declared order",
        )
        check(
            _ordered_display_textures(
                ("CarBody_nrm", "CarBody_alb", "CarBody_rgh"),
                ("_n0", "_a0", "_r0"),
            ) == ("CarBody_alb", "CarBody_nrm", "CarBody_rgh"),
            "Named albedo was not followed by remaining FMAT textures",
        )
        check(_texture_role("Body_alb") == "ALBEDO", "Albedo role was missed")
        check(_texture_role("Body_rgh") == "ROUGHNESS", "Roughness role was missed")
        check(_texture_role("Body_mtl") == "METALLIC", "Metallic role was missed")
        check(_texture_role("Body_nrm") == "NORMAL", "Normal role was missed")
        check(_texture_role("Body_emm") == "EMISSION", "Emission role was missed")
        check(_texture_role("Body_mask") == "UNASSIGNED", "Unknown map was assigned")
        check(
            _texture_role("Diffuse", "_a0") == "ALBEDO",
            "_a0 sampler fallback was missed",
        )
        check(
            _texture_role("Surface", "_n0") == "NORMAL",
            "_n0 sampler fallback was missed",
        )
        bc5_snorm = _decode_block(
            bytes((0, 0, 0, 0, 0, 0, 0, 0, 127, 127, 0, 0, 0, 0, 0, 0)),
            "BC5_SNORM",
        )
        check(
            bc5_snorm[0] == (128, 255, 0, 255),
            f"BC5 SNORM decoding is wrong: {bc5_snorm[0]}",
        )
        reconstructed = _reconstruct_normal_blue(
            bytes((128, 128, 0, 255, 255, 128, 0, 255))
        )
        check(
            reconstructed[:4] == bytes((128, 128, 255, 255)),
            f"Flat normal blue reconstruction is wrong: {reconstructed[:4]}",
        )
        check(
            reconstructed[4:8] == bytes((255, 128, 128, 255)),
            f"Side normal blue reconstruction is wrong: {reconstructed[4:8]}",
        )
        rgba_2x2 = bytes(
            (
                128, 128, 0, 255,
                255, 128, 0, 255,
                0, 128, 0, 255,
                128, 255, 0, 255,
            )
        )
        numpy_pixels = _rgba8_to_blender_pixels(rgba_2x2, 2, 2, True)
        saved_numpy = static_model_import._numpy

        try:
            static_model_import._numpy = None
            fallback_pixels = _rgba8_to_blender_pixels(
                rgba_2x2,
                2,
                2,
                True,
            )
        finally:
            static_model_import._numpy = saved_numpy

        check(
            tuple(float(value) for value in numpy_pixels)
            == tuple(float(value) for value in fallback_pixels),
            "NumPy and pure-Python texture post-processing differ",
        )

        asset = (Path("C:/ObjectData/BlockBrick.szs"), "BlockBrick.bfres")
        forest_context = _asset_context_key(
            asset,
            (Path("C:/ObjectData/ForestWorldHomeStageTexture.szs"),),
        )
        city_context = _asset_context_key(
            asset,
            (Path("C:/ObjectData/CityWorldHomeStageTexture.szs"),),
        )
        check(forest_context != city_context, "Texture context is absent from asset cache key")

        decoded = SimpleNamespace(
            name="SharedTexture",
            width=1,
            height=1,
            rgba8=bytes((255, 255, 255, 255)),
            format_value=0x1A01,
        )
        image_a = _create_albedo_image(decoded, (Path("C:/A/Shared.szs"), "A.bfres"))
        image_b = _create_albedo_image(decoded, (Path("C:/B/Shared.szs"), "B.bfres"))
        check(image_a is not image_b, "Images from different sources collided")
        check(image_a.name != image_b.name, "Image names do not identify their source")
        check(
            image_a.packed_file is not None
            and image_a.get("smo_pixels_packed"),
            "Decoded image pixels were not packed for persistence",
        )
        check(
            image_a.name.startswith(
                f"SMO [{image_a['smo_image_identity']}] "
            ),
            "Image identity can be lost to Blender name truncation",
        )
        reused_image_a = _create_albedo_image(
            decoded,
            (Path("C:/A/Shared.szs"), "A.bfres"),
        )
        check(
            reused_image_a is image_a
            and image_a["smo_pixel_upload_count"] == 1,
            "Source-identical image pixels were uploaded again",
        )

        identity_a = _identity_digest("A", "Body", "SharedTexture")
        identity_b = _identity_digest("B", "Body", "SharedTexture")
        material_a = _create_albedo_material(
            "SharedAsset", "Body", "SharedTexture", image_a, False, identity_a
        )
        material_b = _create_albedo_material(
            "SharedAsset", "Body", "SharedTexture", image_b, False, identity_b
        )
        check(material_a is not material_b, "Materials from different contexts collided")
        check(
            material_a.name.startswith(f"SMO [{identity_a}] "),
            "Material identity can be lost to Blender name truncation",
        )
        changed_decoded = SimpleNamespace(
            name=decoded.name,
            width=decoded.width,
            height=decoded.height,
            rgba8=bytes((0, 0, 0, 255)),
            format_value=decoded.format_value,
        )
        refreshed_image_a = _create_albedo_image(
            changed_decoded,
            (Path("C:/A/Shared.szs"), "A.bfres"),
        )
        check(
            refreshed_image_a is image_a
            and image_a["smo_pixel_upload_count"] == 2
            and image_a.pixels[0] == 0.0,
            "Changed source image content did not refresh",
        )

        sampler_decoded = SimpleNamespace(
            name="Surface",
            width=1,
            height=1,
            rgba8=bytes((128, 128, 0, 255)),
            format_value=0x1E02,
        )
        sampler_source = (
            Path("C:/SamplerRole/Textures.szs"),
            "Textures.bfres",
        )
        sampler_normal = _create_texture_image(
            sampler_decoded,
            sampler_source,
            "Non-Color",
            "NORMAL",
        )
        sampler_unassigned = _create_texture_image(
            sampler_decoded,
            sampler_source,
            "Non-Color",
            "UNASSIGNED",
        )
        check(
            sampler_normal is not sampler_unassigned,
            "Semantic texture roles collided in the image cache",
        )
        check(
            sampler_normal.get("smo_texture_role") == "NORMAL",
            "Sampler-only normal role was not stored on the image",
        )
        check(
            sampler_normal.get("smo_normal_blue_reconstructed")
            and sampler_normal.pixels[2] > 0.99,
            "Sampler-only normal map did not reconstruct blue",
        )
        check(
            not sampler_unassigned.get("smo_normal_blue_reconstructed"),
            "Unassigned data map incorrectly reconstructed blue",
        )

        multi_source = (Path("C:/Multi/Textures.szs"), "Textures.bfres")

        def multi_image(name: str, colour_space: str) -> bpy.types.Image:
            decoded_map = SimpleNamespace(
                name=name,
                width=1,
                height=1,
                rgba8=bytes((128, 192, 255, 255)),
                format_value=0x1A01,
            )
            return _create_texture_image(decoded_map, multi_source, colour_space)

        multi_albedo = multi_image("Body_alb", "sRGB")
        multi_bindings = (
            ("ALBEDO", "Body_alb", "_a0", multi_albedo),
            (
                "ROUGHNESS",
                "Body_rgh",
                "_a1",
                multi_image("Body_rgh", "Non-Color"),
            ),
            (
                "METALLIC",
                "Body_mtl",
                "_a2",
                multi_image("Body_mtl", "Non-Color"),
            ),
            (
                "NORMAL",
                "Body_nrm",
                "_n0",
                multi_image("Body_nrm", "Non-Color"),
            ),
            (
                "EMISSION",
                "Body_emm",
                "_e0",
                multi_image("Body_emm", "sRGB"),
            ),
            (
                "UNASSIGNED",
                "Body_mask",
                "_a5",
                multi_image("Body_mask", "Non-Color"),
            ),
        )
        check(
            multi_bindings[3][3].get("smo_normal_blue_reconstructed"),
            "Normal image was not marked as blue-channel reconstructed",
        )
        normal_pixel = tuple(multi_bindings[3][3].pixels[:4])
        check(
            normal_pixel[2] > 0.89,
            f"Normal image blue channel was not reconstructed: {normal_pixel}",
        )
        multi_material = _create_albedo_material(
            "MultiAsset",
            "Body",
            "Body_alb",
            multi_albedo,
            False,
            _identity_digest("multi material"),
            texture_bindings=multi_bindings,
        )
        multi_shader = next(
            node
            for node in multi_material.node_tree.nodes
            if node.type == "BSDF_PRINCIPLED"
        )
        for input_name in (
            "Base Color",
            "Roughness",
            "Metallic",
            "Normal",
            "Emission Color",
        ):
            check(
                multi_shader.inputs[input_name].is_linked,
                f"{input_name} texture was not connected",
            )
        check(
            abs(multi_shader.inputs["Specular IOR Level"].default_value - 0.2)
            < 1e-6,
            "Specular IOR Level is not 0.2",
        )
        normal_node = next(
            node
            for node in multi_material.node_tree.nodes
            if node.get("smo_texture_role") == "NORMAL"
        )
        check(
            normal_node.image.colorspace_settings.name == "Non-Color",
            "Normal map is not Non-Color",
        )
        check(
            normal_node.image.alpha_mode == "CHANNEL_PACKED",
            "Normal map is not marked as channel-packed data",
        )
        check(
            any(node.type == "NORMAL_MAP" for node in multi_material.node_tree.nodes),
            "Normal map node was not created",
        )
        for role, input_name in (
            ("ROUGHNESS", "Roughness"),
            ("METALLIC", "Metallic"),
        ):
            role_node = next(
                node
                for node in multi_material.node_tree.nodes
                if node.get("smo_texture_role") == role
            )
            link = role_node.outputs["Color"].links[0]
            check(
                link.to_node == multi_shader
                and link.to_socket == multi_shader.inputs[input_name],
                f"{role.title()} was not connected directly as grayscale data",
            )
        check(
            not any(
                node.type == "SEPARATE_COLOR"
                for node in multi_material.node_tree.nodes
            ),
            "Grayscale scalar maps created a Separate Color node",
        )
        mask_node = next(
            node
            for node in multi_material.node_tree.nodes
            if node.get("smo_texture_role") == "UNASSIGNED"
        )
        check(not mask_node.outputs["Color"].is_linked, "Unknown map was connected")
        connected = json.loads(multi_material["smo_connected_textures"])
        check(
            connected
            == {
                "ALBEDO": "Body_alb",
                "EMISSION": "Body_emm",
                "METALLIC": "Body_mtl",
                "NORMAL": "Body_nrm",
                "ROUGHNESS": "Body_rgh",
            },
            f"Connected texture metadata is wrong: {connected}",
        )
        check(
            json.loads(multi_material["smo_loaded_textures"])
            == [binding[1] for binding in multi_bindings],
            "Not every FMAT texture was retained on the material",
        )
        layered_material = _create_albedo_material(
            "WaterAsset",
            "Water",
            "RippleDummy_nrm",
            multi_image("RippleDummy_nrm", "sRGB"),
            False,
            _identity_digest("layered normals"),
            texture_bindings=(
                (
                    "NORMAL",
                    "RippleDummy_nrm",
                    "_n1",
                    multi_image("RippleDummy_nrm", "Non-Color"),
                ),
                (
                    "NORMAL",
                    "WaterSurface00_nrm",
                    "_n0",
                    multi_image("WaterSurface00_nrm", "Non-Color"),
                ),
            ),
        )
        layered_connected = json.loads(
            layered_material["smo_connected_textures"]
        )
        check(
            layered_connected.get("NORMAL") == "WaterSurface00_nrm",
            f"Primary normal sampler was not selected: {layered_connected}",
        )

        alpha_identity = _identity_digest("alpha reset")
        alpha_material = _create_albedo_material(
            "AlphaAsset", "Body", "SharedTexture", image_a, True, alpha_identity
        )
        shader = next(node for node in alpha_material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
        check(shader.inputs["Alpha"].is_linked, "Transparent material has no alpha link")
        alpha_material.surface_render_method = "BLENDED"
        reused = _create_albedo_material(
            "AlphaAsset", "Body", "SharedTexture", image_a, False, alpha_identity
        )
        shader = next(node for node in reused.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
        check(reused is alpha_material, "Material identity was not stable")
        check(not shader.inputs["Alpha"].is_linked, "Opaque reuse retained alpha link")
        check(shader.inputs["Alpha"].default_value == 1.0, "Opaque reuse retained alpha value")
        check(reused.surface_render_method == "DITHERED", "Opaque reuse retained blended state")

        sky_decoded = SimpleNamespace(
            name="SkyRegression_alb",
            width=1,
            height=1,
            rgba8=bytes((64, 128, 255, 48)),
            format_value=0x0B01,
        )
        sky_image = _create_albedo_image(
            sky_decoded,
            (Path("C:/Sky/SkyRegression.szs"), "SkyRegression.bfres"),
        )
        sky_material = _create_albedo_material(
            "SkyRegression",
            "Sky",
            "SkyRegression_alb",
            sky_image,
            True,
            _identity_digest("sky alpha data"),
            ignore_texture_alpha=True,
        )
        sky_shader = next(
            node
            for node in sky_material.node_tree.nodes
            if node.type == "BSDF_PRINCIPLED"
        )
        check(
            not sky_shader.inputs["Alpha"].is_linked,
            "Sky HDR alpha was connected to Principled Alpha",
        )
        check(
            sky_material.get("smo_texture_alpha_ignored"),
            "Sky alpha policy was not recorded on the material",
        )
        check(
            abs(sky_image.pixels[3] - 48.0 / 255.0) < 1e-6,
            "Sky HDR alpha data was discarded from the image",
        )

        fallback_archive = SimpleNamespace(
            names=frozenset(("Water00_nrm", "Water00_rgh")),
            decode=lambda name: SimpleNamespace(
                name=name,
                width=1,
                height=1,
                rgba8=bytes((128, 255, 0, 255)),
                has_transparency=True,
                format_value=0x1E02,
            ),
        )
        fallback_harness = SimpleNamespace(
            _texture_archive_cache={},
            _decoded_texture_cache={},
            _image_cache={},
            _image_transparency={},
            _material_cache={},
            _missing_albedo_textures=set(),
            _fallback_display_textures=set(),
            _texture_errors={},
            _load_texture_archive=lambda asset_key, data: fallback_archive,
            _load_shared_texture_archive=lambda archive_path: None,
        )
        fallback_source = SimpleNamespace(
            material_name="WaterMaterial",
            albedo_texture_name="MissingWater_alb",
            texture_names=(
                "MissingWater_alb",
                "Water00_nrm",
                "Water00_rgh",
            ),
            base_color=(0.1, 0.2, 0.3, 1.0),
        )
        fallback_material = SMO_OT_import_static_models._material_for_mesh(
            fallback_harness,
            fallback_source,
            (Path("C:/ObjectData/Water.szs"), "Water.bfres"),
            b"",
            (),
            "Water",
        )
        check(
            fallback_material["smo_display_texture"] == "Water00_nrm",
            "Importer did not use the next decodable FMAT texture",
        )
        check(
            bool(fallback_material["smo_display_texture_fallback"]),
            "Fallback display texture was not identified",
        )
        check(
            fallback_material["smo_display_texture_index"] == 1,
            "Fallback display texture index is wrong",
        )
        fallback_shader = next(
            node
            for node in fallback_material.node_tree.nodes
            if node.type == "BSDF_PRINCIPLED"
        )
        check(
            fallback_shader.inputs["Base Color"].is_linked,
            "Fallback texture is not connected directly to Base Color",
        )
        check(
            fallback_shader.inputs["Roughness"].is_linked,
            "Fallback material did not connect its roughness map",
        )
        check(
            fallback_shader.inputs["Normal"].is_linked,
            "Fallback material did not connect its normal map",
        )
        check(
            not fallback_shader.inputs["Alpha"].is_linked,
            "Data-map Base Color fallback incorrectly used data alpha",
        )
        check(
            json.loads(fallback_material["smo_loaded_textures"])
            == ["Water00_nrm", "Water00_rgh"],
            "Fallback material did not load every available FMAT texture",
        )
        fallback_base = fallback_material.node_tree.nodes["SMO Base Color"]
        fallback_normal = next(
            node
            for node in fallback_material.node_tree.nodes
            if node.get("smo_texture_role") == "NORMAL"
        )
        check(
            fallback_base.image.colorspace_settings.name == "sRGB",
            "Fallback Base Color image is not sRGB",
        )
        check(
            fallback_base.image.alpha_mode == "CHANNEL_PACKED",
            "Data-map Base Color fallback is not channel packed",
        )
        check(
            fallback_normal.image.colorspace_settings.name == "Non-Color",
            "Fallback normal image is not Non-Color",
        )
        check(
            "WaterMaterial: Water00_nrm"
            in fallback_harness._fallback_display_textures,
            "Fallback display texture was not recorded",
        )

        ocean_mesh = _create_ocean_wave_mesh()
        check(len(ocean_mesh.vertices) == 4, "Procedural ocean is not a quad")
        check(len(ocean_mesh.polygons) == 2, "Procedural ocean topology is wrong")
        check(
            max(abs(vertex.co.x) for vertex in ocean_mesh.vertices) == 2500.0,
            "Procedural ocean X extent is wrong",
        )
        check(
            max(abs(vertex.co.y) for vertex in ocean_mesh.vertices) == 2500.0,
            "Procedural ocean Y extent is wrong",
        )
        check(
            all(vertex.co.z == 0.0 for vertex in ocean_mesh.vertices),
            "Procedural ocean is not locally level",
        )
        check(
            bool(ocean_mesh.materials[0].get("smo_simplified_water")),
            "Procedural ocean did not receive a water material",
        )

        colour_source = SimpleNamespace(
            name="ColourNormalTriangle",
            material_name="ColourMT",
            vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            triangles=((0, 1, 2),),
            uvs=None,
            normals=((0.0, 1.0, 0.0),) * 3,
            colour_sets=(
                (
                    (1.0, 0.0, 0.25, 1.0),
                    (0.0, 1.0, 0.5, 0.75),
                    (0.0, 0.0, 1.0, 0.5),
                ),
                None,
                None,
                None,
            ),
        )
        colour_mesh = _create_mesh_data(
            colour_source,
            "Phase1",
            material_a,
            apply_custom_normals=True,
        )
        check(
            colour_mesh.name == "Phase1_ColourNormalTriangle_ColourMT",
            f"Mesh display name is incomplete: {colour_mesh.name}",
        )
        check(colour_mesh.has_custom_normals, "Valid source normals were not applied")
        check(
            colour_mesh.get("smo_custom_normals") == "APPLIED",
            "Normal status is wrong",
        )
        imported_normal = tuple(colour_mesh.corner_normals[0].vector)
        check(
            all(
                abs(actual - expected) < 1e-6
                for actual, expected in zip(imported_normal, (0.0, 0.0, 1.0))
            ),
            f"Custom normal basis conversion is wrong: {imported_normal}",
        )
        colour_attribute = colour_mesh.color_attributes.get("Color")
        check(colour_attribute is not None, "BFRES _c0 did not create Color")
        check(colour_attribute.domain == "POINT", "Vertex colour domain is not POINT")
        check(len(colour_attribute.data) == 3, "Vertex colour count is wrong")
        imported_colour = tuple(colour_attribute.data[1].color)
        check(
            all(
                abs(actual - expected) < 1e-6
                for actual, expected in zip(
                    imported_colour,
                    (0.0, 1.0, 0.5, 0.75),
                )
            ),
            f"Vertex colour values changed: {imported_colour}",
        )

        try:
            _apply_custom_normals(colour_mesh, ((0.0, 0.0, 0.0),) * 3)
        except ValueError as exc:
            check("zero length" in str(exc), "Zero normal reported the wrong failure")
        else:
            raise AssertionError("Zero-length normals were accepted")

        malformed_uv_source = SimpleNamespace(
            name="MalformedUVTriangle",
            material_name="MalformedMT",
            vertices=(
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
            ),
            triangles=((0, 1, 2),),
            uv_sets=(((0.0, 0.0), (1.0, 0.0)),),
            uvs=None,
            normals=None,
            colour_sets=(),
        )
        mesh_count_before_failure = len(bpy.data.meshes)

        try:
            _create_mesh_data(
                malformed_uv_source,
                "Phase1",
                material_a,
            )
        except ValueError as exc:
            check(
                "UV pairs" in str(exc),
                "Malformed UV data reported the wrong failure",
            )
        else:
            raise AssertionError("Malformed UV data was accepted")

        check(
            len(bpy.data.meshes) == mesh_count_before_failure,
            "Failed mesh construction left an orphan datablock",
        )

        print("PHASE1_REGRESSION: PASS")
    finally:
        if registered:
            addon.unregister()


if __name__ == "__main__":
    run()

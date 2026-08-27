from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import StaticMesh, read_static_bfres
from smo_kingdom_importer.bntx_texture import BntxTextureArchive
from smo_kingdom_importer.static_model_import import (
    _texture_role,
    SMO_OT_import_static_models,
)
from smo_kingdom_importer.world_list import extract_file, read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def asset_bfres_data(romfs_root: Path, asset_name: str) -> bytes:
    archive_path = romfs_root / "ObjectData" / f"{asset_name}.szs"
    archive = read_szs(archive_path)
    bfres_name = next(
        entry.name
        for entry in archive.get_files()
        if Path(entry.name).suffix.casefold() == ".bfres"
    )
    return bytes(extract_file(archive, bfres_name))


def asset_meshes(romfs_root: Path, asset_name: str) -> tuple[StaticMesh, ...]:
    models = read_static_bfres(asset_bfres_data(romfs_root, asset_name))
    return tuple(mesh for model in models for mesh in model.meshes)

def appearances(romfs_root: Path, asset_name: str) -> dict[str, set[str | None]]:
    result: dict[str, set[str | None]] = {}

    for mesh in asset_meshes(romfs_root, asset_name):
        result.setdefault(mesh.material_name, set()).add(mesh.albedo_texture_name)

    return result

def run(romfs_root: Path) -> None:
    car = appearances(romfs_root, "Car")
    body_textures = {
        texture
        for material, textures in car.items()
        if "body" in material.casefold()
        for texture in textures
    }
    check("CarBody_alb" in body_textures, "Car body did not select CarBody_alb")
    car_body = next(
        mesh
        for mesh in asset_meshes(romfs_root, "Car")
        if mesh.material_name == "BodyMT"
    )
    expected_car_roles = {
        "CarBody_alb": "ALBEDO",
        "CarBody_rgh": "ROUGHNESS",
        "CarBody_mtl": "METALLIC",
        "CarBody_nrm": "NORMAL",
        "CarBody_emm": "EMISSION",
    }
    check(
        {
            name: _texture_role(name)
            for name in car_body.texture_names
            if name in expected_car_roles
        }
        == expected_car_roles,
        f"Car body texture roles are wrong: {car_body.texture_names}",
    )
    car_bfres = asset_bfres_data(romfs_root, "Car")
    car_textures = BntxTextureArchive.from_bfres(car_bfres)
    check(car_textures is not None, "Car BFRES has no embedded BNTX")
    car_asset_key = (
        romfs_root / "ObjectData" / "Car.szs",
        "Car.bfres",
    )
    material_harness = SimpleNamespace(
        _decoded_texture_cache={},
        _image_cache={},
        _image_transparency={},
        _material_cache={},
        _missing_albedo_textures=set(),
        _fallback_display_textures=set(),
        _texture_errors={},
        _load_texture_archive=lambda asset_key, data: car_textures,
        _load_shared_texture_archive=lambda archive_path: None,
    )
    car_material = SMO_OT_import_static_models._material_for_mesh(
        material_harness,
        car_body,
        car_asset_key,
        car_bfres,
        (),
        "Car",
    )
    connected_car_textures = json.loads(
        car_material["smo_connected_textures"]
    )
    check(
        connected_car_textures
        == {role: name for name, role in expected_car_roles.items()},
        f"Car material connections are wrong: {connected_car_textures}",
    )
    check(
        json.loads(car_material["smo_loaded_textures"])
        == list(car_body.texture_names),
        "Car material did not load every FMAT texture",
    )
    car_shader = next(
        node
        for node in car_material.node_tree.nodes
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
            car_shader.inputs[input_name].is_linked,
            f"Car {input_name} input is not linked",
        )

    check(
        abs(car_shader.inputs["Specular IOR Level"].default_value - 0.2)
        < 1e-6,
        "Car Specular IOR Level is not 0.2",
    )
    car_role_nodes = {
        node.get("smo_texture_role"): node
        for node in car_material.node_tree.nodes
        if node.type == "TEX_IMAGE"
    }
    check(
        car_role_nodes["ALBEDO"].image.colorspace_settings.name == "sRGB",
        "Car albedo is not sRGB",
    )

    for role in ("ROUGHNESS", "METALLIC", "NORMAL"):
        check(
            car_role_nodes[role].image.colorspace_settings.name == "Non-Color",
            f"Car {role.lower()} is not Non-Color",
        )
        check(
            car_role_nodes[role].image.alpha_mode == "CHANNEL_PACKED",
            f"Car {role.lower()} is not marked as channel-packed data",
        )

    car_normal_image = car_role_nodes["NORMAL"].image
    check(
        bool(car_normal_image.get("smo_normal_blue_reconstructed")),
        "Car normal blue channel was not reconstructed",
    )
    sampled_blue = max(
        car_normal_image.pixels[index]
        for index in range(
            2,
            min(len(car_normal_image.pixels), 16384),
            4,
        )
    )
    check(
        sampled_blue > 0.9,
        f"Car normal sample did not recover positive Z: {sampled_blue}",
    )

    for material, textures in car.items():
        if "brakelight" in material.casefold() or "winker" in material.casefold():
            check("CarBody_nrm" not in textures, f"{material} selected a normal map")

    water_meshes = asset_meshes(romfs_root, "SeaWorldHomeWater000")
    check(bool(water_meshes), "Seaside water BFRES has no supported meshes")
    first_water_texture = water_meshes[0].texture_names[0]
    check(
        first_water_texture == "RippleDummy_nrm",
        f"Seaside water FMAT order changed: {water_meshes[0].texture_names}",
    )
    check(
        all(mesh.base_color is None for mesh in water_meshes if mesh.texture_names),
        "A water shader colour displaced its declared texture candidates",
    )
    texture_archive_path = (
        romfs_root / "ObjectData" / "SeaWorldHomeStageTexture.szs"
    )
    texture_sarc = read_szs(texture_archive_path)
    texture_bfres_name = next(
        entry.name
        for entry in texture_sarc.get_files()
        if Path(entry.name).suffix.casefold() == ".bfres"
    )
    texture_bfres = bytes(extract_file(texture_sarc, texture_bfres_name))
    texture_archive = BntxTextureArchive.from_bfres(texture_bfres)
    check(texture_archive is not None, "Seaside shared BFRES has no BNTX")
    first_water_image = texture_archive.decode(first_water_texture)
    check(
        first_water_image.format_value == 0x1A06,
        f"RippleDummy_nrm format is 0x{first_water_image.format_value:04X}",
    )
    check(
        len(first_water_image.rgba8)
        == first_water_image.width * first_water_image.height * 4,
        "Decoded first water texture byte count is wrong",
    )
    water_normal = texture_archive.decode("WaterSurface00_nrm")
    check(water_normal.format_value == 0x1E02, "Water surface normal is not BC5 SNORM")
    check(
        len(water_normal.rgba8) == water_normal.width * water_normal.height * 4,
        "Decoded BC5 water normal byte count is wrong",
    )
    water_mesh = water_meshes[0]
    check(
        tuple(
            zip(water_mesh.texture_sampler_names, water_mesh.texture_names)
        )
        == (
            ("_n1", "RippleDummy_nrm"),
            ("_n0", "WaterSurface00_nrm"),
        ),
        "Seaside water FMAT sampler bindings changed",
    )
    water_bfres = asset_bfres_data(romfs_root, "SeaWorldHomeWater000")
    water_material_harness = SimpleNamespace(
        _decoded_texture_cache={},
        _image_cache={},
        _image_transparency={},
        _material_cache={},
        _missing_albedo_textures=set(),
        _fallback_display_textures=set(),
        _texture_errors={},
        _load_texture_archive=lambda asset_key, data: None,
        _load_shared_texture_archive=lambda archive_path: (
            (texture_archive_path, texture_bfres_name),
            texture_archive,
        ),
    )
    water_material = SMO_OT_import_static_models._material_for_mesh(
        water_material_harness,
        water_mesh,
        (
            romfs_root / "ObjectData" / "SeaWorldHomeWater000.szs",
            "SeaWorldHomeWater000.bfres",
        ),
        water_bfres,
        (texture_archive_path,),
        "SeaWorldHomeWater000",
    )
    water_connected = json.loads(
        water_material["smo_connected_textures"]
    )
    check(
        water_connected.get("NORMAL") == "WaterSurface00_nrm",
        f"Seaside water selected the wrong normal: {water_connected}",
    )

    cloud = appearances(romfs_root, "CloudForestDayLight")
    print("CLOUD", {name: sorted(str(value) for value in values) for name, values in cloud.items()})
    check(
        any(
            texture is not None and texture.casefold().endswith("_dns")
            for textures in cloud.values()
            for texture in textures
        ),
        "Valid CloudForestDayLight _a0 fallback was lost",
    )
    sky = appearances(romfs_root, "SkyForestDayLight")
    print("SKY", {name: sorted(str(value) for value in values) for name, values in sky.items()})
    check(
        any("SkyForestDayLight_color" in textures for textures in sky.values()),
        "Valid SkyForestDayLight _a0 fallback was lost",
    )
    print("PHASE1_ROMFS_REGRESSION: PASS")
    print("CAR", {name: sorted(str(value) for value in values) for name, values in car.items()})


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: blender --background --python phase1_romfs_regression.py -- ROMFS")

    run(Path(arguments[0]).resolve())

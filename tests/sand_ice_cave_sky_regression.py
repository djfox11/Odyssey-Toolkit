from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.bntx_texture import BntxTextureArchive
from smo_kingdom_importer.static_model_import import (
    SMO_OT_import_static_models,
)
from smo_kingdom_importer.stage_data import (
    read_stage_layer,
    read_stage_scenario,
)
from smo_kingdom_importer.stage_lighting import (
    read_stage_graphics_sky_name,
)
from smo_kingdom_importer.world_list import extract_file, read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    stage_name = "SandWorldPressExStage"
    expected_sky = "SkyDesertIceCave"
    check(
        (romfs_root / "ObjectData" / f"{expected_sky}.szs").is_file(),
        f"{expected_sky}.szs is missing",
    )

    sky_path = romfs_root / "ObjectData" / f"{expected_sky}.szs"
    sky_sarc = read_szs(sky_path)
    sky_bfres_name = next(
        entry.name
        for entry in sky_sarc.get_files()
        if entry.name.casefold().endswith(".bfres")
    )
    sky_bfres = bytes(extract_file(sky_sarc, sky_bfres_name))
    sky_archive = BntxTextureArchive.from_bfres(sky_bfres)
    check(sky_archive is not None, "Ice Cave sky contains no BNTX")
    sky_texture = sky_archive.decode("SkyDesertIceCave_color")
    alpha_values = sky_texture.rgba8[3::4]
    check(
        sky_texture.has_transparency
        and min(alpha_values) < max(alpha_values),
        "Ice Cave sky no longer exposes its data-bearing alpha channel",
    )
    sky_mesh = next(
        mesh
        for model in read_static_bfres(sky_bfres)
        for mesh in model.meshes
        if mesh.texture_names
    )
    harness = SimpleNamespace(
        _decoded_texture_cache={},
        _image_cache={},
        _image_transparency={},
        _material_cache={},
        _missing_albedo_textures=set(),
        _fallback_display_textures=set(),
        _texture_errors={},
        _load_texture_archive=lambda asset_key, data: sky_archive,
        _load_shared_texture_archive=lambda archive_path: None,
    )
    sky_material = SMO_OT_import_static_models._material_for_mesh(
        harness,
        sky_mesh,
        (sky_path, sky_bfres_name),
        sky_bfres,
        (),
        expected_sky,
        ignore_texture_alpha=True,
    )
    output = next(
        node
        for node in sky_material.node_tree.nodes
        if node.type == "OUTPUT_MATERIAL"
    )
    check(
        output.inputs["Surface"].is_linked
        and output.inputs["Surface"].links[0].from_node.name
        == "SMO Sky Emission",
        "Ice Cave sky does not use the dedicated Emission surface",
    )
    check(
        not any(
            node.type == "BSDF_PRINCIPLED"
            for node in sky_material.node_tree.nodes
        ),
        "Ice Cave sky retained a Principled BSDF",
    )
    hdr_multiplier = sky_material.node_tree.nodes.get(
        "SMO Sky HDR Multiplier"
    )
    check(
        hdr_multiplier is not None
        and hdr_multiplier.inputs[0].is_linked
        and hdr_multiplier.inputs[0].links[0].from_socket.name == "Alpha",
        "Ice Cave sky alpha is not routed into its HDR multiplier",
    )
    check(
        sky_material["smo_texture_alpha_ignored"],
        "Sky alpha policy is missing",
    )

    for scenario_number in (1, 2):
        raw_map = read_stage_layer(
            romfs_root,
            stage_name,
            scenario_number,
            "Map",
        )
        raw_skies = tuple(
            placement.unit_config_name
            for placement in raw_map.placements
            if placement.category == "SkyList"
        )
        check(
            not raw_skies,
            f"Ice Cave scenario {scenario_number} unexpectedly has raw skies",
        )
        check(
            read_stage_graphics_sky_name(
                romfs_root,
                stage_name,
                scenario_number,
            )
            == expected_sky,
            f"Ice Cave scenario {scenario_number} graphics sky is wrong",
        )

        stage = read_stage_scenario(
            romfs_root,
            stage_name,
            scenario_number,
        )
        synthesised_skies = [
            placement
            for placement in stage.placements
            if placement.category == "SkyList"
            and placement.raw.get("SMOSynthesised")
        ]
        check(
            [placement.unit_config_name for placement in synthesised_skies]
            == [expected_sky],
            f"Ice Cave scenario {scenario_number} selected the wrong sky: "
            f"{[placement.unit_config_name for placement in synthesised_skies]}",
        )
        check(
            not synthesised_skies[0].raw.get("SMOSkyInherited"),
            "The Ice Cave graphics-preset sky was marked as inherited",
        )

    print("SAND_ICE_CAVE_SKY_REGRESSION: PASS")


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: sand_ice_cave_sky_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())

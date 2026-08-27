from __future__ import annotations

from hashlib import blake2s
import json
from pathlib import Path
import sys

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smo_kingdom_importer as addon


ASSETS = (
    "BossKnuckleBody",
    "BossKnuckleHead",
    "BossKnuckleHeadInner",
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def referenced_data_images(asset_names: tuple[str, ...]) -> dict[int, bpy.types.Image]:
    images: dict[int, bpy.types.Image] = {}

    for asset_name in asset_names:
        collection = bpy.data.collections.get(f"SMO Test - {asset_name}")
        check(collection is not None, f"{asset_name} collection was not created")

        for obj in collection.objects:
            if obj.type != "MESH":
                continue

            for material in obj.data.materials:
                if material is None or material.node_tree is None:
                    continue

                for node in material.node_tree.nodes:
                    if (
                        node.type == "TEX_IMAGE"
                        and node.image is not None
                        and node.get("smo_texture_role")
                        in {"NORMAL", "ROUGHNESS"}
                    ):
                        images[node.image.as_pointer()] = node.image

    return images


def image_state(image: bpy.types.Image) -> dict[str, object]:
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgba = pixels.reshape(-1, 4)
    return {
        "name": image.name,
        "size": tuple(image.size),
        "colour_space": image.colorspace_settings.name,
        "alpha_mode": image.alpha_mode,
        "digest": blake2s(pixels.tobytes(), digest_size=12).hexdigest(),
        "rgb_min": tuple(float(value) for value in rgba[:, :3].min(axis=0)),
        "rgb_max": tuple(float(value) for value in rgba[:, :3].max(axis=0)),
        "rgb_mean": tuple(float(value) for value in rgba[:, :3].mean(axis=0)),
        "identity": str(image.get("smo_image_identity", "")),
        "source_digest": str(image.get("smo_pixel_source_digest", "")),
        "upload_count": int(image.get("smo_pixel_upload_count", 0)),
        "packed": image.packed_file is not None,
    }


def run(romfs_root: Path) -> None:
    object_data = romfs_root / "ObjectData"
    addon.register()

    try:
        settings = bpy.context.scene.smo_settings
        settings.romfs_path = str(romfs_root)
        settings.kingdom = "CityWorldHomeStage"

        for asset_name in ASSETS[:2]:
            result = bpy.ops.smo.import_test_model(
                filepath=str(object_data / f"{asset_name}.szs"),
                use_selected_stage_textures=True,
            )
            check(result == {"FINISHED"}, f"{asset_name} returned {result}")

        before_images = referenced_data_images(ASSETS[:2])
        before = {
            pointer: image_state(image)
            for pointer, image in before_images.items()
        }
        result = bpy.ops.smo.import_test_model(
            filepath=str(object_data / f"{ASSETS[2]}.szs"),
            use_selected_stage_textures=True,
        )
        check(result == {"FINISHED"}, f"{ASSETS[2]} returned {result}")
        after_images = referenced_data_images(ASSETS[:2])
        after = {
            pointer: image_state(image)
            for pointer, image in after_images.items()
        }
        inner_images = referenced_data_images((ASSETS[2],))
        shared_pointers = after_images.keys() & inner_images.keys()
        check(
            before_images.keys() == after_images.keys(),
            "Body/Head material nodes changed image datablocks",
        )
        changed = {
            before[pointer]["name"]: {
                "before": before[pointer],
                "after": after[pointer],
            }
            for pointer in before
            if before[pointer] != after[pointer]
        }
        check(
            not changed,
            "HeadInner mutated Body/Head images:\n"
            + json.dumps(changed, indent=2, sort_keys=True),
        )
        black = {
            state["name"]: state
            for state in after.values()
            if max(state["rgb_max"]) <= 1e-6
        }
        check(
            not black,
            "Body/Head data images are black:\n"
            + json.dumps(black, indent=2, sort_keys=True),
        )
        repeatedly_uploaded = {
            state["name"]: state["upload_count"]
            for state in after.values()
            if state["upload_count"] != 1
        }
        check(
            not repeatedly_uploaded,
            "Source-identical images were uploaded more than once:\n"
            + json.dumps(repeatedly_uploaded, indent=2, sort_keys=True),
        )
        check(
            all(state["identity"] and state["source_digest"] for state in after.values()),
            "Image provenance metadata is incomplete",
        )
        check(
            all(state["packed"] for state in after.values()),
            "Decoded data images were not packed",
        )
        misplaced_identities = {
            state["name"]: state["identity"]
            for state in after.values()
            if not state["name"].startswith(
                f"SMO [{state['identity']}] "
            )
        }
        check(
            not misplaced_identities,
            "Image identities can be lost to Blender name truncation",
        )
        print(
            "BOSS_KNUCKLE_TEXTURE_REGRESSION: PASS "
            f"stable_images={len(after)} shared_images={len(shared_pointers)}"
        )
    finally:
        addon.unregister()


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit(
            "Usage: blender --background --python "
            "boss_knuckle_texture_regression.py -- ROMFS"
        )

    run(Path(arguments[0]).resolve())

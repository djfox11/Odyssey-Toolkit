from __future__ import annotations

from hashlib import blake2s
from pathlib import Path
import json
import sys

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bntx_texture import BntxTextureArchive
from smo_kingdom_importer.static_model_import import _create_texture_image
from smo_kingdom_importer.world_list import extract_file, read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def image_state(image: bpy.types.Image) -> dict[str, object]:
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgba = pixels.reshape(-1, 4)
    return {
        "size": tuple(image.size),
        "colour_space": image.colorspace_settings.name,
        "alpha_mode": image.alpha_mode,
        "digest": blake2s(pixels.tobytes(), digest_size=12).hexdigest(),
        "rgb_min": tuple(float(value) for value in rgba[:, :3].min(axis=0)),
        "rgb_max": tuple(float(value) for value in rgba[:, :3].max(axis=0)),
        "packed": image.packed_file is not None,
        "source": image.source,
        "source_digest": str(image.get("smo_pixel_source_digest", "")),
        "upload_count": int(image.get("smo_pixel_upload_count", 0)),
    }


def run(romfs_root: Path) -> None:
    archive_path = romfs_root / "ObjectData" / "ForestWorldHomeFlower002.szs"
    archive = read_szs(archive_path)
    bfres_name = next(
        entry.name
        for entry in archive.get_files()
        if entry.name and entry.name.casefold().endswith(".bfres")
    )
    texture_archive = BntxTextureArchive.from_bfres(
        extract_file(archive, bfres_name)
    )
    check(texture_archive is not None, "Flower BFRES contains no BNTX")
    decoded = texture_archive.decode("PlantAll00_mask")
    image = _create_texture_image(
        decoded,
        (archive_path, bfres_name),
        "Non-Color",
    )

    material = bpy.data.materials.new("SMO image persistence regression")
    material.use_nodes = True
    texture_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture_node.image = image
    image_name = image.name
    before = image_state(image)
    check(before["packed"], "Decoded mask was not packed before saving")
    check(max(before["rgb_max"]) > 0.0, "Decoded mask was black before saving")

    output = Path(bpy.app.tempdir) / "smo_image_persistence_regression.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(output), load_ui=False)

    reopened = bpy.data.images[image_name]
    after = image_state(reopened)
    check(after["packed"], "Decoded mask was unpacked after reopening")
    check(after["source"] == "FILE", "Packed image did not reopen as a file image")
    check(before == after, "Decoded mask pixels or metadata changed after reopening")
    print(
        "IMAGE_PERSISTENCE_REGRESSION="
        + json.dumps(after, sort_keys=True),
        flush=True,
    )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: image_persistence_regression.py -- ROMFS")

    run(Path(arguments[0]))

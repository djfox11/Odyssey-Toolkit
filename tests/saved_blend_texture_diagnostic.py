from __future__ import annotations

from hashlib import blake2s
import json
from pathlib import Path
import sys

import bpy
import numpy as np


def image_state(image: bpy.types.Image) -> dict[str, object]:
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgba = pixels.reshape(-1, 4)
    return {
        "name": image.name,
        "size": tuple(image.size),
        "users": image.users,
        "source": image.source,
        "has_data": image.has_data,
        "is_dirty": image.is_dirty,
        "packed": image.packed_file is not None,
        "colour_space": image.colorspace_settings.name,
        "alpha_mode": image.alpha_mode,
        "texture_name": str(image.get("smo_texture_name", "")),
        "texture_format": str(image.get("smo_texture_format", "")),
        "image_identity": str(image.get("smo_image_identity", "")),
        "source_digest": str(image.get("smo_pixel_source_digest", "")),
        "upload_count": int(image.get("smo_pixel_upload_count", 0)),
        "normal_blue_reconstructed": bool(
            image.get("smo_normal_blue_reconstructed", False)
        ),
        "float_digest": blake2s(
            pixels.tobytes(),
            digest_size=12,
        ).hexdigest(),
        "rgba_min": tuple(float(value) for value in rgba.min(axis=0)),
        "rgba_max": tuple(float(value) for value in rgba.max(axis=0)),
        "rgba_mean": tuple(float(value) for value in rgba.mean(axis=0)),
        "first_pixels": tuple(
            tuple(float(value) for value in pixel)
            for pixel in rgba[:4]
        ),
    }


def run() -> None:
    target_images = {
        image.as_pointer(): image
        for image in bpy.data.images
        if any(
            marker in (
                str(image.get("smo_texture_name", "")) + " " + image.name
            ).casefold()
            for marker in ("mask", "_msk", "rippledummy")
        )
    }
    image_states = {
        str(pointer): image_state(image)
        for pointer, image in target_images.items()
    }
    material_states = []

    for material in bpy.data.materials:
        if not material.use_nodes or material.node_tree is None:
            continue

        nodes = []

        for node in material.node_tree.nodes:
            if node.type != "TEX_IMAGE" or node.image is None:
                continue

            pointer = node.image.as_pointer()

            if pointer not in target_images:
                continue

            nodes.append(
                {
                    "node": node.name,
                    "label": node.label,
                    "role": str(node.get("smo_texture_role", "")),
                    "sampler": str(node.get("smo_sampler_name", "")),
                    "texture_name": str(node.get("smo_texture_name", "")),
                    "image_pointer": str(pointer),
                    "image": node.image.name,
                    "links": [
                        {
                            "from_socket": link.from_socket.name,
                            "to_node": link.to_node.name,
                            "to_socket": link.to_socket.name,
                        }
                        for link in material.node_tree.links
                        if link.from_node == node
                    ],
                }
            )

        if nodes:
            objects = sorted(
                obj.name
                for obj in bpy.data.objects
                if obj.type == "MESH"
                and material.name in obj.data.materials
            )
            material_states.append(
                {
                    "material": material.name,
                    "connected_textures": str(
                        material.get("smo_connected_textures", "")
                    ),
                    "loaded_textures": str(
                        material.get("smo_loaded_textures", "")
                    ),
                    "objects": objects,
                    "nodes": nodes,
                }
            )

    duplicates: dict[str, list[str]] = {}

    for state in image_states.values():
        duplicates.setdefault(state["texture_name"], []).append(state["name"])

    print(
        "SMO_SAVED_BLEND_TEXTURE_DIAGNOSTIC="
        + json.dumps(
            {
                "blend": bpy.data.filepath,
                "images": image_states,
                "materials": material_states,
                "duplicates": duplicates,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    run()

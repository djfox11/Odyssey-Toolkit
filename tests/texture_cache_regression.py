from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import sys
import time

import bpy


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bntx_texture import (
    BntxTextureArchive,
    DecodedTexture,
)
from smo_kingdom_importer.static_model_import import SMO_OT_import_static_models
from smo_kingdom_importer.texture_cache import (
    PersistentTextureCache,
    clear_texture_cache,
    texture_cache_status,
)
from smo_kingdom_importer.world_list import extract_file, read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RejectDecodeArchive:
    def decode(self, name: str) -> DecodedTexture:
        raise AssertionError(f"Cache hit unexpectedly decoded {name}")


class CountingArchive:
    def __init__(self, decoded: DecodedTexture):
        self.decoded = decoded
        self.calls = 0

    def decode(self, name: str) -> DecodedTexture:
        self.calls += 1
        check(name == self.decoded.name, "Unexpected synthetic texture name")
        return self.decoded


def real_car_normal(
    romfs_root: Path,
) -> tuple[tuple[Path, str], BntxTextureArchive]:
    archive_path = romfs_root / "ObjectData" / "Car.szs"
    archive = read_szs(archive_path)
    bfres_name = next(
        entry.name
        for entry in archive.get_files()
        if Path(entry.name).suffix.casefold() == ".bfres"
    )
    bfres_data = bytes(extract_file(archive, bfres_name))
    textures = BntxTextureArchive.from_bfres(bfres_data)
    check(textures is not None, "Car BFRES has no embedded BNTX")
    return (archive_path, bfres_name), textures


def run(romfs_root: Path) -> None:
    source_key, textures = real_car_normal(romfs_root)

    with TemporaryDirectory(prefix="smo_texture_cache_regression_") as temporary:
        cache_root = (
            Path(temporary)
            / "smo_kingdom_importer"
            / "texture_cache"
        )
        cold_cache = PersistentTextureCache(cache_root)
        cold_host = SimpleNamespace(_persistent_texture_cache=cold_cache)
        started = time.perf_counter()
        cold = SMO_OT_import_static_models._decode_texture(
            cold_host,
            source_key,
            textures,
            "CarBody_nrm",
        )
        cold_seconds = time.perf_counter() - started
        check(cold_cache.stats.misses == 1, "Cold cache did not miss once")
        check(cold_cache.stats.writes == 1, "Cold cache did not write once")
        png_path, metadata_path = cold_cache.entry_paths(
            source_key,
            "CarBody_nrm",
        )
        check(png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "Cache is not PNG")
        png_image = bpy.data.images.load(str(png_path), check_existing=False)
        check(
            tuple(png_image.size) == (cold.width, cold.height),
            "Blender could not load the cached PNG dimensions",
        )
        bpy.data.images.remove(png_image)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        check(metadata["has_transparency"] == cold.has_transparency, "Alpha metadata changed")
        check(metadata["format_value"] == cold.format_value, "Format metadata changed")

        warm_cache = PersistentTextureCache(cache_root)
        warm_host = SimpleNamespace(_persistent_texture_cache=warm_cache)
        started = time.perf_counter()
        warm = SMO_OT_import_static_models._decode_texture(
            warm_host,
            source_key,
            RejectDecodeArchive(),
            "CarBody_nrm",
        )
        warm_seconds = time.perf_counter() - started
        check(warm_cache.stats.hits == 1, "Warm cache did not hit once")
        check(warm.rgba8 == cold.rgba8, "Cached RGBA8 bytes changed")
        check(warm.width == cold.width and warm.height == cold.height, "Cached dimensions changed")
        check(warm.has_transparency == cold.has_transparency, "Cached transparency changed")
        check(warm.format_value == cold.format_value, "Cached BNTX format changed")
        check(warm_seconds < cold_seconds, "Warm cache was not faster than BC5 decoding")

        synthetic_source = Path(temporary) / "synthetic.szs"
        synthetic_source.write_bytes(b"first")
        synthetic_key = (synthetic_source, "Synthetic.bfres")
        synthetic = DecodedTexture(
            name="Synthetic_nrm",
            width=2,
            height=2,
            rgba8=bytes(range(16)),
            has_transparency=True,
            format_value=0x1E02,
        )
        synthetic_cache = PersistentTextureCache(cache_root)
        check(synthetic_cache.store(synthetic_key, synthetic), "Synthetic cache write failed")
        synthetic_source.write_bytes(b"changed-size")
        invalidated_cache = PersistentTextureCache(cache_root)
        check(
            invalidated_cache.load(synthetic_key, synthetic.name) is None,
            "Changed source fingerprint did not invalidate the cache",
        )

        current_key = (synthetic_source, "Synthetic.bfres")
        check(invalidated_cache.store(current_key, synthetic), "Invalidated entry was not rewritten")
        corrupt_png, _ = invalidated_cache.entry_paths(current_key, synthetic.name)
        corrupt_png.write_bytes(b"not a png")
        corrupt_cache = PersistentTextureCache(cache_root)
        check(
            corrupt_cache.load(current_key, synthetic.name) is None,
            "Corrupt cache entry did not fall back to a miss",
        )
        check(corrupt_cache.stats.errors == 1, "Corrupt cache error was not recorded")

        disabled_archive = CountingArchive(synthetic)
        disabled_host = SimpleNamespace(_persistent_texture_cache=None)
        direct = SMO_OT_import_static_models._decode_texture(
            disabled_host,
            current_key,
            disabled_archive,
            synthetic.name,
        )
        check(direct == synthetic and disabled_archive.calls == 1, "Disabled cache changed decoding")

        deferred_status = texture_cache_status(cache_root)
        check(
            deferred_status.exists and not deferred_status.statistics_known,
            "Preferences cache status unexpectedly scanned the cache",
        )
        status = texture_cache_status(cache_root, refresh=True)
        check(status.statistics_known, "Explicit cache status refresh stayed deferred")
        check(status.file_count > 0 and status.byte_count > 0, "Cache status is empty")
        check(clear_texture_cache(cache_root), "Populated cache was not cleared")
        check(not cache_root.exists(), "Clear left the cache directory behind")

    print(
        "TEXTURE_CACHE_REGRESSION: PASS "
        f"cold={cold_seconds:.3f}s warm={warm_seconds:.3f}s "
        f"speedup={cold_seconds / max(warm_seconds, 1e-9):.1f}x"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: blender --python texture_cache_regression.py -- ROMFS")

    run(Path(sys.argv[-1]).resolve())

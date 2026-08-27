from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2s
import json
import os
from pathlib import Path
import shutil
import struct
import time
from typing import Any
from uuid import uuid4
import zlib

from .performance import record_timing


CACHE_SCHEMA_VERSION = 1
CACHE_DIRECTORY_NAME = "texture_cache"
_CACHE_MARKER_NAME = ".smo_texture_cache_v1"
_CACHE_MARKER_CONTENT = "SMO Kingdom Importer texture cache schema 1\n"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_DIMENSION = 16384


@dataclass(slots=True)
class TextureCacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    errors: int = 0
    bytes_read: int = 0
    bytes_written: int = 0


@dataclass(slots=True, frozen=True)
class TextureCacheStatus:
    exists: bool
    file_count: int
    byte_count: int
    message: str
    statistics_known: bool = True


_STATUS_CACHE: dict[Path, TextureCacheStatus] = {}


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(data, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", checksum)
    )


def _encode_rgba8_png(width: int, height: int, rgba8: bytes) -> bytes:
    expected_size = width * height * 4

    if (
        width <= 0
        or height <= 0
        or width > _MAX_DIMENSION
        or height > _MAX_DIMENSION
        or len(rgba8) != expected_size
    ):
        raise ValueError(
            f"Invalid RGBA8 cache image {width}x{height} with "
            f"{len(rgba8)} bytes."
        )

    stride = width * 4
    scanlines = bytearray((stride + 1) * height)

    for row in range(height):
        destination = row * (stride + 1)
        source = row * stride
        scanlines[destination + 1 : destination + 1 + stride] = (
            rgba8[source : source + stride]
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"".join(
        (
            _PNG_SIGNATURE,
            _png_chunk(b"IHDR", header),
            _png_chunk(b"IDAT", zlib.compress(scanlines, level=1)),
            _png_chunk(b"IEND", b""),
        )
    )


def _decode_rgba8_png(data: bytes) -> tuple[int, int, bytes]:
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("Cache file is not a PNG image.")

    offset = len(_PNG_SIGNATURE)
    width = 0
    height = 0
    idat_parts: list[bytes] = []
    saw_header = False
    saw_end = False

    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("Truncated PNG chunk header.")

        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        checksum_end = data_end + 4

        if checksum_end > len(data):
            raise ValueError("Truncated PNG chunk data.")

        chunk_data = data[data_start:data_end]
        expected_checksum = struct.unpack_from(">I", data, data_end)[0]
        checksum = zlib.crc32(chunk_type)
        checksum = zlib.crc32(chunk_data, checksum) & 0xFFFFFFFF

        if checksum != expected_checksum:
            raise ValueError("PNG cache checksum mismatch.")

        if chunk_type == b"IHDR":
            if saw_header or length != 13:
                raise ValueError("Invalid PNG cache header.")

            (
                width,
                height,
                bit_depth,
                colour_type,
                compression,
                filtering,
                interlace,
            ) = struct.unpack(">IIBBBBB", chunk_data)

            if (
                width <= 0
                or height <= 0
                or width > _MAX_DIMENSION
                or height > _MAX_DIMENSION
                or bit_depth != 8
                or colour_type != 6
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise ValueError("Unsupported PNG cache encoding.")

            saw_header = True
        elif chunk_type == b"IDAT":
            if not saw_header:
                raise ValueError("PNG cache data precedes its header.")
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            if length != 0:
                raise ValueError("Invalid PNG cache terminator.")
            saw_end = True
            break

        offset = checksum_end

    if not saw_header or not saw_end or not idat_parts:
        raise ValueError("Incomplete PNG cache image.")

    stride = width * 4
    expected_size = (stride + 1) * height
    decompressor = zlib.decompressobj()
    scanlines = decompressor.decompress(
        b"".join(idat_parts),
        expected_size + 1,
    )

    if decompressor.unconsumed_tail or len(scanlines) > expected_size:
        raise ValueError("PNG cache expands beyond its declared dimensions.")

    scanlines += decompressor.flush()

    if not decompressor.eof or len(scanlines) != expected_size:
        raise ValueError("PNG cache has an invalid decompressed size.")

    rgba8 = bytearray(width * height * 4)

    for row in range(height):
        source = row * (stride + 1)

        if scanlines[source] != 0:
            raise ValueError("PNG cache uses an unexpected row filter.")

        destination = row * stride
        rgba8[destination : destination + stride] = scanlines[
            source + 1 : source + 1 + stride
        ]

    return width, height, bytes(rgba8)


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")

    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()

        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _ensure_cache_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    marker = root / _CACHE_MARKER_NAME

    if marker.exists():
        if marker.read_text(encoding="utf-8") != _CACHE_MARKER_CONTENT:
            raise ValueError(f"Texture cache marker is invalid: {marker}")
        return

    _atomic_write(marker, _CACHE_MARKER_CONTENT.encode("utf-8"))


def ensure_texture_cache_root(root: Path) -> Path:
    resolved = _validate_cache_root(root)
    _ensure_cache_root(resolved)
    return resolved


def _validate_cache_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    allowed_parent_names = {"odyssey_toolkit", "smo_kingdom_importer"}

    if (
        resolved.name != CACHE_DIRECTORY_NAME
        or resolved.parent.name not in allowed_parent_names
    ):
        raise ValueError(
            "Texture cache must be a dedicated "
            "odyssey_toolkit/texture_cache directory."
        )

    return resolved


class PersistentTextureCache:
    def __init__(self, root: Path):
        self.root = _validate_cache_root(root)
        self.stats = TextureCacheStats()
        self._source_signatures: dict[Path, tuple[str, int, int]] = {}
        self._reported_error = False

    def _record_error(self, action: str, exc: Exception) -> None:
        self.stats.errors += 1

        if not self._reported_error:
            print(
                "[Odyssey Toolkit] Persistent texture cache "
                f"{action} failed; using direct BNTX decoding: {exc}"
            )
            self._reported_error = True

    def _source_signature(self, source_path: Path) -> tuple[str, int, int]:
        resolved = source_path.expanduser().resolve()
        cached = self._source_signatures.get(resolved)

        if cached is not None:
            return cached

        stat = resolved.stat()
        signature = (
            resolved.as_posix().casefold(),
            int(stat.st_size),
            int(stat.st_mtime_ns),
        )
        self._source_signatures[resolved] = signature
        return signature

    def entry_key(
        self,
        source_key: tuple[Path, str],
        texture_name: str,
    ) -> str:
        source_path, bfres_name = source_key
        identity = {
            "schema": CACHE_SCHEMA_VERSION,
            "source": self._source_signature(source_path),
            "bfres": str(bfres_name).replace("\\", "/").casefold(),
            "texture": str(texture_name),
        }
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return blake2s(encoded, digest_size=20).hexdigest()

    def entry_paths(
        self,
        source_key: tuple[Path, str],
        texture_name: str,
    ) -> tuple[Path, Path]:
        key = self.entry_key(source_key, texture_name)
        directory = self.root / key[:2] / key[2:4]
        return directory / f"{key}.png", directory / f"{key}.json"

    def load(
        self,
        source_key: tuple[Path, str],
        texture_name: str,
    ) -> Any | None:
        started = time.perf_counter()

        try:
            png_path, metadata_path = self.entry_paths(
                source_key,
                texture_name,
            )

            if not png_path.is_file() or not metadata_path.is_file():
                self.stats.misses += 1
                return None

            metadata_bytes = metadata_path.read_bytes()
            png_bytes = png_path.read_bytes()
            metadata = json.loads(metadata_bytes.decode("utf-8"))

            if (
                not isinstance(metadata, dict)
                or metadata.get("schema_version") != CACHE_SCHEMA_VERSION
                or metadata.get("texture_name") != texture_name
                or type(metadata.get("width")) is not int
                or type(metadata.get("height")) is not int
                or type(metadata.get("format_value")) is not int
                or type(metadata.get("has_transparency")) is not bool
                or not isinstance(metadata.get("pixel_digest"), str)
            ):
                raise ValueError("Texture cache metadata is invalid.")

            width, height, rgba8 = _decode_rgba8_png(png_bytes)

            if (width, height) != (
                metadata["width"],
                metadata["height"],
            ):
                raise ValueError("Texture cache dimensions do not match metadata.")

            digest = blake2s(rgba8, digest_size=12).hexdigest()

            if digest != metadata["pixel_digest"]:
                raise ValueError("Texture cache pixel digest does not match.")

            from .bntx_texture import DecodedTexture

            self.stats.hits += 1
            self.stats.bytes_read += len(metadata_bytes) + len(png_bytes)
            return DecodedTexture(
                name=texture_name,
                width=width,
                height=height,
                rgba8=rgba8,
                has_transparency=metadata["has_transparency"],
                format_value=metadata["format_value"],
            )
        except Exception as exc:
            self.stats.misses += 1
            self._record_error("read", exc)
            return None
        finally:
            record_timing("texture_cache_read", time.perf_counter() - started)

    def store(
        self,
        source_key: tuple[Path, str],
        decoded: Any,
    ) -> bool:
        started = time.perf_counter()

        try:
            png_path, metadata_path = self.entry_paths(
                source_key,
                decoded.name,
            )
            _ensure_cache_root(self.root)
            png_path.parent.mkdir(parents=True, exist_ok=True)
            png_bytes = _encode_rgba8_png(
                int(decoded.width),
                int(decoded.height),
                bytes(decoded.rgba8),
            )
            metadata = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "texture_name": str(decoded.name),
                "width": int(decoded.width),
                "height": int(decoded.height),
                "format_value": int(decoded.format_value),
                "has_transparency": bool(decoded.has_transparency),
                "pixel_digest": blake2s(
                    decoded.rgba8,
                    digest_size=12,
                ).hexdigest(),
            }
            metadata_bytes = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            _atomic_write(png_path, png_bytes)
            _atomic_write(metadata_path, metadata_bytes)
            self.stats.writes += 1
            self.stats.bytes_written += len(png_bytes) + len(metadata_bytes)
            _STATUS_CACHE.pop(self.root, None)
            return True
        except Exception as exc:
            self._record_error("write", exc)
            return False
        finally:
            record_timing("texture_cache_write", time.perf_counter() - started)

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "enabled": True,
            "directory": str(self.root),
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "writes": self.stats.writes,
            "errors": self.stats.errors,
            "bytes_read": self.stats.bytes_read,
            "bytes_written": self.stats.bytes_written,
        }


def disabled_cache_payload(directory: Path) -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "enabled": False,
        "directory": str(directory),
        "hits": 0,
        "misses": 0,
        "writes": 0,
        "errors": 0,
        "bytes_read": 0,
        "bytes_written": 0,
    }


def _scan_texture_cache_status(root: Path) -> TextureCacheStatus:
    resolved = _validate_cache_root(root)

    if not resolved.is_dir():
        return TextureCacheStatus(False, 0, 0, "Cache is empty")

    file_count = 0
    byte_count = 0

    try:
        for path in resolved.rglob("*"):
            if not path.is_file() or path.name == _CACHE_MARKER_NAME:
                continue
            file_count += 1
            byte_count += path.stat().st_size
    except OSError as exc:
        return TextureCacheStatus(True, file_count, byte_count, str(exc))

    texture_count = file_count // 2
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(byte_count)
    unit = units[0]

    for candidate in units:
        unit = candidate

        if size < 1024.0 or candidate == units[-1]:
            break

        size /= 1024.0

    return TextureCacheStatus(
        True,
        file_count,
        byte_count,
        f"{texture_count:,} textures, {size:.1f} {unit}",
    )


def texture_cache_status(
    root: Path,
    *,
    refresh: bool = False,
) -> TextureCacheStatus:
    resolved = _validate_cache_root(root)
    cached = _STATUS_CACHE.get(resolved)

    if cached is not None and not refresh:
        return cached

    if not refresh:
        if not resolved.is_dir():
            status = TextureCacheStatus(False, 0, 0, "Cache is empty")
            _STATUS_CACHE[resolved] = status
            return status

        return TextureCacheStatus(
            True,
            0,
            0,
            "Statistics not loaded",
            statistics_known=False,
        )

    status = _scan_texture_cache_status(resolved)
    _STATUS_CACHE[resolved] = status
    return status


def clear_texture_cache(root: Path) -> bool:
    resolved = _validate_cache_root(root)

    if not resolved.exists():
        return False

    marker = resolved / _CACHE_MARKER_NAME

    if (
        not marker.is_file()
        or marker.read_text(encoding="utf-8") != _CACHE_MARKER_CONTENT
    ):
        raise ValueError(
            "Refusing to clear a directory without a valid Odyssey Toolkit "
            "cache marker."
        )

    shutil.rmtree(resolved)
    _STATUS_CACHE[resolved] = TextureCacheStatus(
        False,
        0,
        0,
        "Cache is empty",
    )
    return True

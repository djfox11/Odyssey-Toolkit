from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

import numpy as np
import oead


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from smo_kingdom_importer.bntx_texture import BntxTextureArchive
from smo_kingdom_importer.texture_cache import _encode_rgba8_png
from smo_kingdom_importer.world_list import read_szs


LAYOUT_SIZE = (300, 256)
COUNTER_COLOR = (240, 0, 5)
DEFAULT_ALIGN_MARGIN = 10.0


def _u16(data: bytes, offset: int) -> int:
    return int(struct.unpack_from("<H", data, offset)[0])


def _u32(data: bytes, offset: int) -> int:
    return int(struct.unpack_from("<I", data, offset)[0])


def _i8(data: bytes, offset: int) -> int:
    return int(struct.unpack_from("<b", data, offset)[0])


def _archive_file(path: Path, name: str) -> bytes:
    archive = read_szs(path)
    entry = archive.get_file(name)
    if entry is None:
        raise FileNotFoundError(f"{name!r} was not found in {path}.")
    return bytes(entry.data)


def _layout_icon(romfs: Path) -> np.ndarray:
    layout_data = _archive_file(
        romfs / "LayoutData" / "ShineTowerTotalCount.szs",
        "layout.lyarc",
    )
    layout_archive = oead.Sarc(layout_data)
    entry = layout_archive.get_file("timg/__Combined.bntx")
    if entry is None:
        raise FileNotFoundError("The total-count layout contains no combined BNTX.")

    textures = BntxTextureArchive.from_bfres(bytes(entry.data))
    if textures is None:
        raise ValueError("The total-count layout BNTX could not be read.")

    decoded = textures.decode("CmIconShine128^s")
    return np.frombuffer(decoded.rgba8, dtype=np.uint8).reshape(
        decoded.height,
        decoded.width,
        4,
    )


def _find_glyph_index(font: bytes, character: str) -> int:
    finf = font.find(b"FINF")
    if finf < 0:
        raise ValueError("HeadFont72 contains no FINF section.")

    cmap = _u32(font, finf + 0x1C) - 8
    code = ord(character)

    while cmap >= 0:
        if font[cmap : cmap + 4] != b"CMAP":
            raise ValueError(f"Invalid CMAP pointer at 0x{cmap:X}.")

        code_begin = _u32(font, cmap + 8)
        code_end = _u32(font, cmap + 12)
        method = _u16(font, cmap + 16)
        next_offset = _u32(font, cmap + 20)

        if code_begin <= code <= code_end:
            data_offset = cmap + 24
            if method == 0:
                return _u16(font, data_offset) + code - code_begin
            if method == 1:
                index = _u16(font, data_offset + (code - code_begin) * 2)
                if index != 0xFFFF:
                    return index
            elif method == 2:
                entry_count = _u16(font, data_offset)
                for entry_index in range(entry_count):
                    offset = data_offset + 4 + entry_index * 8
                    if _u32(font, offset) == code:
                        index = _u16(font, offset + 4)
                        if index != 0xFFFF:
                            return index
            else:
                raise NotImplementedError(f"Unsupported CMAP method {method}.")

        if next_offset == 0:
            break
        cmap = next_offset - 8

    raise KeyError(f"HeadFont72 contains no glyph for {character!r}.")


def _glyph_width(font: bytes, glyph_index: int) -> tuple[int, int, int]:
    finf = font.find(b"FINF")
    cwdh = _u32(font, finf + 0x18) - 8

    while cwdh >= 0:
        if font[cwdh : cwdh + 4] != b"CWDH":
            raise ValueError(f"Invalid CWDH pointer at 0x{cwdh:X}.")

        start = _u16(font, cwdh + 8)
        end = _u16(font, cwdh + 10)
        next_offset = _u32(font, cwdh + 12)
        if start <= glyph_index <= end:
            offset = cwdh + 16 + (glyph_index - start) * 3
            return _i8(font, offset), font[offset + 1], font[offset + 2]

        if next_offset == 0:
            break
        cwdh = next_offset - 8

    raise KeyError(f"HeadFont72 contains no width data for glyph {glyph_index}.")


def _font_data(romfs: Path) -> tuple[bytes, np.ndarray, dict[str, float | int]]:
    font = _archive_file(
        romfs / "LocalizedData" / "USen" / "LayoutData" / "FontData.szs",
        "HeadFont72.bffnt",
    )
    finf = font.find(b"FINF")
    tglp = _u32(font, finf + 0x14) - 8
    if font[tglp : tglp + 4] != b"TGLP":
        raise ValueError("HeadFont72 contains an invalid TGLP pointer.")

    textures = BntxTextureArchive.from_bfres(font)
    if textures is None or not textures.names:
        raise ValueError("HeadFont72 contains no readable BNTX glyph atlas.")
    texture_name = min(textures.names)
    decoded = textures.decode(texture_name)
    atlas = np.frombuffer(decoded.rgba8, dtype=np.uint8).reshape(
        decoded.height,
        decoded.width,
        4,
    )

    # Nintendo's NX font sheets store their first glyph row at the bottom.
    atlas = atlas[::-1]
    metrics: dict[str, float | int] = {
        "cell_width": font[tglp + 8],
        "cell_height": font[tglp + 9],
        "line_feed": _u16(font, finf + 0x0C),
        "columns": _u16(font, tglp + 20),
        "rows": _u16(font, tglp + 22),
        "font_width": 85.8499984741211,
        "font_height": 103.69999694824219,
    }
    return font, atlas, metrics


def _resize(array: np.ndarray, width: int, height: int) -> np.ndarray:
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive.")
    source_height, source_width = array.shape[:2]
    if source_width == width and source_height == height:
        return array.astype(np.float32, copy=True)

    x = np.linspace(0.0, source_width - 1.0, width, dtype=np.float32)
    y = np.linspace(0.0, source_height - 1.0, height, dtype=np.float32)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.minimum(x0 + 1, source_width - 1)
    y1 = np.minimum(y0 + 1, source_height - 1)
    wx = (x - x0)[None, :, None]
    wy = (y - y0)[:, None, None]

    top = array[y0[:, None], x0[None, :]].astype(np.float32) * (1.0 - wx)
    top += array[y0[:, None], x1[None, :]].astype(np.float32) * wx
    bottom = array[y1[:, None], x0[None, :]].astype(np.float32) * (1.0 - wx)
    bottom += array[y1[:, None], x1[None, :]].astype(np.float32) * wx
    return top * (1.0 - wy) + bottom * wy


def _render_text_mask(
    text: str,
    font: bytes,
    atlas: np.ndarray,
    metrics: dict[str, float | int],
) -> tuple[np.ndarray, float]:
    cell_width = int(metrics["cell_width"])
    cell_height = int(metrics["cell_height"])
    columns = int(metrics["columns"])
    x_scale = float(metrics["font_width"]) / cell_width
    y_scale = float(metrics["font_height"]) / cell_height

    glyphs: list[tuple[np.ndarray, float, float]] = []
    pen = 0.0
    for character in text:
        glyph_index = _find_glyph_index(font, character)
        left, glyph_width, char_width = _glyph_width(font, glyph_index)
        column = glyph_index % columns
        row = glyph_index // columns
        x = column * (cell_width + 1) + 1
        y = row * (cell_height + 1) + 1
        cell = atlas[y : y + cell_height, x : x + glyph_width, 3:4]
        scaled = _resize(
            cell,
            max(1, round(glyph_width * x_scale)),
            max(1, round(cell_height * y_scale)),
        )[:, :, 0]
        glyphs.append((scaled, pen + left * x_scale, pen))
        pen += char_width * x_scale

    width = max(1, round(pen))
    height = max(1, round(float(metrics["font_height"])))
    mask = np.zeros((height, width), dtype=np.float32)
    for glyph, position, _ in glyphs:
        x = round(position)
        end = min(width, x + glyph.shape[1])
        if end > x:
            mask[: min(height, glyph.shape[0]), x:end] = np.maximum(
                mask[: min(height, glyph.shape[0]), x:end],
                glyph[:height, : end - x],
            )
    return mask / 255.0, pen


def _blend_mask(
    canvas: np.ndarray,
    mask: np.ndarray,
    left: float,
    top: float,
    color: tuple[int, int, int],
    opacity: float = 1.0,
) -> None:
    x = round(left)
    y = round(top)
    height, width = mask.shape
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(canvas.shape[1], x + width)
    y1 = min(canvas.shape[0], y + height)
    if x0 >= x1 or y0 >= y1:
        return

    source_alpha = np.clip(
        mask[y0 - y : y1 - y, x0 - x : x1 - x] * opacity,
        0.0,
        1.0,
    )[:, :, None]
    destination = canvas[y0:y1, x0:x1]
    destination_alpha = destination[:, :, 3:4]
    output_alpha = source_alpha + destination_alpha * (1.0 - source_alpha)
    source_color = np.asarray(color, dtype=np.float32)[None, None, :] / 255.0
    premultiplied = source_color * source_alpha
    premultiplied += destination[:, :, :3] * destination_alpha * (1.0 - source_alpha)
    destination[:, :, :3] = np.divide(
        premultiplied,
        output_alpha,
        out=np.zeros_like(premultiplied),
        where=output_alpha > 0.0,
    )
    destination[:, :, 3:4] = output_alpha


def reconstruct(romfs: Path, output: Path, count: int) -> None:
    if count < 0 or count > 999:
        raise ValueError("The Odyssey total-count display supports 0 through 999.")

    icon = _layout_icon(romfs)
    font, atlas, metrics = _font_data(romfs)
    text_mask, text_advance = _render_text_mask(str(count), font, atlas, metrics)

    canvas_width, canvas_height = LAYOUT_SIZE
    canvas = np.zeros((canvas_height, canvas_width, 4), dtype=np.float32)

    icon_scale = 0.8
    icon_size = round(icon.shape[0] * icon_scale)
    icon_mask = _resize(icon[:, :, 3:4], icon_size, icon_size)[:, :, 0] / 255.0
    content_width = icon_size + DEFAULT_ALIGN_MARGIN + text_advance
    content_left = (canvas_width - content_width) * 0.5

    icon_center_x = content_left + icon_size * 0.5
    shadow_center_y = canvas_height * 0.5 - 10.0
    icon_center_y = canvas_height * 0.5 - (10.0 + 6.0 * icon_scale)
    _blend_mask(
        canvas,
        icon_mask,
        icon_center_x - icon_size * 0.5,
        shadow_center_y - icon_size * 0.5,
        (0, 0, 0),
        100.0 / 255.0,
    )
    _blend_mask(
        canvas,
        icon_mask,
        icon_center_x - icon_size * 0.5,
        icon_center_y - icon_size * 0.5,
        COUNTER_COLOR,
    )

    text_left = content_left + icon_size + DEFAULT_ALIGN_MARGIN
    text_top = (canvas_height - text_mask.shape[0]) * 0.5
    _blend_mask(canvas, text_mask, text_left, text_top + 6.0, (0, 0, 0))
    _blend_mask(canvas, text_mask, text_left, text_top, COUNTER_COLOR)

    rgba8 = np.clip(np.rint(canvas * 255.0), 0.0, 255.0).astype(np.uint8)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_encode_rgba8_png(canvas_width, canvas_height, rgba8.tobytes()))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconstruct the Odyssey ShineTowerTotalCount render texture."
    )
    parser.add_argument("romfs", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--count", type=int, default=21)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else None)

    reconstruct(args.romfs.resolve(), args.output.resolve(), args.count)
    print(f"Wrote ShineTower count {args.count} to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

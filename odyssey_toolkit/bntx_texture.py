from dataclasses import dataclass
import struct

from .performance import timed


@dataclass(slots=True, frozen=True)
class DecodedTexture:
    name: str
    width: int
    height: int
    rgba8: bytes
    has_transparency: bool
    format_value: int


@dataclass(slots=True, frozen=True)
class _TextureEntry:
    name: str
    width: int
    height: int
    depth: int
    array_count: int
    mip_count: int
    tile_mode: int
    block_height_log2: int
    format_value: int
    channels: tuple[int, int, int, int]
    image_size: int
    data_pointers_offset: int


@dataclass(slots=True, frozen=True)
class _FormatInfo:
    block_width: int
    block_height: int
    bytes_per_block: int
    decoder: str


_FORMAT_INFO = {
    0x0B01: _FormatInfo(1, 1, 4, "RGBA8"),
    0x0B06: _FormatInfo(1, 1, 4, "RGBA8"),
    0x0C01: _FormatInfo(1, 1, 4, "BGRA8"),
    0x0C06: _FormatInfo(1, 1, 4, "BGRA8"),
    0x1A01: _FormatInfo(4, 4, 8, "BC1"),
    0x1A06: _FormatInfo(4, 4, 8, "BC1"),
    0x1B01: _FormatInfo(4, 4, 16, "BC2"),
    0x1B06: _FormatInfo(4, 4, 16, "BC2"),
    0x1C01: _FormatInfo(4, 4, 16, "BC3"),
    0x1C06: _FormatInfo(4, 4, 16, "BC3"),
    0x1D01: _FormatInfo(4, 4, 8, "BC4"),
    0x1D02: _FormatInfo(4, 4, 8, "BC4_SNORM"),
    0x1E01: _FormatInfo(4, 4, 16, "BC5"),
    0x1E02: _FormatInfo(4, 4, 16, "BC5_SNORM"),
}


class _Reader:
    def __init__(self, data: bytes, base: int, size: int):
        self.data = data
        self.base = base
        self.size = size

    def _absolute(self, offset: int, size: int) -> int:
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError(
                f"BNTX read outside container at 0x{offset:X} "
                f"for 0x{size:X} bytes."
            )

        return self.base + offset

    def bytes(self, offset: int, size: int) -> bytes:
        absolute = self._absolute(offset, size)
        return self.data[absolute : absolute + size]

    def u16(self, offset: int) -> int:
        absolute = self._absolute(offset, 2)
        return int(struct.unpack_from("<H", self.data, absolute)[0])

    def u32(self, offset: int) -> int:
        absolute = self._absolute(offset, 4)
        return int(struct.unpack_from("<I", self.data, absolute)[0])

    def u64(self, offset: int) -> int:
        absolute = self._absolute(offset, 8)
        return int(struct.unpack_from("<Q", self.data, absolute)[0])

    def string(self, offset: int) -> str:
        length = self.u16(offset)
        raw = self.bytes(offset + 2, length)

        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Invalid UTF-8 BNTX string at 0x{offset:X}."
            ) from exc


class BntxTextureArchive:
    def __init__(
        self,
        reader: _Reader,
        entries: dict[str, _TextureEntry],
    ):
        self._reader = reader
        self._entries = entries
        self._names = frozenset(entries)

    @classmethod
    def from_bfres(cls, data: bytes) -> "BntxTextureArchive | None":
        base = data.find(b"BNTX")

        if base < 0:
            return None

        if base + 32 > len(data):
            raise ValueError("Truncated embedded BNTX header.")

        file_size = int(struct.unpack_from("<I", data, base + 28)[0])

        if file_size < 80 or base + file_size > len(data):
            raise ValueError(
                f"Invalid embedded BNTX size 0x{file_size:X}."
            )

        reader = _Reader(data, base, file_size)

        if reader.bytes(32, 4) != b"NX  ":
            raise NotImplementedError("Only Nintendo Switch BNTX is supported.")

        texture_count = reader.u32(36)
        texture_info_array = reader.u64(40)
        entries: dict[str, _TextureEntry] = {}

        for index in range(texture_count):
            offset = reader.u64(texture_info_array + index * 8)

            if reader.bytes(offset, 4) != b"BRTI":
                raise ValueError(f"Expected BRTI at 0x{offset:X}.")

            name = reader.string(reader.u64(offset + 0x60))
            entries[name] = _TextureEntry(
                name=name,
                width=reader.u32(offset + 0x24),
                height=reader.u32(offset + 0x28),
                depth=reader.u32(offset + 0x2C),
                array_count=reader.u32(offset + 0x30),
                mip_count=reader.u16(offset + 0x16),
                tile_mode=reader.u16(offset + 0x12),
                block_height_log2=reader.u32(offset + 0x34) & 7,
                format_value=reader.u32(offset + 0x1C),
                channels=tuple(reader.bytes(offset + 0x58, 4)),
                image_size=reader.u32(offset + 0x50),
                data_pointers_offset=reader.u64(offset + 0x70),
            )

        return cls(reader, entries)

    @property
    def names(self) -> frozenset[str]:
        return self._names

    @timed("bntx_texture_decoding")
    def decode(self, name: str) -> DecodedTexture:
        try:
            entry = self._entries[name]
        except KeyError as exc:
            raise KeyError(f"BNTX texture {name!r} was not found.") from exc

        if entry.width <= 0 or entry.height <= 0:
            raise ValueError(f"BNTX texture {name!r} has invalid dimensions.")

        if entry.depth != 1 or entry.array_count != 1:
            raise NotImplementedError(
                f"BNTX texture {name!r} is not a single 2D image."
            )

        try:
            format_info = _FORMAT_INFO[entry.format_value]
        except KeyError as exc:
            raise NotImplementedError(
                f"BNTX texture {name!r} uses unsupported format "
                f"0x{entry.format_value:04X}."
            ) from exc

        blocks = _deswizzle(self._reader, entry, format_info)
        rgba = _decode_blocks(
            blocks,
            entry.width,
            entry.height,
            format_info,
        )
        rgba = _apply_channel_sources(rgba, entry.channels)
        has_transparency = _has_transparency(
            rgba,
            entry.channels[3],
        )
        return DecodedTexture(
            name=name,
            width=entry.width,
            height=entry.height,
            rgba8=rgba,
            has_transparency=has_transparency,
            format_value=entry.format_value,
        )


def _round_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _block_linear_address(
    x: int,
    y: int,
    width_in_blocks: int,
    bytes_per_block: int,
    block_height: int,
) -> int:
    image_width_in_gobs = (
        width_in_blocks * bytes_per_block + 63
    ) // 64
    gob_address = (
        (y // (8 * block_height))
        * 512
        * block_height
        * image_width_in_gobs
        + (x * bytes_per_block // 64) * 512 * block_height
        + (y % (8 * block_height) // 8) * 512
    )
    byte_x = x * bytes_per_block
    return (
        gob_address
        + ((byte_x % 64) // 32) * 256
        + ((y % 8) // 2) * 64
        + ((byte_x % 32) // 16) * 32
        + (y % 2) * 16
        + byte_x % 16
    )


def _deswizzle(
    reader: _Reader,
    entry: _TextureEntry,
    format_info: _FormatInfo,
) -> bytes:
    width_in_blocks = (
        entry.width + format_info.block_width - 1
    ) // format_info.block_width
    height_in_blocks = (
        entry.height + format_info.block_height - 1
    ) // format_info.block_height
    first_data_offset = reader.u64(entry.data_pointers_offset)
    output = bytearray(
        width_in_blocks
        * height_in_blocks
        * format_info.bytes_per_block
    )

    if entry.tile_mode == 1:
        pitch = _round_up(
            width_in_blocks * format_info.bytes_per_block,
            32,
        )
    elif entry.tile_mode == 0:
        block_height = 1 << entry.block_height_log2
    else:
        raise NotImplementedError(
            f"Unsupported BNTX tile mode {entry.tile_mode}."
        )

    for y in range(height_in_blocks):
        for x in range(width_in_blocks):
            if entry.tile_mode == 1:
                source = y * pitch + x * format_info.bytes_per_block
            else:
                source = _block_linear_address(
                    x,
                    y,
                    width_in_blocks,
                    format_info.bytes_per_block,
                    block_height,
                )

            target = (
                y * width_in_blocks + x
            ) * format_info.bytes_per_block
            output[target : target + format_info.bytes_per_block] = (
                reader.bytes(
                    first_data_offset + source,
                    format_info.bytes_per_block,
                )
            )

    return bytes(output)


def _rgb565(value: int) -> tuple[int, int, int]:
    red = (value >> 11) & 31
    green = (value >> 5) & 63
    blue = value & 31
    return (
        (red << 3) | (red >> 2),
        (green << 2) | (green >> 4),
        (blue << 3) | (blue >> 2),
    )


def _mix(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    first_weight: int,
    second_weight: int,
    divisor: int,
) -> tuple[int, int, int]:
    return tuple(
        (first[index] * first_weight + second[index] * second_weight)
        // divisor
        for index in range(3)
    )


def _decode_bc1_colours(
    block: bytes,
    force_four_colour: bool,
) -> tuple[tuple[int, int, int, int], ...]:
    first_value, second_value, indices = struct.unpack("<HHI", block)
    first = _rgb565(first_value)
    second = _rgb565(second_value)
    colours = [
        (*first, 255),
        (*second, 255),
    ]

    if first_value > second_value or force_four_colour:
        colours.extend(
            (
                (*_mix(first, second, 2, 1, 3), 255),
                (*_mix(first, second, 1, 2, 3), 255),
            )
        )
    else:
        colours.extend(
            (
                (*_mix(first, second, 1, 1, 2), 255),
                (0, 0, 0, 0),
            )
        )

    return tuple(colours[(indices >> (pixel * 2)) & 3] for pixel in range(16))


def _decode_bc_alpha(block: bytes) -> tuple[int, ...]:
    first = block[0]
    second = block[1]
    values = [first, second]

    if first > second:
        values.extend(
            (
                (6 * first + second) // 7,
                (5 * first + 2 * second) // 7,
                (4 * first + 3 * second) // 7,
                (3 * first + 4 * second) // 7,
                (2 * first + 5 * second) // 7,
                (first + 6 * second) // 7,
            )
        )
    else:
        values.extend(
            (
                (4 * first + second) // 5,
                (3 * first + 2 * second) // 5,
                (2 * first + 3 * second) // 5,
                (first + 4 * second) // 5,
                0,
                255,
            )
        )

    indices = int.from_bytes(block[2:8], "little")
    return tuple(values[(indices >> (pixel * 3)) & 7] for pixel in range(16))


def _decode_bc_signed(block: bytes) -> tuple[int, ...]:
    first = max(struct.unpack("<b", block[:1])[0], -127)
    second = max(struct.unpack("<b", block[1:2])[0], -127)
    values = [first, second]

    if first > second:
        values.extend(
            int((first * weight + second * (7 - weight)) / 7)
            for weight in range(6, 0, -1)
        )
    else:
        values.extend(
            (
                int((4 * first + second) / 5),
                int((3 * first + 2 * second) / 5),
                int((2 * first + 3 * second) / 5),
                int((first + 4 * second) / 5),
                -127,
                127,
            )
        )

    indices = int.from_bytes(block[2:8], "little")
    return tuple(
        round(
            (values[(indices >> (pixel * 3)) & 7] + 127)
            * 255
            / 254
        )
        for pixel in range(16)
    )


def _decode_block(
    block: bytes,
    decoder: str,
) -> tuple[tuple[int, int, int, int], ...]:
    if decoder == "BC1":
        return _decode_bc1_colours(block, False)

    if decoder == "BC2":
        colours = _decode_bc1_colours(block[8:16], True)
        alpha = int.from_bytes(block[:8], "little")
        return tuple(
            (
                colour[0],
                colour[1],
                colour[2],
                ((alpha >> (pixel * 4)) & 15) * 17,
            )
            for pixel, colour in enumerate(colours)
        )

    if decoder == "BC3":
        colours = _decode_bc1_colours(block[8:16], True)
        alpha = _decode_bc_alpha(block[:8])
        return tuple(
            (colour[0], colour[1], colour[2], alpha[pixel])
            for pixel, colour in enumerate(colours)
        )

    if decoder == "BC4":
        values = _decode_bc_alpha(block)
        return tuple((value, value, value, 255) for value in values)

    if decoder == "BC4_SNORM":
        values = _decode_bc_signed(block)
        return tuple((value, value, value, 255) for value in values)

    if decoder in {"BC5", "BC5_SNORM"}:
        decode_channel = (
            _decode_bc_signed
            if decoder == "BC5_SNORM"
            else _decode_bc_alpha
        )
        red = decode_channel(block[:8])
        green = decode_channel(block[8:16])
        return tuple(
            (red[pixel], green[pixel], 0, 255)
            for pixel in range(16)
        )

    raise ValueError(f"Unknown block decoder {decoder!r}.")


def _decode_blocks(
    blocks: bytes,
    width: int,
    height: int,
    format_info: _FormatInfo,
) -> bytes:
    if format_info.decoder in {"RGBA8", "BGRA8"}:
        output = bytearray(blocks[: width * height * 4])

        if format_info.decoder == "BGRA8":
            for offset in range(0, len(output), 4):
                output[offset], output[offset + 2] = (
                    output[offset + 2],
                    output[offset],
                )

        return bytes(output)

    width_in_blocks = (
        width + format_info.block_width - 1
    ) // format_info.block_width
    height_in_blocks = (
        height + format_info.block_height - 1
    ) // format_info.block_height
    output = bytearray(width * height * 4)

    for block_y in range(height_in_blocks):
        for block_x in range(width_in_blocks):
            block_index = block_y * width_in_blocks + block_x
            block_offset = block_index * format_info.bytes_per_block
            pixels = _decode_block(
                blocks[
                    block_offset : block_offset + format_info.bytes_per_block
                ],
                format_info.decoder,
            )

            for pixel_y in range(format_info.block_height):
                target_y = block_y * format_info.block_height + pixel_y

                if target_y >= height:
                    continue

                for pixel_x in range(format_info.block_width):
                    target_x = block_x * format_info.block_width + pixel_x

                    if target_x >= width:
                        continue

                    pixel = pixels[
                        pixel_y * format_info.block_width + pixel_x
                    ]
                    target = (target_y * width + target_x) * 4
                    output[target : target + 4] = bytes(pixel)

    return bytes(output)


def _has_transparency(rgba: bytes, alpha_source: int) -> bool:
    if not rgba:
        return False

    if alpha_source == 0:
        return True

    if alpha_source == 1:
        return False

    alpha = rgba[3::4]
    return alpha.count(255) != len(alpha)


def _apply_channel_sources(
    rgba: bytes,
    channels: tuple[int, int, int, int],
) -> bytes:
    if channels == (2, 3, 4, 5):
        return rgba

    if len(rgba) % 4:
        raise ValueError("RGBA texture data length must be divisible by four.")

    invalid_source = next(
        (source for source in channels if source < 0 or source > 5),
        None,
    )

    if invalid_source is not None:
        raise ValueError(f"Invalid BNTX channel source {invalid_source}.")

    pixel_count = len(rgba) // 4
    output = bytearray(len(rgba))

    for channel_index, channel_source in enumerate(channels):
        if channel_source == 0:
            values = bytes(pixel_count)
        elif channel_source == 1:
            values = b"\xFF" * pixel_count
        else:
            values = rgba[channel_source - 2 :: 4]

        output[channel_index::4] = values

    return bytes(output)

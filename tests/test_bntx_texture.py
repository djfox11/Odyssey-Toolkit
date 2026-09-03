from __future__ import annotations

import struct
import unittest

from pure_module_loader import load_toolkit_module


bntx_texture = load_toolkit_module("bntx_texture")


def alpha_block(first: int, second: int, index: int = 0) -> bytes:
    indices = sum(index << (pixel * 3) for pixel in range(16))
    return bytes((first, second)) + indices.to_bytes(6, "little")


def colour_block(first: int = 0xF800, second: int = 0x07E0, index: int = 0) -> bytes:
    indices = sum(index << (pixel * 2) for pixel in range(16))
    return struct.pack("<HHI", first, second, indices)


class BNTXDecodeTests(unittest.TestCase):
    def decode(self, data: bytes, format_value: int) -> bytes:
        return bntx_texture._decode_blocks(
            data,
            4,
            4,
            bntx_texture._FORMAT_INFO[format_value],
        )

    def test_rgba8_and_bgra8_channel_order(self) -> None:
        rgba = bytes((10, 20, 30, 40))
        self.assertEqual(
            bntx_texture._decode_blocks(
                rgba,
                1,
                1,
                bntx_texture._FORMAT_INFO[0x0B01],
            ),
            rgba,
        )
        self.assertEqual(
            bntx_texture._decode_blocks(
                rgba,
                1,
                1,
                bntx_texture._FORMAT_INFO[0x0C01],
            ),
            bytes((30, 20, 10, 40)),
        )

    def test_bc1_through_bc5_decode_known_constant_blocks(self) -> None:
        cases = (
            (0x1A01, colour_block(), (255, 0, 0, 255)),
            (0x1B01, b"\xFF" * 8 + colour_block(), (255, 0, 0, 255)),
            (0x1C01, alpha_block(255, 0) + colour_block(), (255, 0, 0, 255)),
            (0x1D01, alpha_block(255, 0), (255, 255, 255, 255)),
            (0x1E01, alpha_block(255, 0) + alpha_block(0, 255), (255, 0, 0, 255)),
        )

        for format_value, block, expected_pixel in cases:
            with self.subTest(format=hex(format_value)):
                decoded = self.decode(block, format_value)
                self.assertEqual(len(decoded), 4 * 4 * 4)
                self.assertEqual(decoded[:4], bytes(expected_pixel))
                self.assertEqual(decoded, bytes(expected_pixel) * 16)

    def test_bc1_transparent_palette_entry(self) -> None:
        decoded = self.decode(
            colour_block(first=0x0000, second=0xFFFF, index=3),
            0x1A01,
        )
        self.assertEqual(decoded, bytes((0, 0, 0, 0)) * 16)

    def test_bc4_and_bc5_signed_formats_map_endpoints(self) -> None:
        negative = alpha_block(0x81, 0x7F)
        positive = alpha_block(0x7F, 0x81)
        self.assertEqual(self.decode(negative, 0x1D02)[:4], bytes((0, 0, 0, 255)))
        self.assertEqual(
            self.decode(positive + negative, 0x1E02)[:4],
            bytes((255, 0, 0, 255)),
        )


class BNTXChannelTests(unittest.TestCase):
    def test_channel_sources_remap_and_supply_constants(self) -> None:
        rgba = bytes((10, 20, 30, 40, 50, 60, 70, 80))
        self.assertEqual(
            bntx_texture._apply_channel_sources(rgba, (4, 3, 2, 1)),
            bytes((30, 20, 10, 255, 70, 60, 50, 255)),
        )
        self.assertEqual(
            bntx_texture._apply_channel_sources(rgba, (0, 0, 0, 5)),
            bytes((0, 0, 0, 40, 0, 0, 0, 80)),
        )

    def test_invalid_channel_data_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "divisible by four"):
            bntx_texture._apply_channel_sources(b"abc", (4, 3, 2, 5))
        with self.assertRaisesRegex(ValueError, "channel source 6"):
            bntx_texture._apply_channel_sources(b"rgba", (2, 3, 4, 6))

    def test_transparency_honours_constant_and_texture_alpha_sources(self) -> None:
        opaque = bytes((1, 2, 3, 255))
        translucent = bytes((1, 2, 3, 254))
        self.assertTrue(bntx_texture._has_transparency(opaque, 0))
        self.assertFalse(bntx_texture._has_transparency(translucent, 1))
        self.assertFalse(bntx_texture._has_transparency(opaque, 5))
        self.assertTrue(bntx_texture._has_transparency(translucent, 5))


if __name__ == "__main__":
    unittest.main()

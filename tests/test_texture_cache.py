from __future__ import annotations

import unittest

from pure_module_loader import load_toolkit_module


texture_cache = load_toolkit_module("texture_cache")


class PNGCacheTests(unittest.TestCase):
    def test_rgba8_png_round_trip_preserves_dimensions_and_pixels(self) -> None:
        pixels = bytes(
            (
                255, 0, 0, 255,
                0, 255, 0, 128,
                0, 0, 255, 64,
                10, 20, 30, 0,
            )
        )
        encoded = texture_cache._encode_rgba8_png(2, 2, pixels)

        self.assertTrue(encoded.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(
            texture_cache._decode_rgba8_png(encoded),
            (2, 2, pixels),
        )

    def test_png_cache_rejects_corrupted_checksum(self) -> None:
        encoded = bytearray(
            texture_cache._encode_rgba8_png(1, 1, bytes((1, 2, 3, 4)))
        )
        encoded[20] ^= 0x01

        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            texture_cache._decode_rgba8_png(bytes(encoded))

    def test_png_cache_rejects_truncation_and_wrong_pixel_size(self) -> None:
        encoded = texture_cache._encode_rgba8_png(
            1,
            1,
            bytes((1, 2, 3, 4)),
        )
        with self.assertRaisesRegex(ValueError, "Truncated|Incomplete"):
            texture_cache._decode_rgba8_png(encoded[:-5])
        with self.assertRaisesRegex(ValueError, "Invalid RGBA8"):
            texture_cache._encode_rgba8_png(2, 1, bytes((1, 2, 3, 4)))


if __name__ == "__main__":
    unittest.main()

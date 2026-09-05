from __future__ import annotations

import math
import struct
import unittest

from pure_module_loader import load_toolkit_module


bfres_mesh = load_toolkit_module("bfres_mesh")


class BFRESReaderBoundsTests(unittest.TestCase):
    def test_reader_accepts_exact_end_and_rejects_out_of_bounds_reads(self) -> None:
        reader = bfres_mesh._Reader(b"\x01\x02\x03\x04")

        self.assertEqual(reader.bytes(2, 2), b"\x03\x04")
        self.assertEqual(reader.bytes(4, 0), b"")
        for offset, size in ((-1, 1), (0, -1), (3, 2), (5, 0)):
            with self.subTest(offset=offset, size=size):
                with self.assertRaisesRegex(ValueError, "outside file bounds"):
                    reader.bytes(offset, size)

    def test_reader_rejects_truncated_struct_and_strings(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside file bounds"):
            bfres_mesh._Reader(b"\x00\x01").u32(0)
        with self.assertRaisesRegex(ValueError, "Unterminated BFRES string"):
            bfres_mesh._Reader(b"\x00\x00\x00name").string(1)


class BFRESVertexFormatTests(unittest.TestCase):
    def assertVectorAlmostEqual(
        self,
        actual: tuple[float, float, float, float],
        expected: tuple[float, float, float, float],
    ) -> None:
        for actual_value, expected_value in zip(actual, expected):
            self.assertTrue(
                math.isclose(actual_value, expected_value, abs_tol=1e-6),
                f"{actual!r} != {expected!r}",
            )

    def test_every_supported_vertex_format_decodes_known_values(self) -> None:
        packed_10_10_10_2 = (
            (0x200)
            | (0x1FF << 10)
            | (0x3FF << 20)
            | (0x3 << 30)
        )
        cases = (
            ("_FORMAT_32_32_32_SINGLE", struct.pack("<fff", 1.5, -2.0, 3.25), (1.5, -2.0, 3.25, 0.0)),
            ("_FORMAT_32_32_SINGLE", struct.pack("<ff", -4.0, 0.25), (-4.0, 0.25, 0.0, 0.0)),
            ("_FORMAT_16_16_SINGLE", struct.pack("<ee", 1.5, -2.0), (1.5, -2.0, 0.0, 0.0)),
            ("_FORMAT_16_16_16_16_SINGLE", struct.pack("<eeee", 1.0, 2.0, 3.0, 4.0), (1.0, 2.0, 3.0, 4.0)),
            ("_FORMAT_8_UINT", bytes((255,)), (255.0, 0.0, 0.0, 0.0)),
            ("_FORMAT_8_8_UINT", bytes((1, 254)), (1.0, 254.0, 0.0, 0.0)),
            ("_FORMAT_8_8_8_8_UINT", bytes((1, 2, 3, 255)), (1.0, 2.0, 3.0, 255.0)),
            ("_FORMAT_16_16_UNORM", struct.pack("<HH", 0, 65535), (0.0, 1.0, 0.0, 0.0)),
            ("_FORMAT_16_16_SNORM", struct.pack("<hh", -32768, 16384), (-1.0, 16384.0 / 32767.0, 0.0, 0.0)),
            ("_FORMAT_8_8_UNORM", bytes((0, 255)), (0.0, 1.0, 0.0, 0.0)),
            ("_FORMAT_8_8_SNORM", struct.pack("<bb", -128, 64), (-1.0, 64.0 / 127.0, 0.0, 0.0)),
            ("_FORMAT_8_8_8_8_UNORM", bytes((0, 85, 170, 255)), (0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0)),
            ("_FORMAT_8_8_8_8_SNORM", struct.pack("<bbbb", -128, -127, 0, 127), (-1.0, -1.0, 0.0, 1.0)),
            ("_FORMAT_10_10_10_2_SNORM", struct.pack("<I", packed_10_10_10_2), (-1.0, 1.0, -1.0 / 511.0, -1.0)),
        )

        for constant_name, encoded, expected in cases:
            with self.subTest(format=constant_name):
                actual = bfres_mesh._decode_attribute(
                    encoded,
                    0,
                    getattr(bfres_mesh, constant_name),
                )
                self.assertVectorAlmostEqual(actual, expected)

    def test_unknown_vertex_format_is_rejected(self) -> None:
        with self.assertRaisesRegex(NotImplementedError, "0xFFFF"):
            bfres_mesh._decode_attribute(b"", 0, 0xFFFF)


if __name__ == "__main__":
    unittest.main()

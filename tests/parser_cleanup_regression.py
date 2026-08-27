from __future__ import annotations

from collections import Counter
from itertools import product
from pathlib import Path
import math
import sys
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import (
    _normal_transform_coefficients,
    _transform_normal,
)
from smo_kingdom_importer.bntx_texture import (
    BntxTextureArchive,
    _apply_channel_sources,
    _has_transparency,
)
import smo_kingdom_importer.stage_data as stage_data
from smo_kingdom_importer.stage_data import (
    StageLayer,
    StagePlacement,
    Vector3,
    _make_placement,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def reference_channel_sources(
    rgba: bytes,
    channels: tuple[int, int, int, int],
) -> bytes:
    output = bytearray(len(rgba))

    for offset in range(0, len(rgba), 4):
        source = rgba[offset : offset + 4]

        for channel_index, channel_source in enumerate(channels):
            if channel_source == 0:
                value = 0
            elif channel_source == 1:
                value = 255
            else:
                value = source[channel_source - 2]

            output[offset + channel_index] = value

    return bytes(output)


def test_bntx_helpers() -> float:
    rgba = bytes(range(128))

    for channels in product(range(6), repeat=4):
        mapping = tuple(channels)
        check(
            _apply_channel_sources(rgba, mapping)
            == reference_channel_sources(rgba, mapping),
            f"Channel remapping changed for {mapping}",
        )

    check(
        _apply_channel_sources(rgba, (2, 3, 4, 5)) is rgba,
        "Identity channel mapping no longer returns its input bytes",
    )

    for invalid_source in (-1, 6):
        try:
            _apply_channel_sources(rgba, (2, 3, 4, invalid_source))
        except ValueError as exc:
            check(
                str(invalid_source) in str(exc),
                "Invalid channel source reported the wrong error",
            )
        else:
            raise AssertionError("Invalid BNTX channel source was accepted")

    try:
        _apply_channel_sources(b"\x00", (2, 3, 0, 1))
    except ValueError as exc:
        check(
            "divisible by four" in str(exc),
            "Malformed RGBA data reported the wrong error",
        )
    else:
        raise AssertionError("Malformed RGBA data was accepted")

    opaque = bytes((10, 20, 30, 255, 40, 50, 60, 255))
    transparent = bytes((10, 20, 30, 255, 40, 50, 60, 254))
    check(not _has_transparency(b"", 0), "Empty texture became transparent")
    check(_has_transparency(opaque, 0), "Constant-zero alpha was not detected")
    check(not _has_transparency(transparent, 1), "Constant-one alpha changed")
    check(not _has_transparency(opaque, 5), "Opaque alpha was misclassified")
    check(_has_transparency(transparent, 5), "Transparency was not detected")

    archive = BntxTextureArchive(None, {"A": None, "B": None})
    first_names = archive.names
    check(first_names == frozenset(("A", "B")), "BNTX names changed")
    check(first_names is archive.names, "BNTX names are rebuilt on each access")

    large_rgba = bytes(range(256)) * 16_384
    started = time.perf_counter()
    remapped = _apply_channel_sources(large_rgba, (2, 3, 0, 1))
    elapsed = time.perf_counter() - started
    check(len(remapped) == len(large_rgba), "Large remap changed byte count")
    check(not _has_transparency(remapped, 1), "Large remap alpha changed")
    return elapsed


def normalise(vector):
    length = math.sqrt(sum(component * component for component in vector))

    if length == 0.0:
        return vector

    return tuple(component / length for component in vector)


def reference_transform_normal(matrix, vector):
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    cofactors = (
        (e * i - f * h, f * g - d * i, d * h - e * g),
        (c * h - b * i, a * i - c * g, b * g - a * h),
        (b * f - c * e, c * d - a * f, a * e - b * d),
    )
    determinant = (
        a * cofactors[0][0]
        + b * cofactors[0][1]
        + c * cofactors[0][2]
    )

    if abs(determinant) < 1e-12:
        return normalise(
            (
                a * vector[0] + b * vector[1] + c * vector[2],
                d * vector[0] + e * vector[1] + f * vector[2],
                g * vector[0] + h * vector[1] + i * vector[2],
            )
        )

    transformed = tuple(
        sum(row[index] * vector[index] for index in range(3))
        / determinant
        for row in cofactors
    )
    return normalise(transformed)


def test_bfres_normal_cache() -> None:
    matrices = (
        (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        (
            (2.0, 0.25, 0.0, 3.0),
            (0.0, 3.0, 0.5, -2.0),
            (0.125, 0.0, 4.0, 7.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        (
            (0.0, 0.0, 0.0, 1.0),
            (0.0, 2.0, 0.0, 2.0),
            (0.0, 0.0, 3.0, 3.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
    )
    vectors = (
        (1.0, 0.0, 0.0),
        (0.25, -0.5, 0.75),
        (0.0, 0.0, 0.0),
    )

    for matrix in matrices:
        for vector in vectors:
            check(
                _transform_normal(matrix, vector)
                == reference_transform_normal(matrix, vector),
                f"Cached normal transform changed {matrix!r} / {vector!r}",
            )

    _normal_transform_coefficients.cache_clear()
    matrix = matrices[1]

    for vector in vectors:
        _transform_normal(matrix, vector)

    cache_info = _normal_transform_coefficients.cache_info()
    check(cache_info.misses == 1, f"Unexpected cache misses: {cache_info}")
    check(
        cache_info.hits == len(vectors) - 1,
        f"Normal coefficients were not reused: {cache_info}",
    )


class CountingDict(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_counts = Counter()

    def get(self, key, default=None):
        self.get_counts[key] += 1
        return super().get(key, default)


def placement(identifier: str, unit_name: str, category: str) -> StagePlacement:
    zero = Vector3(0.0, 0.0, 0.0)
    return StagePlacement(
        identifier=identifier,
        unit_config_name=unit_name,
        model_name=None,
        category=category,
        stage_layer="Map",
        placement_file_name="",
        layer_config_name="",
        translate=zero,
        rotate=zero,
        scale=Vector3(1.0, 1.0, 1.0),
        rotation_quaternion=(1.0, 0.0, 0.0, 0.0),
        links={},
        unit_config={},
        is_link_destination=False,
        is_root=True,
        raw={},
        source_stage_name="RootStage",
    )


def test_stage_data_cleanup() -> None:
    target = CountingDict({"Id": "Linked"})
    record = CountingDict(
        {
            "Id": "Root",
            "UnitConfigName": "Actor",
            "Translate": {"X": 1.0, "Y": 2.0, "Z": 3.0},
            "Rotate": {"X": 4.0, "Y": 5.0, "Z": 6.0},
            "Scale": {"X": 2.0, "Y": 3.0, "Z": 4.0},
            "Links": {"Next": [target]},
        }
    )
    result = _make_placement(record, "MapList", "Map", is_root=True)
    check(result is not None, "Synthetic placement was rejected")
    check(record.get_counts["Rotate"] == 1, "Rotate was converted more than once")
    check(target.get_counts["Id"] == 1, "Linked Id was read more than once")
    check(result.links == {"Next": ("Linked",)}, "Placement links changed")

    root_layer = StageLayer(
        name="Map",
        archive_path=Path("RootStageMap.szs"),
        byml_name="RootStageMap.byml",
        scenario_data={},
        placements=(
            placement("Zone1", "ZoneA", "ZoneList"),
            placement("Zone2", "ZoneA", "ZoneList"),
        ),
    )
    zone_layer = StageLayer(
        name="Map",
        archive_path=Path("ZoneAMap.szs"),
        byml_name="ZoneAMap.byml",
        scenario_data={},
        placements=[],
    )
    file_checks = Counter()

    def fake_is_file(path: Path) -> bool:
        file_checks[path.name] += 1
        return path.name in {"RootStageMap.szs", "ZoneAMap.szs"}

    def fake_read_stage_layer(
        _romfs: Path,
        source_stage_name: str,
        _scenario_number: int,
        layer_name: str,
    ) -> StageLayer:
        if (source_stage_name, layer_name) == ("RootStage", "Map"):
            return root_layer

        if (source_stage_name, layer_name) == ("ZoneA", "Map"):
            return zone_layer

        raise AssertionError("Missing optional layer was parsed")

    with (
        patch.object(Path, "is_file", fake_is_file),
        patch.object(stage_data, "read_stage_layer", fake_read_stage_layer),
        patch.object(
            stage_data,
            "_sky_resources_for_layers",
            return_value=("Sky",),
        ),
        patch.object(
            stage_data,
            "_add_synthesised_sky_placements",
            return_value=("Sky",),
        ),
    ):
        scenario = stage_data.read_stage_scenario(
            Path("synthetic_romfs"),
            "RootStage",
            1,
        )

    check(len(scenario.expanded_zones) == 2, "Repeated zones were not expanded")

    for layer_name in ("Map", "Design", "Sound"):
        check(
            file_checks[f"ZoneA{layer_name}.szs"] == 1,
            f"ZoneA {layer_name} cache missed: {file_checks}",
        )


def run() -> None:
    remap_seconds = test_bntx_helpers()
    test_bfres_normal_cache()
    test_stage_data_cleanup()
    print(
        "PARSER_CLEANUP_REGRESSION: PASS "
        f"remap_4mib={remap_seconds:.6f}s",
        flush=True,
    )


if __name__ == "__main__":
    run()

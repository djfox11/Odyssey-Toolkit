from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pure_module_loader import load_toolkit_module


actor_registry = load_toolkit_module("actor_registry")


def registry_fixture(romfs_path: Path | None = None) -> object:
    return actor_registry.ActorRegistry(
        romfs_key=(
            actor_registry.romfs_key(romfs_path)
            if romfs_path is not None
            else "fixture-romfs-key"
        ),
        created_utc="2026-01-02T03:04:05+00:00",
        archive_inventory_digest=hashlib.sha256().hexdigest(),
        archives_scanned=2,
        scenario_count=3,
        placement_count=5,
        placement_with_model_count=4,
        build_errors=("synthetic warning",),
        actors=(
            actor_registry.RegistryActorEvidence(
                unit_config_name="FixtureActor",
                parameter_config_name="FixtureVariant",
                models=(
                    actor_registry.RegistryModelEvidence(
                        model_name="FixtureModel",
                        occurrence_count=4,
                        source_stages=("FixtureStage",),
                    ),
                ),
                modelless_occurrence_count=1,
                modelless_source_stages=("FixtureStage",),
                modelless_stage_layers=("Design",),
                modelless_categories=("ObjList",),
            ),
        ),
    )


class ActorRegistrySerializationTests(unittest.TestCase):
    def test_registry_data_round_trip_preserves_evidence(self) -> None:
        original = registry_fixture()
        encoded = json.loads(json.dumps(original.to_data()))

        self.assertEqual(
            encoded["schema_version"],
            actor_registry.REGISTRY_SCHEMA_VERSION,
        )
        self.assertEqual(
            encoded["summary"]["actor_signature_count"],
            1,
        )
        self.assertEqual(
            actor_registry.actor_registry_from_data(encoded),
            original,
        )

    def test_saved_registry_loads_from_an_empty_synthetic_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            romfs = root / "romfs"
            cache = root / "cache"
            (romfs / "StageData").mkdir(parents=True)
            (romfs / "ObjectData").mkdir()
            original = registry_fixture(romfs)

            path = actor_registry.save_actor_registry(original, romfs, cache)
            loaded = actor_registry.load_actor_registry(romfs, cache)

            self.assertTrue(path.is_file())
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))
            self.assertEqual(loaded, original)

    def test_invalid_cache_shapes_and_values_are_rejected(self) -> None:
        valid = registry_fixture().to_data()
        cases = (
            ([], TypeError, "root"),
            ({**valid, "schema_version": 99}, ValueError, "schema version"),
            (
                {
                    **valid,
                    "summary": {**valid["summary"], "placement_count": -1},
                },
                ValueError,
                "non-negative integer",
            ),
            (
                {**valid, "actors": [{**valid["actors"][0], "models": "bad"}]},
                ValueError,
                "actor signature",
            ),
        )

        for data, error_type, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(error_type, message):
                    actor_registry.actor_registry_from_data(data)


if __name__ == "__main__":
    unittest.main()

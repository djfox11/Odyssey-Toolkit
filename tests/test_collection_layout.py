from __future__ import annotations

from types import SimpleNamespace
import unittest

from pure_module_loader import load_toolkit_module


collection_layout = load_toolkit_module("collection_layout")


class FakeCollection(dict[str, object]):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.children = FakeChildren()


class FakeChildren(list[FakeCollection]):
    def link(self, collection: FakeCollection) -> None:
        self.append(collection)


class FakeCollections:
    def __init__(self) -> None:
        self.created: list[FakeCollection] = []

    def new(self, name: str) -> FakeCollection:
        collection = FakeCollection(name)
        self.created.append(collection)
        return collection


def placement(
    *,
    identifier: str = "2183",
    unit_config_name: str = "CapFlowerBloom",
    model_name: str | None = None,
    source_stage_name: str = "PeachWorldHomeStage",
    stage_layer: str = "Common",
    zone_path: tuple[str, ...] = (),
) -> object:
    return SimpleNamespace(
        identifier=identifier,
        unit_config_name=unit_config_name,
        model_name=model_name,
        source_stage_name=source_stage_name,
        stage_layer=stage_layer,
        zone_path=zone_path,
    )


class ImportPlacementCollectionTests(unittest.TestCase):
    def test_collection_is_named_for_the_individual_placement(self) -> None:
        specification = collection_layout.import_placement_collection(
            placement(),
        )

        self.assertEqual(specification.label, "[2183] CapFlowerBloom")
        self.assertTrue(specification.key.startswith("PLACEMENT:"))

    def test_two_instances_of_one_asset_get_distinct_collections(self) -> None:
        first = collection_layout.import_placement_collection(
            placement(identifier="2183"),
        )
        second = collection_layout.import_placement_collection(
            placement(identifier="9217"),
        )

        self.assertNotEqual(first.key, second.key)
        self.assertEqual(first.label, "[2183] CapFlowerBloom")
        self.assertEqual(second.label, "[9217] CapFlowerBloom")

    def test_same_placement_reuses_its_stable_collection(self) -> None:
        first = collection_layout.import_placement_collection(placement())
        second = collection_layout.import_placement_collection(placement())

        self.assertEqual(first, second)

    def test_placement_context_prevents_identifier_collisions(self) -> None:
        first = collection_layout.import_placement_collection(
            placement(source_stage_name="MainStage"),
        )
        second = collection_layout.import_placement_collection(
            placement(
                source_stage_name="SubZoneStage",
                zone_path=("SubZoneStage",),
            ),
        )

        self.assertNotEqual(first.key, second.key)

    def test_missing_unit_name_falls_back_to_model_name(self) -> None:
        specification = collection_layout.import_placement_collection(
            placement(unit_config_name="", model_name="KnownModel"),
        )

        self.assertEqual(specification.label, "[2183] KnownModel")

    def test_collection_is_created_marked_and_reused_by_stable_key(
        self,
    ) -> None:
        parent = FakeCollection("Gameplay")
        collections = FakeCollections()
        specification = collection_layout.ImportPlacementCollection(
            label="[2183] CapFlowerBloom",
            key="PLACEMENT:fixture",
        )

        created = collection_layout.ensure_import_placement_collection(
            parent,
            specification,
            collections,
        )
        created.name = "Renamed by user"
        reused = collection_layout.ensure_import_placement_collection(
            parent,
            specification,
            collections,
        )

        self.assertIs(created, reused)
        self.assertEqual(collections.created, [created])
        self.assertEqual(parent.children, [created])
        self.assertEqual(created.name, "[2183] CapFlowerBloom")
        self.assertTrue(created["smo_import_generated"])
        self.assertTrue(created["smo_import_placement"])
        self.assertEqual(
            created["smo_import_placement_key"],
            "PLACEMENT:fixture",
        )
        self.assertEqual(
            created["smo_import_placement_name"],
            "[2183] CapFlowerBloom",
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
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
    identifier: str = "Obj2183",
    unit_config_name: str = "CapFlower",
    model_name: str | None = None,
) -> object:
    return SimpleNamespace(
        identifier=identifier,
        unit_config_name=unit_config_name,
        model_name=model_name,
    )


def resource(
    requested_name: str | None,
    archive_name: str | None,
    *bfres_files: str,
    components: tuple[object, ...] = (),
) -> object:
    return SimpleNamespace(
        requested_name=requested_name,
        archive_path=(
            Path("D:/romfs/ObjectData") / archive_name
            if archive_name is not None
            else None
        ),
        bfres_files=bfres_files,
        components=components,
    )


class ImportAssetCollectionTests(unittest.TestCase):
    def test_resolved_asset_groups_every_placement_under_requested_name(
        self,
    ) -> None:
        resolved = resource(
            "CapFlowerBloom",
            "CapFlowerBloom.szs",
            "CapFlowerBloom.bfres",
        )
        first = collection_layout.import_asset_collection(
            placement(identifier="Obj2183"),
            resolved,
        )
        second = collection_layout.import_asset_collection(
            placement(identifier="Obj9217"),
            resolved,
        )

        self.assertEqual(first.label, "CapFlowerBloom")
        self.assertEqual(first, second)
        self.assertTrue(first.key.startswith("ASSET:"))

    def test_different_source_models_do_not_share_a_collection(self) -> None:
        first = collection_layout.import_asset_collection(
            placement(),
            resource("Fixture", "Fixture.szs", "ModelA.bfres"),
        )
        second = collection_layout.import_asset_collection(
            placement(),
            resource("Fixture", "Fixture.szs", "ModelB.bfres"),
        )

        self.assertNotEqual(first.key, second.key)

    def test_composite_identity_is_independent_of_component_order(self) -> None:
        body = resource("GunetterBody", "GunetterBody.szs", "Body.bfres")
        head = resource("GunetterHead", "GunetterHead.szs", "Head.bfres")
        first = resource(
            "Gunetter",
            "GunetterBody.szs",
            "Body.bfres",
            components=(body, head),
        )
        reordered = resource(
            "Gunetter",
            "GunetterBody.szs",
            "Body.bfres",
            components=(head, body),
        )

        first_spec = collection_layout.import_asset_collection(
            placement(unit_config_name="Gunetter"),
            first,
        )
        second_spec = collection_layout.import_asset_collection(
            placement(unit_config_name="Gunetter"),
            reordered,
        )

        self.assertEqual(first_spec.label, "Gunetter")
        self.assertEqual(first_spec, second_spec)

    def test_unresolved_placements_fall_back_to_game_object_name(self) -> None:
        unresolved = resource(None, None)
        by_model = collection_layout.import_asset_collection(
            placement(model_name="KnownModel"),
            unresolved,
        )
        by_unit = collection_layout.import_asset_collection(
            placement(unit_config_name="MysteryActor"),
            unresolved,
        )

        self.assertEqual(by_model.label, "KnownModel")
        self.assertEqual(by_unit.label, "MysteryActor")

    def test_collection_is_created_marked_and_reused_by_stable_key(
        self,
    ) -> None:
        parent = FakeCollection("Gameplay")
        collections = FakeCollections()
        specification = collection_layout.ImportAssetCollection(
            label="CapFlowerBloom",
            key="ASSET:fixture",
        )

        created = collection_layout.ensure_import_asset_collection(
            parent,
            specification,
            collections,
        )
        created.name = "Renamed by user"
        reused = collection_layout.ensure_import_asset_collection(
            parent,
            specification,
            collections,
        )

        self.assertIs(created, reused)
        self.assertEqual(collections.created, [created])
        self.assertEqual(parent.children, [created])
        self.assertEqual(created.name, "CapFlowerBloom")
        self.assertTrue(created["smo_import_generated"])
        self.assertTrue(created["smo_import_asset"])
        self.assertEqual(created["smo_import_asset_key"], "ASSET:fixture")
        self.assertEqual(
            created["smo_import_asset_name"],
            "CapFlowerBloom",
        )


if __name__ == "__main__":
    unittest.main()

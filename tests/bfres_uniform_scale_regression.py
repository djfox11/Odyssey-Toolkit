from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_animation import read_skeletal_animations
from smo_kingdom_importer.bfres_animation_import import (
    AnimationClip,
    AnimationSource,
    import_bfres_animation,
)
from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.static_model_import import _create_armature_object
from smo_kingdom_importer.world_list import read_szs


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(romfs_root: Path) -> None:
    archive_path = romfs_root / "ObjectData" / "Kuribo.szs"
    archive = read_szs(archive_path)
    entry = next(item for item in archive.get_files() if item.name == "Kuribo.bfres")
    data = bytes(entry.data)
    model = read_static_bfres(data, include_rigging=True)[0]
    check(model.skeleton is not None, "Kuribo has no parsed skeleton")
    animation = next(
        item for item in read_skeletal_animations(data) if item.name == "Wait"
    )
    clip = AnimationClip(skeletal=animation, visibility=None)
    source = AnimationSource(
        archive_path=archive_path,
        bfres_name=entry.name,
        data=data,
        skeletal_animations=(animation,),
        visibility_animations=(),
        animations=(clip,),
    )
    collection = bpy.data.collections.new("SMO Uniform Scale Regression")
    bpy.context.scene.collection.children.link(collection)
    armature, _bone_names = _create_armature_object(
        collection,
        "Kuribo_Uniform_Scale_Regression_Armature",
        model.skeleton,
    )
    armature["smo_armature_generated"] = True
    armature["smo_source_archive"] = str(archive_path)
    armature["smo_source_bfres"] = entry.name
    armature["smo_source_model"] = model.name
    armature["smo_rig_key"] = "kuribo-uniform-scale-regression"
    import_bfres_animation(armature, source, clip)

    bpy.context.scene.frame_set(108)
    bpy.context.view_layer.update()
    cap_scale = tuple(float(value) for value in armature.pose.bones["Cap"].scale)
    check(
        max(cap_scale) - min(cap_scale) < 1e-5,
        "Wait frame 108 turned Cap's authored uniform 1.0 scale into "
        f"axis stretch {cap_scale}",
    )
    check(
        max(abs(value - 1.0) for value in cap_scale) < 1e-5,
        f"Wait frame 108 changed Cap's authored 1.0 scale to {cap_scale}",
    )
    print("BFRES uniform-scale regression passed: Cap frame 108 =", cap_scale)


if __name__ == "__main__":
    separator = sys.argv.index("--")
    run(Path(sys.argv[separator + 1]).resolve())

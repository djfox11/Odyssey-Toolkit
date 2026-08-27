from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_animation import read_skeletal_animations
from smo_kingdom_importer.world_list import read_szs


def main() -> None:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: inspect_native_animation.py -- ARCHIVE.szs")

    archive_path = Path(arguments[0]).resolve()
    archive = read_szs(archive_path)

    for entry in archive.get_files():
        if not entry.name or Path(entry.name).suffix.casefold() != ".bfres":
            continue

        animations = read_skeletal_animations(bytes(entry.data))
        print(f"{entry.name}: {len(animations)} skeletal animations")

        for animation in animations:
            curve_count = sum(len(bone.curves) for bone in animation.bones)
            print(
                f"  {animation.name}: frames=0..{animation.frame_count} "
                f"bones={len(animation.bones)} curves={curve_count} "
                f"loop={animation.looping} euler={animation.euler_xyz}"
            )


if __name__ == "__main__":
    main()

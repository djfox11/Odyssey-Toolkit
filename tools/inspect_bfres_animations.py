from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smo_kingdom_importer.world_list import read_szs


def main(path: Path) -> None:
    archive = read_szs(path)

    for entry in archive.get_files():
        data = bytes(entry.data)
        print(
            f"{entry.name} size={len(data)} "
            f"FRES={data.count(b'FRES')} FSKA={data.count(b'FSKA')}"
        )


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(arguments) != 1:
        raise SystemExit("Usage: inspect_bfres_animations.py -- ARCHIVE.szs")
    main(Path(arguments[0]).resolve())

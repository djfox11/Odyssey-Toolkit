from __future__ import annotations

import argparse
from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "smo_kingdom_importer" / "blender_manifest.toml"


def expected_tag() -> str:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    return f"v{manifest['version']}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Require a release tag to match blender_manifest.toml exactly."
    )
    parser.add_argument("tag", help="Git tag, for example v1.0.0")
    args = parser.parse_args()

    expected = expected_tag()
    if args.tag != expected:
        raise SystemExit(f"RELEASE_TAG: FAIL expected={expected} actual={args.tag}")
    print(f"RELEASE_TAG: PASS tag={args.tag}")


if __name__ == "__main__":
    main()

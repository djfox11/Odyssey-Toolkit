from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import zipfile


ROOT_FILES = ("LICENSE", "CHANGELOG.md")


def package_files(source: Path) -> tuple[tuple[Path, PurePosixPath], ...]:
    files: list[tuple[Path, PurePosixPath]] = []
    for path in sorted(source.rglob("*")):
        relative_path = path.relative_to(source)
        if not path.is_file():
            continue
        if "__pycache__" in relative_path.parts or path.suffix == ".pyc":
            continue
        files.append((path, PurePosixPath(relative_path.as_posix())))

    repository_root = source.parent
    for name in ROOT_FILES:
        path = repository_root / name
        if not path.is_file():
            raise ValueError(f"required package file does not exist: {path}")
        files.append((path, PurePosixPath(name)))
    return tuple(files)


def build_archive(source: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source_path, member_path in package_files(source):
            archive.write(source_path, member_path.as_posix())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a root-layout Odyssey Toolkit extension ZIP."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_archive(args.source, args.archive)
    print(f"Built package: {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

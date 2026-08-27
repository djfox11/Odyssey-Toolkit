from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any


_REGISTRY_PATH = Path(__file__).with_name("resolved_model_categories.json")
_ALLOWED_CATEGORIES = {
    "ENVIRONMENT",
    "CHARACTERS",
    "ENEMIES",
    "GAMEPLAY",
    "COLLECTIBLES",
    "EFFECTS",
    "LIGHTING",
    "AUDIO",
    "TECHNICAL",
    "UNCLASSIFIED",
}


def _normalise_category(value: Any) -> str:
    category = str(value).strip().upper()

    if category == "ENEMIES":
        return "CHARACTERS"

    if category not in _ALLOWED_CATEGORIES:
        raise ValueError(f"Unsupported resolved-model category: {value!r}")

    return category


@lru_cache(maxsize=1)
def resolved_model_categories() -> dict[str, str]:
    try:
        data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))

        if data.get("schema_version") != 1:
            raise ValueError(
                "Unsupported resolved-model category schema: "
                f"{data.get('schema_version')!r}"
            )

        raw_categories = data.get("archive_categories")

        if not isinstance(raw_categories, dict):
            raise TypeError("archive_categories must be a JSON object")

        categories: dict[str, str] = {}

        for archive_name, raw_category in raw_categories.items():
            key = str(archive_name).strip().casefold()

            if not key:
                raise ValueError("Resolved-model category contains an empty key")

            categories[key] = _normalise_category(raw_category)

        return categories

    except Exception as exc:
        print(
            "[Odyssey Toolkit] Bundled model categories could not be "
            f"loaded; using heuristic classification instead: {exc}"
        )
        return {}


def resolved_model_category(
    archive_path_or_name: Path | str | None,
) -> str | None:
    if archive_path_or_name is None:
        return None

    archive_name = Path(str(archive_path_or_name)).stem.strip().casefold()

    if not archive_name:
        return None

    return resolved_model_categories().get(archive_name)
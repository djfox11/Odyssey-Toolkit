from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import oead

from .actor_registry import configured_actor_registry
from .resource_rules import (
    ACTOR_COMPOSITE_RESOURCE_ALIASES,
    resource_rule_candidates,
)

if TYPE_CHECKING:
    from .stage_data import StagePlacement


@dataclass(slots=True, frozen=True)
class ObjectArchiveMetadata:
    archive_path: Path
    bfres_files: tuple[str, ...]
    byml_files: tuple[str, ...]
    init_model_references: tuple[str, ...]
    sub_actor_model_names: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class ResourceAttempt:
    source_field: str
    requested_name: str
    status: str
    archive_path: Path | None
    bfres_files: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class ResourceSuggestion:
    archive_path: Path
    score: float
    reasons: tuple[str, ...]
    inspected: bool
    bfres_files: tuple[str, ...]
    init_model_references: tuple[str, ...]
    sub_actor_model_names: tuple[str, ...]
    inspection_error: str | None = None


@dataclass(slots=True, frozen=True)
class ObjectResource:
    source_field: str | None
    requested_name: str | None
    archive_path: Path | None
    bfres_files: tuple[str, ...]
    attempts: tuple[ResourceAttempt, ...] = ()
    suggestions: tuple[ResourceSuggestion, ...] = ()
    init_model_references: tuple[str, ...] = ()
    sub_actor_model_names: tuple[str, ...] = ()
    components: tuple["ObjectResource", ...] = ()

    @property
    def has_archive(self) -> bool:
        return self.archive_path is not None or bool(self.components)

    @property
    def has_model(self) -> bool:
        return bool(self.bfres_files) or any(
            component.bfres_files for component in self.components
        )

    @property
    def model_resources(self) -> tuple["ObjectResource", ...]:
        if self.components:
            return tuple(
                component
                for component in self.components
                if component.bfres_files
            )

        return (self,) if self.bfres_files else ()


def _normalised_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _name_bigrams(value: str) -> frozenset[str]:
    if len(value) < 2:
        return frozenset({value})

    return frozenset(
        value[index : index + 2]
        for index in range(len(value) - 1)
    )


def _named_strings(value: Any, names: set[str]) -> set[str]:
    result: set[str] = set()

    if hasattr(value, "items"):
        for key, item in value.items():
            key_text = str(key)

            if key_text in names and isinstance(item, str) and item:
                result.add(item)

            result.update(_named_strings(item, names))
    elif isinstance(value, (list, tuple, oead.byml.Array)):
        for item in value:
            result.update(_named_strings(item, names))

    return result


class ObjectDataIndex:
    def __init__(self, romfs_path: Path):
        self.object_data_path = romfs_path / "ObjectData"

        if not self.object_data_path.is_dir():
            raise FileNotFoundError(
                f"Could not find ObjectData inside {romfs_path}."
            )

        self._archives = {
            path.stem.casefold(): path
            for path in self.object_data_path.glob("*.szs")
        }
        self._catalogue = tuple(
            (
                folded_stem,
                archive_path,
                _normalised_name(archive_path.stem),
                _name_bigrams(_normalised_name(archive_path.stem)),
            )
            for folded_stem, archive_path in self._archives.items()
        )
        self._actor_registry = configured_actor_registry(romfs_path)
        self._metadata_cache: dict[Path, ObjectArchiveMetadata] = {}
        self._suggestion_cache: dict[
            tuple[str, str, str, str],
            tuple[ResourceSuggestion, ...],
        ] = {}
        self._observed_unit_models: dict[tuple[str, str], tuple[str, ...]] = {}
        self._observed_parameter_models: dict[tuple[str, str], tuple[str, ...]] = {}

    def learn_stage_placements(
        self,
        placements: Iterable["StagePlacement"],
    ) -> None:
        by_unit: dict[tuple[str, str], set[str]] = {}
        by_parameter: dict[tuple[str, str], set[str]] = {}
        self._suggestion_cache.clear()

        for placement in placements:
            model_name = (placement.model_name or "").strip()

            if not model_name:
                continue

            by_unit.setdefault(
                (
                    placement.unit_config_name,
                    placement.source_stage_name,
                ),
                set(),
            ).add(model_name)
            parameter_name = str(
                placement.unit_config.get("ParameterConfigName") or ""
            ).strip()

            if parameter_name:
                by_parameter.setdefault(
                    (parameter_name, placement.source_stage_name),
                    set(),
                ).add(model_name)

        self._observed_unit_models = {
            key: tuple(sorted(values))
            for key, values in by_unit.items()
        }
        self._observed_parameter_models = {
            key: tuple(sorted(values))
            for key, values in by_parameter.items()
        }

    def _inspect_archive(self, archive_path: Path) -> ObjectArchiveMetadata:
        cached = self._metadata_cache.get(archive_path)

        if cached is not None:
            return cached

        try:
            data = archive_path.read_bytes()

            if data.startswith(b"Yaz0"):
                data = bytes(oead.yaz0.decompress(data))

            if data.startswith(b"FRES"):
                metadata = ObjectArchiveMetadata(
                    archive_path=archive_path,
                    bfres_files=(archive_path.name,),
                    byml_files=(),
                    init_model_references=(),
                    sub_actor_model_names=(),
                )
            else:
                archive = oead.Sarc(data)
                files = tuple(archive.get_files())
                bfres_files = tuple(
                    entry.name
                    for entry in files
                    if entry.name.casefold().endswith((".bfres", ".sbfres"))
                    or bytes(entry.data[:4]) == b"FRES"
                )
                byml_files = tuple(
                    entry.name
                    for entry in files
                    if entry.name.casefold().endswith((".byml", ".byaml"))
                    or bytes(entry.data[:2]) in {b"BY", b"YB"}
                )
                init_model_references: set[str] = set()
                sub_actor_model_names: set[str] = set()

                for entry in files:
                    if entry.name not in {"InitModel.byml", "InitSubActor.byml"}:
                        continue

                    try:
                        document = oead.byml.from_binary(bytes(entry.data))
                    except Exception:
                        continue

                    if entry.name == "InitModel.byml":
                        init_model_references.update(
                            _named_strings(
                                document,
                                {"ArchiveName", "ArcName", "ModelName", "TextureArc"},
                            )
                        )
                    else:
                        sub_actor_model_names.update(
                            _named_strings(document, {"ModelName"})
                        )

                metadata = ObjectArchiveMetadata(
                    archive_path=archive_path,
                    bfres_files=bfres_files,
                    byml_files=byml_files,
                    init_model_references=tuple(
                        sorted(init_model_references)
                    ),
                    sub_actor_model_names=tuple(
                        sorted(sub_actor_model_names)
                    ),
                )

        except Exception as exc:
            raise RuntimeError(
                f"Failed to inspect ObjectData archive {archive_path}: {exc}"
            ) from exc

        self._metadata_cache[archive_path] = metadata
        return metadata

    def _bfres_files(self, archive_path: Path) -> tuple[str, ...]:
        return self._inspect_archive(archive_path).bfres_files

    def archive_metadata(self, name: str) -> ObjectArchiveMetadata | None:
        archive_path = self._archives.get(name.strip().casefold())

        if archive_path is None:
            return None

        return self._inspect_archive(archive_path)

    def _observed_candidates(
        self,
        placement: "StagePlacement",
        parameter_name: str,
    ) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        unit_models = self._observed_unit_models.get(
            (
                placement.unit_config_name,
                placement.source_stage_name,
            ),
            (),
        )

        if len(unit_models) == 1:
            result.append(("ObservedUnitModelName", unit_models[0]))

        parameter_models = self._observed_parameter_models.get(
            (parameter_name, placement.source_stage_name),
            (),
        )

        if len(parameter_models) == 1:
            result.append(
                ("ObservedParameterModelName", parameter_models[0])
            )

        return tuple(result)

    def _registry_candidates(
        self,
        placement: "StagePlacement",
        parameter_name: str,
    ) -> tuple[tuple[str, str], ...]:
        if self._actor_registry is None:
            return ()

        candidate = self._actor_registry.candidate(
            placement.unit_config_name,
            parameter_name,
        )
        return (candidate,) if candidate is not None else ()

    def _suggestions(
        self,
        placement: "StagePlacement",
        parameter_name: str,
        attempted_names: set[str],
    ) -> tuple[ResourceSuggestion, ...]:
        cache_key = (
            placement.unit_config_name,
            placement.model_name or "",
            parameter_name,
            placement.source_stage_name,
        )
        cached = self._suggestion_cache.get(cache_key)

        if cached is not None:
            return cached

        source_names = tuple(
            (
                field,
                name,
                _normalised_name(name),
                _name_bigrams(_normalised_name(name)),
            )
            for field, name in (
                ("ModelName", placement.model_name or ""),
                ("UnitConfigName", placement.unit_config_name),
                ("ParameterConfigName", parameter_name),
            )
            if name
        )
        stage_family = placement.source_stage_name.partition("World")[0]
        shortlist: list[
            tuple[float, str, Path, str, tuple[str, ...]]
        ] = []

        for (
            folded_stem,
            archive_path,
            normalised_stem,
            stem_bigrams,
        ) in self._catalogue:
            if folded_stem in attempted_names:
                continue

            best_score = 0.0
            best_field = ""

            for source_field, _, _, source_bigrams in source_names:
                denominator = len(source_bigrams) + len(stem_bigrams)
                score = (
                    2.0 * len(source_bigrams & stem_bigrams) / denominator
                    if denominator
                    else 0.0
                )

                if score > best_score:
                    best_score = score
                    best_field = source_field

            reasons = [f"name_similarity:{best_field}"]

            if (
                stage_family
                and len(stage_family) >= 3
                and stage_family.casefold() in archive_path.stem.casefold()
            ):
                best_score = min(1.0, best_score + 0.04)
                reasons.append(f"stage_family:{stage_family}")

            if best_score >= 0.25:
                shortlist.append(
                    (
                        best_score,
                        archive_path.stem.casefold(),
                        archive_path,
                        normalised_stem,
                        tuple(reasons),
                    )
                )

        shortlist.sort(key=lambda item: (-item[0], item[1]))
        ranked: list[tuple[float, str, Path, tuple[str, ...]]] = []

        for _, folded_stem, archive_path, normalised_stem, reasons in (
            shortlist[:48]
        ):
            best_score = max(
                SequenceMatcher(
                    None,
                    source_name,
                    normalised_stem,
                ).ratio()
                for _, _, source_name, _ in source_names
            )

            if any(reason.startswith("stage_family:") for reason in reasons):
                best_score = min(1.0, best_score + 0.04)

            if best_score >= 0.70:
                ranked.append(
                    (best_score, folded_stem, archive_path, reasons)
                )

        ranked.sort(key=lambda item: (-item[0], item[1]))
        suggestions: list[ResourceSuggestion] = []

        for score, _, archive_path, reasons in ranked[:5]:
            inspected = score >= 0.90
            metadata: ObjectArchiveMetadata | None = None
            error: str | None = None

            if inspected:
                try:
                    metadata = self._inspect_archive(archive_path)
                except Exception as exc:
                    error = str(exc)

            suggestions.append(
                ResourceSuggestion(
                    archive_path=archive_path,
                    score=score,
                    reasons=reasons,
                    inspected=inspected,
                    bfres_files=(
                        metadata.bfres_files if metadata is not None else ()
                    ),
                    init_model_references=(
                        metadata.init_model_references
                        if metadata is not None
                        else ()
                    ),
                    sub_actor_model_names=(
                        metadata.sub_actor_model_names
                        if metadata is not None
                        else ()
                    ),
                    inspection_error=error,
                )
            )

        result = tuple(suggestions)
        self._suggestion_cache[cache_key] = result
        return result
    def resolve(
        self,
        placement: "StagePlacement",
        *,
        include_suggestions: bool = False,
    ) -> ObjectResource:
        parameter_name = str(
            placement.unit_config.get("ParameterConfigName") or ""
        ).strip()
        primary_candidates = (
            ("ModelName", placement.model_name or ""),
            ("UnitConfigName", placement.unit_config_name),
            ("ParameterConfigName", parameter_name),
            *self._observed_candidates(placement, parameter_name),
        )
        fallback_candidates = (
            *resource_rule_candidates(placement),
            *self._registry_candidates(placement, parameter_name),
        )
        attempts: list[ResourceAttempt] = []
        matches: list[ObjectResource] = []
        seen_names: set[str] = set()

        def try_candidates(
            candidates: Iterable[tuple[str, str]],
        ) -> ObjectResource | None:
            for source_field, candidate_name in candidates:
                name = candidate_name.strip()
                folded_name = name.casefold()

                if not name or folded_name in seen_names:
                    continue

                seen_names.add(folded_name)
                archive_path = self._archives.get(folded_name)

                if archive_path is None:
                    attempts.append(
                        ResourceAttempt(
                            source_field=source_field,
                            requested_name=name,
                            status="MISSING_ARCHIVE",
                            archive_path=None,
                            bfres_files=(),
                        )
                    )
                    continue

                metadata = self._inspect_archive(archive_path)
                status = (
                    "ARCHIVE_WITH_MODEL"
                    if metadata.bfres_files
                    else "ARCHIVE_WITHOUT_MODEL"
                )
                attempts.append(
                    ResourceAttempt(
                        source_field=source_field,
                        requested_name=name,
                        status=status,
                        archive_path=archive_path,
                        bfres_files=metadata.bfres_files,
                    )
                )
                resource = ObjectResource(
                    source_field=source_field,
                    requested_name=name,
                    archive_path=archive_path,
                    bfres_files=metadata.bfres_files,
                    init_model_references=metadata.init_model_references,
                    sub_actor_model_names=metadata.sub_actor_model_names,
                )
                matches.append(resource)

                if resource.has_model:
                    return ObjectResource(
                        source_field=resource.source_field,
                        requested_name=resource.requested_name,
                        archive_path=resource.archive_path,
                        bfres_files=resource.bfres_files,
                        attempts=tuple(attempts),
                        init_model_references=resource.init_model_references,
                        sub_actor_model_names=resource.sub_actor_model_names,
                    )

            return None

        resolved = try_candidates(primary_candidates)

        if resolved is not None:
            return resolved

        component_names = ACTOR_COMPOSITE_RESOURCE_ALIASES.get(
            placement.unit_config_name,
            (),
        )

        if component_names:
            components: list[ObjectResource] = []

            for name in component_names:
                folded_name = name.casefold()
                seen_names.add(folded_name)
                archive_path = self._archives.get(folded_name)

                if archive_path is None:
                    attempts.append(
                        ResourceAttempt(
                            source_field="CompositeActorResource",
                            requested_name=name,
                            status="MISSING_ARCHIVE",
                            archive_path=None,
                            bfres_files=(),
                        )
                    )
                    components.clear()
                    break

                metadata = self._inspect_archive(archive_path)
                status = (
                    "ARCHIVE_WITH_MODEL"
                    if metadata.bfres_files
                    else "ARCHIVE_WITHOUT_MODEL"
                )
                attempts.append(
                    ResourceAttempt(
                        source_field="CompositeActorResource",
                        requested_name=name,
                        status=status,
                        archive_path=archive_path,
                        bfres_files=metadata.bfres_files,
                    )
                )

                if not metadata.bfres_files:
                    components.clear()
                    break

                components.append(
                    ObjectResource(
                        source_field="CompositeActorResource",
                        requested_name=name,
                        archive_path=archive_path,
                        bfres_files=metadata.bfres_files,
                        init_model_references=metadata.init_model_references,
                        sub_actor_model_names=metadata.sub_actor_model_names,
                    )
                )

            if len(components) == len(component_names):
                first = components[0]
                return ObjectResource(
                    source_field="CompositeActorResource",
                    requested_name=placement.unit_config_name,
                    archive_path=first.archive_path,
                    bfres_files=first.bfres_files,
                    attempts=tuple(attempts),
                    init_model_references=tuple(
                        dict.fromkeys(
                            reference
                            for component in components
                            for reference in component.init_model_references
                        )
                    ),
                    sub_actor_model_names=tuple(
                        dict.fromkeys(
                            reference
                            for component in components
                            for reference in component.sub_actor_model_names
                        )
                    ),
                    components=tuple(components),
                )

        resolved = try_candidates(fallback_candidates)

        if resolved is not None:
            return resolved

        suggestions = (
            self._suggestions(placement, parameter_name, seen_names)
            if include_suggestions
            else ()
        )

        if matches:
            first = matches[0]
            return ObjectResource(
                source_field=first.source_field,
                requested_name=first.requested_name,
                archive_path=first.archive_path,
                bfres_files=first.bfres_files,
                attempts=tuple(attempts),
                suggestions=suggestions,
                init_model_references=first.init_model_references,
                sub_actor_model_names=first.sub_actor_model_names,
            )

        return ObjectResource(
            source_field=None,
            requested_name=None,
            archive_path=None,
            bfres_files=(),
            attempts=tuple(attempts),
            suggestions=suggestions,
        )
_OBJECT_DATA_INDEX_CACHE: dict[
    Path,
    tuple[int, ObjectDataIndex],
] = {}


def get_object_data_index(romfs_path: Path) -> ObjectDataIndex:
    root = romfs_path.resolve()
    object_data_path = root / "ObjectData"

    if not object_data_path.is_dir():
        raise FileNotFoundError(f"Could not find ObjectData inside {root}.")

    modified_ns = object_data_path.stat().st_mtime_ns
    cached = _OBJECT_DATA_INDEX_CACHE.get(root)

    if cached is not None and cached[0] == modified_ns:
        return cached[1]

    index = ObjectDataIndex(root)
    _OBJECT_DATA_INDEX_CACHE[root] = (modified_ns, index)
    return index


def clear_object_data_index_cache() -> None:
    _OBJECT_DATA_INDEX_CACHE.clear()

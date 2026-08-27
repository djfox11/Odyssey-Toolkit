from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


from .stage_data import (
    STAGE_LAYERS,
    _is_byml_mapping,
    _is_byml_sequence,
    _mapping_get,
    _read_stage_layer_document,
)


REGISTRY_SCHEMA_VERSION = 2


@dataclass(slots=True, frozen=True)
class StageArchiveTask:
    archive_path: Path
    stage_name: str
    stage_layer: str


@dataclass(slots=True, frozen=True)
class RegistryModelEvidence:
    model_name: str
    occurrence_count: int
    source_stages: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class RegistryActorEvidence:
    unit_config_name: str
    parameter_config_name: str
    models: tuple[RegistryModelEvidence, ...]
    modelless_occurrence_count: int
    modelless_source_stages: tuple[str, ...]
    modelless_stage_layers: tuple[str, ...]
    modelless_categories: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class ActorRegistry:
    romfs_key: str
    created_utc: str
    archive_inventory_digest: str
    archives_scanned: int
    scenario_count: int
    placement_count: int
    placement_with_model_count: int
    build_errors: tuple[str, ...]
    actors: tuple[RegistryActorEvidence, ...]
    _models_by_signature: dict[
        tuple[str, str], tuple[str, ...]
    ] = field(init=False, repr=False, compare=False)
    _models_by_unit: dict[str, tuple[str, ...]] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        by_signature: dict[
            tuple[str, str],
            tuple[str, ...],
        ] = {}
        by_unit: dict[str, set[str]] = {}

        for actor in self.actors:
            unit_key = actor.unit_config_name.casefold()
            parameter_key = actor.parameter_config_name.casefold()
            model_names = tuple(
                sorted(
                    {model.model_name for model in actor.models},
                    key=str.casefold,
                )
            )
            by_signature[(unit_key, parameter_key)] = model_names
            by_unit.setdefault(unit_key, set()).update(model_names)

        object.__setattr__(self, "_models_by_signature", by_signature)
        object.__setattr__(
            self,
            "_models_by_unit",
            {
                key: tuple(sorted(values, key=str.casefold))
                for key, values in by_unit.items()
            },
        )

    @property
    def actor_signature_count(self) -> int:
        return len(self.actors)

    @property
    def unambiguous_signature_count(self) -> int:
        return sum(len(actor.models) == 1 for actor in self.actors)

    def models_for_signature(
        self,
        unit_config_name: str,
        parameter_config_name: str,
    ) -> tuple[str, ...]:
        return self._models_by_signature.get(
            (
                unit_config_name.strip().casefold(),
                parameter_config_name.strip().casefold(),
            ),
            (),
        )

    def models_for_unit(self, unit_config_name: str) -> tuple[str, ...]:
        return self._models_by_unit.get(unit_config_name.strip().casefold(), ())

    def candidate(
        self,
        unit_config_name: str,
        parameter_config_name: str,
    ) -> tuple[str, str] | None:
        unit_key = unit_config_name.strip().casefold()
        parameter_key = parameter_config_name.strip().casefold()

        if not unit_key:
            return None

        signature_models = self._models_by_signature.get(
            (unit_key, parameter_key),
            (),
        )

        if len(signature_models) == 1:
            return ("ActorRegistryParameterModel", signature_models[0])

        unit_models = self._models_by_unit.get(unit_key, ())

        if len(unit_models) == 1:
            return ("ActorRegistryUnitModel", unit_models[0])

        return None

    def to_data(self) -> dict[str, Any]:
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "romfs_key": self.romfs_key,
            "created_utc": self.created_utc,
            "archive_inventory_digest": self.archive_inventory_digest,
            "summary": {
                "archives_scanned": self.archives_scanned,
                "scenario_count": self.scenario_count,
                "placement_count": self.placement_count,
                "placement_with_model_count": self.placement_with_model_count,
                "actor_signature_count": self.actor_signature_count,
                "unambiguous_signature_count": (
                    self.unambiguous_signature_count
                ),
                "build_error_count": len(self.build_errors),
            },
            "build_errors": list(self.build_errors),
            "actors": [
                {
                    "unit_config_name": actor.unit_config_name,
                    "parameter_config_name": actor.parameter_config_name,
                    "modelless_occurrence_count": (
                        actor.modelless_occurrence_count
                    ),
                    "modelless_source_stages": list(
                        actor.modelless_source_stages
                    ),
                    "modelless_stage_layers": list(
                        actor.modelless_stage_layers
                    ),
                    "modelless_categories": list(
                        actor.modelless_categories
                    ),
                    "models": [
                        {
                            "model_name": model.model_name,
                            "occurrence_count": model.occurrence_count,
                            "source_stages": list(model.source_stages),
                        }
                        for model in actor.models
                    ],
                }
                for actor in self.actors
            ],
        }


@dataclass(slots=True, frozen=True)
class RegistryFileStatus:
    path: Path
    exists: bool
    valid: bool
    message: str
    registry: ActorRegistry | None = None


def _normalised_root(romfs_path: Path) -> Path:
    return romfs_path.resolve()


def romfs_key(romfs_path: Path) -> str:
    normalised = str(_normalised_root(romfs_path)).replace("\\", "/").casefold()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:20]


def registry_cache_path(romfs_path: Path, cache_directory: Path) -> Path:
    return cache_directory / f"actor_registry_{romfs_key(romfs_path)}.json"


def _inventory_digest(romfs_path: Path) -> str:
    digest = hashlib.sha256()

    for directory_name in ("StageData", "ObjectData"):
        directory = romfs_path / directory_name

        if not directory.is_dir():
            continue

        for path in sorted(
            directory.glob("*.szs"),
            key=lambda item: item.name.casefold(),
        ):
            stat = path.stat()
            digest.update(directory_name.encode("utf-8"))
            digest.update(b"/")
            digest.update(path.name.casefold().encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(b"\n")

    return digest.hexdigest()


def discover_stage_archives(romfs_path: Path) -> tuple[StageArchiveTask, ...]:
    root = _normalised_root(romfs_path)
    stage_data_path = root / "StageData"

    if not stage_data_path.is_dir():
        raise FileNotFoundError(f"Could not find StageData inside {root}.")

    if not (root / "ObjectData").is_dir():
        raise FileNotFoundError(f"Could not find ObjectData inside {root}.")

    tasks: list[StageArchiveTask] = []

    for stage_layer in STAGE_LAYERS:
        suffix = f"{stage_layer}.szs"

        for archive_path in stage_data_path.glob(f"*{suffix}"):
            stage_name = archive_path.name[: -len(suffix)]

            if stage_name:
                tasks.append(
                    StageArchiveTask(
                        archive_path=archive_path,
                        stage_name=stage_name,
                        stage_layer=stage_layer,
                    )
                )

    tasks.sort(
        key=lambda task: (
            task.stage_name.casefold(),
            STAGE_LAYERS.index(task.stage_layer),
        )
    )

    if not tasks:
        raise RuntimeError(
            f"No StageData archives were found inside {stage_data_path}."
        )

    return tuple(tasks)


def _optional_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _walk_placement_records(scenario: Any) -> Iterable[tuple[Any, str]]:
    if not _is_byml_mapping(scenario):
        return

    roots: list[tuple[Any, str]] = []

    for category_name, values in scenario.items():
        if not _is_byml_sequence(values):
            continue

        roots.extend(
            (value, str(category_name))
            for value in values
            if _is_byml_mapping(value)
        )

    seen_identifiers: set[str] = set()

    def visit(record: Any, category_name: str) -> Iterable[tuple[Any, str]]:
        identifier = _optional_text(_mapping_get(record, "Id", ""))

        if identifier:
            if identifier in seen_identifiers:
                return

            seen_identifiers.add(identifier)

        if _optional_text(_mapping_get(record, "UnitConfigName", "")):
            yield record, category_name

        links = _mapping_get(record, "Links")

        if not _is_byml_mapping(links):
            return

        for _, targets in links.items():
            if not _is_byml_sequence(targets):
                continue

            for target in targets:
                if _is_byml_mapping(target):
                    yield from visit(target, category_name)

    for root, category_name in roots:
        yield from visit(root, category_name)

class ActorRegistryBuilder:
    def __init__(
        self,
        romfs_path: Path,
        *,
        tasks: Iterable[StageArchiveTask] | None = None,
    ) -> None:
        self.romfs_path = _normalised_root(romfs_path)
        self.tasks = tuple(tasks) if tasks is not None else discover_stage_archives(
            self.romfs_path
        )
        self.archive_inventory_digest = _inventory_digest(self.romfs_path)
        self.current_index = 0
        self.scenario_count = 0
        self.placement_count = 0
        self.placement_with_model_count = 0
        self.build_errors: list[str] = []
        self._evidence: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    @property
    def complete(self) -> bool:
        return self.current_index >= self.total_count

    @property
    def current_label(self) -> str:
        if self.complete:
            return "Finalising actor registry"

        task = self.tasks[self.current_index]
        return task.archive_path.name

    def _record(
        self,
        record: Any,
        source_stage_name: str,
        stage_layer: str,
        placement_category: str,
    ) -> None:
        unit_config_name = _optional_text(_mapping_get(record, "UnitConfigName", ""))

        if not unit_config_name:
            return

        model_name = _optional_text(_mapping_get(record, "ModelName", ""))
        unit_config = _mapping_get(record, "UnitConfig")
        parameter_config_name = (
            _optional_text(_mapping_get(unit_config, "ParameterConfigName", ""))
            if _is_byml_mapping(unit_config)
            else ""
        )
        key = (unit_config_name, parameter_config_name)
        evidence = self._evidence.setdefault(
            key,
            {
                "models": {},
                "modelless_occurrence_count": 0,
                "modelless_source_stages": set(),
                "modelless_stage_layers": set(),
                "modelless_categories": set(),
            },
        )
        self.placement_count += 1

        if not model_name:
            evidence["modelless_occurrence_count"] += 1
            evidence["modelless_source_stages"].add(source_stage_name)
            evidence["modelless_stage_layers"].add(stage_layer)

            if placement_category:
                evidence["modelless_categories"].add(placement_category)

            return

        self.placement_with_model_count += 1
        model_evidence = evidence["models"].setdefault(
            model_name,
            {"occurrence_count": 0, "source_stages": set()},
        )
        model_evidence["occurrence_count"] += 1
        model_evidence["source_stages"].add(source_stage_name)

    def _scan_scenario(
        self,
        scenario: Any,
        source_stage_name: str,
        stage_layer: str,
    ) -> None:
        if not _is_byml_mapping(scenario):
            return

        self.scenario_count += 1

        for record, placement_category in _walk_placement_records(scenario):
            self._record(
                record,
                source_stage_name,
                stage_layer,
                placement_category,
            )

    def process_next(self) -> bool:
        if self.complete:
            return False

        task = self.tasks[self.current_index]

        try:
            _, byml_name, document = _read_stage_layer_document(
                self.romfs_path,
                task.stage_name,
                task.stage_layer,
            )

            if document is None:
                pass
            elif _is_byml_sequence(document):
                for scenario in document:
                    self._scan_scenario(
                        scenario, task.stage_name, task.stage_layer
                    )
            else:
                raise TypeError(
                    f"Expected {byml_name}'s root node to be an array or null, "
                    f"but got {type(document).__name__}."
                )
        except Exception as exc:
            self.build_errors.append(f"{task.archive_path.name}: {exc}")
        finally:
            self.current_index += 1

        return not self.complete

    def finish(self) -> ActorRegistry:
        if not self.complete:
            raise RuntimeError("Actor registry build has not processed every archive.")

        current_inventory_digest = _inventory_digest(self.romfs_path)

        if current_inventory_digest != self.archive_inventory_digest:
            raise RuntimeError(
                "ROMFS StageData or ObjectData changed during the registry build."
            )
        actors: list[RegistryActorEvidence] = []

        for (
            unit_config_name,
            parameter_config_name,
        ), evidence in self._evidence.items():
            models = tuple(
                RegistryModelEvidence(
                    model_name=model_name,
                    occurrence_count=int(model_data["occurrence_count"]),
                    source_stages=tuple(
                        sorted(model_data["source_stages"], key=str.casefold)
                    ),
                )
                for model_name, model_data in sorted(
                    evidence["models"].items(),
                    key=lambda item: item[0].casefold(),
                )
            )
            actors.append(
                RegistryActorEvidence(
                    unit_config_name=unit_config_name,
                    parameter_config_name=parameter_config_name,
                    models=models,
                    modelless_occurrence_count=int(
                        evidence["modelless_occurrence_count"]
                    ),
                    modelless_source_stages=tuple(
                        sorted(
                            evidence["modelless_source_stages"],
                            key=str.casefold,
                        )
                    ),
                    modelless_stage_layers=tuple(
                        sorted(
                            evidence["modelless_stage_layers"],
                            key=str.casefold,
                        )
                    ),
                    modelless_categories=tuple(
                        sorted(
                            evidence["modelless_categories"],
                            key=str.casefold,
                        )
                    ),
                )
            )

        actors.sort(
            key=lambda actor: (
                actor.unit_config_name.casefold(),
                actor.parameter_config_name.casefold(),
            )
        )
        return ActorRegistry(
            romfs_key=romfs_key(self.romfs_path),
            created_utc=datetime.now(timezone.utc).isoformat(),
            archive_inventory_digest=self.archive_inventory_digest,
            archives_scanned=self.total_count,
            scenario_count=self.scenario_count,
            placement_count=self.placement_count,
            placement_with_model_count=self.placement_with_model_count,
            build_errors=tuple(self.build_errors),
            actors=tuple(actors),
        )


def _expect_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"Actor registry {name} must be a non-negative integer.")

    return value


def _expect_text_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"Actor registry {name} must be a list of strings.")

    return tuple(value)


def actor_registry_from_data(data: Any) -> ActorRegistry:
    if not isinstance(data, dict):
        raise TypeError("Actor registry root must be a JSON object.")

    if data.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported actor registry schema version: "
            f"{data.get('schema_version')!r}."
        )

    summary = data.get("summary")
    actors_data = data.get("actors")

    if not isinstance(summary, dict) or not isinstance(actors_data, list):
        raise ValueError("Actor registry is missing its summary or actor records.")

    actors: list[RegistryActorEvidence] = []

    for actor_data in actors_data:
        if not isinstance(actor_data, dict):
            raise TypeError("Actor registry actor entries must be JSON objects.")

        unit_config_name = _optional_text(actor_data.get("unit_config_name"))
        parameter_config_name = _optional_text(
            actor_data.get("parameter_config_name")
        )
        models_data = actor_data.get("models")

        if not unit_config_name or not isinstance(models_data, list):
            raise ValueError("Actor registry contains an invalid actor signature.")

        models: list[RegistryModelEvidence] = []

        for model_data in models_data:
            if not isinstance(model_data, dict):
                raise TypeError("Actor registry model entries must be JSON objects.")

            model_name = _optional_text(model_data.get("model_name"))
            source_stages = model_data.get("source_stages")

            if not model_name or not isinstance(source_stages, list) or not all(
                isinstance(stage, str) and stage for stage in source_stages
            ):
                raise ValueError("Actor registry contains invalid model evidence.")

            models.append(
                RegistryModelEvidence(
                    model_name=model_name,
                    occurrence_count=_expect_int(
                        model_data.get("occurrence_count"),
                        "model occurrence count",
                    ),
                    source_stages=tuple(source_stages),
                )
            )

        actors.append(
            RegistryActorEvidence(
                unit_config_name=unit_config_name,
                parameter_config_name=parameter_config_name,
                models=tuple(models),
                modelless_occurrence_count=_expect_int(
                    actor_data.get("modelless_occurrence_count"),
                    "modelless occurrence count",
                ),
                modelless_source_stages=_expect_text_list(
                    actor_data.get("modelless_source_stages"),
                    "modelless source stages",
                ),
                modelless_stage_layers=_expect_text_list(
                    actor_data.get("modelless_stage_layers"),
                    "modelless stage layers",
                ),
                modelless_categories=_expect_text_list(
                    actor_data.get("modelless_categories"),
                    "modelless placement categories",
                ),
            )
        )

    build_errors = data.get("build_errors", [])

    if not isinstance(build_errors, list) or not all(
        isinstance(error, str) for error in build_errors
    ):
        raise ValueError("Actor registry build errors must be strings.")

    return ActorRegistry(
        romfs_key=_optional_text(data.get("romfs_key")),
        created_utc=_optional_text(data.get("created_utc")),
        archive_inventory_digest=_optional_text(
            data.get("archive_inventory_digest")
        ),
        archives_scanned=_expect_int(
            summary.get("archives_scanned"), "archive count"
        ),
        scenario_count=_expect_int(summary.get("scenario_count"), "scenario count"),
        placement_count=_expect_int(
            summary.get("placement_count"), "placement count"
        ),
        placement_with_model_count=_expect_int(
            summary.get("placement_with_model_count"),
            "placement-with-model count",
        ),
        build_errors=tuple(build_errors),
        actors=tuple(actors),
    )


def save_actor_registry(
    registry: ActorRegistry,
    romfs_path: Path,
    cache_directory: Path,
) -> Path:
    cache_directory.mkdir(parents=True, exist_ok=True)
    path = registry_cache_path(romfs_path, cache_directory)
    temporary_path = path.with_name(path.name + ".tmp")

    try:
        temporary_path.write_text(
            json.dumps(registry.to_data(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    finally:
        if temporary_path.is_file():
            temporary_path.unlink()

    clear_actor_registry_runtime_cache()
    return path


def load_actor_registry(
    romfs_path: Path,
    cache_directory: Path,
) -> ActorRegistry:
    path = registry_cache_path(romfs_path, cache_directory)
    registry = actor_registry_from_data(
        json.loads(path.read_text(encoding="utf-8"))
    )
    expected_key = romfs_key(romfs_path)

    if registry.romfs_key != expected_key:
        raise ValueError(
            "Actor registry belongs to a different ROMFS location."
        )

    if registry.archive_inventory_digest != _inventory_digest(romfs_path):
        raise ValueError(
            "ROMFS StageData or ObjectData has changed; rebuild the registry."
        )

    return registry


_RUNTIME_CACHE_DIRECTORY: Path | None = None
_RUNTIME_ENABLED = True
_RUNTIME_REGISTRIES: dict[
    tuple[Path, Path],
    tuple[int, ActorRegistry],
] = {}
_REPORTED_LOAD_ERRORS: set[Path] = set()
_FILE_STATUS_CACHE: dict[
    tuple[Path, Path],
    tuple[int, RegistryFileStatus],
] = {}


def configure_actor_registry(
    cache_directory: Path | None,
    *,
    enabled: bool,
) -> None:
    global _RUNTIME_CACHE_DIRECTORY, _RUNTIME_ENABLED
    resolved_directory = (
        cache_directory.resolve() if cache_directory is not None else None
    )

    if (
        resolved_directory != _RUNTIME_CACHE_DIRECTORY
        or bool(enabled) != _RUNTIME_ENABLED
    ):
        _RUNTIME_REGISTRIES.clear()
        _REPORTED_LOAD_ERRORS.clear()

    _RUNTIME_CACHE_DIRECTORY = resolved_directory
    _RUNTIME_ENABLED = bool(enabled)


def clear_actor_registry_runtime_cache() -> None:
    _RUNTIME_REGISTRIES.clear()
    _REPORTED_LOAD_ERRORS.clear()
    _FILE_STATUS_CACHE.clear()


def configured_actor_registry(romfs_path: Path) -> ActorRegistry | None:
    if not _RUNTIME_ENABLED or _RUNTIME_CACHE_DIRECTORY is None:
        return None

    root = _normalised_root(romfs_path)
    path = registry_cache_path(root, _RUNTIME_CACHE_DIRECTORY)

    if not path.is_file():
        return None

    modified_ns = path.stat().st_mtime_ns
    cache_key = (root, path)
    cached = _RUNTIME_REGISTRIES.get(cache_key)

    if cached is not None and cached[0] == modified_ns:
        return cached[1]

    status_cached = _FILE_STATUS_CACHE.get(cache_key)

    if status_cached is not None and status_cached[0] == modified_ns:
        status = status_cached[1]

        if status.registry is None:
            return None

        _RUNTIME_REGISTRIES[cache_key] = (modified_ns, status.registry)
        return status.registry

    try:
        registry = load_actor_registry(root, _RUNTIME_CACHE_DIRECTORY)
    except Exception as exc:
        if path not in _REPORTED_LOAD_ERRORS:
            print(
                "[Odyssey Toolkit] Ignoring invalid actor registry "
                f"{path}: {exc}"
            )
            _REPORTED_LOAD_ERRORS.add(path)
        return None

    _RUNTIME_REGISTRIES[cache_key] = (modified_ns, registry)
    return registry


def cached_registry_file_status(
    romfs_path: Path,
    cache_directory: Path,
) -> RegistryFileStatus:
    """Return display status without parsing JSON or inventorying the ROMFS."""
    root = _normalised_root(romfs_path)
    path = registry_cache_path(root, cache_directory)

    if not path.is_file():
        return RegistryFileStatus(
            path=path,
            exists=False,
            valid=False,
            message="No actor registry has been built for this ROMFS.",
        )

    modified_ns = path.stat().st_mtime_ns
    cache_key = (root, path)
    cached = _FILE_STATUS_CACHE.get(cache_key)

    if cached is not None and cached[0] == modified_ns:
        return cached[1]

    runtime_cached = _RUNTIME_REGISTRIES.get(cache_key)

    if runtime_cached is not None and runtime_cached[0] == modified_ns:
        registry = runtime_cached[1]
        status = RegistryFileStatus(
            path=path,
            exists=True,
            valid=True,
            message=(
                f"{registry.actor_signature_count:,} actor signatures; "
                f"{registry.unambiguous_signature_count:,} unambiguous"
            ),
            registry=registry,
        )
        _FILE_STATUS_CACHE[cache_key] = (modified_ns, status)
        return status

    return RegistryFileStatus(
        path=path,
        exists=True,
        valid=False,
        message="Registry status not checked",
    )


def registry_file_status(
    romfs_path: Path,
    cache_directory: Path,
    *,
    refresh: bool = False,
) -> RegistryFileStatus:
    root = _normalised_root(romfs_path)
    path = registry_cache_path(root, cache_directory)

    if not path.is_file():
        return RegistryFileStatus(
            path=path,
            exists=False,
            valid=False,
            message="No actor registry has been built for this ROMFS.",
        )

    modified_ns = path.stat().st_mtime_ns
    cache_key = (root, path)
    cached = _FILE_STATUS_CACHE.get(cache_key)

    if not refresh and cached is not None and cached[0] == modified_ns:
        return cached[1]

    try:
        registry = load_actor_registry(root, cache_directory)
    except Exception as exc:
        status = RegistryFileStatus(
            path=path,
            exists=True,
            valid=False,
            message=f"Registry could not be loaded: {exc}",
        )
    else:
        status = RegistryFileStatus(
            path=path,
            exists=True,
            valid=True,
            message=(
                f"{registry.actor_signature_count:,} actor signatures; "
                f"{registry.unambiguous_signature_count:,} unambiguous"
            ),
            registry=registry,
        )

    _FILE_STATUS_CACHE[cache_key] = (modified_ns, status)
    return status

def remove_actor_registry(romfs_path: Path, cache_directory: Path) -> bool:
    path = registry_cache_path(romfs_path, cache_directory)

    if not path.is_file():
        return False

    path.unlink()
    clear_actor_registry_runtime_cache()
    return True

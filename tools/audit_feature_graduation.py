from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smo_kingdom_importer.bfres_mesh import read_static_bfres
from smo_kingdom_importer.world_list import read_szs


def run(romfs_root: Path) -> None:
    object_data = romfs_root / "ObjectData"
    counters: Counter[str] = Counter()
    rig_influences: Counter[int] = Counter()
    errors: list[dict[str, str]] = []
    invalid_normals: list[dict[str, str]] = []
    start = time.perf_counter()

    for archive_path in sorted(object_data.glob("*.szs"), key=lambda p: p.name.casefold()):
        counters["archives"] += 1

        try:
            archive = read_szs(archive_path)
        except Exception as exc:
            errors.append({"archive": archive_path.name, "error": f"SZS: {exc}"})
            counters["archive_errors"] += 1
            continue

        for entry in archive.get_files():
            if not entry.name or Path(entry.name).suffix.casefold() != ".bfres":
                continue

            counters["bfres_members"] += 1

            try:
                models = read_static_bfres(bytes(entry.data), include_rigging=True)
            except (NotImplementedError, ValueError) as exc:
                message = str(exc)

                if message in {
                    "BFRES contains no models.",
                    "BFRES contains no GPU buffer information.",
                }:
                    counters["non_model_bfres"] += 1
                else:
                    counters["bfres_parse_errors"] += 1
                    errors.append(
                        {
                            "archive": archive_path.name,
                            "bfres": entry.name,
                            "error": message,
                        }
                    )
                continue

            if not models:
                counters["non_model_bfres"] += 1
                continue

            counters["model_bfres"] += 1

            for model in models:
                counters["models"] += 1
                skeleton = model.skeleton

                if skeleton is not None:
                    counters["models_with_skeleton"] += 1
                    counters["bones"] += len(skeleton.bones)

                deformable = False

                for mesh in model.meshes:
                    counters["meshes"] += 1
                    normals = mesh.normals

                    if normals is not None:
                        counters["meshes_with_normals"] += 1
                        reason = ""

                        if len(normals) != len(mesh.vertices):
                            reason = f"count {len(normals)} != {len(mesh.vertices)}"
                        else:
                            for index, normal in enumerate(normals):
                                if not all(math.isfinite(float(value)) for value in normal):
                                    reason = f"non-finite normal {index}"
                                    break
                                if sum(float(value) ** 2 for value in normal) <= 1e-12:
                                    reason = f"zero normal {index}"
                                    break

                        if reason:
                            counters["meshes_with_invalid_normals"] += 1
                            invalid_normals.append(
                                {
                                    "archive": archive_path.name,
                                    "bfres": entry.name,
                                    "model": model.name,
                                    "mesh": mesh.name,
                                    "reason": reason,
                                }
                            )
                        else:
                            counters["meshes_with_valid_normals"] += 1

                    if mesh.bone_weights:
                        deformable = True
                        counters["weighted_meshes"] += 1
                        rig_influences[mesh.skin_influence_count] += 1

                        if len(mesh.bone_weights) != len(mesh.vertices):
                            counters["invalid_weighted_meshes"] += 1
                            continue

                        valid = True

                        for weights in mesh.bone_weights:
                            if not 1 <= len(weights) <= 4:
                                valid = False
                                break
                            total = sum(float(weight) for _bone, weight in weights)
                            if not math.isclose(total, 1.0, abs_tol=1e-5):
                                valid = False
                                break
                            if any(
                                not math.isfinite(float(weight)) or float(weight) <= 0.0
                                for _bone, weight in weights
                            ):
                                valid = False
                                break

                        if valid:
                            counters["valid_weighted_meshes"] += 1
                        else:
                            counters["invalid_weighted_meshes"] += 1

                if (
                    deformable
                    and skeleton is not None
                    and len(skeleton.bones) > 1
                ):
                    counters["deformable_models"] += 1

    report = {
        "elapsed_seconds": round(time.perf_counter() - start, 3),
        "counts": dict(sorted(counters.items())),
        "rig_influence_meshes": {
            str(key): value for key, value in sorted(rig_influences.items())
        },
        "parse_error_count": len(errors),
        "parse_error_examples": errors[:25],
        "invalid_normal_count": len(invalid_normals),
        "invalid_normal_examples": invalid_normals[:25],
    }
    print("FEATURE_GRADUATION_AUDIT=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []

    if len(arguments) != 1:
        raise SystemExit("Usage: audit_feature_graduation.py -- ROMFS")

    run(Path(arguments[0]).resolve())

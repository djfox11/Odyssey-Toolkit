# Diagnostics

Odyssey Toolkit records enough information on generated objects to distinguish
unsupported game content from importer failures. Keep these diagnostics when
reporting a problem, but redact private filesystem paths.

## Diagnostics panel

In the 3D Viewport, press <kbd>N</kbd>, open the **Odyssey** tab, and expand
**Diagnostics**. Select an object from an imported stage to display its stage
summary, including:

- placement and generated mesh-object counts;
- diagnostic cube fallbacks;
- unsupported assets, texture errors and custom-normal failures.

A fallback is not automatically a bug. Runtime-created actors, collision,
particles and other unsupported systems can intentionally remain visible as
diagnostic placeholders.

## Generated root properties

For a detailed record, select the generated stage root and inspect **Object
Properties > Custom Properties**. The most useful fields are:

- `smo_import_status`, `smo_stage_name` and `smo_scenario`;
- `smo_placement_count` and `smo_static_mesh_object_count`;
- `smo_cube_fallback_count` and `smo_unsupported_assets`;
- `smo_texture_errors`, `smo_rig_errors` and
  `smo_custom_normal_failure_count`;
- `smo_performance_total_seconds` and `smo_performance_timings`;
- `smo_texture_cache`.

Successful re-imports replace the generated result transactionally. If a
re-import fails or is cancelled, the previous finished result is preserved and
the most recent attempt is recorded separately.

## Model-resolution report

Choose **Export Current Stage Report** in the Diagnostics panel, or run **Export
Model Report** from Blender's <kbd>F3</kbd> search. The JSON report contains
placement classification, nested-zone paths, composed transforms and model
resolution results. ObjectData paths are relative to the ROMFS; the report does
not store the absolute ROMFS path.

Do not attach the ROMFS, extracted assets, texture caches, or `.blend` files
containing game assets to a public issue.

## Console output

On Windows, open **Window > Toggle System Console** before reproducing a
problem. Completed imports print an aggregate timing summary. Exceptions and
tracebacks in this console are usually the most useful evidence for an actual
failure.

When reporting a bug, include:

- Odyssey Toolkit, Blender and Windows versions;
- the affected workflow, stage/scenario or asset type;
- the smallest exact reproduction sequence;
- the Diagnostics summary and relevant root properties;
- the traceback or warning text, with private paths redacted;
- whether the same operation worked in an earlier Toolkit version.

See [Troubleshooting](troubleshooting.md) for common fixes before filing an
issue.

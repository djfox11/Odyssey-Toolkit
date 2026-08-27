# Experimental Feature Graduation Audit

Date: 20 August 2026
Audited version: 0.37.0
Primary runtime: Blender 4.5.5 LTS

## Decision summary

| Feature | Current state | Decision | Default policy |
|---|---|---|---|
| FSKL armatures and skin weights | Labelled experimental; globally opt-in | Graduate to a supported basic import option | Off for stage imports because of object/memory cost; on by user choice. Expose it as an ordinary import option rather than an experimental preference. |
| Native FSKA/FBVS animations and FSCN/FCAM cameras | Main UI, no experimental gate | Keep as supported basic features | Available by default when a compatible armature/package is selected. |
| BFRES custom normals | Labelled experimental; globally opt-in | Graduate to a supported basic shading option | Default on for new installs, with an ordinary compatibility switch to disable it. |
| Persistent texture cache | Ordinary option, default off | Already graduated | Keep off by default because it writes external files; retain its safe direct-decode fallback. |
| Actor registry | Ordinary option, default on | Already graduated | Keep current behavior. |
| Stage lighting and placed lights | Main stage option, default on | Already graduated, but documented as approximate | Keep current behavior and wording. |
| Cloth NoV/Fresnel translation | Automatic main material path but documented experimental | Keep experimental and move behind an explicit shader-approximation gate | Default off until visual reference tests match game output. Always preserve parsed metadata. |
| Switch Toolbox SMD import | Compatibility-only File menu operator | Keep as compatibility tooling, not experimental | Do not return it to the main sidebar. |

## ROMFS-wide evidence

`tools/audit_feature_graduation.py` performed a read-only scan of all 3,637
`ObjectData/*.szs` archives. It parsed model BFRES resources with rigging enabled and
validated every source-normal and weight row.

- 3,351 BFRES members inspected.
- 2,927 model BFRES resources parsed.
- 2,927 models and 19,535 bones inspected.
- 15,936 meshes contained source normals; all 15,936 were finite, non-zero and matched
  their vertex counts.
- 15,936 meshes contained rigid or weighted binding payloads; all 15,936 had valid
  vertex counts, one-to-four positive influences where applicable, and normalized weights.
- Influence declarations covered rigid/base-bone bindings and one-, two-, three- and
  four-weight skinning.
- 424 remaining BFRES members did not yield mesh models. Every recorded example reported
  either no models or no GPU buffers and belonged to a texture, animation, cubemap or
  controller/position-style package; the corrected audit tool now counts those expected
  resources separately from any other parser failure on future runs.

The complete v0.37.0 36-test release matrix also covers armature creation, shared rest
data, independent pose state, actual deformation, malformed-binding fallback, native
animation discovery/import, bone visibility, external animation packages, scale
compensation, camera animation, custom-normal conversion and transactional cleanup.

## Why armatures should not become universally default-on

Graduation means the feature is supported, documented and tested; it does not require
creating an armature for every placement. BFRES models commonly retain FSKL and rigid
base-bone data even when the user only needs a static environment reconstruction. Enabling
armatures globally for a large kingdom can add many Armature objects, vertex groups and
modifiers, increasing import time, file size and viewport complexity.

Recommended UI policy:

- Rename the preference to **Import armatures and skin weights**.
- Remove all “Experimental rigging” wording.
- Present the option alongside normal import controls, with a note that it is required
  for skeletal and bone-visibility animation.
- Keep stage imports off by default for performance and compatibility.
- Keep standalone character/model workflows easy to enable without visiting a hidden
  experimental section.
- Retain the current validated static-bind fallback and `smo_rig_errors` diagnostics.

## Why custom normals can graduate

The source data passed a complete structural audit and the application path validates
count, shape, finiteness and length before calling Blender's supported
`normals_split_custom_set` API. Failures are recorded per mesh and do not abort the import.
The remaining historical warning about an uncatchable Blender exit is not backed by a
current Blender 4.5.5 reproduction.

Recommended UI policy:

- Rename the preference to **Apply BFRES custom normals**.
- Enable it by default for new installs.
- Keep a compatibility switch for assets or Blender versions that exhibit a native
  driver/API problem.
- Replace the experimental/crash wording with a concise explanation that disabling it
  lets Blender recalculate smooth normals.

## Why cloth NoV should remain experimental

The importer recovers the BFRES parameters and masks, but the original shader equation is
not present in the resource. Recent MarioFace and Cafe Shader Studio comparisons show
incorrect inner bands, polarity/intensity differences and overly broad colour/emission.
Node-graph regression tests currently prove wiring and parameter preservation, not visual
parity.

Recommended policy:

- Add **Experimental cloth NoV/Fresnel approximation** under a clearly separated shader
  section, default off.
- When disabled, retain `smo_cloth_nov` metadata but do not inject the visual Base Color or
  Emission branches.
- Graduate it only after image-based references cover Mario face/hair, Peach cloth and at
  least one emission-bearing material from fixed camera/light setups.

## Proposed cleanup release

A focused 0.38.0 cleanup can implement the policy without changing file formats:

1. Remove experimental wording from rigging and custom normals.
2. Make custom normals default on; retain its compatibility switch.
3. Keep armatures optional for stage performance, but expose them as a normal import
   feature and explain their animation dependency.
4. Gate cloth NoV/Fresnel behind a default-off experimental shader option while preserving
   all parsed metadata.
5. Update preference/UI regressions and add a migration test for existing saved settings.
6. Run the full release matrix plus interactive Mario/Bowser/camera validation before
   packaging.

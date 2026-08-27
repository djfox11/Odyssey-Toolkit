# Odyssey Toolkit Release Checklist

This is the bounded acceptance contract for 1.0. New capabilities discovered during release work go to the post-1.0 backlog unless they fix a release blocker.

## Automated gates

- Require the Release hygiene workflow on main to pass.
- Run python tools/build_release.py.
- Run the complete 38-script Blender matrix through tests/release_hygiene.py --run using Blender 4.5 LTS and a valid ROMFS.
- Preserve the resulting console output with the release candidate.
- Require matching source, manifest, wheel, ZIP, changelog, and Git tag versions.
- Require a clean register/unregister cycle and no unlisted headless test.

## Clean-profile gates

- Install odyssey_toolkit.zip into a clean Blender 4.5 profile without manually installing Python packages.
- Confirm oead imports from the extension site-packages directory.
- Import a standalone asset before selecting a ROMFS.
- Select a valid ROMFS and import a stage.
- Update from the previous public package without losing saved preferences.
- Uninstall and confirm imported Blender data remains intact.
- Confirm a stale legacy add-on copy produces clear cleanup guidance.

## Interactive gates

- Load every kingdom catalogue entry without a crash.
- Perform deep visual checks on Cap, Wooded, Metro, Seaside, and Moon content.
- Cover nested zones, sky, water, lighting, rigs, skeletal animation, material animation, and camera animation.
- Cancel and re-import one large stage while observing memory and viewport responsiveness.
- Save, close, and reopen one complete imported stage.
- Confirm the Diagnostics panel reports fallbacks and warnings for the selected import.

## Release-candidate policy

- Run a small external beta on the release-candidate ZIP.
- Block release for installation failure, crash, data corruption, unusable primary workflow, or silent unsupported-format failure.
- Fix release blockers only during the candidate period.
- Do not create v1.0.0 until every automated, clean-profile, interactive, and beta gate passes.
- Update the manifest, Python version tuple, and changelog together, then tag the exact validated source as v1.0.0.
- Let the tag workflow create the private draft release; verify its SHA-256 checksum and attached ZIP before publishing.
- Archive the Blender matrix output and benchmark summary with the release record.

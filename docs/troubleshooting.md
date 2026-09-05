# Troubleshooting

## Blender does not show Odyssey Toolkit

- Confirm that you installed the attached `odyssey_toolkit_v0.xx.x.zip` from a
  release, not GitHub's **Source code (zip)** archive.
- Open **Edit > Preferences > Get Extensions > Installed** and check that
  Odyssey Toolkit is enabled.
- Restart Blender after installing or updating.
- Do not keep both an extension install and an extracted copy under
  `scripts\addons`.

If Blender still loads an old version, close every Blender process and remove
only the leftover Odyssey Toolkit directories, if present:

```text
%APPDATA%\Blender Foundation\Blender\<version>\extensions\user_default\odyssey_toolkit
%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\odyssey_toolkit
```

Do not delete the surrounding Blender, `extensions`, `scripts` or `addons`
directories.

## The stage list does not load

- Select the ROMFS root itself, not a parent directory or `StageData` alone.
- Confirm it contains `SystemData\WorldList.szs`, `StageData` and `ObjectData`.
- Read the error shown below the ROMFS field.
- Select **Reload Stages** after changing or replacing the ROMFS.

An error mentioning a missing `oead` module usually means the wrong ZIP was
installed or the release ZIP is incomplete. Re-download the attached release
asset and compare it with `SHA256SUMS.txt`; do not install a separate `oead`
package as a workaround.

## An import is slow

The first import can spend most of its time decoding BNTX textures. Enable the
persistent texture cache in the add-on preferences to speed up later imports.
The Diagnostics properties `smo_performance_timings` and `smo_texture_cache`
show where time was spent and whether entries were hits or misses.

If cached results look corrupt, use **Clear Cache** in the preferences and run
one cold import. Clearing the cache does not modify the ROMFS or images already
packed into a `.blend` file.

## Objects are fallbacks or materials look approximate

Check the selected import's **Diagnostics** summary before assuming the import
failed. Runtime-only actors, particles, cubemaps, texture arrays and exact game
shader behavior are outside the current supported scope. Their placeholders or
approximations can be expected.

For unexpected missing models, run **Export Current Stage Report** and inspect
the resolution status. For texture, rig or normal problems, inspect
`smo_texture_errors`, `smo_rig_errors` and
`smo_custom_normal_failure_count` on the generated root.

## Re-import failed or was cancelled

Re-import is transactional: the previous successful result should remain in the
scene if its replacement fails or is cancelled. Confirm that the old root still
has `smo_import_status` set to `FINISHED`, then collect the latest attempt status
and console traceback. Do not delete the preserved result before reporting a
reproducible failure.

## Report a bug

Read [Diagnostics](diagnostics.md), then open a
[bug report](https://github.com/djfox11/Odyssey-Toolkit/issues/new/choose).
Never upload ROMFS files, extracted game assets, encryption keys, texture-cache
entries or `.blend` files containing Nintendo-owned assets.

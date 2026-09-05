# Installation

Odyssey Toolkit supports Blender 4.5 LTS on Windows 10/11 x64. It requires a
legally obtained Super Mario Odyssey ROMFS; the extension does not include game
data or tools for obtaining it.

## Download the extension

1. Open the [Odyssey Toolkit releases page](https://github.com/djfox11/Odyssey-Toolkit/releases).
2. Open the release you want to install.
3. Under **Assets**, download `odyssey_toolkit_v0.xx.x.zip`.

Do not use GitHub's automatically generated **Source code (zip)** or **Source
code (tar.gz)** downloads. Those repository snapshots are not installable
Blender extension packages.

Each release also includes `SHA256SUMS.txt`. To verify the download in
PowerShell, put both files in the same directory and run:

```powershell
Get-FileHash .\odyssey_toolkit_v0.xx.x.zip -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

The hashes must match; letter case does not matter.

## Install in Blender

1. Open **Edit > Preferences > Get Extensions**.
2. Open the top-right menu and choose **Install from Disk...**.
3. Select the downloaded release ZIP.
4. Enable Odyssey Toolkit if Blender asks, then restart Blender.
5. In the 3D Viewport, press <kbd>N</kbd> and open the **Odyssey** tab.

The compatible `oead` wheel is bundled in the release. Do not install `oead`
separately into Blender.

## Select the ROMFS

Choose the extracted ROMFS directory in the Odyssey sidebar. The selected
directory must contain at least:

```text
romfs\
|-- ObjectData\
|-- StageData\
`-- SystemData\
    `-- WorldList.szs
```

Once validation succeeds, choose a stage and scenario and select **Import
Stage**. Use **Reload Stages** after changing the ROMFS contents.

## Update or remove

For versions 0.41.0 and later, install the newer release ZIP with **Install
from Disk...**. If Blender refuses to replace the installed extension, remove
Odyssey Toolkit from the **Installed** extensions list, restart Blender, then
install the new ZIP.

When upgrading from 0.40.0 or earlier, remove the old
`smo_kingdom_importer` package first. Imported scene data and the established
`smo.*` operators remain compatible.

Removing the extension does not delete meshes, materials, images or other data
already saved in a `.blend` file.

See [Troubleshooting](troubleshooting.md) if Blender loads an unexpected version
or does not accept the ROMFS.

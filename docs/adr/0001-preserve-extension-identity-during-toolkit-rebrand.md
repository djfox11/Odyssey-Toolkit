# Preserve extension identity during the Toolkit rebrand

Odyssey Toolkit replaces the public “Super Mario Odyssey Kingdom Importer” identity, but the manifest ID `smo_kingdom_importer`, Python package name, `smo.*` operators, saved preferences, and generated `smo_*` metadata remain stable. Renaming those interfaces would break updates, automation, and existing Blender files for cosmetic consistency, while retaining them has no user-facing cost.

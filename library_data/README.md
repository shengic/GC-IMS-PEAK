# library_data/

Default location for `.ril` (Retention Index) and `.iml` (IMS Drift Time)
library files. `library.py` looks here first when no explicit path is passed;
`main.py` "Browse library data..." lets the user override at runtime.

## Getting the standard NIST set

Copy the contents of `VOCal Release 0.4.31.412/_portable/data/` into this
folder (there are ~646 `.ril` NIST columns + 7 built-in `.iml` files, all
tab-separated plain text). Files stay at the top level of `library_data/`
— no subdirectories, matching VOCal's convention.

## Resolution order (see `library.resolve_data_dir()`)

1. Path passed explicitly by caller
2. `GCIMS_LIBRARY_DIR` environment variable
3. `<project_root>/library_data/` ← this folder
4. `<project_root>/VOCal Release .../` `_portable/data/` (backward-compat fallback)
5. `None` — caller should open a file-picker

## git status

This folder is **not committed**. Each dev/user syncs their own library set
(see `.gitignore`).

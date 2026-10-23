# Runtime Data

**Status:** placeholder. Directory contains no user data.

This directory is the local working area for runtime inputs and intermediate
files. Its contents are excluded from version control except for `.gitkeep`
markers and this document.

## Layout

- `uploads/`: user-provided books and PDFs.
- `extracted/`: parsed pages, normalized text, figures, and intermediate
  extraction artifacts.
- `generated/`: intermediate scene assets and temporary generation outputs.

Final deliverables belong in `artifacts/`. Retention, cleanup, and access rules
will be defined with the storage implementation.

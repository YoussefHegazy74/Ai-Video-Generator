# Runtime Data

**Status:** reserved for future upstream ingestion/planning stages. Parts 5-7 do
not read or write this directory; the current pipeline starts from a canonical
`SceneSet` JSON file and writes outputs under `artifacts/`.

This directory is the local working area for runtime inputs and intermediate
files. Contents are excluded from version control except for `.gitkeep` markers
and this document.

## Layout

- `uploads/`: user-provided books and PDFs (future ingestion).
- `extracted/`: parsed pages, normalized text, figures, and intermediate
  extraction artifacts (future ingestion).
- `generated/`: intermediate upstream scene assets, if needed (future).

Final deliverables belong in `artifacts/`. No user data is present in this
checkout. Retention, cleanup, and access rules will be defined with the ingestion
and storage implementations.

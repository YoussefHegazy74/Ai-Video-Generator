# Scripts

**Status:** placeholder. No scripts are included yet.

This directory will contain developer and operational entry points that are not
part of the application runtime, for example:

- local environment checks;
- document ingestion smoke tests;
- scene generation and validation dry runs;
- artifact inspection and cleanup utilities;
- release and maintenance tasks.

Scripts must document their inputs, outputs, and side effects, and must not
contain embedded credentials. Application logic belongs in `src/`, not here.

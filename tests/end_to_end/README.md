# End-to-End Tests

**Status:** placeholder. No tests are implemented.

This directory will contain full-workflow tests that start from a small sample
document, run the agent pipeline, validate every scene, assemble the final video,
and check the resulting metadata.

These tests will likely be slower and may use opt-in provider access. Default
runs must remain deterministic and must not expose credentials.

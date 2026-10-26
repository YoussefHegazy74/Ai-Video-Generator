# Artifacts

**Status:** generated outputs are produced here by `scripts/run_pipeline.py` and
by the services. Contents are runtime data and are excluded from version control;
this README documents the layout.

```text
artifacts/
  artifact_registry.json          # atomic provenance registry (single-process)
  .work/                          # job-scoped temporary render directories
  jobs/<job_id>/
    job.json                      # job status, stage, progress, failures
    scene_set.json                # full canonical scene state
    scenes/<index>-<scene_id>/
      video.mp4                   # generated scene video
      manifest.json               # deterministic text/style manifest (if provided)
      narration.wav               # optional separate audio (if provided)
      validation.json             # structured validation report
      scene.json                  # per-scene record
    final/
      final.mp4                   # verified final video
      assembly_manifest.json      # output hash/size/duration, order, verification
```

The final video and manifest are stored only after FFprobe/FFmpeg verification and
audio-level checks pass. Artifact references in JSON are storage-relative keys,
not host paths. Secret-like metadata keys are redacted before registration.

The registry is not a distributed database. Do not run multiple writer processes
against the same storage root. Temporary directories are removed after use;
stale directories under `.work/` are safe to inspect before manual cleanup.

A local smoke run produced `jobs/smoke2/final/final.mp4` at 640x360, 12 fps,
H.264/AAC, 24.853 s, with two validated scenes (11.5 s and 13.333 s). That output
is environment-specific and is not committed.

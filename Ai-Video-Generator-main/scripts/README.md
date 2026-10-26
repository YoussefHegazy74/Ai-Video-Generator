# Scripts

Two source-checkout entry points are implemented. Both insert `src/` on
`sys.path` and work without installing the package.

## `check_environment.py`

Read-only environment/capability report. It does not write files, make network
calls, or call paid APIs.

```powershell
python scripts\check_environment.py
python scripts\check_environment.py --json
```

Reports Python/platform, installed packages, loaded settings, FFmpeg/FFprobe
versions and required encoders/filters, local TTS availability, selected provider,
and whether `GEMINI_API_KEY` is configured. It explicitly reports
`gemini_live_call_tested: false` and `gemini_duration_control: false`.

Exit code: `0` when FFmpeg and FFprobe are found, `1` otherwise. A missing TTS path
is reported as a warning; the default deterministic provider then cannot render.

## `run_pipeline.py`

Runs the implemented generate -> validate -> assemble pipeline over a canonical
`SceneSet` JSON file.

```powershell
python scripts\run_pipeline.py --scene-set examples\scene_set.sample.json --job-id demo1
python scripts\run_pipeline.py --width 640 --height 360 --fps 12 --no-intro --no-outro --plain-logs
python scripts\run_pipeline.py --provider gemini_omni --job-id gemini-demo
```

Options include `--config-dir`, `--provider`, `--width`, `--height`, `--fps`,
`--no-intro`, `--no-outro`, `--plain-logs`, `--log-level`, and `--cancel-file`.

`--cancel-file` enables cooperative cancellation: creating the file requests a
stop between work units. The process prints a JSON summary and returns:

- `0`: completed;
- `1`: failed or input/configuration error;
- `2`: cancelled.

Side effects: writes scene/final artifacts and the artifact registry under the
configured storage root (`artifacts/` by default), and temporary work directories
under the configured temp root. The default provider is local and free. Selecting
`gemini_omni` makes paid API calls and requires `GEMINI_API_KEY`.

# YAM3 episode replay and video export

The runner already invokes `hardware_replay.export_replay()` in its post-run cleanup,
after leaving the motion session. That function now generates the publication viewer.
`export_replay_mp4.py` uses the same function and HTML template. No separate UI draft
or robot connection is involved. MP4 encoding is deliberately an offline command;
`astra_run.py` does not wait for a browser/encoder after an episode.

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir integrations/inspect_robots/astra-yam3-run-011 \
  --speed 4 --fps 30
```

Default output: `<run-dir>/replay_4x.mp4` (1600×1080, H.264, yuv420p, CRF 18).
The adjacent JSON records rendering settings and browser version. Use `--out`,
`--title`, `--harness-label`, `--width`, `--height`, or `--browser-path` to override.
Use `--html-only` to generate just the interactive viewer. Open `replay.html`
directly or serve the run directory. Play/pause, 1×/2×/4×, the slider and clickable
swimlanes all share the same clock. The timeline supports arrow keys, Home and End.
`?export=1` removes controls and evidence disclosure from the publication layout.

Dependencies: FFmpeg (including libx264), Python Playwright and Chromium. This
workspace was validated with Playwright 1.64.1 and its cached Chromium. On a new host:

```bash
uv pip install --python YAM/gello_software/.venv/bin/python playwright==1.64.1
YAM/gello_software/.venv/bin/python -m playwright install chromium
```

Inter and JetBrains Mono variable WOFF2 files are bundled under
`replay_assets/fonts/`, together with their OFL licenses. They cover the requested
400/500/600 weights. Export copies the assets into the run directory, waits for
fonts and image decoding, and fails if fonts do not load. Rendering never needs a
font CDN. The video uses deterministic episode times `frame_index * speed / fps`;
it does not record a real-time browser playback session. The last frame includes
the final episode state. The duration differs from episode_duration/speed by less
than one frame. Final video replacement is atomic; interrupted encoding leaves a
previous valid MP4 intact.

## Audit of existing implementations

- `hardware_replay.py` / `hardware_replay.html`: Astra hardware evidence journal,
  continuous preview recorder and runner's original HTML exporter. This is the
  upgraded implementation; the previous template is `hardware_replay.html.bak`.
- `replay.py`: explicitly synthetic/mock framework-to-video bridge. It imports
  `yam3_trial_replay/execution_timeline.py`; it is not a hardware log parser.
- `yam3_trial_replay/{build_replay.py,execution_timeline.py,replay_template.html}`:
  older MCAP/Pillow/FFmpeg pipeline; uses MCAP receipt clocks and DejaVu fonts.
  Preserved for its distinct source format rather than connected to Astra logs.
- Existing exports: root `replay.mp4`, `yam3_trial_replay/output*/replay.{html,mp4}`,
  and `integrations/inspect_robots/{demo-output/replay,first-action-ui,replay-ui}`.
  These are earlier outputs, not alternate sources for this new viewer.
- Vendored `inspect_robots/_video.py` and `_html.py`: framework stream/video exports;
  they do not implement the hardware journal's request/dispatch synchronization.
- `YAM/gello_software/scripts/replay.py` and data-utils replay code can replay robot
  trajectories. They are not imported or executed by this offline exporter.

## Actual recorded schema and clock mapping (runs 011 and 012)

| Evidence | Fields and interpretation |
| --- | --- |
| Continuous cameras | `station-events.jsonl`: `kind=camera_frame`, `source=preview_recording`, `camera=main`, content-addressed `image`/`sha256`, `received_monotonic_ns`, `received_time_ns`. Preview targets 5 Hz, but actual cadence is irregular. Hold the most recent frame at or before playback time; never select a future frame. |
| Model input images | `kind=decision_observation` records the **decision boundary**, `step`, `observation_id`, and `cameras[view].sha256` plus receipt timestamps. Match hashes against `camera_frame` images. `policy_observation` frames are also captured at every physical step, so selecting the latest such frame is incorrect. Freeze the matched `right` and `main` pair until the next decision. |
| Opened time | `decision_observation.recorded_monotonic_ns` is the observable boundary when the assembled observation was journaled. Image receipts precede it. There is no separately instrumented model “image opened” event. UI `Opened at` means this recorded boundary, not camera exposure or an internal model event. |
| Model interval | `model_round_trip.duration_s` is monotonic elapsed time including response-body reception. Its journal `recorded_monotonic_ns` anchors the end; subtract duration to reconstruct the start. Journal overhead means these are close recorded bounds, not independently timestamped socket send/receive instants. Wire `calls.jsonl` has wall-clock `t`, `duration_s`, zero-based `call`, `attempt`, `status`, request and response. Transport `call` is one-based. Match wire completion wall time to the transport journal wall-clock end (within 1 s), including failed attempts. This tolerates missing wire rows; older fixtures without paired clocks fall back to attempt order. Do not join on differing call IDs. |
| Tool name/arguments | Wire `response.output[]` items with `type=function_call`: `name`, `call_id`, JSON-string `arguments` (`targets`, `phase`, `note`, etc.). Response arrival is the earliest point these become available. No future tool/note is shown during an earlier model interval. |
| Returned | `transcripts/**/*.jsonl` has `role=tool`, `tool_call_id`, `content`. Join by call ID, never by guessed step counts. `executing … N steps (X.Xs)` is a planner return, not measured completion. The card shows actual recorded step count and execution-envelope wall duration, then the last `observation.measured_joints` readback only after its timestamp. Original returned content is retained in the normalized payload. |
| Tools interval | Historical logs have **no tool-handler entry/exit timestamps**. The orange lane is explicitly an inferred execution envelope: response arrival to last motion/readback before the next decision (or next decision boundary for non-motion tools). It must not be interpreted as measured handler latency. |
| Motion interval | Join `command_dispatch` to `motion_time` by `command_id`; each interval is journaled dispatch through the paced step's end. Preserve gaps for observations/settling. `command_acceptance` records `accepted`, `limited`, `applied`, `accepted_time_ns`; acceptance alone is not encoder-confirmed completion. Motion here means controller-step execution, not independently measured onset/cessation of physical movement. |
| Terminal outcome | `attempt-result.json`, `policy_termination`, `framework-log.json` preserve partial/error/termination evidence. The viewer never turns `done` or a completed evaluation into a claim of verified physical success. |

Origin is the earliest frame's `received_monotonic_ns`. Subtract integer nanoseconds
**in Python** before converting to seconds, avoiding JS integer precision loss.
Camera `time_s` uses receipt time; other event `time_s` uses journal time. The
original `frames`, `events`, `calls`, `result`, damage and missing-frame fields
remain in `const data=` for old consumers. A new `presentation` field supplies
normalized UI data. If the station journal is absent, embedded legacy replay data
can be upgraded without discarding its original fields. Raw request headers and
credentials are not added to the HTML; only model/effort metadata is read from
requests. Missing/corrupt frames and incomplete journals remain reviewable.

Run 011: 16,497 camera rows, 4,231 continuous main samples, 27 decision observations,
27 transport intervals, 1,231 command/motion steps; duration 1,011.854104018 s.
Run 012: 16,555 camera rows, 22 decision observations, 22 transport intervals,
1,072 command/motion steps. Both have unknown acquisition times/frame IDs.

The phase breadcrumb is a presentation classification: Observe at a decision
boundary, Program during model work, Act during tool/motion envelopes, and Verify
between completed execution and the next decision. It is not a new logged robot
state machine.

## Validation

`replay-schema-audit.json` records counts, camera cadence and source journal hashes;
`replay-browser-checks.json` records browser validation on both episodes. Tests cover
clock alignment, missing wire rows, exact observation hashes, camera-source selection,
legacy payload preservation and partial journal recovery. Re-run browser checks with:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/verify_replay_browser.py \
  integrations/inspect_robots/astra-yam3-run-011/replay.html \
  integrations/inspect_robots/astra-yam3-run-012/replay.html
ffprobe -v error -show_entries stream=codec_name,width,height,pix_fmt,r_frame_rate,nb_frames \
  -show_entries format=duration,size -of json \
  integrations/inspect_robots/astra-yam3-run-011/replay_4x.mp4
```

The viewer presents 640×480 source camera images without inventing higher-resolution
camera detail; text and UI are rendered at the video canvas resolution.

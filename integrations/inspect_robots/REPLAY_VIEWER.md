## Two- and three-camera layouts

The **Views** selector switches between **Main + right** (the original layout)
and **Main + left + right**. Active-observer runs default to three; right-only
runs and legacy recordings default to two. Archival left video alone does not
enable the observer layout. The selector preserves playback time and speed.
`?layout=two` or `?layout=three` can also select the initial view.

All three continuous feeds select their most recent recorded sample at or before
the playback time. The three decision thumbnails use exact observation hashes,
including the left image actually delivered to Astra; they remain frozen until
the next decision. Missing left footage stays explicitly empty. If left was
masked for the model, a forced three-camera view labels its continuous footage
as archival and its decision thumbnail as masked. It never substitutes the
archival left feed for a model observation.

MP4 export uses the same layout and clock:

```bash
# Run after trial 5 has finished. The current trial need not be restarted.
RUN_DIR=integrations/inspect_robots/experiments/plug-socket/runs/run-005
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir "$RUN_DIR" --layout three --speed 16 --fps 30

# Preserve the original two-camera option, even for an observer recording.
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir "$RUN_DIR" --layout two --speed 16 --fps 30
```

Explicit layouts produce `replay_16x_three-camera.mp4` and
`replay_16x_two-camera.mp4`, so both can coexist. `--layout auto` is the default
and retains the historical `replay_16x.mp4` filename. An explicit layout also
sets the initial HTML layout; the selector still offers both. `--html-only`
rebuilds the viewer without encoding a video. Layout selection also works with
`--combine-resumes`; left images and decision state clear at segment boundaries.

Video defaults are 1600x1080 for two cameras and 1920x1440 for three. With no
explicit `--height`, the exporter increases height if needed to fit the longest
recorded tool card without cropping. The actual dimensions and selected camera
views are recorded in the adjacent JSON. Explicit dimensions must fit the
content; failures leave an existing MP4 intact.

The current runner already records left video. It reads the HTML template and
imports timeline preparation during post-run export, so an in-progress observer
run can use the new viewer when it finishes. Active mode is recognized from its
manifest or existing observer/13-joint decision events. No replay rebuild or
MP4 rendering is needed inside an active trial's directory while it is running.

## Original two-camera layout

The replay now presents main and right-wrist recordings in equal large panels
across the top, without cropping or picture-in-picture occlusion. At 1600×1080
each camera image occupies 600×450 pixels (source 640×480); telemetry and decision
snapshots sit below. Smaller screens stack the panels. Both streams select the
latest preview_recording frame at or before the current playback time, independently
by receipt timestamp; the sample-age label exposes holds/gaps. Policy snapshots
remain separate and update only at decision boundaries. Missing continuous wrist
recordings show an explicit empty state rather than substituting model snapshots.

Regenerate an existing replay with export_replay_mp4.py --run-dir RUN --html-only.
The layout remains selectable; existing MP4 files keep their previous layout
until explicitly re-exported. No robot connection is required to rebuild replays.

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

Default output: `<run-dir>/replay_4x.mp4` (automatic camera layout, H.264,
yuv420p, CRF 18; dimensions described above).
The adjacent JSON records rendering settings and browser version. Use `--layout`, `--out`,
`--title`, `--harness-label`, `--width`, `--height`, or `--browser-path` to override.
Use `--html-only` to generate just the interactive viewer. Open `replay.html`
directly or serve the run directory. Play/pause, 1×/2×/4×/8×/16×/32×, the slider and clickable
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

## Playback and MP4 speeds

Select 1×, 2×, 4×, 8×, 16× or 32× in the viewer. Changing playback speed does
not modify the evidence. Regenerate an existing viewer with `--html-only` to get
the updated controls. Each resumed segment has its own replay; the initial run's
HTML does not automatically include later segments.

MP4 export accepts **any positive multiplier**, including decimals:

```bash
# Set RUN_DIR to the exact segment to export.
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir "$RUN_DIR" --speed 32 --fps 30
# Or use --speed 8, --speed 16, --speed 2.5, etc.
```

Output is `$RUN_DIR/replay_32x.mp4` for 32×, plus a settings JSON. An existing
export at that same filename is replaced only after encoding succeeds. Use
`--out /path/to/custom-name.mp4` to choose another destination. Encoding is
entirely offline, includes the camera/tool timeline layout, and has no audio.
A 32-minute segment becomes roughly a one-minute video at 32×.

## One viewer and MP4 for a resumed experiment

Start from the original run directory, not the final resume segment:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir integrations/inspect_robots/experiments/usb-insertion/runs/run-001 \
  --combine-resumes --speed 32 --fps 30
```

Outputs: `<run-dir>/combined/replay.html`, `replay_32x.mp4`, its settings JSON,
and `segments.json`. Add `--html-only` to generate only the combined viewer, or
change `--speed` for another positive multiplier. The standalone combiner is
`python integrations/inspect_robots/combine_replays.py --run-dir ORIGINAL_RUN`.

The combiner follows a single nested resume lineage, validates parent provenance,
and refuses unfinished or branching lineages rather than silently mixing trials.
It concatenates recorded segment durations, omitting unrecorded downtime between
resumes. Time within every segment, decision images, tool envelopes and original
evidence timestamps are preserved. Boundary labels appear in the viewer and video.
Old camera/tool/decision state clears at each boundary until new samples arrive.
Individual segment evidence is retained. The HTML references images in the source
segment folders, so keep the full run folder together when copying it; the MP4 is
standalone. Combined output is written separately and never replaces the original
segment's replay or evidence.

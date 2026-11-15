## Two continuous camera views

The replay now presents main and right-wrist recordings in equal large panels
across the top, without cropping or picture-in-picture occlusion. At 1600×1080
each camera image occupies 600×450 pixels (source 640×480); telemetry and decision
snapshots sit below. Smaller screens stack the panels. Both streams select the
latest preview_recording frame at or before the current playback time, independently
by receipt timestamp; the sample-age label exposes holds/gaps. Policy snapshots
remain separate and update only at decision boundaries. Missing continuous wrist
recordings show an explicit empty state rather than substituting model snapshots.

Regenerate an existing replay with export_replay_mp4.py --run-dir RUN --html-only.
Future HTML and MP4 exports use this layout; existing MP4 files keep their previous
layout until re-exported. No robot connection is required to rebuild replays.

## Separate USB-A insertion experiments

Use `astra_run.py --task usb-insertion --attempt 1` for offline preflight;
add `--check-station` for the read-only service check and `--run
--right-channel-confirmed can_yam_a` only for an attended live episode.
The USB prompt lives in `experiments/usb-insertion/prompt.txt`; recordings go to
`experiments/usb-insertion/runs/run-NNN/`, including the prompt snapshot, task
metadata, model/tool evidence, camera journal, result and generated replay.html.
The existing exporter accepts these folders with `--title 'USB-A cable insertion'`.

[USB setup and fish run/replay commands](experiments/usb-insertion/README.md)
include optional console logging and cold-start instructions. The default task
remains pick-and-place with its original prompt/output locations. USB requests
use plug/port guidance rather than block descent or return-to-rest instructions.
Cross-task checkpoint resumes are rejected. Both tasks share the same station
lock: separate recording namespaces do not permit simultaneous control of YAM3.
The 0.80/1.0 caps, low-level guards and medium reasoning effort are unchanged.

## Trial 14 review: wrist-depth gating and recoverable contact

Trial 14 issued two empty closures (decisions at steps 538 and 731), followed
by a model-issued give_up at step 884. No guard_intervention was recorded;
the largest decision residual was about 0.0416 rad, below the unchanged 0.08
hardware tracking guard. The saved frame shows a tipped block against a pad.

Rule 2 now prioritizes wrist-view pad-to-side depth evidence over apparent
main-view overlap. It uses scale change as supporting evidence, not a fixed
frame-fill threshold. Misses require reassessing fore-aft/XY alignment as well
as height. Contact pauses descent; a small, visually supported, guard-permitted
upward withdrawal/reversal can precede re-centering and another grasp attempt.
Tool/observation guidance and give_up descriptions use the same recovery policy.
A visibly clear recovery path is required; this is not an automatic recovery
controller or permission to override faults. Grasp readback must be corroborated
by seeing the block lift. Cap/interpolation/lag guards and medium effort are unchanged.

See [the evidence review](offline-review/trial014-depth-recovery/REVIEW.md) and
its decision-image comparison. Archived Trial 14 prompt/logs remain unchanged.

## Trial 14: V4 prompt and 0.80 rad call ceiling

The current prompt is V4: center the held block directly over the black X,
use object-agnostic grasp feedback (>0.10 and steady during lift), then verify
placement and return to the step-0 resting angles using coupled move_joints
before done. The scene instruction is aligned with that goal. No left-camera
instructions were added; reasoning.effort remains medium.

MAX_MOVE_RAD and the optional attended-path ceiling are 0.80 rad/joint.
This remains a global per-call guard, as before; the prompt requests 0.40–0.80
only in clear transit, 0.10–0.20 during descent, and 0.02–0.05 for final XY
centering. The 1.0 gripper cap, 0.003/0.02 per-waypoint interpolation, joint
bounds and 0.08 rad measured/setpoint lag guard are unchanged.

The supplied prompt requests two return calls; this is not an automatic homing
routine or a guarantee that every starting displacement fits in two calls.
Any joint displacement above 1.60 rad requires more than two calls at this cap;
existing guards remain authoritative. Contact/grasp criteria are visual/model
instructions, not new automatic contact detection. Saved trials and resumed
prompt histories retain their original evidence. Older sections below describe
historical settings. Pre-change .bak files are kept alongside modified files.

Offline preflight for the new episode (no service or model connection):

```fish
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py --attempt 14
```

## Publication replay viewer and MP4 export (2026-11-15)

See [REPLAY_VIEWER.md](REPLAY_VIEWER.md) for the shared viewer/exporter, CLI, run-011/012 schema audit, clock semantics and local font assets. Existing runner HTML export uses the new UI automatically; MP4 rendering is offline.

## Trial 12: V2 prompt and full-range gripper goals (2026-11-14)

The current `move_joints` per-call ceilings are **0.45 rad per arm joint** and
**1.0 normalized gripper delta**. Open (`1.0`) and close (`0.0`) each take one
model/tool call, with the existing local interpolation and all low-level joint,
tracking, feedback and hold guards preserved. Actual grasp closure is not a
measured arrival guarantee. The earlier 0.50 allowance below is historical.

[astra_exploration.txt](astra_exploration.txt) now contains the supplied V2
five-stage policy: actively square the jaws with `right_j5`, inspect joint lag,
stop lowering on the specified contact cues, and release early above the paper.
No scene-specific joint waypoints or initial pose vector were added. The V2
0.015 rad contact cue is a model instruction, not a new automatic contact guard;
joint lag alone is not an independently verified contact measurement.

URDF and MJCF identify model `joint6` (logical `right_j5`) as the flange hinge
with axis approximately local `(0, 0, -1)`. Offline FK confirms this joint rotates
the grasp frame around its approach axis without translating its center; that
axis is vertical when the approach is vertical, rather than at every wrist pose.

The Responses request already uses `reasoning.effort="medium"`; it remains so.
No `max_output_tokens` is sent: the pinned policy supports that option only for
the Messages wire. No latency reduction is claimed from an API-setting change.
Fresh episodes use V2; existing resumed checkpoints retain their saved prompt.

## Current motion caps and Astra prompt (2026-11-14)

`astra_run.py` enforces **0.45 rad per arm joint** and **1.0 normalized
gripper delta** per `move_joints` call in every phase. Clear-space transit uses
coupled axes; the task prompt requests 0.03–0.06 rad terminal alignment steps.
The 0.15 rad / 0.10 gripper limits in older trial notes below are historical.
Optional exact-path attended review remains available, with the same 0.45 cap;
it is no longer required to unlock that cap. No automatic collision checker is
installed, and the cap itself does not certify swept clearance.

The supplied five-stage prompt and kinematic priors are in
[astra_exploration.txt](astra_exploration.txt), appended verbatim through the
agent's `eval_notes` system-prompt extension. The priors are operator-supplied,
not newly calibrated by this change. Each call still uses the existing local
interpolation (at most 0.003 rad / 0.02 gripper per waypoint, 30 Hz), daemon
joint/velocity limits, feedback/freshness guards, fixed left-arm targets and
final SAFE HOLD. Low-level gains and limits are unchanged.

This is a custom YAM adapter using the vendored **robocurve/inspect-robots**
framework (`Task`, `Scene`, `eval`, tools and guards) and `inspect_robots_agent`.
It does **not** import UK AISI `inspect_ai`, `Sample`, `@solver` or `@tool`.
The pinned source is recorded in `upstream-lock.json`.

`planned_motion.py` has nominal MuJoCo IK; YAM/i2rt also has MuJoCo/Mink
kinematics. The live runner installs no pose planner, grounded pose inputs or
trusted swept-path checker, so Cartesian tools remain disabled. Its active
control path is joint targets through the existing safety daemon, logical
right=`can_yam_a`, left=`can_yam_b`.

Offline preflight (no station connection, LLM request or motion):

```sh
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py
```

It reports `effective_cap_rad=0.45`, `max_gripper_delta=1.0`, prompt path and hash.
`--check-station` additionally reads station services; `--run` starts a live
episode. Historical trial/resume evidence retains its original prompt.

Offline run 001/003 diagnosis, reference fix, regression results and pre-grasp station checks: [review](OFFLINE_RUN_001_003_REVIEW.md). Current gains and both archives are preserved.

# Paper-X / Inspect Robots integration

## Interrupted experiments: retries and resume (2026-11-14)

The runner now defaults to a **600-second HTTP timeout and four total attempts**
per model decision. `--model-timeout SECONDS` and `--model-attempts N` override
these defaults. Timeouts, transport failures, HTTP 429 and 5xx are retried by the
existing Responses client with backoff. Other HTTP 4xx fail immediately. Retries
send the same request without executing any tool or resending a robot command.
The daemon retains its target; the existing heartbeat thread continues running.
Fresh feedback, recorder/session checks and remaining request/time budgets are
checked before each HTTP attempt. Failure still exits into confirmed SAFE HOLD.
The vendored agent/wire implementation is unchanged.

Before each model call, `checkpoint.json` atomically saves and fsyncs the model
input, image data, returned reasoning items, prior tool calls/results, arm target,
measured joints, fixed left pose, recheck count and cumulative call/step counters.
HTTP attempts are checkpointed before sending. The checkpoint is invalidated
before executing the returned tool. Automatic resume is supported at an
unanswered model boundary; interruptions during dispatch need motion-outcome
review and are refused. Hidden reasoning never returned by the API cannot be
recovered. Existing frame/wire/journal logging remains available for review;
normal inference still uses the existing three-observation image horizon.

Assess Run 010 without connecting to the station:

```sh
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --resume integrations/inspect_robots/astra-yam3-run-010
```

Add `--check-station` for read-only daemon, camera and pose checks. On the normal
station terminal, resume the same experiment with:

```sh
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --resume integrations/inspect_robots/astra-yam3-run-010 \
  --run --right-channel-confirmed can_yam_a
```

Resume requires the existing identified daemon in confirmed SAFE HOLD, fresh
feedback, both arms within 0.008 of their saved measured boundary (radians for
arm joints, normalized gripper units), and right target residual at most 0.08.
It never repositions the arm to match a checkpoint. It preserves conversation
and returned opaque reasoning, adds an interruption notice and fresh camera/state
observation, and asks for a new decision. It never replays a saved motion chunk.
Old attended path authorizations are not restored. Existing guards still apply.
A kernel lock prevents simultaneous runner processes.

Each continuation writes to a new `resume-TIMESTAMP-ID/` child directory, leaving
original evidence intact. If that continuation is interrupted at a model
boundary, pass its child directory to `--resume`. Calls/HTTP attempts and motion
steps remain cumulative across segments (180/180/5400 ceilings); each explicitly
requested continuation receives a new 2400-second wall-clock window. An existing
attempt number is still rejected unless the explicit resume path is supplied.

Run 010 is recoverable offline from its legacy final failed wire request:
26 requests succeeded; request 27 timed out after 122.99 seconds, at cumulative
motion step 1014. Saved image blob checksums and exact Responses history
round-trip are verified by `test_experiment_resume.py`. Legacy recovery requires
that the last request failed transiently and the journal contains no accepted
command after that request started. Its old `evaluation_completed=true` field
was misleading: `framework-log.json` has status `error`. Future runner errors
are propagated and recorded as incomplete, while preserving the checkpoint.

A live read-only check in the restricted coding session timed out reaching the
safety daemon on 127.0.0.1:6002. **No live pose validation or resumed motion has
occurred.** Run the station check/continuation in the normal station environment;
do not restart or reset the held robot merely to resume.

```sh
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -m unittest discover \
  -s integrations/inspect_robots -p 'test_experiment_resume.py' -q
```


## Run 009 startup fix (offline verified)

The current station offers `move_joints`, `done`, `give_up`, `sleep`, and
`observation_recheck`. Astra is instructed to use the current main/left/right
views and measured feedback to choose a supported bounded next action. Missing
pose prerequisites alone must not end the episode while another supported action
is available. `move_joints` supports task progress as well as diagnostic probes.

`move_gripper_to_pose` is offered and recommended only when trusted station setup
supplies `pose_inputs_available=True` (grounded right_base position/orientation
inputs), an installed `pose_planner` with a solve method, and a callable
`path_clearance_check`. These are integration inputs, never model assertions or
pixel-derived guesses. `set_gripper` requires that checker too. The current
hardware startup installs none of these prerequisites and omits both planned tools.
An already offered pose tool also rechecks availability before planning.

Guarded joint moves use 0.45 rad/joint and 1.0 gripper total caps in all
phases. Exact checker-verified or attended review is optional for joint moves.
Bounds, per-step limits, interpolation, tracking/freshness checks, watchdog,
station ownership, left-arm hold and final SAFE HOLD are unchanged.

The optional local pose planner in [planned_motion.py](planned_motion.py) uses
nominal YAM Pro/linear-4310 grasp_site IK and quintic timing. Planned goals still
require trusted acceptance of the complete immutable path before bypassing total
displacement caps. IK does not establish physical clearance or a calibrated TCP.

[Run 009 replay](offline-review/run-009-startup/result.json) records a saved-input
startup through the real policy, wire encoder, tool adapter, precheck and guards.
[Exact tool schemas](offline-review/run-009-startup/tools.json),
[exact instructions](offline-review/run-009-startup/instructions.txt),
[observation report](offline-review/run-009-startup/observation-report.txt), and
[complete request](offline-review/run-009-startup/request.json) are included.
The response is a deterministic mock joint probe, **not fresh Astra inference**.
No sockets, station connection, robot dispatch or trial launch occurs. The replay
checks at and just above both motion caps across all six phases. Saved images do
not certify real-world swept clearance. Original Run 009 evidence is preserved.

```sh
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/offline_run009_startup.py
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -m unittest discover -s integrations/inspect_robots -p 'test_*.py' -q
```

Resume validation: **67 integration tests pass**, including six recovery tests.
[Run 010 assessment](offline-review/run-010-resume/assessment.json) records the offline
recovery result and the unsuccessful read-only station connection.

The sections below document the earlier joint-tool workflow and historical runs.


An existing `astra-yam3-run-NNN/` directory consumes that episode number,
independently of daemon restarts. Keep the directory and its evidence. To
explicitly select a separate episode, add `--attempt 3` (or another unused
positive number). The default remains attempt 2; numbers never advance
automatically. Inspect the selection offline first:

```sh
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py --attempt 3
```

For a new supervised hardware episode, use the same selection with
`--run --right-channel-confirmed can_yam_a`. Existing selections are rejected
before station connections, with an exclusive directory creation check after
station preflight as well.


## Current camera and motion contract (offline verified)

YAM3 now delivers all three existing cameras: `main`, `left` (left-wrist), and
`right` (right-wrist). `main_comparison` remains a derived display crop.

The effective cap is **0.45 rad/joint**, with **1.0 gripper** per call.
Use coupled clear-space transit and **0.03–0.06 rad** terminal alignment.
Controller bounds, interpolation, 30 Hz cadence, tracking checks, watchdog
and confirmed SAFE HOLD behavior remain unchanged.

For optional attended path review, the operator supplies an absolute right-arm target
(J0–J5 radians and unchanged normalized gripper). No target is inferred from
saved camera images. On YAM3, run this **read-only, attended station check**, replacing
the placeholders with those seven values:

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --check-station --approach-target J0 J1 J2 J3 J4 J5 GRIPPER \
  --station-review-dir /tmp/yam3-approach-review
```

The new review directory contains `path.json` with the measured start, fixed
left pose, exact pinned-planner waypoints including endpoint, and SHA256;
`main.jpg`, `left.jpg`, and `right.jpg` contain the three station views.
The exact path to verify is the **forward joint-space interpolation through every
listed waypoint from that start to that target**, including the entire swept
links, wrist, fingers and cables, with tracking uncertainty up to 0.08 rad.
Check clearance from tabletop, block, paper, fixtures, stationary left arm and
people throughout. Verify camera identities and live views, physical right arm
`can_yam_a`, unchanged scene/left pose, reachable stop and continuous supervision.
The path must stay in open space without descent/contact or gripper changes.
Images and a phase label do not establish clearance. Decline if any part has
not been verified in the lab.

Typing `VERIFIED <full path SHA256>` records the operator's attestation. Approval
is in memory, expires after five minutes, and authorizes only one exact forward
planner output. The saved JSON cannot unlock a later run. A future `--run` using attended review must
repeat the same attended check with `--approach-target` and a new review directory
before acquiring the motion session. A changed target/path, other phase, expired
or consumed approval leaves the default 0.45 rad cap in force. Starting state changes over 0.008 rad
require a new check. Exact approved waypoints are checked again at dispatch.

The trusted Python `path_clearance_check` seam remains for integrations/offline
tests; model text cannot set it. No automatic scene-clearance checker is installed.
**No station verification or trial was performed for this code change.**

`model_round_trip` records full HTTP response reception (including failures),
separately from per-waypoint `motion_time` (RPC, acceptance logging and cadence
wait; excludes image capture, feedback checks and passive decision settling).
These are host timings, not encoder-confirmed physical completion. Station
observation/decision events retain timestamps for the remaining trial overhead.
No timing claims are made from offline simulated responses.

Reproduce without sockets, model inference or dispatch:

```sh
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/offline_run007_approach.py
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -m unittest discover -s integrations/inspect_robots -p 'test_*.py' -q
```

Final descent and closure require supported relative height, alignment and swept
clearance from delivered observations and accumulated motion evidence. If those
remain unresolved, report precisely which candidate is blocked and why safe
alternatives cannot progress; do not wait for another camera or a human image.

The historical sections below describe earlier runs and their then-current caps.

## Larger exploration and experimental gains (2026-11-12)

The prompt now asks for clearly visible displacement during initial exploration:
0.08–0.12 rad single-joint probes in clear free space, up to 0.15 rad after
checking measured tracking and swept-path clearance. Supported free-space travel
uses 0.10–0.15 rad moves; approach/contact uses 0.01–0.03 rad or smaller.
The adapter now permits 0.15 rad per tool move. Interpolation remains 0.003 rad
per step at at most 30 Hz; gripper caps and all feedback/hold checks remain.
There is no calibrated Cartesian distance guarantee.

The actual YAML follower gains are now Kp `[300,450,450,120,30,30,30]`,
Kd `[6,6,6,1.8,1.8,1.8,1.8]`. They affect both YAM Pro followers and callers
of the shared gripper config. Dynamixel leaders do not consume these gains.
These are experimental values, not validated hardware tuning. Higher stiffness
can improve position tracking but cannot guarantee displacement or bypass torque
limits. See [gain details and restoration](../../YAM/docs/PD_GAINS_20261112.md).

Backups were made before editing; verify them with
`python3 integrations/inspect_robots/restore_experiment_gains.py` from the
workspace root. After the experiment and normal hardware-owner shutdown, add
`--restore` to restore the initial defaults, or `--baseline prior --restore`
for the immediately preceding supplied gains. Restart normally after applying
or restoring gains: existing processes cache them. Confirm fresh runtime
Kp/Kd diagnostics before evaluating tracking. No daemon restart or hardware
trial is performed by these source changes. Existing run-001/run-002 artifacts
and their consumed attempt latches are preserved.

Validation for this update: nine Astra adapter/agent tests pass, including a
0.12 rad move through the actual upstream tools with unchanged interpolation,
the 0.15 rad boundary, tracking rejection, both restoration baselines in a
temporary directory, and corrupt-backup rejection before writes. Fresh offline
configuration loaders return the new seven-element gains; backup verification
and default preflight pass. Physical tracking remains untested.

## Next Astra YAM3 episode: run-002 (2026-11-12)


Run-001 executed on 2026-11-12 and stopped voluntarily after **3 calls**, one
+0.02 rad J0 probe and a one-second hold. It did not attempt a grasp. See
[the evidence and run-002 changes](RUN_002_POSTMORTEM.md). The original replay,
frames, prompt and attempt latch are preserved. The next `--run` consumes the
separate fixed `astra-yam3-run-002/` latch; it cannot resume either attempt.

Run-002 adds a local `StationPolicy` subclass around the unchanged upstream
policy. Before each policy decision it passively allows 1–3 seconds of settling
after changed targets (arm residual tolerance 0.008 rad), then refreshes both
preview views and measured joints. A residual over 0.08 rad stops; a smaller
persistent residual is reported without compensation. Existing freshness,
deadline, recorder and session checks run throughout the wait. This wait adds
wall time, not environment motion steps; the daemon keeps its existing target.
The first observation and decisions after an explicit sleep refresh immediately.

The policy also receives `main_comparison`: a derived side-by-side display crop
of main pixels x=96:544, y=192:448, resized to 640×366 per side. LEFT is the prior
policy decision; RIGHT is the refreshed current decision. The initial pair is
identical. Full main/left/right views remain available. This fixed crop is display
only; it may omit objects if the scene changes and supplies no geometry/depth.
Measured joint changes and JPEG-content-change flags accompany it. Hash changes
are explicitly not proof of useful motion or fresh exposure. Decision refreshes
and the comparison JPEG are persisted in the journal; the upstream rollout still
stores its ordinary per-step observations. `decision_observation` events identify
the actual refreshed policy input and preserve rollout step/approval metadata.
The comparison JPEG is recorded as a derived view, not shown as a third physical
camera in the two-camera replay player.

The prompt distinguishes low-signal exploration from hazardous ambiguity: up to
six distinct free-space probes, normally 0.08–0.12 rad and up to 0.15 rad in visibly clear free space, with settling and
reversible directions, instead of stopping solely because one probe was
inconclusive. Immediate stops for unsafe clearance, stale/inconsistent imagery,
tracking failure and controller limits remain. It names all task milestones and
requires retention-on-lift plus withdrawn post-release verification. The larger
budget accommodates small bounded moves and up to ten calls each for full
closure/reopening. Termination kind/reason are saved separately from evaluation
completion, and physical success remains unverified even for model `done`.

Validation: **28 integration tests pass**, including the actual upstream
agent/Responses/tools/eval with injected station and HTTP, passive settling for
the observed partial-response case, bounded stalled-response waiting, before/after
image ordering, refreshed readback, preserved rollout step, and hard residual and
stale-feedback stops. Default offline preflight passes. No new model request or
hardware run was made. These checks validate software behavior, not Astra's
future decisions or physical pick-and-place success. Full success is an untested
hypothesis until approach, grasp, retention, placement and release are observed.

The supervisor's updated approach is prepared in [astra_run.py](astra_run.py).
**A grasp example and verified camera-to-robot calibration are not prerequisites.**
[astra_exploration.txt](astra_exploration.txt) instructs Astra to learn local
joint-to-image relationships through visible reversible free-space probes, compare predictions
with subsequent views and measured feedback, refine its estimate during approach,
grasp and placement, and stop if critical uncertainty cannot be resolved.
The task remains right-arm pink block → fully inside white paper marked X.

This reuses the actual pinned upstream `LLMAgentPolicy`, Responses client,
`control_tools`, `move_joints`, `done`/`give_up`, residual reporting, transcripts,
wire capture, `eval`, guards and frame/record logging. It does not implement a new
agent loop, IK stack or semantic pick compiler. The unmodified agent's 11 source
files are in `vendor-agent/`, at the **same upstream commit** as the existing
core (`772019598e42ccc7921a043dee3a2ea2692020ea`, agent version 0.30.0).
[File pins](vendor-agent/upstream-files.json) verify Git blob SHA-1 and SHA-256;
the included MIT license and upstream README describe the original functionality.
The small `.dist-info/METADATA` is local packaging metadata for upstream's version
lookup. This is still the public fallback revision, not a claimed QZ checkout.

Only the station boundary is new: seven right-arm joint/gripper dimensions map
to the existing daemon's 14-D interface while the left target stays at its initial
readback. It reuses `SupervisedMotionSession`, `SafetyClient`, `ZMQClientRobot`,
daemon identity/watchdog checks and the existing observer's `PreviewClient` and
measured-state parser. It starts no services and opens no CAN or camera drivers.
Reset only observes; no homing, calibration or parking is implicit. The outer
session requires confirmed SAFE HOLD at completion or failure. Existing heartbeat
and hardware watchdog enforcement remains in the daemon; untimed RPC commands
retain their last setpoint while Astra thinks, as in the existing return-zero
client (no background stream of generated motion).

From the workspace root, offline preflight (default; no sockets/API/motion):

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py
```

Read-only station check, when its existing services are available:

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py --check-station
```

**The following command executes hardware and calls Astra.** Use next, for the
attended experiment, with `RHOS_API_KEY` already exported in that shell. The
channel argument records the operator's physical identification of the task arm;
it is not camera calibration. Keep the workspace clear and stop control available.

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py --run --right-channel-confirmed can_yam_a
```

One episode means one scene/epoch, **up to 180 model calls**, zero HTTP retries,
5400 control steps and a 2400 s deadline checked before requests and commands.
An in-flight HTTP request can take up to its 120 s timeout; no subsequent command
is permitted past the episode deadline. The fixed new output directory
`astra-yam3-run-002/` is the durable attempt latch. It is created only after
station preflight passes; an uncertain/failed episode is never automatically
retried or resumed. Old trial latches and artifacts remain unchanged. Do not
remove the new latch to retry the experiment silently.

Routing is pinned to `POST https://api.rhos.cc/v1/responses`, bare
`gpt-6-astra`, medium effort, `store=false`, and the established actor header.
Upstream's supported transport injection adds that header and enforces the
destination and request budget. Optional upstream GPT-6 explicit-cache extensions
are disabled locally via the pinned client's `_cache_anchors` switch because
their compatibility with RHOS has not been established. Standard raw reasoning
item replay remains upstream behavior. No readable reasoning summary is assumed;
operator-visible motion notes and transcripts record supported observations.

The joint path bounds each motion to 0.45 rad/joint and 1.0 normalized gripper,
with interpolated waypoint maxima 0.003 rad / 0.02 gripper at at most 30 Hz.
The prompt requests coupled 0.25–0.45 rad transit moves and 0.03–0.06 rad terminal alignment. Daemon joint
bounds, source-state freshness (250 ms), left drift tolerance (0.05), and arm
tracking residual checks remain separate from camera geometry. Command clamping
by the daemon stops the run instead of continuing a stale interpolation. These
bounds are conservative experiment defaults, not a verified collision envelope.
There is no calibrated Cartesian/table collision model in this joint-only path.

Existing preview supplies `main` and `right` JPEGs; **exposure timestamps and
frame IDs are unavailable**. The policy is told this, and receive time/hash is
logged without claiming synchronized or fresh exposure evidence. The preview
server itself rejects frames over 1 s old; this adapter cannot independently
certify that contract over its JPEG-only endpoint. Astra must stop if images
appear frozen or inconsistent. Confirmed physical arm identity, functional live
observations and daemon readiness are still operational requirements. They are
not replaced by requiring a calibration bundle or reviewed grasp trajectory.

`framework-log.json`, the native wire/frame/action/record sidecars,
`station-events.jsonl`, `prompt.txt`, `attempt.json` and `attempt-result.json`
provide the audit trail. Events distinguish command acceptance, measured residual
and physical outcome; measured completion stays unknown unless established by
evidence. The scorer abstains: model `done` is not independent physical success.
Inspect the post-release frames to assess the entire block footprint and withdrawal.

### Hardware experiment replay (2026-11-12)

`astra_run.py --run` now records the existing main/left/right preview throughout the
session, including model waits, at a target 5 samples/s/view, plus policy frames.
It syncs camera JPEGs and append-only station events as they arrive. Every motion
has a dispatch ID saved before RPC, with matching acceptance saved after return;
a missing acceptance is uncertain. Recorder errors stop subsequent adapter actions.
The preview recorder uses its own HTTP client and never opens camera drivers.

After the motion context exits and requests hold, the runner automatically writes
`astra-yam3-run-002/replay.html`. Open locally in a browser: play/pause, scrub,
change speed, view both cameras and sample age, inspect station events and returned
Astra response/tool-call captures. Keep `replay-frames/` next to the page; copy the
whole run directory for viewing elsewhere. This is sampled preview playback with
actual receive timing, not full-rate synchronized video. Physical success and
camera exposure timestamps remain unverified.

Recover/rebuild a partial run offline, without framework finalization or hardware:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/hardware_replay.py --run-dir integrations/inspect_robots/astra-yam3-run-001
```

Complete journal rows survive normal cancellation and can be recovered after a
hard stop to the extent writes reached storage. The exporter counts malformed
rows and missing/hash-mismatched frames. An unfinalized run is labeled partial;
returned API content is available only for completed wire captures. A hard stop
cannot guarantee final hold or recover unsaved data. The old `replay.py` remains
for the historical mock format. Offline recovery tests cover cancellation,
uncertain RPC, disk failure before dispatch, idle camera capture and damaged data.
Validation: all 26 integration tests pass; offline preflight passes without
consuming the attempt. Replay JavaScript initial render, seeking, camera sources,
playback and event display pass a Node DOM-stub check. No live hardware run or
Astra request was made for this change.

Historical run-001 preparation validation: 22 integration tests and 5 existing replay tests pass.
The actual agent/wire/tools/eval loop passes an offline
HTTP fake test that rejects an oversized probe, executes a small synthetic probe,
then receives updated images/readback and terminates. No experiment API or motor
commands are used by tests. Default preflight passed. The read-only live check
timed out on safety `status` at localhost:6002; live daemon/camera readiness and
RHOS tool-call compatibility are **not verified**. `RHOS_API_KEY` was absent in
the preparation process. These were historical preparation results; run-001 subsequently executed and ended in give_up (see the postmortem).

The station Python lacked `httpx`/`websockets`; PyPI access was blocked. Existing
installed pure-Python copies and their licenses were copied from
`/home/rhos/dream-env/lib/python3.12/site-packages` into isolated `runtime-deps/`
(no shared environment edits, compiled extensions excluded). Versions and file
SHA-256 values are in `runtime-deps-lock.json`; import and the real offline agent
test passed under the station's Python 3.11. Nothing is fetched at launch.

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -m unittest discover -s integrations/inspect_robots -p 'test_*.py' -v
```

The sections below describe the **earlier saved-frame/mock workflows**. Their
closed semantic gate and former calibration/primitive prerequisites apply to
those historical trials; they do not block this new exploratory joint-control
episode or modify its authorization.

## Policy evidence UI (2026-11-10)

Open [the saved Astra response view](first-action-ui/replay.html) or
[the simulated framework replay](replay-ui/replay.html). Both use the extended
existing `yam3_trial_replay/replay_template.html`; no service is required.
The saved-response view has four scrub positions: proposal, validation, blocked
command acceptance, and unavailable measured completion. Evidence appears only
at its stage. This is synthetic ordering, not measured timing.

Panels separate returned reasoning summaries, the proposal's `reason`, semantic
validation/motion permission, command acceptance, measured completion, input
observation identity/freshness, and physical outcome. Assistant output, function
calls and token metadata have a separate expandable panel. Unrecorded values
remain missing. Mock state cannot establish physical completion.

Trial 005's actual saved response has `summary: []`: the UI reports **NOT RETURNED**.
`policy_evidence.py` accepts readable `reasoning.summary[].summary_text` parts
and excludes encrypted content and unrelated response metadata. Reasoning token
counts do not provide readable reasoning. Future Responses requests can request
`reasoning.summary="auto"` where supported, per the
[official OpenAI documentation](https://developers.openai.com/api/docs/guides/reasoning).
No request configuration, saved response, attempt latch, gateway transport, or
hardware path was changed. This viewer consumes completed saved responses; live
streaming and RHOS summary availability have not been tested.

Inspect Robots already supplies policy transcripts, inference/approval/error
events, frame sidecars, JSON logging and its own HTML viewer in the pinned core.
Our bridge must explicitly carry station-specific validation, mock outcomes,
observation freshness, and acceptance versus measured-completion evidence into
this UI. It does not automatically obtain unavailable Astra summaries or supply
station calibration, semantic-to-motion planning, or physical motion permission.

Reproduce the saved Astra view into a new directory:

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/first_action_mock.py --output integrations/inspect_robots/first-action-ui-new
```

The original `first-action-demo/` and `demo-output/` stay intact. The new views
use zero API calls, robot connections or robot commands. Regression checks cover
empty/multiple summaries, opaque-content exclusion, saved-response identity,
stage causality, actual JavaScript scrubbing, missing-data reset, and safe text
rendering. Browser layout could not be checked: installed Chromium exits under
the environment's socket restrictions. The JavaScript tests run with Node and a
minimal DOM, not a full browser.

## Trial 005 first-action boundary (2026-11-10)

No suitable lab-reviewed right-arm primitive was found locally. See the
[source audit and operator handoff](FIRST_ACTION_AUDIT.md) for exact candidates,
profile/mapping/calibration evidence and missing inputs. `first_action_mock.py`
replays the hash-pinned saved validated Astra response through a closed mock gate:
proposal → semantic validation → blocked acceptance → unavailable measured
completion. It creates no joint targets, hardware adapter or fresh API request.

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/first_action_mock.py --output integrations/inspect_robots/first-action-demo-new
```

The output must be new; checked artifacts are in `first-action-demo/`. Events use
synthetic ordering and never claim measured completion. Existing Trial 004 demo
and replay commands below continue to use their separate toy world.

This directory contains a working saved-frame mock integration using the **actual
Inspect Robots Policy, Embodiment, Task, eval(), EvalLog, frame store and JSON sink**.
There is no live mode. The default demo makes zero experiment API calls, zero robot
connections and zero robot commands. It neither reads NOTES nor credential files,
loads dotenv, invokes capture, nor starts a controller. Only socket calls that
would initiate network access are intercepted; the mock owns no device handles.

## Revision and QZ handoff

No pinned QZ checkout or QZ mock integration was found in accessible local paths.
Directory searches covered `/home/rhos` (including workspace), `/home`, `/opt`,
`/mnt`, and `/tmp` to bounded depths of 4–6. These are **local searches**, not a
claim to have accessed QZ. Direct GitHub cloning failed with “Couldn't connect to
server”; the connected GitHub reader could access the public upstream.

The reproducible fallback is upstream commit
[`772019598e42ccc7921a043dee3a2ea2692020ea`](https://github.com/robocurve/inspect-robots/tree/772019598e42ccc7921a043dee3a2ea2692020ea)
(2026-11-04). **This is not claimed to be the QZ pin.** `upstream-lock.json` records
that distinction. `vendor/` is an unmodified snapshot of all 51 core Python files
plus the MIT license, fetched by that commit; all 52 files have verified upstream
Git blob SHA-1 and local SHA-256 values. It excludes upstream environment examples,
agent instructions, credentials and dependency installation. Core needs NumPy;
this adapter additionally uses the station's existing Pillow and Python 3.11.

To reproduce QZ exactly, the missing inputs are **QZ's absolute checkout path (or
an accessible copy), its full 40-character core commit SHA, the QZ mock integration
path and revision/local diff, and any separately pinned agent/YAM plugin commits**.
No checkout path or commit for QZ can be inferred safely from this station. When
provided, compare its interfaces and transport before replacing this fallback.

The independently audited YAM plugin is
[`5a017d1b47b731f00cb7f26e59831dca03a15a71`](https://github.com/robocurve/inspect-robots-yam/tree/5a017d1b47b731f00cb7f26e59831dca03a15a71).
It was read through GitHub only; it was not installed, imported or instantiated.

## Run the mock

From `/home/rhos/workspace/jiaxin/shurui`:

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/demo.py --output integrations/inspect_robots/demo-new
```

The output directory must be new and inside this integration, outside `vendor/`.
Existing outputs are refused. The checked demo is in `demo-output/`:

- `framework-log.json`: stable copy of the genuine framework EvalLog schema 3.
- `paper-x-offline-integration_*.json`: framework JsonLogSink's canonical output.
- `records/`, `actions/`, `frames/`: framework-generated record, chunk, action and
  camera sidecars; numeric actions here are toy actions, never physical commands.
- `trial004.manifest.json`, `trial004.prompt.txt`: allowlisted input snapshots.
- `replay/replay.html`: offline play/pause/scrubbing using the existing template.
- `summary.json`: revision, three mock steps, zero-call/connection/command counts.

The reviewed source frame remains in Trial 004, SHA-256
`dd5b3634b293b5fa88cde2b24a2f49a687c531aeee8bdf9fef9fb9be01de6822`.
The loader checks this fixed hash and 640×480 RGB shape before using it. The source
manifest supplies the task, success criterion, logical right arm and registered
`pink_block` / `paper_x` targets. Its existing `dry_run.validate()` and strict JSON
parser are imported read-only; runner entry points are never invoked.

`SimulatedDecisionPolicy` implements `reset(Scene)` and `act(Observation) ->
ActionChunk`; `SavedFrameMockArm` implements `reset`, `step(Action) -> StepResult`
and `close`. Both satisfy the framework runtime protocols. They declare a **toy
4-D delta-position plus gripper space**, not the YAM 14-D joint interface. The
coordinates are illustrative constants, not camera grounding, IK or calibration.
The policy does not run Astra: it consumes `decisions.mock.json` as simulated
responses, checking each response's expected state against the observation it
actually receives.

The framework closes the loop: saved frame + empty mock gripper → simulated pick
→ updated held state → simulated place → updated released state → observation
request. The camera pixels intentionally stay unchanged; only mock proprioception,
object state, revision and observation ID update. A newly numbered mock observation
is **not** a fresh physical camera frame. Invalid semantic proposals become holds;
the mock also checks state preconditions and refuses mismatched numeric vectors.
Trial 004's `motion_permitted=false` remains intact: the only state changes are
in the toy world. No real execution permission is inferred from semantic validity.

The scorer abstains on physical task success. Framework `status="success"` means
that evaluation completed, not that a physical pick-and-place succeeded. A saved
frame and mock release cannot establish the required full-footprint post-release
placement inside the paper boundary.

## Framework log and replay bridge

Each `samples[0].policy_transcripts[0]` entry records input identity, decoded RGB
hash, mock state, proposal and validator result. `on_trial_end` copies the actual
framework `record.steps` into
`samples[0].trial_metadata[0].paper_x_transitions`, adding each `StepResult.info`
mock outcome and next input. This explicit copy matters: upstream's JSON EvalLog
does not automatically persist every per-step `StepResult.info`. The log also
records the source manifest/validator hashes, framework revision and unverified
physical outcome. Raw pixels are in framework frame sidecars, not transcripts.

`replay.py` reads this log with framework `read_eval_log()` and exports event IDs,
input→decision→next-observation links, labeled mock outcome panels and timeline
JSON. It reuses `yam3_trial_replay/replay_template.html`'s player, scrubbing and
local evidence UI, with mock-specific labels and its own rendered panels. Example:

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/replay.py \
  --log integrations/inspect_robots/demo-output/framework-log.json \
  --output integrations/inspect_robots/replay-new
```

All mock steps use an explicitly synthetic clock. This bridge does not attach
mock events to the unrelated stationary60 MCAP or claim device/host clock
alignment. The existing MCAP builder and its outputs are unchanged; its scan
allowlist does not ingest this JSON log automatically. No MP4 is generated by the
bridge. Browser rendering was not certified; HTML parsing, local image decoding
and exact event/observation references are tested.

## Astra through the approved RHOS gateway — identified, not enabled

At this core revision, the bundled
[agent plugin's provider resolver](https://github.com/robocurve/inspect-robots/blob/772019598e42ccc7921a043dee3a2ea2692020ea/plugins/inspect-robots-agent/src/inspect_robots_agent/_llm.py)
accepts an explicit `base_url` and `api_key_env`. Its
[Responses client](https://github.com/robocurve/inspect-robots/blob/772019598e42ccc7921a043dee3a2ea2692020ea/plugins/inspect-robots-agent/src/inspect_robots_agent/_responses.py)
posts `/responses`. The dormant routing description in `gateway-route.json` is:

```text
model=gpt-6-astra
base_url=https://api.rhos.cc/v1
api_key_env=RHOS_API_KEY
wire=responses
effort=medium
max_retries=1
=> POST https://api.rhos.cc/v1/responses, store=false
```

Use the **bare** model ID: explicit custom base URLs preserve it rather than
stripping `openai/`. An explicit RHOS URL prevents direct-provider/OpenRouter
fallback. No key value was read. This is configuration documentation, not a live
command or certification that the unknown QZ pin supports it.

There are unresolved transport and authorization gaps:

- Trial 004's approved transport also sends
  `x-openai-actor-authorization: local-image-extension`. The pinned agent's
  Responses client constructs only Authorization and exposes no `extra_headers`
  parameter. A reviewed client injection/transport change is needed; endpoint
  configuration alone is insufficient.
- `max_retries=1` is one HTTP attempt per request (zero is rejected), not a global
  one-request budget. The agent can make several requests in a tool loop. It does
  not enforce the existing trial attempt latches. They remain untouched.
- Agent tools normally emit numeric actions. Preserving Trial 004's constrained
  `arm/action/target/reason` proposal and validator requires this custom Policy or
  an equivalently constrained tool adapter. General agent tooling does not supply
  semantic grounding or motion permission.

No live client, credential loader, alternative endpoint, latch bypass or model
request was added to the mock.

## Station gaps before any future hardware adapter

The following comes from static source/config inspection only, not hardware
probing. This integration intentionally implements none of these live paths.

| Area | Audited YAM plugin | This station / gap |
|---|---|---|
| Controller ownership | `_default_driver_factory` opens both i2rt CAN drivers directly | `profiles/shurui-mcap.yaml` routes hardware/safety at localhost 6001/6002. GELLO `SafetyController` owns sessions, parent/collector leases, SAFE_HOLD/FAULT latches and measured limits. A future adapter must use that owner, not open competing CAN drivers. |
| Motor watchdog | Plugin driver pin is i2rt `ac096928d6899ddf852a71c5e8fbaa6055cd9745` | Station explicitly verifies a 400 ms watchdog on seven motors per arm before enable; upstream direct factory does not integrate this station's verification/session contract. |
| Arm model | Plugin driver call supplies channel, gripper type and zero-gravity mode; no `arm_type` override | Station `gello/robots/yam.py` explicitly uses `yam_pro` (motor mapping, dynamics and PD gains together). Local i2rt defaults to `ArmType.YAM`. This must be reconciled, not assumed equivalent. |
| Profile / mapping | Defaults `left=can0`, `right=can1`, 10 Hz | Versioned profile `version-0218d55f1295b4c9` has left `can_yam_b`, right `can_yam_a`, both 30 Hz. These are configured names only. Trial 004 explicitly leaves physical follower mapping unverified; repository README has older mapping guidance. No channel swap or physical identity is inferred. |
| Cameras | Slots `top/left/right`; V4L2 or RealSense sources | Station uses `main/left/right`, D405, 640×480 at 30 Hz; main configured serial `218622271430`, wrists `230322274623` / `230422273023`. Need explicit main↔top mapping, wrist binding/orientation and freshness verification. Profile has 100 ms max age / 25 ms skew; saved image has no fresh acquisition claim. |
| Units / right-only task | 14-D `[left 6 joints, gripper, right 6 joints, gripper]`, radians, normalized gripper 1=open | Toy space is deliberately incompatible with real joint control. Need verified ordering/signs, measured gripper endpoints/limits and an explicit stationary-left policy; invoking bimanual reset/park would otherwise affect both arms. Leader offsets in YAML are not follower Cartesian calibration. |
| Limits and lifecycle | Own clamp, optional collision/thermal/settling, zero gravity, home/reset/park/close behavior | Need parity with station safety leases, per-step limiting, fault handling, calibrated home/rest and shutdown. Plugin construction/reset/close can calibrate grippers, home or park arms. None of these methods were invoked here. |
| Geometry | Plugin supports EEF IK and measured collision base/table configuration; defaults include placeholders | Trial 004 says `geometry_verified=false`. No verified camera intrinsics/extrinsics, paper plane, arm bases, TCP/grasp pose or collision geometry is supplied by the reviewed image. Semantic IDs cannot become physical poses without these. |
| Success | Operator/VLM or task scorer | Require a genuinely new post-release observation verifying the whole block inside the white paper boundary. Mock location `paper_x` cannot substitute. |

Source anchors:
[YAM config](https://github.com/robocurve/inspect-robots-yam/blob/5a017d1b47b731f00cb7f26e59831dca03a15a71/src/inspect_robots_yam/config.py),
[YAM driver factory](https://github.com/robocurve/inspect-robots-yam/blob/5a017d1b47b731f00cb7f26e59831dca03a15a71/src/inspect_robots_yam/embodiment.py),
[YAM lifecycle/calibration documentation](https://github.com/robocurve/inspect-robots-yam/blob/5a017d1b47b731f00cb7f26e59831dca03a15a71/README.md),
local `YAM/gello_software/gello/safety/{controller,watchdog}.py`,
`YAM/gello_software/gello/robots/yam.py`, `profiles/shurui-mcap.yaml`,
and that profile's allowlisted arm/camera YAML files. No report-token, bindings,
credentials or NOTES contents were opened.

## Verification and files

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -m unittest discover -s integrations/inspect_robots -p 'test_*.py' -v
```

17 integration tests pass: real interfaces/source hashes, observation feedback,
semantic and state rejection, numeric bypass rejection, log/replay linkage,
physical-success abstention, blocked network entry points, output isolation and
unchanged trial/replay/profile hashes, saved first-action boundary, readable
summary extraction and causal UI behavior. Existing replay tests pass (2 tests).
Total for this UI change: 19 passing.
No experiment or robot traffic is used by these checks.

All authored changes are in this directory: `demo.py`, `decisions.mock.json`,
`replay.py`, `test_integration.py`, `gateway-route.json`, `upstream-lock.json`,
`README.md`, `feishu.md`, `test-result.json`, the 52-file `vendor/` source snapshot,
and the generated `demo-output/` artifacts. Documentation impact is contained
here; no existing runner, trial bundle, attempt latch, YAM file or replay source
was edited. Git status cannot be used in this supplied workspace because its
`.git` directory is not a readable working repository; isolation is checked by
content hashes across the protected existing directories during the tests.

## Run 006 offline revision

The Astra prompt now uses task progress and specific uncertainty reduction
within the existing episode budgets, replacing its prompt-only six-probe count.
Main-view fingertip/block gap governs approach; wrist images refine alignment.
All motion and safety code is unchanged. The task still requires grasp, lift,
placement, withdrawal and post-release visual verification.

[Run 006 review](offline-review/run-006-prompt-revision/REVIEW.md) includes the
[prompt diff](offline-review/run-006-prompt-revision/prompt.diff), model-only FK
candidate replay and [saved-input result](offline-review/run-006-prompt-revision/saved-input-result.json).
Fresh Astra inference was unavailable (ConnectError); local checks are not a
behavioral pass. No live run was launched. `offline_run006_review.py` builds
saved step-446 requests/FK evidence offline; optional `--infer` sends only those
inputs and saves returned proposals, with no station or dispatch path.

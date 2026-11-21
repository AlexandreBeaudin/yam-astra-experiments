# Optional high-rate gripper diagnostics on PS

This recorder investigates the repeated right-gripper vibration after close/reopen.
It does not fix that behavior, detect sound, or authorize restarting a faulted arm.
No gains, endpoint calibration, commanded targets, force-limiter decisions, or
safety thresholds are changed. Recording is off unless explicitly enabled.

## Enable on the next controlled cold start

The hooks change the daemon's source fingerprint. First complete the station's
controlled shutdown with the arms supported and verify the old hardware owner has
exited. Do not try to hot-load this code into a running daemon or bypass an identity
mismatch. Since the preceding trial ended with unconfirmed hold, inspect/support
the arms and resolve the previous fault before any motor-enable operation.

In the **daemon terminal**, from the experiment repository root, before running
the existing profile-based startup command in `lab-transfer/PS_ASTRA_RUNBOOK.md`:

```bash
export YAM_GRIPPER_TRACE_DIR="$PWD/.local/gripper-traces/$(date +%Y%m%d-%H%M%S)"
export YAM_GRIPPER_TRACE_HZ=250
mkdir -p "$YAM_GRIPPER_TRACE_DIR"
```

Then use the normal attended daemon startup. Alternatively append
`--gripper-trace-dir /absolute/output/directory --gripper-trace-hz 250` to the
`experiments.yam_safety_daemon` arguments. Do not put these flags on `astra_run.py`.
The same environment variables work with the existing inline Python launcher.

Both follower grippers are recorded, including while the model is thinking and
while the daemon holds an idle pose. Each initialized gripper prints its trace
filename. Expect one `gripper-can_follower_r-PID-ID.jsonl` and one corresponding
left file, plus a `.status.json` sidecar for each. Startup/calibration itself is
not traced: metadata records the resulting endpoints before the robot update
thread starts. A recorder-start failure prints an exception and leaves control
unchanged; **do not assume capture is enabled merely because the daemon started**.

Before relying on the data, inspect the output directory and status sidecars:

```bash
ls -lh "$YAM_GRIPPER_TRACE_DIR"
cat "$YAM_GRIPPER_TRACE_DIR"/*.status.json
```

Require `writer_alive: true`, increasing `written`, `error: null`, and preferably
`dropped: 0`. If capture fails, diagnose recording without repeatedly provoking the
vibration. The recorder does not automatically stop or change motor control.

To disable on a later cold start, omit the CLI option and unset both variables:

```bash
unset YAM_GRIPPER_TRACE_DIR YAM_GRIPPER_TRACE_HZ
```

Do not restart the daemon just to stop logging while the arms are unsupported.

## Signals

The first JSONL row records channel, PID, endpoints, gains, configured force limit,
requested sampling rate and units. Subsequent rows include:

- Wall/monotonic sample times, cached feedback source timestamp, and sequence.
- Requested opening in normalized and raw motor coordinates.
- Target after force limiting and final submitted target after endpoint clipping.
- Normalized/raw measured position and velocity, motor-reported effort, temperature.
- Limiter clogged state before/after the update and its internal adjusted target.
- Endpoint-clipping flag, PD gains and feedforward effort.

Raw position/velocity are motor radians/radians per second. Reported effort is a
motor torque estimate, **not measured fingertip force**. Feedforward effort excludes
the motor's onboard PD contribution. Driver targets are before CAN quantization;
they are not proof that a motor received a command. Feedback is the cached sample
used by the controller in that update, not a synchronized response to that target.

The producer offers scalar snapshots to a bounded queue; a dedicated background
thread performs JSON encoding and file I/O. Queue overflow drops diagnostics and
increments counters. A writer or instrumentation failure disables capture and
reports an error rather than failing motor control. Status sidecars update roughly
every 0.25 s; inspect existing daemon logs as well after a fault.

Sampling is capped at the requested rate (250 Hz by default, configurable up to
1000 Hz), not guaranteed at that rate. Actual timing and unique feedback timestamps
are recorded. Repeated source timestamps are duplicate cached encoder feedback.
250 Hz sampling cannot resolve arbitrary high-frequency vibration or acoustic
whine. There is no audio recording. Files are flushed roughly every 0.25 s and at
normal close, but not fsynced per sample; abrupt computer power loss can lose buffered
rows. Pulling robot power does not by itself remove PC power or erase flushed data.
Disk usage grows with sample rate and run duration; use a writable local filesystem.

## Analyze offline

After a short **attended, approved diagnostic session**, or after a stopped trial:

```bash
YAM/gello_software/.venv/bin/python \
  integrations/inspect_robots/summarize_gripper_trace.py \
  "$YAM_GRIPPER_TRACE_DIR" --tail-seconds 10
```

In another terminal, replace the variable with the actual trace directory. This
command only reads files. It summarizes the final recorded window: sampling rate,
unique feedback timestamps, position/effort ranges, requested versus submitted
target changes, limiter transitions, endpoint clipping, and recording errors/drops.
Partial final lines are ignored and counted. It does not diagnose automatically.

A steady requested opening with changing submitted target and limiter state would
support a limiter-interaction investigation. A steady submitted target with changing
position/effort would point toward tracking or mechanical dynamics. An endpoint hit
requires comparing the calibrated raw endpoints and actual mechanism, not merely
observing normalized 0.90. Compare with the trial command journal and daemon log;
source timestamps permit alignment even while no model observation was recorded.

## Verification and limits

Offline fake-hardware tests compare submitted position, velocity, torque and gains
with capture on/off, including limiter transitions. They also exercise queue overflow,
disk failure, malformed diagnostic data, sampling, shutdown and partial-file analysis.
These checks establish software behavior on those inputs. They do not establish
live timing, safe endpoints, stable gains, or physical acceptance of the gripper.
Use a supervised diagnostic check before another long autonomous trial.

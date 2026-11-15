# Trial 005: stop at the mock boundary

**Historical Trial 005 audit.** The supervisor subsequently authorized learning
camera/robot spatial relationships during one exploratory grasp episode, without
a grasp example or verified camera calibration. See
[the new preparation and launch workflow](README.md#one-astra-yam3-episode-gradual-spatial-exploration-2026-11-10).
The missing-primitive/calibration checklist below describes the former semantic
`pick` adapter approach; it is not a prerequisite for the new bounded joint-tool
path. Its observations about station ownership, mapping and missing measurement
evidence remain relevant. The old trial artifacts and closed gate are unchanged.

Local static audit, 2026-11-10. No suitable lab-reviewed right-arm pickup primitive
was found in the accessible station/integration sources. This is an absence of
review evidence in those sources, not a claim that the lab has no such example.
No executable hardware adapter was added. No hardware module was imported.

## Exact candidates and why they do not qualify

- [`run_return_zero`](../../YAM/gello_software/scripts/return_zero.py#L34)
  uses [`SupervisedMotionSession`](../../YAM/gello_software/gello/safety/motion_session.py#L12).
  It reads measured joints, commands **both** followers and opens both grippers;
  its [target/trajectory/feedback checks](../../YAM/gello_software/gello/safety/return_zero.py#L8)
  expect 14 joints ordered left J1–J6/gripper, right J1–J6/gripper, radians and
  normalized grippers (1=open). Verification requires three consecutive samples
  within 0.03 rad / 0.05 gripper units, with a three-second timeout. It cannot
  implement `pick(pink_block)` or guarantee an inactive left arm. It names no
  camera/TCP calibration or lab approval for a right-only task.
- [`run_replay`](../../YAM/gello_software/gello/data_utils/replay.py#L423)
  uses that same session and joint-limit checks, moves to the episode start pose,
  then replays full joint vectors. It needs a compatible recorded episode and
  measured tracking evidence. No reviewed right-only pickup episode, scene-bound
  grasp pose or approval linking such an episode to Trial 005 was located.
- [`FIRST_SUPERVISED_TRIAL.md`](../../yam3-sunday/FIRST_SUPERVISED_TRIAL.md#L8)
  is an unfilled readiness checklist, not approval or a motion implementation.
  Direct i2rt examples and the separately audited Inspect YAM driver factory do
  not meet the existing station-controller requirement.

Search scope: local workspace filenames, station scripts, safety/controller code,
motion-session tests, YAM documentation, yam3-sunday and this integration. No
external lookup, experiment API, live service query, NOTES or credential reads.

## Station configuration and calibration evidence

[`profiles/shurui-mcap.yaml`](../../profiles/shurui-mcap.yaml#L1) selects
`RhOS-nuc-1`, `127.0.0.1:6001` hardware / `:6002` safety, `yam-v1`, seven motors
per arm and 400 ms watchdog. Its arm YAMLs are:

| Config file, read statically | Configured follower | Rate |
|---|---|---|
| `/home/rhos/.config/yam/setup/version-0218d55f1295b4c9/right.yaml` | `can_yam_a` | 30 Hz |
| `/home/rhos/.config/yam/setup/version-0218d55f1295b4c9/left.yaml` | `can_yam_b` | 30 Hz |

These configured names do **not** establish physical right/left identity or the
effective running service profile. YAML Dynamixel signs/offsets/gripper values
describe the leader; they are not follower TCP or camera calibration. The local
[robot class](../../YAM/gello_software/gello/robots/yam.py#L14) specifies `yam_pro`.

[`YAM_CALIBRATION_CURRENT.md`](../../YAM/docs/YAM_CALIBRATION_CURRENT.md#L1)
documents `yam_chenyuan_20260915_v1` at
`/home/chenyuan/workspace/real2sim/configs/stations/yam_chenyuan_20260915_v1.json`,
with `.provenance.json` and release review HTML. Its stated acceptance is for
scene reconstruction and placement guidance, and explicitly excludes TCP,
workspace and contact dynamics. It does not establish applicability to today's
YAM3 setup. No suitable primitive exists here from which to infer an expected
motion calibration. Trial 005's [manifest](../../gpt_policy_trial_005/manifest.json)
explicitly retains `geometry_verified=false` and unverified physical mapping.

## Reproducible saved-decision boundary

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/first_action_mock.py --output integrations/inspect_robots/first-action-demo-new
```

Run from `/home/rhos/workspace/jiaxin/shurui`; output must be new. The checked run
is `first-action-demo/`. This reuses the existing integration's socket guard and
hash helper; imports only Trial 005's pure strict parser/validator, never its
runner entry point. It verifies fixed SHA-256 pins for response, manifest,
validator and decision journal, plus the manifest's saved-image hash, and compares
the decoded response and revalidation with the journal entry for that response ID.
It copies no raw response reasoning/opaque content into the replay.

`events.jsonl` has four linked, ordered events under a synthetic event clock:

1. Proposed action: saved Astra `pick`, logical `right`, `pink_block`.
2. Validation: semantic `valid=true`; `motion_permitted=false`.
3. Command acceptance: mock gate `accepted=false`, `submitted=false`, no primitive.
4. Measured completion: `not_available`, no measurements, completion unknown.

No mock acceptance or sensor measurements are invented. This is a replay journal,
not a new Inspect evaluation or physical success. The older `demo.py` toy vectors
remain unrelated to station motion and are not used here. No fresh Astra request
is made; historical fixture API usage is not counted as a call in this pass.

## Concrete missing evidence before one supervised action

The station operator/lab must supply and confirm:

- A reviewed primitive source file and revision, plus approval record, mapping
  exactly this decision to one bounded right-arm action through the existing
  controller/session. Include an explicit stationary-left policy and no implicit
  bimanual reset, homing, gripper calibration or parking.
- A profile-bound trajectory/pose/grasp file (or reviewed pose-producing code),
  with frame, units, right-joint ordering/signs/zeros, gripper endpoints and limits.
  Neither existing return-zero nor a generic episode replay supplies this mapping.
- Effective service revision/PID/profile hashes, physical `can_yam_a` identity,
  matching `yam_pro` model/gains, controller ownership, heartbeat leases, measured
  joint feedback, watchdog readiness and confirmed hold/stop drill evidence.
- A YAM3 calibration bundle and provenance binding current camera serials/views,
  intrinsics/extrinsics, base frames, TCP/grasp transform and table/collision
  geometry to this setup. The chenyuan calibration documentation alone is insufficient.
- Approved numeric bounds, speed/acceleration/effort limits and completion
  tolerances/timeouts; fresh timestamped state/images before dispatch, separate
  acceptance and measured settling/contact evidence afterward. A pickup result
  does not establish the eventual post-release paper-boundary success criterion.

Until these exist, keep the gate closed. Operator confirmation cannot turn an
absent reviewed primitive into one; implement and mock-test that mapping only
after the source and its station/calibration contract are supplied.

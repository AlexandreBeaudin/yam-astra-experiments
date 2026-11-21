# USB insertion trial 8 review

Trial 8 improved acquisition substantially: after one missed closure, the right
arm lifted the plug at 05:26, later set it down, and lifted it again at 26:11.
It still failed insertion. Most of the remaining time went into orientation and
visibility probes that did not establish the tip-to-port geometry.

This review used recorded commands, encoder feedback, gripper traces and selected
camera frames. It did not connect to the robot, change its prompt/code/settings,
or initiate motion. A report and analysis artifacts were added separately.

## Outcome and timing

| Event | Elapsed time / evidence |
| --- | --- |
| First closure | 03:19–03:22; missed the housing, then lifted/reopened |
| First successful closure | 04:58–05:02; measured opening stopped around 0.107 |
| First successful lift | 05:26, step 1387; plug visibly held, opening around 0.097 |
| Possible hub contact | 16:18, step 3867; J4 residual 0.0185 rad, followed by reversal and lift |
| Set-down/release | 22:06–22:10 |
| Regrasp closure | 25:42–25:46; followed by another visually supported lift |
| Second successful lift | 26:11, step 5734 |
| Second possible contact/binding indication | 29:33, step 6671; J1 residual 0.0154 rad, followed by reversal |
| End | 44:35.8, `give_up`; plug still held, not seated; final hold confirmed |

There were 78 motion calls, 79 successful model responses and three recovered
HTTP 503 responses: 82 HTTP attempts total. The run issued 7,993 waypoints. It
did not exhaust its 60-minute, 16,200-step or 270-request budgets. Model-request
intervals totaled about 30:20; recorded waypoint timing totaled about 04:28.
Other time includes observations, journaling, settling and orchestration.

The maximum recorded arm-joint residual was 0.0430 rad at step 1054. No
`guard_intervention` event occurred. The new 0.08-rad waypoint guard did not need
to trigger. Staying below that threshold does not establish collision-free motion.
All left-arm command targets remained fixed. Framework evaluation completion is
not physical task success; the result correctly retains `physical_success:
unverified` and records `give_up`.

## What improved

The shorter prompt led to a more decisive acquisition sequence in this episode.
The second full closure captured the housing, and the lift was visually supported
by both the main/wrist frames and persistent nonzero gripper opening under a
closed target. The first pickup took 12 motion calls, so it was slower than the
prompt's call-count goals, but much better than trial 5's two missed grasps and
no pickup over 25 minutes.

Astra also demonstrated that it could deliberately set the plug down and acquire
it again. That is useful evidence for the right-arm-only prerequisite discussed
before using a moving left observer. Two successes within one episode do not yet
establish repeatability across fresh setups.

This single comparison does not prove that deliberate mat contact caused the
improvement. Different positions, grasps and local motion choices also changed.
The first closure still missed despite lag near the mat, so lag/contact alone is
not a reliable indicator that both pads flank the housing.

## Why insertion failed

**A secure grasp did not establish a known insertion pose.** The first grasp left
the metal end visible but Astra repeatedly changed pitch and roll to determine
its direction. After regrasping the plug in the opposite orientation, it needed
a substantially different wrist posture. The offset between the plug's axis and
the gripper's axis was not established as a stable geometric reference.

**Visibility and manipulation were coupled.** In several near-hub wrist frames,
the fingers/housing hide the metal tip or the slots. At step 7730 an inspection
tilt reveals all four ports, but it also changes the plug pose and tip position.
The following pitch corrections hide useful relationships again. The parked left
camera does not adequately frame the right contact region in the selected frames.
The overhead main view supports global positioning but does not resolve all depth,
keying and clearance questions. No calibrated depth or force evidence was present.

**One prompt geometry statement is conditional, not universal.** `j5` rotates
about the gripper's approach axis. It rotates about the table vertical only when
that axis is vertical. Offline FK for the recorded final posture puts the gripper
approach axis about 89.3 degrees from base-down: almost horizontal. Thus the
instruction that `j5` rotates the jaws around a vertical axis cannot be applied
unchanged in that phase. Gripper-axis direction is also not the held USB-axis
direction. An image-space rotation or successful pickup does not establish keyed
USB orientation. These are nominal-model observations, not physical calibration.

**Probes did not converge on a consistent correction strategy.** Early large
pitch reversals were followed by repeated small reach, yaw and pitch changes.
After the second pickup there were 33 further motion calls before termination.
Many preserved a local pitch relationship, but no reliable tip-to-slot reference
emerged. Coupled shoulder/elbow corrections also changed height enough to create
possible contact indications. The two recorded reversals were appropriate
responses to those indications; the evidence does not identify the exact contact
point or prove contact solely from lag.

Giving up was justified once the available views no longer supported another
insertion move. The record does not show that a larger budget would solve the
missing geometry. No plug was released as a claimed successful insertion.

## Gripper instability remains

The relevant high-rate files are in `.local/gripper-traces/20261118-012442/`,
PID 3678120. Both status snapshots report zero dropped samples and no recording
error. The recorder remained active at the snapshot; analysis stops at the
recorded experiment end and does not require stopping the daemon.

| Right-gripper window | Requested opening | Motor velocity std (rad/s) | Motor-reported torque range (Nm) |
| --- | --- | --- | --- |
| 01:40–02:55, before first closure | 0.90 | 0.0108 | +0.081 to +0.095 |
| 04:20–04:38, after reopening from missed grasp | 0.90 | 1.8463 | -1.292 to +1.370 |
| 05:50–08:20, holding first grasp | 0.00 | 0.0093 | +1.013 to +1.048 |
| 22:35–23:10, open for regrasp | 0.90 | 1.8844 | -1.375 to +1.443 |
| 43:20–44:10, holding second grasp | 0.00 | 0.0099 | +1.018 to +1.048 |

During the vibrating open windows, the submitted driver target is also constant,
the force limiter is inactive, and endpoint clipping is absent. During the held
grasps, the limiter is active and the jaws are nearly stationary under load.
The left gripper stays quiet in these windows. This repeats trial 5's open-state
oscillation pattern; it does not establish a fix or identify the physical cause.
Motor radians are not jaw travel, reported torque is not fingertip force, and
there is no audio recording.

## What to explore next

1. Preserve the acquisition behavior that worked rather than rewriting the whole
   task again. Evaluate repeatable pickup across fresh setups before calling it
   reliable. Do not treat lag alone as proof of a successful housing grasp.
2. After a verified lift, explicitly establish the plug-axis direction, exposed
   metal length, and grasp-relative orientation before approaching one chosen
   port. Distinguish gripper verticality from plug verticality and treat the `j5`
   roll rule according to the actual posture. Inspection tilts require renewed
   alignment verification.
3. Use a visibility gate: tip, selected port and relevant pad clearance must be
   observable in the intended insertion posture. Repeatedly revealing them only
   by changing that posture is insufficient evidence for descent.
4. The next perception improvement should supply an independent close side view
   of the tip/hub while the right wrist stays still. That could be a stationary
   camera or the prepared left observer, after its station geometry/exclusion
   guard and physical clearance have been established. The prepared observer
   feature was not deployed or used in trial 8.
5. Investigate the recurring gripper instability separately; successful holding
   does not clear the vibration seen during reopening. No gain, endpoint, force
   limiter or motion-control setting was changed in this review.

## Evidence and export

Sources: `runs/run-008/attempt.json`, `attempt-result.json`,
`station-events.jsonl`, saved prompt and camera frames; console log
`.local/station-logs/astra-usb-insertion-008-20261118-035424.log`.
Exact statistics and analysis scripts are under `.local/trial8-review/`.

[Selected main/wrist frames](../../../../.local/trial8-review/key-frames.png)
show the first lift, second lift and late inspection view. The original replay
remains `runs/run-008/replay.html`.

From the repository root, export the trial at 32x:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir integrations/inspect_robots/experiments/usb-insertion/runs/run-008 \
  --speed 32 --fps 30
```

Output: `runs/run-008/replay_32x.mp4`. Change `--speed` to the desired multiplier.
The export command is supplied for the operator; this review did not launch it.

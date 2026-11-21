# USB insertion trial 5: gripper vibration and task failure

Analyzed on 2026-11-18, using recorded data only. No robot connection, actuation,
gain change, endpoint change, or trial-6 deployment was performed.

The right gripper developed a sustained measured oscillation after its first
full close/reopen cycle. The requested opening and submitted motor position were
constant during long vibrating intervals. This rules out Astra alternating
between 0.900 and 0.901 as the source of the sustained motion. The evidence points
to a control/mechanical stability problem; it does not yet identify the exact
physical component or prove which gain needs changing.

## Data and outcome

- Run: `runs/run-005/`, especially `station-events.jsonl`, `attempt-result.json`,
  and replay camera frames.
- Diagnostic traces: `.local/gripper-traces/20261118-012442/`, PID 3254532.
  Right: 294,336 samples; left: 299,778. Both status sidecars report zero dropped
  samples, zero unwritten samples, and no recording error. No malformed rows were
  found. These totals include time before and after the experiment.
- Recording requested 250 Hz; the steady comparison windows actually contain
  approximately 174–180 samples/s with distinct cached feedback timestamps.
- Experiment duration: 25 min 00.4 s; 50 model round trips, 49 motion calls,
  2,624 motion steps. It ended through `give_up`, with final hold confirmed.
  It did not exhaust the 60-minute, 270-call, or 16,200-step budgets.
- No verified pickup or insertion. Both full closures followed by lifts missed
  the plug. The hub was never approached for insertion. Framework evaluation
  completion is not physical task success.

## Timeline

Times below are elapsed from the first run event, approximately 01:26:10.9 local.
Command times identify when an interpolation begins, not physical completion.

| Elapsed | Recorded action / behavior |
| --- | --- |
| 00:11–00:34 | Initial opening is reduced from approximately 0.998 to 0.90. |
| Before 11:44 | Right gripper holds 0.90 quietly while the arm approaches. |
| 11:44–11:46 | First partial closure, 0.90 to 0.45. Transient motion decays; the subsequent hold is much quieter than the later sustained vibration. |
| 12:05–12:07 | First full closure, 0.45 to 0.00. |
| 12:10–12:40 | Closed gripper is nearly stationary, carrying approximately +0.697 Nm reported motor torque. The force limiter is active and its output is clipped at the calibrated closed endpoint. |
| 12:42–12:46 | First reopening to 0.90. Strong oscillation persists after the target settles. |
| 14:05–14:25 | A fixed 0.25 opening also oscillates strongly: velocity standard deviation approximately 1.74 motor rad/s and torque approximately −1.26 to +1.26 Nm. |
| 15:29–15:33 | Second full closure. The following closed hold is again nearly stationary under load. |
| 16:54–16:58 | Reopening to 0.90 restores the strong free-motion oscillation. |
| 19:50–20:15 | A fixed 0.35 opening also oscillates: velocity standard deviation approximately 1.53 motor rad/s. |
| 20:23–25:00 | Opening stays at 0.90; oscillation continues through the final retreat and termination. |

The operator heard vibration beginning with the first grasp. Encoder/torque data
locate the persistent free-motion oscillation at the first reopening. During full
closure, static loading can still produce sound; no audio was recorded, so the
trace cannot identify when every audible component began or ended.

## Same opening before and after: the decisive comparison

| Signal | Quiet 0.90, 10:00–11:20 | Late 0.90, 23:40–24:30 |
| --- | --- | --- |
| Requested normalized opening | Exactly 0.90 | Exactly 0.90 |
| Submitted raw motor target | Exactly 1.7338445106 rad | Exactly 1.7338445106 rad |
| Measured normalized opening | 0.900415–0.900489 | 0.897525–0.903156 |
| Measured motor-position excursion | 0.000381 rad peak-to-peak | 0.028992 rad peak-to-peak |
| Motor-velocity standard deviation | 0.00996 rad/s | 1.82725 rad/s |
| Reported motor torque | +0.0366 to +0.0464 Nm | −1.3114 to +1.3944 Nm |
| Force-limiter active / endpoint clipping | Neither | Neither |
| Gains | Kp 30.0, Kd 1.8 | Kp 30.0, Kd 1.8 |

Motor radians are not jaw width or fingertip displacement. Reported torque is a
motor estimate, not a measured force on the USB housing. A small opening error
can coexist with rapid movement and large alternating torque.

The right gripper rotor temperature rises from 26°C before the cycle to 34–35°C
near the end; the left gripper rotor remains at 26°C in the comparison windows.
This is consistent with additional right-gripper activity, not proof that heat
initiated the oscillation. The left gripper remains quiet under the same logger.

## What the evidence supports

**Astra command rounding is ruled out for the sustained intervals.** The target
does not alternate at either the application or post-limiter driver boundary.
The observed 0.898–0.903 values are feedback, not repeated Astra requests.

**An excessively large requested opening does not explain this trial by itself.**
The identical raw 0.90 target was stable before the cycle, and severe oscillation
also occurs at 0.25 and 0.35. No opening-endpoint clipping occurs in these steady
windows. A physical calibration/mechanism problem remains possible, but lowering
the prompt opening again would not address all recorded vibrating states.

**Force-limiter switching is not driving the sustained open-state oscillation.**
The limiter is inactive throughout the compared vibrating holds, and the final
driver target is constant. At full closure the limiter instead attempts a raw
target around 6.3784 rad, beyond the calibrated closed endpoint 6.3673991 rad;
the final clip holds it at that endpoint. This loaded closure behavior deserves
separate review, but its small pre-clip adjustments do not reach the driver during
that plateau and cannot explain later vibration at fixed open targets.

**Control/mechanical dynamics are the leading investigation.** Persistent motion
at a constant setpoint is consistent with a servo oscillation involving motor
position/velocity feedback and the mechanism. Candidate contributors include
unsuitable gains for this gripper, backlash/friction or a mechanically changed
state after closure, and timing/feedback interactions. The trace cannot separate
these causes or locate the exact vibrating part. Gains stay unchanged throughout;
`linear_4310.yml` labels them experimental, but that annotation alone does not
prove they are unstable. No motor firmware telemetry or acoustic measurement was
captured, and submitted targets are recorded before CAN quantization.

## Why the existing checks did not stop it

The runner's 0.08-rad tracking check compares the six arm joints, excluding the
gripper. It is not a gripper vibration detector.

The force limiter also has a relevant blind spot. In
`YAM/i2rt/i2rt/robots/utils.py`, `GripperForceLimiter.compute_target_gripper_torque`
uses `abs(mean(recent_effort))`, rather than the average magnitude or RMS. Large
alternating positive/negative torque can cancel. In the late comparison window,
signed mean torque is only +0.027 Nm, while mean absolute torque is 0.840 Nm.
Independent 100-ms bins have median absolute signed mean 0.048 Nm and maximum
0.217 Nm, below the 0.5-Nm entry threshold. The actual limiter trace confirms it
stays inactive. Its slow-speed condition and opening-release logic also mean it
is not designed as a general oscillation monitor. This explains missing detection;
it does not establish the limiter as the original excitation source. Replacing
the averaging expression alone is not a validated fix.

## Why manipulation failed

Astra repeatedly treated wrist-image overlap as a possible grasp, while lacking
a side view that established both pads at housing depth. Two full closures and
subsequent lifts left the plug on the mat. The first lift's wrist frame at step
1871 and final overhead frame at step 2624 agree that the plug remains loose.
Earlier descent also produced over 0.015 rad arm lag and possible pad/mat contact;
Astra reversed that descent. Much of the remaining run repeated local centering,
pitch, and depth probes without resolving the contact geometry.

The failure is therefore not explained solely by vibration: grasp depth was
already unresolved, and the record does not prove that suppressing vibration
would have made either grasp succeed. Vibration adds an independent hardware
problem and may worsen contact/image stability, but that contribution is not
measured here. The new observer view could help the geometry problem; it does not
fix gripper dynamics.

## Next investigations, in order

1. Resolve the gripper instability before another long autonomous trial. With the
   mechanism supported and power safely disabled, inspect the right gripper's
   drive, fasteners, finger alignment, binding, and mechanical end stops. Check
   calibrated follower endpoints against actual travel; leader trigger calibration
   in the station YAML is a separate mapping.
2. Review the experimental gripper gain/configuration history and motor settings
   against a known stable configuration for this exact mechanism. Do not assume
   that increasing damping or reducing stiffness by an arbitrary amount is safe.
   Make any later changes separately and preserve backups.
3. After that review, use a short attended, unloaded, recorded bench check rather
   than another USB episode. Compare the same intermediate opening before/after
   a close/reopen cycle, one variable at a time. Stop the check on recurrence;
   do not keep exciting it to collect a longer trace.
4. Add a dedicated gripper oscillation diagnostic based on windowed velocity and
   alternating torque/position activity, with fresh feedback and explicit handling
   during closure versus free motion. Validate any response without abruptly
   releasing a held object. Do not rely on Astra hearing vibration or on average
   position error alone. No such controller change was made in this analysis.
5. Separately improve grasp-depth evidence through a verified side/observer view
   and a clearer grasp geometry. Require a visual lift test; near-zero gripper
   feedback alone does not prove the housing is held.

## Reproducible outputs

Analysis scripts, extracted arrays, exact steady-window statistics and figures:
`.local/trial5-analysis/`. Run `analyze.py` with the experiment virtual environment,
then `inspect_windows.py`. Plotting uses a separate existing environment containing
Matplotlib; no dependencies were installed into the control environment.

[Evidence plot](../../../../.local/trial5-analysis/trial5-gripper-evidence.png)
and [PDF](../../../../.local/trial5-analysis/trial5-gripper-evidence.pdf).
The plotted lines connect irregular samples; they are not an acoustic waveform.
An apparent roughly 50-Hz component in resampled feedback is not treated as a
certified physical vibration frequency because sampling, aliasing and cached
feedback timing limit that inference.

# Plug-socket trial 1: grasp worked, insertion geometry did not

The right arm picked the charger up twice, but never established a verified
insertion-ready grasp and prong orientation. It ultimately returned the charger
to the mat, released it and lifted clear. No insertion was attempted or verified.
An independently positioned side observer is a sensible next experiment, but
simply unmasking the currently parked left camera would not supply the missing
view. The observer cannot compensate for an unsuitable grasp by itself.

This review reads recorded commands, camera frames, attention reports and logs.
No robot connection, motion, model query, prompt change or controller change was
performed. Analysis artifacts and this report were added separately.

## Conditions and outcome

- Actual saved API request: `gpt-6-astra`, reasoning effort `medium`. This trial
  predates the fresh-run default change to `gpt-6.2-astra`.
- Strict baseline: seven right-arm controls; all left command targets remained
  fixed. Every decision marks the left view masked. The left archival images
  discussed below were not provided to Astra.
- Duration: **56 min 11.8 s**. End reason: `give_up`; final hold confirmed.
- 92 API attempts, all HTTP 200; 80 move calls, 10 sleep calls, one observation
  recheck and one give-up. There were 12,649 accepted waypoints and no recorded
  guard-intervention event.
- It did not hit its 60-minute, 16,200-step or 270-request limits, although it was
  near the time budget. Recorded request intervals totaled approximately 37:13;
  waypoint-command intervals approximately 07:03, including holding commands.
- There were six full closing attempts: two visually supported pickups and four
  misses according to the subsequent observations/notes.
- Maximum recorded arm tracking error was **0.0426 rad**, on wrist pitch J3 at
  step 8975. That is below the 0.08-rad hard stop; staying below it is not proof
  of collision-free motion. The separate 0.015-rad task-level lag cues occurred.

## What happened

| Elapsed | Evidence and behavior |
| --- | --- |
| 02:49–02:53 | First closure; subsequent lift did not capture the brick. |
| 07:52–07:56 | Second closure captured the brick; it remained held with a measured opening around 0.49 rather than closing empty. |
| 08:19 | First visually supported lift, step 1496; prongs were clearly visible in the wrist image. |
| About 09:00–21:00 | Inspection, pitch/roll changes, and multiple holds while a person entered the workspace. |
| 15:06 / 15:38 | Positive J3 near +0.65 tilted the first grasp upward rather than producing a verified downward-prong insertion pose. |
| 22:26 | J3 commanded -1.63 but measured about -1.606, approximately 0.024 rad residual; Astra reversed the pitch and recovered tracking. |
| 28:28–28:31 | First set-down/release, followed by inspection and attempted reversed grasps. |
| 33:56–42:09 | Three further closures missed; notes also briefly misidentified the flat charger as upright, then explicitly corrected that interpretation. |
| 45:13–45:16 | Sixth closure captured the charger again. |
| 45:57 | Second visually supported lift, step 10605. |
| 49:19 | Positive J3 near +0.75 again tilted the wrist upward; the rear USB face was visible but both prong tips were not. |
| 50:14–50:46 | J3 was reduced to about +0.30, then the hand was slightly lowered/shifted for inspection. Prong clearance remained unresolved. |
| 54:47–54:51 | Released on the mat. |
| 56:11.8 | Finished with open hand clear, confirmed hold, and `give_up`. |

The human-presence holds were appropriate responses to people entering the motion
area. They account for ten sleep calls requesting 108 seconds of explicit holding,
plus model/observation time around those decisions. They make wall-clock duration
a less clean measure of autonomous progress and should be recorded as a condition
of this baseline, rather than treated as ten failed manipulation moves.

## Why it failed

### 1. The positive-pitch recipe was conditional on the actual grasp

The prompt described a reversed grasp in which positive J3 would put the hand
above the brick with prongs down. The first pickup did not establish that
relationship. Its prongs were visible, but positive pitch raised the held brick
and pointed the wrist camera across the room. Astra then tried the opposite
pitch direction and approached the lower J3 limit without establishing the
required insertion geometry.

After the regrasp, the same numeric recipe still did not guarantee the intended
posture. Nominal FK at the recorded positive-pitch poses gives a gripper approach
axis about 24–25 degrees ABOVE horizontal. At step 11837, reducing J3 to about
0.295 made that axis nearly horizontal. Thus a fixed positive J3 range cannot
replace accounting for shoulder/elbow configuration and the brick-to-gripper
orientation. These calculations describe the nominal gripper axis, not calibrated
physical prong angles, and are not motion commands or certified safe poses.

### 2. The useful view disappeared when insertion orientation was approached

During the first pickup the right camera clearly saw both exposed prongs. During
later positive-pitch inspection it mostly saw the charger rear/USB face and room
background. After the second pickup, the prong tips were hidden from the wrist
view, while the overhead main view did not resolve the underside and fingertip
clearance sufficiently. Lateral shifts did not reveal both tips.

This is stronger evidence for an observability problem than for a failed physical
insertion: there was no verified alignment and no actual seating attempt from
which to infer socket resistance, a wrong slot group, or insufficient insertion
force. The data do not prove that the prongs were physically blocked by fingers;
they show that their exposure and clearance could not be established.

### 3. Regrasping was slow and did not guarantee an insertion-ready result

After the first release at about 28:31, three attempted closures missed before
the second successful lift at 45:57. Image centering and the mat-level/lag cue did
not reliably establish that the rear rim lay inside the pad contact area. The
eventual grasp securely held the rear rim, but did not establish both exposed
prong tips in an independently observable insertion pose. Extra perception can
help distinguish these errors; it cannot mechanically move a badly held brick
into a valid grasp without a separate manipulation decision.

### 4. There was no useful independent side view available to the model

The baseline mask worked: the left policy slot was black. The separately archived
real left frames were inspected at the first lift, positive-pitch inspection and
late regrasp. Their timestamps are within about 0.07–0.09 seconds of the selected
decisions. They show cables and the right edge of the strip; the held charger and
its prongs are largely outside the field of view. Merely enabling the parked
camera would therefore not provide the needed side profile.

## What the attention records establish

There are 80 attention distributions, one per move call. Main was primary on 46
calls and right on 34; every move reported `cross_view_parallel`. That confirms
the model produced structured reports using the two available views and gives
locations to inspect in replay. It does not establish internal model attention,
accurate spatial understanding, or that another camera would cause success.
No billable counterfactual API queries were run for this review.

An offline heatmap video was generated from those reports; decisions such as
sleep, recheck and give-up without a move attention payload are shown without
invented attention overlays.

## Recommended next experiment

**Yes: test an independent close side view, while preserving a verified right
grasp.** Position it so one stable image simultaneously resolves both prong tips,
the chosen parallel slot pair, the vertical gap, and whether any finger hardware
would reach the strip first. Hold the right wrist in its intended insertion
posture while validating that view. The left arm should move for observation,
not to touch the charger or strip.

Before a full episode, use a short perception check to establish that the new
view actually contains those features. If not, reposition the camera rather than
spend another long episode on the same occlusion. Keep the grasp direction and
actual brick-relative prong axis explicit; change grasp if fingers obstruct the
insertion path. Do not reuse a fixed J3 number as a guarantee of perpendicularity.

The prepared active-observer mode is still deliberately blocked: the PS geometry
file has `verified: false` and null base transforms. Verified common-frame base
transforms and physical swept clearance are prerequisites before moving the
observer. Its registered >=0.16-m wrist/gripper-point guard is not whole-arm,
camera, cable or table collision certification. A stationary side camera is an
alternative if observer deployment is not ready.

For a clean camera comparison, keep the model fixed across conditions. Trial 1
used `gpt-6-astra`, medium; new fresh runs now request `gpt-6.2-astra`, medium.
Changing both model and camera at once is a valid new system trial but cannot
attribute any improvement to the camera alone. Use the same model for baseline
and observer when estimating the camera contribution.

No high-rate gripper trace for this November 19 daemon was found in the previously
used trace directory, and there is no audio. This review does not establish that
the previously observed gripper vibration was fixed or absent.

## Saved evidence

Inputs remain under `runs/run-001/`, including the saved prompt, attempt manifest,
result, command/attention journal, wire capture and original replay. Analysis
scripts and exact statistics are in `.local/plug-socket-trial1-review/`.

- [Selected main/right/archival-left frames](../../../../.local/plug-socket-trial1-review/key-frames.png)
- [Reported-attention heatmap MP4](../../../../.local/plug-socket-trial1-review/attention_heatmap.mp4)
- [Exact statistics](../../../../.local/plug-socket-trial1-review/analysis.json)

The original `runs/run-001/replay_16x.mp4` is preserved.

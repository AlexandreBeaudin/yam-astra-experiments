# USB insertion trial 1: failure analysis and next experiments

**Station:** PS, right YAM Pro follower. **Outcome:** plug not inserted; Astra ended with `give_up` after setting the cable down and withdrawing. Final hold was confirmed.

The trial contains **82.4 minutes of recorded execution across four segments**, excluding unrecorded downtime between resumes. It made 98 model requests. Recorded model-request intervals total approximately **65.2 minutes**, versus **5.4 minutes of recorded motion intervals**; the remainder includes observation, settling and other overhead. These are summed journal intervals, not a CPU profile. The trial was therefore dominated by repeated observation/decision cycles, rather than continuous robot motion.

## What happened

| Recorded time | Progress and failure |
| --- | --- |
| First segment, 0–40 min | Numerous housing-alignment and depth corrections. Several closures missed or occurred above the plug; lift checks revealed failed grasps. Astra recorded a visually confirmed lift around minute 35 and approached the hub. The 40-minute timer interrupted a subsequent move. |
| First continuation, about 3.9 min | The cable remained held, but orientation probes did not establish insertion alignment. A J3 command of −1.830 rad produced approximately −1.705 rad measured position: a 0.125 rad residual, exceeding the 0.08 rad tracking guard. |
| Second continuation, about 0.3 min | Recovery software generated a first waypoint inconsistent with its restored reference. The step-size check rejected it **before any command was dispatched**. |
| Final continuation, about 38.2 min | Astra moved inward from the wrist limit, tried further orientation and alignment probes, then set the cable down and attempted a revised grasp. That grasp obscured the metal tip. Astra released the cable and gave up rather than attempt an unsupported insertion. |

## Why it likely failed

**1. Grasp depth and contact geometry were repeatedly misread.** Astra often described the housing as centered or between the pads, but subsequent lift checks showed it still on the cloth. This suggests that image-plane overlap was being treated as evidence of correct grasp depth. The wrist view also changed with wrist motion, complicating comparisons. This is an inference from the recorded descriptions and outcomes; it does not establish a camera-calibration fault. The lift-verification rule worked and prevented several false success claims.

**2. A successful pickup was not an insertion-ready grasp.** After acquisition, the agent struggled to determine whether the exposed tip pointed toward the port's insertion axis. Later notes describe an upward-pointing tip, an opening facing the wrist camera, and roll changing keyed orientation without making the axis vertical. These are separate geometric requirements. The generic instructions “negative J3 pitches downward” and “use J4/J5 as needed” did not provide a sufficiently reliable method for satisfying them. Those station-dependent priors were not validated by this trial as a complete manipulation model.

**3. The exploration did not consistently accumulate useful knowledge.** The record contains repeated pitch, tilt and roll probes followed by reversals or renewed uncertainty. Coupled changes altered both the plug pose and wrist-camera viewpoint, making causal interpretation difficult. The prompt requests a visual explanation and intended action, but does not require a predicted observable effect, an explicit uncertainty to resolve, or a test of whether the probe succeeded. This likely encouraged locally plausible corrections without a stable estimate of plug-to-port geometry. There is no direct evidence here of a model context-overflow failure.

**4. Visibility deteriorated during regrasping.** The revised grasp lifted the cable but hid the metal tip behind pads/cable. That removed the evidence needed for keyed alignment. The prompt already required an exposed tip, so merely repeating that instruction is unlikely to suffice; visibility needs to be a checked condition for accepting the grasp.

**5. Software defects caused real interruptions but do not fully explain the task failure.** The SDK's outward-padded joint limits allowed the −1.830 rad target beyond the model's nominal J3 lower bound of −1.69297 rad. That is a plausible contributor to the tracking stop, but a mechanical hard stop was not independently established. The operator reported no visible external contact or binding. Target bounds were constrained, and the separate recovery-reference bug was fixed. Nevertheless, the final continuation still failed to establish insertion geometry. More runtime alone did not solve that difficulty.

Camera labels and exposure had been corrected before the trial; there is no evidence that the earlier preview brightness problem caused this failure.

## First priority: prompt experiments

Keep the existing lift checks, clearance requirements and tracking guards. Change one prompt component at a time so results remain interpretable.

1. **Make the state assessment explicit.** Require a brief account of tip visibility, housing grip, insertion-axis direction, keyed rotation, lateral alignment and remaining depth uncertainty. Mark unknowns as unknown rather than treating apparent image overlap as alignment.
2. **Make probes informative.** Before a probe, state the uncertainty, predicted image change and success criterion. Prefer changing one variable when diagnosing orientation, in visibly clear space. Afterward, compare prediction with observation and retain the result. This is not a requirement to use single-joint motion for every transit move.
3. **Add a strategy-change rule.** As an initial experiment, after three probes that do not resolve the same uncertainty, require a different view, a justified regrasp, or a stop. Do not repeat an unsuccessful pose without new evidence. The number three is a proposed test parameter, not a validated optimum.
4. **Plan the grasp backward from insertion.** Before closing, explain which housing surfaces will be gripped, whether the metal tip stays visible, and whether a plausible port approach remains available within joint limits. Reject a lifted grasp if the tip is hidden. Pickup alone is not progress toward every insertion pose.
5. **Use explicit stage gates.** Separate acquisition, lift confirmation, orientation verification, pre-insertion positioning, insertion and release verification. Each gate needs visible evidence; near-contact advancement must not be used to resolve an unknown insertion orientation.

Suggested addition to the task prompt:

> Before each alignment probe, identify one unresolved geometric question, predict the observable effect, and specify what evidence would resolve it. After the probe, compare the result with that prediction. Distinguish plug-axis direction from keyed rotation, lateral centering and insertion depth. Do not infer these from image overlap alone. Accept a grasp only after a lift check and confirmation that the metal tip remains visible and a feasible insertion approach remains. After three non-informative probes of the same question, change strategy rather than repeating wrist rotations. Never retry a target that caused a tracking stop or use contact to discover an unknown insertion axis.

Keep prompt text and runner-injected guidance consistent. In particular, replace broad station-dependent kinematic claims with qualified hypotheses or experimentally verified local behavior; prompting cannot change the robot's actual reachability.

## Second priority: camera views and supporting features

| Priority | Experiment to explore | Why it may help / what to verify |
| --- | --- | --- |
| 1 | Explicitly use the existing third view for cross-checks | All three camera views are delivered, but task guidance emphasizes main/right. Test whether the other view actually resolves pad depth or plug-axis ambiguity before buying another camera. |
| 2 | Add or reposition a fixed oblique camera near the hub | A stationary external perspective could show tip-to-port angle and vertical gap without rotating with the wrist. Verify that fingers do not occlude the tip during approach. |
| 3 | Provide synchronized crops and labelled feature overlays | Show the plug tip, housing, finger pads and selected port together with a wider clearance view. Include confidence/unknown states; overlays must not imply calibrated 3D geometry. |
| 4 | Validate local motion effects and add grounded pose assistance | Calibrated camera-to-robot geometry and tested Cartesian/IK tools could reduce trial-and-error joint reasoning. Validate TCP, reachability and swept-path clearance before using such tools for motion. |
| 5 | Evaluate depth and contact sensing | Depth may help estimate approach gaps, but small reflective USB surfaces and port cavities require measurement validation. Contact/force sensing could support later guarded insertion; it should not compensate for an unverified axis or justify raising tracking tolerances. |
| 6 | Reduce cable and hub variability with a documented fixture | A stable hub and accessible cable housing may separate alignment capability from scene variability. Report this as a changed task condition when comparing results. |

## How to judge the next trial

Compare time and model calls to first verified grasp, failed lift checks, time from pickup to an insertion-ready pose, non-informative probes, tip visibility, guard stops and independently verified insertion success. Report model waiting time separately from motion time. Start with a prompt-only trial on comparable hardware/views, then test a camera change separately. Budget extensions should support a defined experiment rather than hide repeated failure to reduce uncertainty.

Giving up was reasonable for the evidence available: the final grasp did not expose enough geometry to justify insertion. The next objective should be **reliable, visible insertion alignment**, not simply a longer attempt.

## Evidence

This analysis uses the saved task prompt, runner guidance, command/feedback journals, tool-call notes and final result. Model descriptions are useful evidence of its reasoning but are not independent measurements of physical geometry.

- [Combined replay](runs/run-001/combined/replay.html)
- [Combined 32× MP4](runs/run-001/combined/replay_32x.mp4)
- [Segment manifest and results](runs/run-001/combined/segments.json)
- [Task prompt](prompt.txt)

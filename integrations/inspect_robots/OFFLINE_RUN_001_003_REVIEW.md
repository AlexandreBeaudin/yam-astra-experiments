# Offline review: Astra runs 001 and 003

No API requests, station connections, robot commands, gain edits, or archive edits were made in this pass. Tests use fake station I/O and HTTP MockTransport. The saved-view test checks evidence delivery to the actual Astra policy implementation with scripted responses; it does not claim a new Astra inference or physical task completion.

## Reproduced failure

Run 001 measured a real response to its J0 probe but did not establish a reliable visual direction. Requested displacement was 0.02004414 rad; measured displacement was 0.00953689 immediately and 0.01411460 after holding, leaving 0.00592955 rad residual. It stopped voluntarily after three calls. This does not diagnose collision, frozen cameras, or inadequate gains. See the preserved [original evidence review](RUN_002_POSTMORTEM.md).

Run 003 transitioned from J1 target 0.1036 to J2 target 0.1734 at framework step 119. `ControlTools._move_absolute` used measured J2 **0.07381551842526868** for the newly named joint, keeping other dimensions at their held commands. `DeltaLimitGuard` used the previous approved J2 command **0.07343404287785127**. These differ by **0.00038147554741741** rad. The decision-boundary refresh did not reset the guard reference; passive settling did not eliminate the measured offset.

The old planner generated 34 waypoints, each incrementing J2 by `(0.1734 - 0.07381551842526868) / 34 = 0.002928955340433274` rad. Its first waypoint was **0.00331043088785068** above the guard reference, exceeding the unchanged 0.003 rad maximum. The planner advances by slightly less than 0.003 while the guard catches up at 0.003; the first five proposals exceed the guard envelope, and the sixth does not. This is an interpolation-reference defect, not evidence that the station rejected five commands or that gains caused clipping.

All values below are J2 radians. Planner start is constant across this chunk. Measured values are the first journal observation after each dispatch, not measured arrival. Framework uses zero-based action indices; station journal steps count completed dispatches, hence `station = framework + 1`.

| Framework / station step | Planner start | Guard reference before review | Proposed | Approved | Dispatched | Measured after dispatch |
| --- | --- | --- | --- | --- | --- | --- |
| 119 / 120 | .073815518425 | .073434042878 | .076744473766 | .076434042878 | .076434042878 | .074196993973 |
| 120 / 121 | .073815518425 | .076434042878 | .079673429106 | .079434042878 | .079434042878 | .074959945068 |
| 121 / 122 | .073815518425 | .079434042878 | .082602384447 | .082434042878 | .082434042878 | .076485847257 |
| 122 / 123 | .073815518425 | .082434042878 | .085531339787 | .085434042878 | .085434042878 | .079156176089 |
| 123 / 124 | .073815518425 | .085434042878 | .088460295127 | .088434042878 | .088434042878 | .082589456016 |

The framework review happens BEFORE `StationArm.step`. Dispatch target and acknowledgment `applied_joint_state` equal the already approved values on every row. Every acknowledgment has `limited=false`: the station did not further modify what it received. These are two separate intervention layers. The reproduction replays **all recorded actions** through the real bounds/delta guard, substituting the original proposed values from approval traces at the five affected steps; it asserts exact equality with recorded approved actions, dispatches and applied acknowledgments. This verifies the cause rather than inferring it from flags alone.

Reproduce without station access:

```sh
YAM/gello_software/.venv/bin/python integrations/inspect_robots/offline_transition_audit.py
```

[Full precision transition and measurement source times](offline-review/run-003-transition.json). Sources are run 003's `records/.../record.json` approval events, `actions/...jsonl`, and `station-events.jsonl` decision at step 119 and dispatch/readback events at steps 120–124. The historical trace also shows the rest of the J2 chunk continued before Astra saw the approval report and gave up; the new intervention stop prevents that delayed response.

## Smallest supported correction

1. Rollout passes a copy of the delta guard's actual last-approved reference into policy observations as `guard_reference`. The station checks that it matches its last accepted command; initial planning uses the station's initial command when the framework guard has no reference yet. Neither refresh nor model latency changes this command reference. Measured `joint_pos` remains unchanged.
2. Absolute trajectory generation uses that reference for its start and held dimensions. Existing speed, headroom, joint limits, gripper limits and total-move cap are retained. The saved transition still takes 34 waypoints to 0.1734, now starting at the commanded reference; the same guard passes every waypoint without modification. The optional field leaves other integrations' behavior unchanged.
3. `StationArm.pre_check` dry-runs the exact bounds/delta chain with an isolated store seeded from its accepted command before allowing a motion. It never advances the real guard or sends commands. A predicted guard intervention stops for review. An additional final guard raises `SafetyAbort` if an unexpected bounds/delta modification occurs during rollout, before station dispatch. Cause is journaled. The existing motion-session exception path requests confirmed hold; watchdog/session/deadline checks and stationary-left enforcement are retained. This pass tests the software stop path, not physical hold hardware.

Only two vendored framework files change (`rollout.py`, `tools.py`); no upstream agent files change. `upstream-lock.json` explicitly records these local patches, retaining original upstream SHA-256/blob identifiers and updated local hashes. The integration adds observation-only feedback and preflight/intervention handling in `astra_run.py`.

## Decision feedback and bounded recheck

Feedback separates encoder motion/residual, visible displacement, camera source freshness, and guard intervention. A small residual does not establish depth or clearance; JPEG differences do not establish a new exposure. `observation_recheck` refreshes both views, the derived comparison and measured feedback without dispatching an action. Its internal marker chunk is consumed inside `StationPolicy.act`, before rollout sees it. It is limited to two requests per motion and shares episode time/call budgets; repeated requests after that return an exhaustion explanation. Existing stale-state, left-drift, session and deadline checks still apply.

Preview adapters may supply a fourth tuple element with acquisition timestamp/frame ID. These are preserved in policy evidence and the journal. Acquisition age is reported against receipt time (0–1 s is labeled recent, negative/older ages stale or clock inconsistent). IDs are compared between decisions; a changed ID establishes a different source frame but not its age. With only receipt times, freshness stays **unknown**. The current saved runs supply no acquisition times or source IDs. No image noise heuristic promotes their freshness.

The real saved main/right pixels from **both** runs pass through the actual policy and Responses serialization in the regression. The mock model requests a recheck, receives refreshed views/reports and gives up with unresolved height/clearance. Decoded request image pixels must exactly match the selected saved views. No robot commands occur. The existing partial-motion regression separately verifies run 001's measured-motion feedback and passive wait without compensating its residual.

## Gains: evidence and limits

| Evidence | Kp, six arm joints then gripper | Kd, six arm joints then gripper | What is established |
| --- | --- | --- | --- |
| Original preserved configuration backups | `[80,80,80,40,20,20,20]` | `[5,5,5,1.5,1.5,1.5,0.5]` | Historical file baseline, not a per-run runtime readout |
| Supplied intermediate configuration backups | `[250,400,400,100,25,25,25]` | `[5,5,5,1.5,1.5,1.5,1.5]` | Intermediate file configuration |
| Current YAML and saved fresh-loader output in `codex-solving-early-giveup-astra-experiment4.log:2734` | `[300,450,450,120,30,30,30]` | `[6,6,6,1.8,1.8,1.8,1.8]` | Effective configuration for a fresh loader before run 003; unchanged in this pass |
| Run 001 archived runtime gains | Unrecorded | Unrecorded | Cannot certify loaded gains from its archived joint positions/acceptances |
| Run 003 archived runtime gains | Unrecorded | Unrecorded | Cannot certify daemon reload from current files or fresh-loader output |

Neither archive logs loaded Kp/Kd or a runtime configuration identity proving the daemon consumed those file settings. Do not silently assign current gains to either historical run. Existing daemons cache gains. Run 001 predates the documented tuning changes; the original baseline is historical context, not proof of its loaded settings. Fresh daemon diagnostics remain the station check needed to bind effective gains to the next run. Both followers use the shared Pro/gripper configuration; manual Dynamixel leaders do not consume these follower arrays. **Current gains and all backups remain in place.**

## First supervised checkpoint and remaining station checks

The first checkpoint is a **verified pre-grasp pose above the block**, fingers open, before descent or closure. It is not a completed pickup. The prompt requires pausing there for supervisor review; this is an evidence/review milestone, not a newly invented numerical Cartesian gate.

[Saved final main/right views](offline-review/saved-final-views.jpg) show the block left of the X paper in main, the right gripper to the paper's right, and the block absent from the wrist view. Run 003's wrist sees part of the X paper; run 001's wrist mostly sees table and fingers. Neither run reached or verifies pre-grasp. Main can establish visible object identity, approximate image alignment and some gross separation. It cannot resolve all fingertip heights or hidden swept links. The wrist view can constrain finger-relative alignment when the block becomes visible; in these views it cannot establish block-relative depth. The derived crop adds legibility, not depth or another viewpoint.

Measured joints establish local response and residual to setpoint, with freshness from measured source timestamps. They do not establish world approach direction, TCP height above the block/table, collision-free swept volume or contact. Run 003's positive-J0 leftward main-image shift and J1/J2 wrist scene shifts are local observations, not world-axis calibration.

Before clearing this checkpoint at the station:

- Obtain a source-fresh view that identifies the block relative to both open fingers. Bring the missing depth relationship into view using another observation/viewpoint with verified clearance, or use a lab-verified pre-grasp reference.
- Establish the approach direction and height/clearance using a lab-verified table/block/gripper reference or calibrated camera/robot geometry with matching joint zeros and TCP. Record a usable clearance margin and account for the entire arm, wrist camera, table and stationary left arm. No metric margin can be extracted defensibly from these saved uncalibrated views alone.
- Check actual measured tracking and loaded gains/configuration identity at that pose, camera source age/IDs, and supervisor review before descent or closure. Exercise the existing watchdog/stop/confirmed-hold behavior under the normal station procedure; this offline pass cannot verify its hardware response.
- Later verify retention after lift. Final task success needs a source-fresh post-release observation showing the **entire block footprint inside the paper boundary, with no ambiguous edge/occlusion, and the gripper withdrawn**. Acceptance, measured settling and model `done` do not substitute for that evidence; the physical scorer remains unverified.

## Offline validation and preservation

Run with:

```sh
YAM/gello_software/.venv/bin/python -m unittest discover -s integrations/inspect_robots -p 'test_*.py'
```

**35 offline tests pass** (latest full suite: 9.946 s). The saved-offset end-to-end test also exercises rollout reference propagation, decision refresh, planning, guard review and fake station dispatch across J1 then J2, with no approval events.

Regression coverage includes exact historical five-event replay; corrected transition with zero guard rewrites; real out-of-bounds rejection inside the total-move cap before dispatch; unexpected runtime clamp pause; saved-view serialization and observation-only recheck for both archives; acquisition/ID/receive-only freshness; existing tracking, stationary-left, stale-state, daemon-modification, transport-budget and preservation checks. Restoration tests write only temporary copies of gain files.

Both ZIP files and all extracted archived members are preserved. [Archive SHA-256 and member comparisons](offline-review/archive-integrity.json) record 597 matching files for run 001 and 1447 for run 003. New review artifacts live outside those run directories.

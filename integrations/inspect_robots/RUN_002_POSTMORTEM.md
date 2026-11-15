# Run-001 evidence and run-002 preparation

Run-001 did not fail during grasping. Astra voluntarily terminated before
approach, after one J0 probe and a hold. The next run is prepared to address
that early-stop mechanism. Full physical success cannot yet be predicted from
the available evidence: no approach, grasp, lift, transfer or release was tested.

## Evidence

Sources: [original replay](astra-yam3-run-001/replay.html),
[transcript](astra-yam3-run-001/transcripts/20261112_124023_1962a457/yam3-astra-one-episode-e0.jsonl),
[station journal](astra-yam3-run-001/station-events.jsonl),
[result](astra-yam3-run-001/attempt-result.json), and the saved prompt/frames.

| Decision | Evidence |
| --- | --- |
| Initial | J0 0.09975586 rad; block visible in main, absent from wrist view |
| Probe | Target J0 0.1198 rad; 7 interpolation steps; measured J0 0.10929274; residual 0.01050726 rad |
| Hold | 30 steps requested as one second; measured J0 0.11387045; residual 0.00592955 rad |
| Stop | `give_up`: could not resolve direction/clearance; explicitly cited requirement to stop on ambiguous feedback |

The exact requested delta was 0.02004414 rad; measured delta was 0.00953689
after the probe and 0.01411460 after the hold. The latter is approximately 70%
of the requested change. This is evidence of partial response, not proof of a
collision, bad controller gains, backlash, or wrong motor mapping. Those causes
cannot be distinguished by this run. No gains or channel mappings were changed.

There were 3 HTTP attempts of 60 allowed, 38 dispatches with 38 matching
acceptances, and a confirmed final hold. The give_up decision arrived at rollout
step 37. No rejection/fault appears in the saved events. The attempt lasted
about 48.3 seconds. The observed early stop was not budget exhaustion.

The overview shows only a small shift; the wrist image shows a small scene
shift with the block still absent. This supports Astra's low-confidence reading,
but does not establish that every other safe probe was unavailable. JPEG hashes
changed, which cannot independently prove camera exposure freshness.

The old prompt said both “learn using small probes” and “stop rather than infer
geometry” on ambiguous imagery. Astra explicitly chose the stopping instruction
after one weak experiment. The primary actionable diagnosis is an overly broad
abort instruction combined with weak before/after visual evidence and a
partially reached probe. This explanation follows the returned tool notes and
termination reason; it does not assume access to private model reasoning.

## Implemented changes

1. `StationPolicy` refreshes readback and both views at decision boundaries.
   After changed targets it waits passively for at least one second and at most
   three seconds, checking a 0.008 rad arm residual tolerance. A persistent
   smaller residual is exposed; a residual over 0.08 rad stops. No compensation
   target or retry is generated. All existing safety/session/deadline checks run.
2. `main_comparison` puts enlarged prior/current main crops side by side,
   alongside full main/right images. Readable reports include measured deltas,
   residuals and JPEG change flags with explicit limits on their interpretation.
   The exact refreshed evidence is journaled independently of rollout frames.
3. The prompt allows bounded alternative probes in visibly clear free space
   after a low-signal result. It does not require continuing when no clear probe
   exists. Up to six distinct <=0.02 rad probes may be used; limits, collisions,
   tracking failures and stale/inconsistent imagery still require stopping.
4. The prompt names the complete task sequence and retention/release evidence,
   permits a safe realignment after a failed grasp, and budgets for ten closure
   and ten reopening calls at the existing gripper limit.
5. The next attempt is the separate, exclusive `astra-yam3-run-002/` latch with
   180 calls, 5400 steps and a 2400 s deadline. Run-001 is not altered or resumed.
   The runner and prompt hashes and budgets are recorded at attempt creation.
6. Model termination kind/reason are saved separately from evaluation completion
   so a clean `give_up` cannot be mistaken for physical task success.

The original pinned policy/client/tools are unchanged. No motion cap, driver,
motor gain, left-arm policy, automatic retry, homing, calibrated geometry or
physical-success assertion was added. More time and better comparisons improve
the chance of learning a direction; they do not supply missing depth information.

## Validation and next experiment

28 offline integration tests pass. They exercise the genuine upstream policy,
Responses adapter, tools and eval using injected HTTP and station I/O. New
regressions cover the recorded partial-response case, bounded passive waiting,
before/after image ordering, refreshed measured input, rollout metadata retention,
and hard tracking/stale-feedback stops. The fake model's decisions are scripted;
these tests do not validate Astra's future decisions or physical manipulation.

Default offline preflight passes. No new API request, station connection or
hardware command was made during this preparation. Existing preview exposure
timestamps remain unavailable; no calibration or full physical simulation exists
in this path. Full success remains an untested hypothesis, not a supported
forecast or a numerical success probability.

The next attended run uses the documented command:

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py --run --right-channel-confirmed can_yam_a
```

Inspect the new decision comparisons to assess whether alternate probes establish
useful local directions, then require visible retention after lift and the whole
block footprint inside the paper after release with gripper withdrawn. Model
`done` and framework `success` alone do not satisfy that physical criterion.

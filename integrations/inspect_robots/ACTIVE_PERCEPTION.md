# Active observer control

The Trial 6 feature sources have been reconciled into this checkout. Both
`--task usb-insertion --active-perception` and
`--task plug-socket --active-perception` select the observer interface. Without
that flag, control remains the original seven right-arm dimensions. The current
right-only USB prompt and pick/place prompt are preserved byte-for-byte.

Observer mode exposes `left_j0..left_j5`, `right_j0..right_j5`, and
`right_gripper` (13 controls). `left_gripper` is inaccessible: every full command
retains its starting opening. The physical PS channels remain `can_follower_l`
and `can_follower_r`. Both arms use existing joint bounds, 0.80-rad per-call caps,
0.003-rad interpolated waypoint limits, fresh measured feedback and the existing
0.08-rad tracking threshold before every waypoint. Right opening is capped at
0.90 in observer mode. A wider starting opening can be held or reduced smoothly;
it is not clipped at startup. Existing right-only USB/pick-place behavior remains
unchanged. Observer mode uses guarded joint moves, not the right-only pose tools.

## Separation geometry

Registered FK uses the existing MuJoCo YAM Pro/linear-4310 model, not an IK solver.
It checks every left/right pairing of wrist (`tcp_site`) and gripper
(`grasp_site`) reference points throughout each candidate path and again from
measured pose to the next target before dispatch. A conservative chain-length
bound covers motion between checked samples. This is not whole-arm collision
checking: elbow links, cameras, fingers' complete volumes, cables, fixtures and
the table are outside this reference-point check.

For plug-socket, `experiments/plug-socket/ps-observer-geometry.json` is the default.
It starts with `verified: false` and null base transforms. Offline preflight is
available; observer `--run` fails before any station connection. No guessed
station registration or another YAM's geometry is used. Usable registration needs
proper 4x4 transforms from BOTH bases into a common station frame, translations
in metres, `verified: true`, and nonempty measurement provenance. Measured rail
spacing alone establishes a full transform only if mounting orientations, axes,
heights and the other offsets are also known. Plug-socket requires a registered
threshold plus margin of at least 0.16 m; its default is 0.16 m with zero extra
margin. The file bytes are hashed into the checkpoint identity.

For USB observer mode, `--observer-geometry PATH` can select registered FK or a
physically reviewed six-joint exclusion box for each arm. The latter is explicitly
labelled non-metric and does not certify 14/16-cm separation. Without geometry,
USB observer mode excludes all arm-joint movement and refuses `--run`. That
non-metric fallback is not accepted for plug-socket observer motion.

## Recording and resume

Observer runs retain all three camera views and use the task's separate
`prompt-active-perception.txt`. Plug-socket's right-only baseline instead masks
left and requires structured attention; see its README for the experimental
conditions. The ordinary USB/pick-place schemas do not acquire attention fields.

Normal unanswered-model checkpoints support both modes, but right-only and
observer histories cannot mix. The original seven-dimensional interrupted-motion
recovery flags are unavailable in observer mode. Geometry changes reject observer
resume. Plug-socket also pins its camera mask condition across resume. No saved
trajectory is automatically replayed.

For an observer run stopped specifically by `inter-arm interpolated separation
cannot be certified`, `--resume <segment> --active-perception
--recover-separation-stop` reconstructs a complete accepted motion prefix and
requires confirmed final hold plus matching live pose. Both arms restart from
recorded held feedback; the accepted right gripper command is retained. Astra
receives the interruption history and fresh views before choosing a new move.
The revised adaptive certificate may replace the old certificate only with
identical registered geometry, separation threshold and margin. Other faults,
unpaired dispatches, changed geometry and moved poses remain blocked. Recovery
creates a nested resume segment; the original checkpoint and evidence are kept.

## Offline checks

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task plug-socket --attempt 1
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task plug-socket --attempt 2 --active-perception
PYTHONPATH=YAM/i2rt:YAM/gello_software:integrations/inspect_robots \
  YAM/gello_software/.venv/bin/python -m unittest \
  test_active_perception test_plug_socket_attention test_waypoint_tracking
```

These commands do not connect to the robot or consume attempts. This feature was
verified with fake station RPC, synthetic model transforms, a real offline video
encoder and mocked API transport. No physical registration or hardware motion was
validated by those tests. Integration changes are loaded by a new runner; motor
control/gains and the safety daemon were not changed by this implementation.

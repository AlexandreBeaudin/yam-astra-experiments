# Platform extraction and YAM migration

Simulation/rendering is pinned across platforms to Isaac Sim 5.1.0 + Python 3.11 + PhysX; use `scripts/isaac_python.sh`. The model-only MuJoCo YAM FK/IK adapter does not change this rule. See [runtime migration](ISAACSIM_5_1_MIGRATION.md).

## Source boundaries

- `real2sim.camera`: calibrated saved-image projection, no robot imports.
- `real2sim.calibration.{board,selection,handeye}`: platform-independent supplied-transform eye-to-hand solve and conditioning/diagnostics. Xtrainer `calibration` files are compatibility imports; device capture, station-specific board configuration and Nova/controller FK remain in its repository.
- `real2sim.collection`: existing directory/JPEG recording bridge, selected projection frozen at record start. Xtrainer's `scripts/guidance_bridge.py` is a thin import facade. This adapter is for directory recordings, not MCAP.
- `real2sim.guidance.GuidanceSession`: robot-independent immutable bundle selection, projection bytes/digest, episode snapshots and export. Intended for both directory and MCAP integrations.
- `real2sim.platforms.xtrainer`: legacy calibrated Nova2/modified-tool IK, explicitly isolated; legacy layout imports are kept for compatibility.
- `real2sim.platforms.yam`: model-only MuJoCo FK/IK. No hardware imports. Default link6 is the flange body, not a calibrated gripper contact point; supply body-to-tool transform for actual tool checks.
- `real2sim.platforms.profile`: explicit station calibration configuration, no fallback from YAM to Xtrainer.
- Material library/Feishu, reconstruction geometry, generic USD authoring remain shared. Stack-cups and goal-scoring samplers are task adapters, not arbitrary-task solvers.
- Historical `scripts/*xtrainer*`, stack-cups contact/RTX examples and `prepare_goal_supervision.py` remain Xtrainer-specific entry points. They are not the YAM live runner and must not be used to generate YAM scenes unchanged.

Runtime layout: `REAL2SIM_ROOT` can point to a checkout, or install `real2sim-workbench`. Xtrainer compatibility helper resolves the existing sibling checkout by default; Python 3.8 bridge/calibration compatibility is preserved. Simulation/calibration dependencies are supplied by the chosen environment, not eagerly loaded by live preview.

## Versioning

Both local workbench and collector now have Git histories and `pre-platform-split` tags. Initial local commits use repository-local `Real2Sim Migration <real2sim-migration@localhost>` identity; no global identity changes. Track source, templates and tests. Credentials, recordings, full environments, third-party checkouts and generated asset binaries are not transported by Git. No GitHub publishing is required for migration: a Git bundle over authenticated SSH preserves commit history.

## Validation before deployment

Workbench unit tests: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`.
Xtrainer: original calibration selftest and bridge tests on Python 3.8; continuous three-episode/browser integration. Tests use existing offline fixtures and synthetic cases; they do not start hardware.

## YAM station prerequisites

Example: configs/platforms/yam.example.json. It intentionally has calibration_status=missing, null camera extrinsics, tool transforms, right-base transform and table bounds. Live YAM bundles require task.platform=yam, calibration_status=verified and matching camera image dimensions. A Xtrainer bundle is rejected.

Provide independent YAM camera intrinsics in its saved-image orientation, eye-to-hand/top extrinsics, wrist chains if used, two-base relation, calibrated installed tools, and table plane. Use YAM measured joints with its model FK, not leader commands or Nova conventions. Offline solve accepts NPZ base_T_tool and camera_T_target via scripts/calibrate_transforms.py; validate on held-out poses before declaring calibration verified.

MCAP integration should snapshot the selected target in memory at actual writer.start (after trigger readiness), enqueue `/guidance/initial` before opening camera record gates, and let MCAP save/discard/recovery own the data lifecycle. This preserves the native prepare/trigger/start/label workflow. Later export that snapshot with the actual first recorded main-camera frame for comparison. No manual marking or two-minute expiry is reintroduced.

Shared ChArUco detector is available in calibration.board. Its defaults preserve the existing printed Xtrainer board for compatibility; a YAM calibration must explicitly verify BoardSpec against the actual printed board, rather than infer it from robot type.

> 历史记录：当前参数已由用户确认并固定，见 [当前 YAM 标定](YAM_CALIBRATION_CURRENT.md)。旧 runs 优化目录已清理。

# YAM migration status — 2026-09-15

## Delivered code

- SSH alias `yam`: chenyuan@192.168.77.21:2222; existing key authentication, host-key verification enabled.
- Shared workbench: `/home/chenyuan/workspace/real2sim`, branch `refactor/platform-adapters`, transported using Git bundles. No GitHub publication.
- Native YAM project: `/home/chenyuan/workspace/YAM`, branch `migration/real2sim-guidance`, feature commit `593e432`.
- Isolated validation worktree remains at `/home/chenyuan/workspace/YAM-real2sim-migration`, detached at the feature commit for review.
- Original user edits to feishu.md are still uncommitted and preserved byte-for-byte before the added migration section. Backup is in `.git/migration-backups/feishu.before-real2sim.md` and a named Git stash. They were not folded into the feature commit.
- Seven existing estimated/material reconstruction candidates and paper-cup assets were transferred separately from Git. They remain candidates; their old physics reports do not establish YAM grasp/contact success.

## Native integration

`--guidance-bundle` is optional. Disabled by default, so standard collection behavior is preserved. Validation happens before device startup and again against actual camera configuration. Existing preview token/origin protection is retained; original motion control permission and trigger sequence are unchanged. The same guidance selection is available in the native preview and MCAP writer.

At writer.start, enqueue an immutable `/guidance/initial` message before opening source recording gates. JSON includes task/layout/camera metadata and projection PNG bytes encoded as base64. Save, failure retention, discard and recovery use the same MCAP file lifecycle. Offline export uses the actual first main-camera image via `scripts/export_guidance.py`. No manual point marking or two-minute timeout.

## Tests

- Local workbench: 47 unit tests passed after platform extraction; full original calibration selftest passed on Xtrainer Python 3.8.
- Xtrainer bridge: 5 tests passed; continuous three-episode/browser regression passed.
- YAM native preview + new integration + MCAP: 29 tests passed.
- YAM collection/camera/hardening regression: 57 tests passed.
- YAM model: six joints loaded, FK finite, IK round-trip passed (approximately 1.5e-16 m residual for the tested pose). This is not a workspace/collision or physical-coordinate validation.
- Shared core on YAM: 3 tests passed, including exact hand-eye recovery and rejection of uncalibrated/wrong-platform bundles; ChArUco estimator initialized and rejected an empty image as expected.
- No physical robot motion or camera streaming was started by migration tests.

## Environment

Existing collector venv is unchanged. `/home/chenyuan/workspace/real2sim/.venv` uses read-only dependencies from the existing `YAM/i2rt/.venv` through a .pth file, plus an OpenCV runtime copied into `.runtime/opencv`. This is offline dependency reuse, not a fully standalone environment; removing/upgrading the i2rt venv can affect the workbench. Initial online wheel download was cancelled after stalling. Versions/provenance are in `runs/migration/environment.json`. For a future standalone installation, provision the required numpy/scipy/Pillow/MuJoCo/OpenCV dependencies separately.

## What is still required for physical use

`configs/yam_station.local.json` records three camera serials/settings extracted from an existing MCAP session, not proof of current mounting. K, distortion, camera extrinsics, world-to-right-base, installed-tool transforms and table plane/bounds are deliberately missing. `calibration_status=missing` prevents live projection. The generic template is versioned; this station-specific inventory stays local.

Need YAM-specific calibration observations with verified board dimensions and synchronized measured follower joints/images; inspect held-out residuals before declaring calibration verified. Then select a YAM task and its linked material list, generate YAM scene/layouts with model/tool reachability checks and camera bounds, and test first-frame registration on the real station. Do not relabel an Xtrainer bundle as YAM.

The existing model loader currently uses bare arm link6 by default; actual mounted gripper geometry/contact transform must be supplied for reachability/contact tests. Full simulated contact replay and actual scene reconstruction remain to be validated once YAM calibration/task data are available. Feishu credentials and Xtrainer calibration were not copied; source-level material access is reusable, local library snapshots/assets can be used offline.

# Internal YAM toolkit foundation

This workspace is an internal monorepo:

```text
YAM/
├── .git/
├── gello_software/
└── i2rt/
```

Both repositories support Python 3.11 only (`>=3.11,<3.12`). The original
public repositories are configured locally as fetch-only `gello-upstream` and
`i2rt-upstream` remotes; their push URLs are deliberately disabled. Internal
development is published only through the root `origin`:

```bash
cd YAM
git remote -v
```

Do not turn either upstream remote into a writable remote.

## Reproducible installation

From `gello_software`:

```bash
uv sync --frozen --extra yam --extra realsense --extra mcap
uv run yam --version
# Migrate the calibrated profile before invoking hardware commands.
uv run python -m unittest discover -s tests -q
```

`pyproject.toml` is the dependency source of truth. The compatibility
requirements files only select extras. Available stacks are:

- common: HDF5 recording, replay, visualization, profile, and doctor;
- `yam`: I2RT YAM, CAN, GELLO Dynamixel, and YAML launcher;
- `realsense`: RGB/depth capture;
- `sim`, `ur`, and `xarm`: optional robot backends;
- `dev`: build and lint tools.

## Machine profile

Real serial numbers and calibration files are not committed. The resolution
order is:

1. `--profile /path/to/profile.yaml`;
2. `YAM_PROFILE`;
3. `${XDG_CONFIG_HOME:-~/.config}/yam/profiles/default.yaml`.

Start from `configs/profiles/yam-bimanual.example.yaml` and the sanitized arm
and camera examples. The current workstation uses:

```text
~/.config/yam/
├── arms/
│   ├── yam_left.yaml
│   └── yam_right.yaml
├── profiles/default.yaml
└── realsense_bimanual.yaml
```

Schema v1 composes the two calibrated arm files, the camera rig, data storage,
local control ports, and the built-in `yam-v1` safety contract. Validation is
strict: unknown fields, duplicate CAN/FTDI assignments, mismatched rates,
invalid motor mappings, non-local control hosts, and any watchdog other than
400 ms are rejected.

All existing wrappers remain supported:

```bash
scripts/teleop
scripts/replay.sh --speed 0.3
scripts/visualize.sh
scripts/return_zero.sh
```

The installed equivalents are `yam teleop`, `yam replay`, `yam visualize`,
and `yam return-zero`.

## Read-only diagnostics

```bash
yam profile validate
yam doctor
yam doctor --deep
yam doctor --offline
yam doctor --json
```

`yam doctor` never enables or disables motors, resets CAN, writes watchdogs or
sysfs, starts or kills the safety daemon, resets cameras, installs packages, or
creates storage. Normal mode inventories the configured devices and observes
an existing daemon. `--deep` additionally opens RGB camera streams. `--offline`
skips every hardware check.

Statuses are `PASS`, `WARN`, `FAIL`, and `SKIP`. Exit code 0 means no failures,
1 means at least one diagnostic failed, and 2 means the invocation or profile
is invalid.

## CI setup

The root GitHub Actions workflow checks out the monorepo, reproduces both lock
files, compiles the code, runs both complete test suites, builds the wheel,
installs it with local `i2rt[yam]` in a fresh environment, and smoke-tests the
installed `yam` command. No cross-repository token is required and no CI job
touches robot hardware.

## Rollback

The pre-foundation recovery bundle is outside both repositories at:

```text
/mnt/sdb/YAM/backups/foundation-20260723-001
```

It contains HEAD/remotes/submodules, porcelain status, binary tracked patches,
null-delimited untracked manifests and archives, system/freeze information,
and verified SHA-256 checksums for both former repositories. Their final
nested Git directories are additionally stored in
`pre-monorepo-gitdirs/`. Namespaced v0.1.0 tags preserve both former
baselines; the root `internal-yam-baseline-v0.2.0` tag is the normal monorepo
rollback point.

## Account and directory migration

See [migration.md](migration.md) for the current workstation recovery and rollback
procedure. Recreate virtual environments after copying; motion commands now
require a valid machine profile and validate final CLI overrides. Default teleop
uses two pairs; select `--arm-count 4` explicitly for four pairs. `teleop --check`
also checks storage without creating directories or episodes. MP4 export requires
FFmpeg on PATH (`sudo apt-get install ffmpeg` on Ubuntu).

# Workstation migration runbook

This checkout runs as `chenyuan` at `/home/chenyuan/workspace/YAM`. It uses the
same physical two-arm rig as the original `xiangyushun` environment and shares
`/mnt/sdb/YAM/data`. Machine calibration stays outside Git.

## Environment and configuration

Never copy a virtual environment into service. Preserve the copied environment
for diagnosis, then rebuild under the destination account:

```bash
cd /home/chenyuan/workspace/YAM/gello_software
# Move an existing copied .venv aside before running this command.
uv sync --frozen --extra yam --extra realsense --extra dev
cd ../i2rt
uv sync --frozen --extra yam --extra dev --extra visualization
```

Both projects require Python 3.11. The operator environment imports the local
I2RT source through the relative source declaration in its lock/setup files.
Verify `sys.executable`, `gello.__file__`, and `i2rt.__file__` from outside the
checkout, not just from its source directory. Repository wrappers put the current
source roots first, reject a broken local `.venv`, and only use an explicitly
activated Python 3.11 environment when the local `.venv` is absent.

For this same-rig account migration, preview or apply the calibrated host setup:

```bash
cd /home/chenyuan/workspace/YAM
gello_software/.venv/bin/python gello_software/scripts/migrate_host.py \
  --source-user xiangyushun --target-user chenyuan
sudo gello_software/.venv/bin/python gello_software/scripts/migrate_host.py \
  --source-user xiangyushun --target-user chenyuan --apply
```

The administrator command validates the original profile before applying changes,
backs up both configs and recursive ACLs, preserves calibration values, adds the
target account to `dialout`, and grants both accounts shared-data access. Existing
ACL entries retain their effective permissions when the ACL mask is widened.
Symlinks in the data tree are not followed. New directories inherit default ACLs;
regular files receive read/write access without gaining execute permission.

The default profile selects left/right and `main/left/right`. The complete source
rig remains available in `~/.config/yam/profiles/original-rig.yaml`, with local
references to any auxiliary arm configs. Use that profile and `--arm-count 4`
only when the additional devices are connected. Original source configs are
archived without modification. Exposure and motor calibration are not guessed.

Log out and back in after the group change. An existing shell retains its old
supplementary groups. For a one-command verification, `sg dialout -c '<command>'`
can use the new group membership; run robot tools as the ordinary user.

Motion commands require a profile, including when arm overrides are supplied.
CLI `--profile` overrides `YAM_PROFILE`, which overrides the XDG default.
Missing profiles, invalid effective device mappings, placeholder identities,
duplicate ports and invalid port ranges fail before hardware access. Offline
visualization accepts explicit files/directories without a default profile;
explicitly selected invalid profiles still fail. Safety status/hold/off retains
its independent port-based interface.

MP4 export additionally needs `ffmpeg` on PATH. The normal Ubuntu installation
is `sudo apt-get install ffmpeg`. During this repair, the Ubuntu package was
downloaded with `apt-get download ffmpeg` and extracted with `dpkg-deb -x` into
`~/.local/lib/yam-ffmpeg-4.4.2`, using the already installed Ubuntu shared libraries.
`~/.local/bin/ffmpeg` and `ffprobe` link to those executables. Keep `~/.local/bin`
on PATH. This user-local binary installation is host-specific; do not copy it
to a machine with different system libraries.

## Verification on 2026-09-09

- Git status and both submodule worktrees work under the target account.
- The operator and driver environments use the target account's Python 3.11.13.
- Profile validation and offline diagnostics passed.
- Normal doctor: 22 PASS, 0 FAIL, 0 WARN; safety daemon absent (SKIP).
- `teleop --check` passed under the refreshed `dialout` group. Both GELLO buses
  were ready; CAN interfaces were UP / ERROR-ACTIVE at 1 Mbps with FTDI 1 ms.
  D435 `408122072021` and D405 `260322274929` / `260422272011` streamed at the
  configured 640×480, 30 FPS modes. The short-window FPS estimates are diagnostic,
  not a long-duration recording benchmark.
- An isolated temporary directory in shared storage passed HDF5 write/read,
  rename, ACL inheritance and cleanup checks. A historical episode was opened
  read-only; no historical episode content was changed.
- Built the sdist and wheel; installed the wheel with hardware extras into a fresh
  environment. Version, profile validation and all installed command help entries
  passed outside the checkout, with package imports confirmed from site-packages.
- GELLO software suite: 260 tests passed, including actual FFmpeg encoding and
  the migration regression tests. Selected I2RT tests: 31 passed (mocked motor
  initialization, GPIO, gravity-comp launcher and command-queue lag regression).

No followers were enabled, no motion commands were issued, and no real robot
episode was recorded. These checks do not verify motion accuracy or watchdog
behavior on powered followers.

## Backups, logs and rollback

The original copied checkout is preserved at
`/home/chenyuan/workspace/YAM-original-20260909-193304`. Its source files and
symlinks were compared against the replacement before use; disposable lint and
Python caches were excluded from comparison. The replacement preserves the
original uncommitted work. Git submodule `core.worktree` values now use paths
relative to their Git metadata directories. The original backup retains its old
ownership and environment and should not be used to run robot tools.

Recovery logs are under
`~/.local/state/yam-migration/20260909-193304/`: the pre-repair Git diff/status,
copied environments, `doctor.json`, `teleop-check.log`, `storage-check.json`,
software test logs, `entrypoints.json`, `wheel-smoke.json`, build artifacts, and
`migration-only.patch` (this repair relative to the original copied working tree). The host migration backup is
`~/.local/state/yam-migration/host-20260909-194125-826746/`, containing original
configs, previous group membership and `data-acl.txt`.

Rollback is manual so newer work and data are not overwritten:

1. Preserve any work and data created after migration. Restore selected source
   files from the original backup or reverse only the migration diff; do not
   reset the entire dirty working tree. Rebuild environments at their final path.
2. Restore the previous target config from the host backup if one existed;
   otherwise move the migrated config aside. Never substitute example calibration.
3. An administrator can restore pre-migration ACL metadata with
   `sudo setfacl --restore=<host-backup>/data-acl.txt`. Review directories created
   since migration separately: they are absent from this snapshot. This command
   does not restore or delete episode contents.
4. Remove the added `dialout` membership only if it was absent in `groups.txt`
   and the account no longer needs serial access; log in again afterward.
5. If replacing the user-local FFmpeg with a system installation, remove only
   the two migration-created symlinks after verifying their targets.

The migration script does not alter CAN/RealSense udev rules because the existing
rules and device access already passed verification. On another machine, inspect
and install matching device rules separately before considering migration done.

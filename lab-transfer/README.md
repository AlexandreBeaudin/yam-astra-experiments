# Share Astra experiments with another YAM station

All command blocks below use fish. Stay in your fish terminal; no Bash switch is needed.
Run these on the computer identified by each step. The prepared source snapshot is
`~/workspace/jiaxin/yam-astra-shared` on the current machine. Preparing the snapshot itself does not upload anything.

## 1. What is being transferred

| Path | Purpose |
| --- | --- |
| `integrations/inspect_robots/astra_run.py` | Episode runner and hardware guards |
| `integrations/inspect_robots/astra_exploration.txt` | Current V3 policy prompt |
| `integrations/inspect_robots/{planned_motion.py,experiment_resume.py}` | Required runner imports |
| `integrations/inspect_robots/{vendor/,vendor-agent/,runtime-deps/}` and lock JSONs | Pinned framework, agent and runtime dependencies |
| `integrations/inspect_robots/{hardware_replay.py,hardware_replay.html,replay_timeline.py,export_replay_mp4.py,replay_assets/}` | Recording, HTML viewer, MP4 export and fonts |
| `yam3-sunday/capture_observations.py` | Preview client dynamically imported by the runner |
| `preview_cameras.py` | Camera-only preview service |
| `YAM/gello_software/`, `YAM/i2rt/` | Safety daemon, robot driver, models and Python project/lock files |
| `YAM/real2sim/` | Initialized tracked submodule source, flattened for installation compatibility; guidance is not enabled by this procedure |
| `profiles/shurui-mcap.yaml.example` | Source-station profile **example**, not an operational destination profile |
| `lab-transfer/` | This guide, packaging script and local-profile setup helper |
| `SOURCE_SNAPSHOT.json` | File hashes, original YAM commit and excluded paths |

The snapshot copies current working-tree source, including local tracked edits. A
fresh clone of the original upstream YAM repo would miss your Astra integration
and local changes. Git metadata is flattened: this is a new private project, not
an upstream YAM checkout. Existing license files remain included. Uninitialized
optional submodules are listed in the manifest; the Python Dynamixel SDK is
installed through the declared dependency rather than that empty source folder.

Do not copy `.venv/`, `~/.config/gh`, API keys, `~/.config/yam`, CAN udev rules,
camera serial assignments, calibration files, `recordings/`, or historical run
folders into Git. The second computer needs **its own** verified arm/camera setup.
Run-013 evidence can be copied separately with rsync (step 10).

The source has local motor-default/gain changes in
`YAM/i2rt/i2rt/robots/config/{yam_pro.yml,linear_4310.yml}`. These are preserved as
source, and documented in `YAM/docs/PD_GAINS_20261112.md`. Before a live run, have
the lab owner compare these with the destination robot's approved configuration;
this procedure does not assume that YAM3's gains or calibration suit other hardware.

## 2. Log into GitHub — current/source computer

GitHub CLI login works from **any folder**. It authenticates this Linux user on
this computer. Git author name/email are separate from GitHub authentication.

```fish
if not command -q gh
    sudo apt-get update; and sudo apt-get install -y gh
end
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
```

Follow the displayed browser/device-code instructions. On an SSH-only terminal,
open the displayed URL on your laptop and enter the code. Do not paste your GitHub
password into a Git command. If the CLI asks whether to authenticate Git with your
GitHub credentials, choose Yes. On a shared Unix login, use your own OS account
or log out after use; do not leave your personal credentials for teammates.

## 3. Create and push the private source repo — current/source computer

The snapshot has already been prepared. Use that folder, **not** the original
`/home/rhos/workspace/jiaxin/shurui` experiment directory:

```fish
cd ~/workspace/jiaxin/yam-astra-shared
git init -b main
read -P 'Your Git commit display name: ' AUTHOR_NAME
read -P 'Your Git commit email (or GitHub noreply email): ' AUTHOR_EMAIL
git config user.name "$AUTHOR_NAME"
git config user.email "$AUTHOR_EMAIL"
python3 lab-transfer/stage_source.py
git diff --cached --stat
git status --short
```

Review the file list before the first upload. It should contain source and local
font/model assets, not `.venv`, experiment images, logs, MP4s or credentials.
Then:

```fish
git commit -m "Snapshot working YAM Astra runner, V3 prompt and replay exporter"
set GH_OWNER (gh api user --jq .login)
gh repo create "$GH_OWNER/yam-astra-experiments" --private --source=. --remote=origin --push
gh repo view --web
```

If that repo name already exists, choose a new name instead of overwriting another
project. On the repo page, use **Settings → Collaborators** (or your organization's
repository-access page) to invite your teammate with write access. They accept the
invitation using their own GitHub account. An organization repository is also fine;
replace the owner/name in the commands with your authorized organization/repo.

To regenerate a snapshot later, choose a **new** output folder:

```fish
python3 /home/rhos/workspace/jiaxin/shurui/lab-transfer/prepare_source.py \
  --out "$HOME/workspace/jiaxin/yam-astra-shared-next"
```

The helper refuses to overwrite an existing directory. Work in the shared Git
checkout going forward; changes made only in the old experiment directory do not
magically appear in GitHub.

## 4. Clone — second YAM computer

Log into the second computer locally, or SSH using its actual lab address.
After connecting, run `fish` if its default shell is different:

```fish
ssh TEAMMATE_LINUX_USER@SECOND_YAM_HOST_OR_IP
sudo apt-get update
sudo apt-get install -y git gh curl ffmpeg build-essential

gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
mkdir -p ~/workspace/jiaxin
cd ~/workspace/jiaxin
gh repo clone OWNER/yam-astra-experiments yam-astra-shared
cd yam-astra-shared
git switch -c experiments/second-yam-task
```

Replace `OWNER` with the GitHub user/organization that owns the repo. On this
computer your teammate should sign into **their own** account, after accepting the
invite. HTTPS through `gh` does not require creating or copying SSH private keys.

Set their Git commit identity once in this repository:

```fish
git config user.name 'TEAMMATE NAME'
git config user.email 'TEAMMATE GITHUB EMAIL'
```

## 5. Rebuild Python environment — second computer

Use Python 3.11 as specified by the checked-in projects. Do not copy the original
`.venv`: it contains machine/path-specific interpreter links and editable installs.
Install uv if missing using its official installer, then sync the checked-in lock:

```fish
if not command -q uv
    curl -LsSf https://astral.sh/uv/install.sh -o /tmp/install-uv.sh
    and sh /tmp/install-uv.sh
end
fish_add_path "$HOME/.local/bin"
cd ~/workspace/jiaxin/yam-astra-shared/YAM/gello_software
uv python install 3.11
uv sync --frozen --extra yam --extra realsense --extra mcap
cd ../..
uv pip install --python YAM/gello_software/.venv/bin/python playwright==1.64.1
YAM/gello_software/.venv/bin/python -m playwright install --with-deps chromium
```

The last command may request sudo to install browser libraries. The installed
RealSense Python package does not replace the station's USB permissions/udev setup;
use the destination station's established setup. A later `uv sync` can remove
Playwright since it is an extra manual install; re-run its install afterwards.

Offline checks, before configuring or starting hardware:

```fish
YAM/gello_software/.venv/bin/python -m py_compile \
  integrations/inspect_robots/astra_run.py \
  integrations/inspect_robots/export_replay_mp4.py
YAM/gello_software/.venv/bin/python -m unittest discover \
  -s integrations/inspect_robots -p test_replay_timeline.py -v
```

The lean source snapshot does not include historical image/run fixtures. Some
other integration tests depend on those recordings; copy the fixtures separately
before expecting the entire historical test suite to run here.

## 6. Configure the second station — do not reuse YAM3 identity

The current runner has an exact hostname check for `rhos-nuc-1`, reads
`profiles/shurui-mcap.yaml`, expects logical left=`can_yam_b` and right=`can_yam_a`,
and imports the observer from `yam3-sunday/`. The directory names can stay unchanged.
Do **not** rename the second computer to `rhos-nuc-1` or remove the hostname guard.

Find the second station's existing profile (read-only):

```fish
hostname -s
ls -l ~/.config/yam/profiles/
ls -l ~/.config/yam/setup/
ip -details link show type can
```

Normally its selected profile is `~/.config/yam/profiles/default.yaml`. Use the
actual lab-approved file; if it doesn't exist, the station owner must complete
that computer's YAM setup/calibration first. The camera config must identify this
station's physical `main`, `left`, and `right` cameras. A matching channel name
alone does not establish which physical follower is connected.

After the lab owner verifies that profile and the physical arm mapping:

```fish
cd ~/workspace/jiaxin/yam-astra-shared
read -P 'Absolute path to THIS station profile: ' STATION_PROFILE
YAM/gello_software/.venv/bin/python lab-transfer/configure_local_station.py \
  --profile "$STATION_PROFILE" --expected-host (hostname -s | string lower)
```

Review the printed paths/channels, then apply:

```fish
YAM/gello_software/.venv/bin/python lab-transfer/configure_local_station.py \
  --profile "$STATION_PROFILE" --expected-host (hostname -s | string lower) --apply
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py --attempt 1
```

The helper copies that station's profile into the ignored local profile path and
replaces the runner's exact hostname check with this computer's exact name, saving
`.bak` files. It does not change calibration, CAN mappings, gains, the original
station profile, or system hostname, and makes no hardware connections. It rejects
nonmatching CAN mappings and nonstandard service ports; those require a reviewed
adapter change, not relabeling a profile to make validation pass. An offline
preflight pass does not prove that the cameras/robot are ready.

`capture_observations.py` has a separate hostname guard in its standalone CLI;
Astra imports only its `PreviewClient`, which does not invoke that CLI. Do not use
the standalone observer CLI on the second station without adapting its own guard.

## 7. Start/check the local services — second station, attended

Use the station's normal shutdown/hold procedure before changing daemon source
paths. **Do not start a second CAN owner or kill a daemon supporting the arms.**
The runner validates daemon owner UID, source path, source fingerprint and motor
config fingerprint. A daemon launched from an older checkout can fail this check
even if it controls the correct robot. Start the daemon from this new checkout,
with the verified destination configs, after the existing owner has been safely
stopped. Review the transferred gain defaults first as described in step 1.

Terminal A, only when no other hardware owner is running: the following command
**initializes/enables both followers** and may initialize/calibrate grippers. Use
the lab's normal physical support and supervised startup procedure.

```fish
cd ~/workspace/jiaxin/yam-astra-shared
set -gx PYTHONPATH "$PWD/YAM/gello_software:$PWD/YAM/i2rt"
YAM/gello_software/.venv/bin/python -u -c '
import os, sys, yaml
from pathlib import Path
p = yaml.safe_load(Path("profiles/shurui-mcap.yaml").read_text())
os.execv(sys.executable, [sys.executable, "-u", "-m", "experiments.yam_safety_daemon",
    "--left-config-path", p["arms"]["left"],
    "--right-config-path", p["arms"]["right"],
    "--hardware-port", str(p["network"]["hardware_port"]),
    "--safety-port", str(p["network"]["safety_port"])])
'
```

Leave it open and wait for confirmed SAFE_HOLD. Terminal B, only if no camera
service already owns these cameras:

```fish
cd ~/workspace/jiaxin/yam-astra-shared
set -gx PYTHONPATH "$PWD/YAM/gello_software:$PWD/YAM/i2rt"
YAM/gello_software/.venv/bin/python -u preview_cameras.py
```

Open `http://127.0.0.1:8765/` on the second computer and verify the physical views
and live updates. Terminal C:

```fish
cd ~/workspace/jiaxin/yam-astra-shared
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py --check-station --attempt 1
```

Require the final `Station observations and daemon checks passed` message, not just
the initial preflight JSON. Fix identity/feedback/camera errors instead of removing
the checks. Both computers can use localhost ports 6001/6002/8765 independently;
there is no need to point either runner at the other's IP address.

## 8. Choose a task, enter the API key, run

The built-in USB task is selected with `--task usb-insertion`; see
`integrations/inspect_robots/experiments/usb-insertion/README.md`. Its goal, prompt
and output folders are already separate. For a new, unregistered task, edit the
relevant prompt and the matching scene instruction:

```fish
nano integrations/inspect_robots/astra_exploration.txt
nano integrations/inspect_robots/astra_run.py
```

In `astra_run.py`, scene goals are selected in `task_configuration()`;
it currently also hardcodes pink-block-to-X-paper. Editing only the text prompt
would leave conflicting goals. Keep arm restrictions, guards and limits unless a
separate reviewed experiment calls for changes. V3's geometric priors assume the
same YAM3 right-arm/gripper arrangement and are not calibration for other hardware.

In the SAME Terminal C that will run the experiment:

```fish
read -s -P 'RHOS API key: ' RHOS_API_KEY
echo
set -gx RHOS_API_KEY "$RHOS_API_KEY"
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py --attempt 1
YAM/gello_software/.venv/bin/python -u integrations/inspect_robots/astra_run.py \
  --run --attempt 1 --right-channel-confirmed can_yam_a
```

Use an authorized RHOS key; GitHub login and RHOS API authentication are unrelated.
Never commit or print the key. Do not use `--resume` with a run recorded on the
other physical robot. For a new experiment, use an unused attempt number (2, 3,
...). Run directories remain named `astra-yam3-run-*` for compatibility; keep a
station/task label in your notes and shared results to avoid ambiguity.

Export an episode after it finishes:

```fish
YAM/gello_software/.venv/bin/python integrations/inspect_robots/export_replay_mp4.py \
  --run-dir integrations/inspect_robots/astra-yam3-run-001 --speed 4 --fps 30 \
  --title 'Your experiment task'
```

## 9. Collaborate without overwriting each other's experiment

Keep each experiment on a separate branch, e.g. `experiments/yam3-pick-place` and
`experiments/second-yam-task`. Stop experiments before updating the source used by
a running daemon: source edits can invalidate its fingerprint.

```fish
git status
git add integrations/inspect_robots/astra_exploration.txt integrations/inspect_robots/astra_run.py
git commit -m "Configure second station and task"
git push -u origin HEAD
```

Hostname adaptations are station-specific: do not merge them into the other
station's branch. Local `profiles/*.yaml` remain ignored. Share common exporter or
prompt improvements via reviewed PRs or cherry-picks, keeping station identity
changes separate from common changes. To update an existing clean branch:

```fish
git pull --ff-only
```

The original live YAM3 folder was not converted to Git. To put YAM3 on this shared
workflow too, use its own shared-checkout branch and repeat profile/daemon setup
for that source path; do not move or replace the running station's code mid-run.

## 10. Transfer completed run data separately (optional)

Use SSH/rsync for images and videos, not ordinary Git commits. From the original
computer, replace the remote login and hostname first:

```fish
rsync -av --progress \
  /home/rhos/workspace/jiaxin/shurui/integrations/inspect_robots/astra-yam3-run-013/ \
  TEAMMATE_LINUX_USER@SECOND_YAM_HOST_OR_IP:~/yam3-run-013-reference/
```

This is reference evidence, not a destination robot checkpoint. For an HTML viewer
copy the whole run directory (including `replay-frames/` and `replay_assets/`);
`replay.html` alone does not contain its JPEG files. For just the finished video:

```fish
scp /home/rhos/workspace/jiaxin/shurui/integrations/inspect_robots/astra-yam3-run-013/replay_4x.mp4 \
  TEAMMATE_LINUX_USER@SECOND_YAM_HOST_OR_IP:~/
```

## Command references

- GitHub CLI device/browser login: https://cli.github.com/manual/gh_auth_login
- Git credential helper: https://cli.github.com/manual/gh_auth_setup-git
- Private repository creation: https://cli.github.com/manual/gh_repo_create
- uv project synchronization: https://docs.astral.sh/uv/concepts/projects/sync/

These are setup instructions; no GitHub login, invitation, push, remote copy,
service startup, or robot command is performed by preparing the snapshot.

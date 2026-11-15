# PS station branch

Use `stations/ps` on this machine. It combines GitHub `main` with the PS station's
startup-hold changes, hostname `ps`, and verified follower channel names
`can_follower_l` (left) and `can_follower_r` (right).
The other station's implementation remains on `main`.

The initial PS changes are separate commits:

- `d734f92`: startup position hold, gripper calibration arm hold, drift checks,
  and fake-motor regression tests.
- `54a07f9`: PS hostname/channel guards, setup helper, and camera preview.

Do not merge this entire branch into `main`: it contains PS-specific guards.
If the other station needs the startup fix, review and test `d734f92` separately
on a feature branch before adopting it. Software tests are not hardware acceptance.
Both branches use the existing safety daemon; the PS changes affect its underlying
robot initialization code, rather than introducing a second daemon executable.

## Update this station

Finish experiments and follow the station's normal controlled shutdown procedure
before updating code used by a live daemon. Do not force-kill it.
Commit intended source changes first; do not commit local profiles or credentials.

```sh
git switch stations/ps
git status --short
git fetch origin
git merge origin/main
# Resolve any conflicts while retaining PS hostname/channel and startup guards.
# Run offline regression tests before operating hardware.
git push origin stations/ps
```

Once tracking is configured, `git pull --ff-only` only retrieves updates to
`stations/ps`; explicitly merge `origin/main` to bring in the other station's work.
Do not use a hard reset to update this checkout.

`profiles/*.yaml`, `.local/` station logs, credentials, and recordings remain local.
`preview_cameras_ps.py` uses `profiles/ps-cameras.yaml`; it is not started by Git updates.

## November 16 integration

Merged upstream `1b7160a` (USB insertion, dual-camera replays, experiment report).
Upstream also raises the runner's per-call arm-joint ceiling from 0.45 to 0.80 rad
and changes task prompts. These upstream changes are included; physical clearance
and suitability for this station have not been validated by the merge or offline tests.

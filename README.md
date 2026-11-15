# Shared YAM Astra experiments

Start with [the lab transfer guide](lab-transfer/README.md).
This is a flattened source snapshot, including local tracked changes. It is not
a clone of the upstream YAM repository. Optional uninitialized submodules,
credentials, station profiles, virtual environments and run data are excluded.
The original YAM documentation includes commands for its original submodule
layout; use the installation commands in the transfer guide for this snapshot.

Each station must use its own arm calibration and camera configuration. The
included motor defaults reflect the source workstation: review
`YAM/docs/PD_GAINS_20261112.md` against the destination robot before enabling it.
Historical startup-guide paths are examples for YAM3, not destination settings.

## Recent experiment tools

- [USB-A task setup and fish commands](integrations/inspect_robots/experiments/usb-insertion/README.md): select `--task usb-insertion`; run evidence uses its own namespace.
- [Replay viewer and MP4 export](integrations/inspect_robots/REPLAY_VIEWER.md): synchronized continuous main/right views above decision snapshots and telemetry.
- [Trials 1–15 report](integrations/inspect_robots/YAM3_ASTRA_EXPERIMENT_REPORT.md): metrics, prompt changes and selected evidence images.

The pick-and-place prompt includes wrist-depth and recoverable-contact guidance.
The current per-call arm/gripper limits are 0.80 rad / 1.0. Raw episode folders,
console logs, videos and station profiles remain local. Configure each destination
station with its own profile and hostname using the transfer guide before live use.

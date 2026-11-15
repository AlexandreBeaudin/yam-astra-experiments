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

# Project runtime contract

- Simulation and rendering for every robot platform use **Isaac Sim 5.1.0,
  Python 3.11, PhysX**. `configs/simulation_runtime.json` is the shared contract.
  Do not choose a simulator/version independently for Xtrainer or YAM, or
  silently fall back when the required runtime is unavailable.
- Launch simulation through `bash scripts/isaac_python.sh`. Install into
  `.runtime/isaacsim-5.1` using the shared requirements and lock file. Keep
  collector, calibration and utility environments independent.
- YAM's existing MuJoCo adapter is permitted for model-only FK/IK and calibration;
  it does not authorize a second production scene/rendering/contact backend.
- For simulation changes, read `docs/ISAACSIM_5_1_MIGRATION.md` and the current
  test report, run affected regression checks, and record actual runtime metadata.
  Distinguish software regression, physical task success and live field acceptance.
- Preserve accepted calibration parameters and raw collection data. Offline
  tests must not start robot or camera hardware implicitly.

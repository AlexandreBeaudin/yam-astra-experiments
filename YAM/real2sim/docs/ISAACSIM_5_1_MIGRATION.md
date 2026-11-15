# Isaac Sim 5.1 unified runtime

User decision: 2026-09-15. Both Xtrainer and YAM simulation/rendering target
**Isaac Sim 5.1.0 + Python 3.11 + PhysX**, recorded in
`configs/simulation_runtime.json`. NVIDIA's `5.1.0.0` Python distribution is the
same release. This is an ecosystem compatibility pin (including RoboDojo), not
a claim that 5.1 is the newest or currently vendor-supported release.

## Environment boundary and installation

- `.runtime/isaacsim-5.1`: dedicated simulator environment; never reuse the
  collector, ROS, calibration, or historical 4.5 site-packages.
- `.venv`: utility/offline data processing, USD authoring, calibration, cloud
  access, and guidance service. On Xtrainer this existing environment still
  contains historical 4.5 packages for rollback; it is **not** a simulation entry.
- `YAM/gello_software/.venv`: collector; do not upgrade for this migration.
- MuJoCo may remain a model-only FK/IK dependency for YAM calibration. It is not
  an alternative production rendering or contact-simulation backend.
- Do not install standalone `usd-core` into the Isaac environment. Kit owns its
  USD/PhysX binaries. Offline USD authoring remains in the utility environment.

```bash
cd /mnt/sdb/rhos_collector/real2sim # YAM: /home/chenyuan/workspace/real2sim
# Only if uv is missing; YAM already has /usr/local/bin/uv:
.venv/bin/python -m pip install uv
bash scripts/install_isaac.sh
bash scripts/isaac_python.sh scripts/smoke_isaac.py
```

The launcher uses the dedicated interpreter, clears inherited Python imports,
disables user site-packages, selects one NVIDIA Vulkan ICD, and checks both
Isaac and Python versions **before** launching Kit. There is no fallback to 4.5
or MuJoCo if 5.1 is missing. All simulation Python entries independently enforce
the same version contract, including when invoked directly.

On YAM, skip the `pip install uv` line: the utility venv has no pip module and
the installer automatically uses `/usr/local/bin/uv`. It creates the separate
simulation venv without changing the collector or utility venv.

Installation uses official NVIDIA packages plus the configured PyPI mirror.
`requirements-isaacsim.txt` follows the SDK constraints: notably NumPy 1.26.0,
trimesh 4.5.1 and imageio 2.37.0. Do not overwrite them with utility-environment
versions. `requirements-isaacsim-linux-x86_64.lock` locks the complete resolved
Linux x86_64 environment; the installer applies it as constraints. Kit extensions
may need downloads/shader compilation on first launch.

## Machine preflight (2026-09-15)

- Xtrainer host: Ubuntu 22.04.5, RTX 4090 24 GB, driver 580.178.04.
- YAM host: RTX 4090 24 GB, driver 550.163.01. This is below the 580.65.06
  Linux driver listed in the official 5.1 requirements. YAM must schedule a
  driver compatibility/update check before adopting the runtime; no driver
  installation or restart is performed by this handoff.
- A container does not replace the host NVIDIA driver. A missing/unsupported
  runtime must not silently trigger a different simulator/version.

[Official 5.1 hardware requirements](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html)

## Compatibility changes

- Version guards before Kit initialization; actual runtime metadata in rendering,
  contact and material reports (no hardcoded historical version labels).
- Semantic tagging uses 5.1 `add_labels` / UsdSemantics.LabelsAPI rather than
  deprecated `add_update_semantics` / SemanticsAPI. Actual segmentation masks
  and projection centroids are tested, not just importability.
- Tested full (`fast_shutdown=False`) extension teardown segfaulted after a
  complete saved PhysX rollout. Keep NVIDIA's default `fast_shutdown=True`.
  `close_app` prints and flushes a pending Python exception and exits nonzero
  before native fast shutdown can mask the failure. On success, normal Kit fast
  shutdown runs. A separate subprocess intentionally throws after app startup
  and must exit 1 with the expected traceback. This is a standalone-process
  lifecycle policy, not a reusable embedded/notebook runtime.
- Visual review video/HTML playback interval now follows selected frame stride
  rather than a fixed 6 fps. The assumed source rate remains explicitly 24 Hz.

## Regression suite

Before this migration, the existing five demo run directories were copied into
`runs/isaacsim_5_1_migration/baseline_4_5/`. Raw demonstrations, calibration
files, collector code, and credentials are not modified by the suite.

```bash
.venv/bin/python scripts/verify_isaac_migration.py
# Resume after correcting a failure, e.g.:
.venv/bin/python scripts/verify_isaac_migration.py --from-phase projection
```

This is an offline **demo rebuilding** command: it rewrites the named demo runs,
not an unattended production collector command. Archive current review/audit
artifacts before rerunning it. It fails at the first failing phase and records
each command, exit code, duration and log in `suite.json`; a resumed run retains
completed phases. Final `acceptance.json` is an additional assertion gate.

Checks:

1. Start/close 5.1; reject wrong SDK/Python versions.
   Explicitly inject a Python failure and require exit code 1. Use Kit's default
   fast shutdown in these dedicated subprocesses; full extension teardown caused
   a native segmentation fault after a completed contact rollout. `close_app`
   prints and flushes pending exceptions, then exits the dedicated subprocess
   with a nonzero status before native cleanup can hide the failure. Failed-attempt logs are retained.
2. Re-extract the 410-frame episode and rebuild USD. Check 100 held-out FK
   configurations per arm and 100 mathematical projection points per camera.
3. Actual RTX segmentation: 5 synthetic markers at 3 recorded poses in each of
   3 cameras, 45 measured image centroids. Check against independent calibrated
   optical rays with a 1.5 px tolerance, including off-center principal points.
4. Render every frame of visual replay (410), physical replay (410), and centered
   grasp bench (180), each with all three cameras; validate images and videos.
5. Five contact experiments: recorded, disabled finger contact, centered bench
   at 240/480 Hz, and zero friction. Verify finite traces, input hashes, dynamic
   cups, no object animation/attachment, bounded motor force, no lift in negative
   controls, bench lift/hold, and <5 mm timestep peak-lift difference.
6. Seven existing material candidates, three initial perturbations each, static
   settling, and three RTX views each.
7. Generate 54 cup arrangements over nine strata, check waypoint IK, table bounds,
   static stability and rendered visibility. Rejected candidates remain rejected;
   each stratum must retain guidance-ready examples. Check exact overlay pixels.
8. Regenerate 36 TASK-X-0001 arrangements from cached linked-material records,
   export metric USD geometry, run all unit tests and browser checks.
9. Continuous collector fixture: historical images simulate cameras, select
   previous/next guidance, freeze projection at record start, record three episodes
   without restarting, reuse a guide, associate first frames, reject stale frames.

The TASK-X-0001 guidance currently draws calibrated geometric overlays in the
shared utility code; it is not an Isaac contact task. Both that path and the
Isaac-rendered cup guidance path are covered. Cloud services remain independent;
these tests do not upload data or write Feishu records.

## Acceptance limits

An SDK migration pass does not certify paper-cup dimensions, friction, contact
parameters, real-world reachability, successful stacking or full release. The
4.5 baseline itself failed full release/stacking; those are recorded physical
model limitations, not conditions to hide or relax for a green migration report.

Live hardware, actual recording triggers and physically aligned live overlays
still require a field session. YAM's complete scene is not made valid by receiving
this document; it must pass its own robot/camera/scene checks using this **same**
runtime. Keep the accepted YAM calibration version unchanged.

## Log review

The default 5.1 application still loads vendor compatibility extensions which
print `omni.isaac.*` and `warp.sim` deprecation notices; our simulation entry
points do not import those legacy Isaac namespaces. Our semantic labeling uses
`add_labels` / `UsdSemantics.LabelsAPI`, replacing `add_update_semantics`.

PhysX warnings about >4 TGS velocity iterations and disjointed original robot
joints also occur in the archived 4.5 `runs/contact_final.log` and
`runs/contact_bench_final.log`. The original arm joints are explicitly disabled
in the contact input; only the force-limited finger joints are enabled and are
checked in the artifact test. Solver-iteration parameters are preserved for the
version comparison; negative controls and timestep checks test actual behavior.
These warnings are recorded rather than suppressed or misreported as new bugs.

## References

- [Isaac Sim 5.1 Python installation](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/install_python.html)
- [Isaac Sim 5.1 release notes](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/overview/release_notes.html)
- [RoboDojo runtime pin](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/main/Dockerfile)

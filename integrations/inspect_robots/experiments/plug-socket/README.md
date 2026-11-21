# Charger brick insertion: matched task conditions

This task has its own prompts, scene ID, checkpoints and `runs/run-NNN` namespace.
The existing USB insertion and pick/place prompts and their default action/camera
behavior are preserved. Pre-existing modified Python sources have timestamped
`.before-plug-socket-*.bak` backups; the manifest is
`.local/plug-socket-implementation/baseline.json`.

| Condition | Controls | Information sent to Astra | Prompt |
| --- | --- | --- | --- |
| Default strict baseline | Seven right-arm controls; left parked | main + right, black left slot | prompt.txt |
| `--active-perception` | Six left observer joints + seven right controls; left gripper locked | main + left + right | prompt-active-perception.txt |

Both prompts specify an unplugged strip for mechanical insertion, the rear-half
rim grasp with both prongs exposed, eight valid parallel slot pairs, a visual
fingertip-level check, immediate closure at the specified mat-level/lag condition,
and the two-phase viewing/insertion strategy. They distinguish the gripper's roll
axis from the table vertical and require verification of the actual prong angle.
The positive-pitch examples are empirical candidates, not calibrated poses for
this new brick. The actual brick-to-gripper relationship must be observed.

## Offline preflight

From the repository root:

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task plug-socket --attempt 1
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py \
  --task plug-socket --attempt 2 --active-perception
```

These only inspect local configuration. They create no run directories and use no
robot or API connection. They print 7/13 controls and the effective mask condition.
Observer motion is blocked until `ps-observer-geometry.json` has measured,
verified common-frame base transforms and provenance; the required registered
separation is at least 0.16 m. See `../../ACTIVE_PERCEPTION.md` for its scope.

The CLI also supports `--mask-left-view` and `--no-mask-left-view`. The default
is masked for right-only plug-socket and unmasked otherwise. Explicit unmasking
is a separately labelled override, not the strict baseline. Masking with an active
observer is rejected. The mask condition is saved with attempts/checkpoints and
cannot be changed when resuming an experiment.

The left policy image is a black uint8 480x640x3 frame, with `masked: true` and a
hash referencing that black image. It does not require a left preview fetch.
No derived `main_comparison` image is sent in either plug-socket condition, so
there are only the three named camera slots (two informative in the baseline).
The independent archival preview recorder may still record real left frames;
they are not exposed through policy images/tools or the saved API request.

## Attention contract

Only plug-socket's `move_joints` acquires a required `attention` argument:

```json
{
  "primary_view": "main",
  "fusion_mode": "cross_view_parallel",
  "views": {
    "main": {"visible": true, "regions": [
      {"label": "charger_brick", "center_uv": [0.5, 0.6], "box_uv": [0.4, 0.5, 0.6, 0.7], "weight": 1.0}
    ]},
    "left": {"visible": false, "regions": []},
    "right": {"visible": true, "regions": [
      {"label": "target_2prong_slot", "center_uv": [0.4, 0.5], "box_uv": [0.3, 0.4, 0.5, 0.6], "weight": 1.0}
    ]}
  }
}
```

Schema/runtime checks require exactly those view keys, finite normalized numbers,
ordered positive-area boxes containing their centers, 1–3 regions for a visible
view and no regions for an invisible view. Weights are positive and sum to one
PER VIEW (tolerance 1e-6). Primary must be visible/unmasked; cross-view fusion
requires two visible views. Masked left must be false/empty.

The wrapper records `attention_distribution` with step, phase, targets, note and
attention, then strips attention before the original planner receives the call.
Planner interpolation, joint selection and guards remain authoritative. Attention
is the model's reported evidence selection, not access to internal attention or
proof of what caused its action. Terminal/observation-only decisions do not invent
an attention report; the renderer labels such decisions accordingly.

## Heatmap export (no API or robot)

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/attention_analysis.py \
  render-heatmap --run-dir integrations/inspect_robots/experiments/plug-socket/runs/run-001
```

Outputs `attention_heatmap.mp4` plus a JSON frame index. Requires the existing
NumPy/Pillow environment and FFmpeg on PATH; no new package installation is needed.
Each decision is shown as a synchronized three-view frame with weighted Gaussian
and box overlays, labels, normalized crosshairs and a step/primary/fusion banner.
Masked left stays black. Default timing is one decision per second, not real-time
trial playback; set `--fps` if desired. Checksums protect saved JPEG identity.

## API-only counterfactual replay

This command makes billable requests using RHOS_API_KEY but never connects to a
robot or executes a returned proposal. Verification used mocked transport only.

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/attention_analysis.py \
  causal-replay --run-dir integrations/inspect_robots/experiments/plug-socket/runs/run-001 \
  --steps 0,20,40
```

Choose actual saved decision steps that have attention reports. Output defaults
to `causal-replay/`: `causal_ablation_report.json`, request/response JSON and
side-by-side baseline/intervention PNGs. API keys/headers are never saved.

For each step the tool rehydrates checksum-verified wire blobs and sends the exact
saved request for a fresh baseline. Ambiguous repeated requests at the same step
are resolved against logged attention or rejected, never guessed. It then queries
three view dropouts, sequential main-only text summary -> left-only with carryover
-> right-only action with both summaries, and reported-highest-weight-region versus
an equal-area control mask. With a usable control this is nine API calls per step.
No tools are offered for the main/left summary stages.

View dropout masks every retained image occurrence of the selected view, including
main-derived comparisons when main is dropped. Region masks affect only the current
step, in the primary view and left when visible/unmasked. Existing historical text
and opaque reasoning remain. A masked baseline left frame is never replaced by
an archival real-left image. A dropout of already masked left is a negative control.

L2 and max-joint differences are computed against the fresh baseline, resolving
omitted targets from the saved accepted reference. Only arm joints contribute to
radian metrics; normalized gripper change is separate. Non-movement responses are
reported as non-comparable, not zero change. Returned proposals are not certified
safe or sent to any controller. One sample per condition does not separate model
randomness from intervention effects; a highest-weight mask is not full RISE.

### Empty-mat controls

By default, a low-detail equal-size patch outside the reported attended boxes is
selected as a CANDIDATE. Vision heuristics cannot certify that it is empty mat;
the report explicitly marks its background as unverified. Inspect the PNGs before
interpreting such a result. For a reviewed background comparison, add:

```bash
... causal-replay --run-dir RUN --steps 20 --control-boxes reviewed-controls.json
```

The annotation format is `{"20": {"main": [umin,vmin,umax,vmax], "left": [...]}}`.
Supply a box for each intervened view, reviewed against that step's actual image.
Each box must have exactly the ROI's pixel width/height and avoid attended boxes;
translate the ROI without scaling it. If no equal-area patch fits, the control is
reported unavailable; the tool does not fabricate an empty-mat comparison.

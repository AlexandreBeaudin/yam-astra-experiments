# Trial 14: early closures and contact termination

Evidence: the unmodified `astra-yam3-run-014/station-events.jsonl`, wire calls,
`prompt.txt` and `attempt-result.json`. `decision-comparison.jpg` pairs exact
hash-matched policy decision images: right wrist on the left, main on the right.
Trial 13's two nonempty closures are shown as comparison, not as a calibrated
pixel threshold or proof that either remained secure throughout transfer.

## Sequence

- Step 538 / call 6: commanded close at j1=1.55, j2=1.30, j3=-0.60.
  Note claimed main showed pads around the lower half. The wrist image shows a
  comparatively small block and visible separation from the pads. At step 588,
  gripper readback was 0.002667: an empty closure.
- Step 639: reopened; then increased j1 by 0.15 and subsequently 0.12 while
  holding j2=1.30. This was a decisive descent, not repeated tiny increments.
- Step 731 / call 10: closed again at j1=1.82, with the note again relying on
  main-view overlap. Step 782 readback was 0.002595: another empty closure.
- Step 833 / call 12: after reopening, descended another +0.15 on j1 to 1.97,
  without changing XY/fore-aft alignment. At step 884 the block appears tipped
  against the right pad. This supports contact and an alignment problem, but
  does not identify the precise force/contact geometry.
- Step 884 / call 13: Astra chose give_up, citing the contact rule. The run
  completed with final hold confirmed; it did not terminate on a logged guard
  intervention. No upward recovery was proposed or tested.

Decision residuals (measured minus target) at termination were approximately:
`j1=-0.01208`, `j2=+0.00484`, `j3=+0.00738`, `j4=+0.04158` rad. The model's
quoted numbers match the feedback. The j1/j2 0.015 cue had not been exceeded,
but the prompt's unthresholded “j3 deflects upward” alternative applied. None
of these residuals exceeded the actual 0.08 rad tracking guard. This does not
prove every retreat was collision-free; it means the cited stop was the model's
interpretation, not a recorded mandatory tracking fault.

## Why this happened

Rule 2 delegated depth to the angled main image and wrist to centering. Both
closure notes asserted the main-image depth condition was met. Rule 3 treated
empty closure as height-only evidence. That encouraged further descent without
reconsidering fore-aft pad alignment. Rule 4 allowed no further downward motion
on contact, but offered no upward recovery branch. In addition, OBSERVATION_GUIDANCE
was repeated into tool descriptions/observations, including give_up, and said
“Stop on unexpected contact or guard trips.” This contradicted the recoverable
block-shift policy and likely reinforced early termination.

## Changes and limits

- Primary grasp-depth evidence is now wrist-view geometry relative to both pads;
  main remains a clearance/placement cross-check. Visual growth during approach
  is useful evidence, not a fixed image-occupancy test.
- Do not require filling the entire frame: actual image occupancy varies with
  object geometry/camera pose; Trial 14's tipped block already appears large.
  No pixel threshold, saved-frame matching or trial-specific joint target is added.
- An empty closure means a miss, not proof of height alone. Reassess lateral and
  fore-aft alignment; avoid another straight descent if the block is caught/tipped.
- Contact stops downward progress. A small upward withdrawal or reversal toward
  a previously clear pose may unload contact, followed by fresh feedback/images
  and re-centering. This is conditional on clearance and all existing guards.
  Do not force recovery, sweep laterally while caught, or bypass fault handling.
- Give_up must explain why withdrawal/reversal is unavailable if contact is the
  reason. Grasp aperture must also be supported by visual lift confirmation.

Only prompt and descriptive tool guidance changed. No automatic motion, grasp
classifier, or fault override was added. Offline tests validate rendered guidance
and unchanged mechanical limits; they cannot establish the revised policy's live
success rate or prove that a retreat at step 884 would have been safe.

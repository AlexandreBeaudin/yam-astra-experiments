# Trial 6 inter-arm guard stop

The runtime separation guard stopped the last command after 66 of its 156
waypoints, at accepted step 2028. The checkpoint was last saved at decision step
1962 and is `dispatching_or_finished`; the partial command must not be replayed.
The saved result confirms final hold. This was a runtime guard abort, not an
Astra `give_up` decision or a time/step budget limit.

## Camera and movement evidence

The final main, left and right frames were inspected. They show a visible gap
between the left observer and right manipulator, with the charger held in front
of the upright strip. Images do not establish a calibrated distance or whole-arm
clearance. The last left view already contains a useful profile of the strip and
right wrist.

The left arm was not locked. Accepted left-joint ranges over the run were:

| Joint | Accepted range, rad |
| --- | --- |
| J0 | -0.910 to +0.044 |
| J1 | +0.004 to +0.950 |
| J2 | +0.007 to +0.580 |
| J3 | +0.014 to +0.211 |
| J4 | -0.420 to +0.330 |
| J5 | Held near -0.019 |

In the last command, measured left J1 increased from approximately 0.653 to
0.805 rad before the stop. Astra explicitly requested moving the observer closer
to slot height; the runtime stopped that repositioning partway through.

## Reconstructed cause

Using the last recorded measured observation and next undispatched target:

- Nominal wrist/grasp-point distances at the interval endpoints: **21.875 cm**
  and **21.939 cm**.
- Configured threshold: **18 cm** (16 cm minimum + 2 cm registration margin).
- Original whole-chain movement bound: **4.211 cm**.
- Original lower bound: `min(endpoint distances) - movement bound` = **17.664 cm**.

That coarse bound rejected an interval even though its endpoint distances were
above the configured threshold. The exact later `get_joint_state()` read used
by the failed check was not logged in the old event, so these are reconstruction
values, not a claim to recover that unlogged read bit-for-bit.

## Revision

The guard now uses both endpoint bounds. For joint interpolation fraction `t`
and movement bound `L`, distance is bounded below by both `d0 - t*L` and
`d1 - (1-t)*L`. Their maximum is at least `(d0+d1-L)/2` everywhere. Intervals
that remain inconclusive are bisected and checked with the same bound. Depth
and additional-sample limits fail closed. Endpoint or interior distances below
the configured threshold remain rejected; passing FK samples alone is not an
acceptance criterion.

The reconstructed interval now has a **19.801 cm certified lower bound**, above
the unchanged 18 cm threshold. The complete planned 156-waypoint command also
passes the revised model check, with a minimum certified bound about **20.261 cm**.
This verifies the math for that modeled path, not physical collision clearance.

New rejection events include the blocked step, actual measured full-joint vector,
proposed target and certificate diagnostics. The guard description records a
new certificate-method identity. Existing older resume identities are not silently
treated as the revised guard.

## Validation and scope

38 offline tests passed, including the recorded-stop regression, safe parallel
motion, an interior collision missed by endpoint/first-midpoint sampling,
nonfinite model results, and fail-closed depth/work budgets. No separation or
tracking threshold, base transform, gripper limit, motor controller or daemon code
was relaxed. The geometry remains operator-attested; the guard still covers
wrist/grasp reference points rather than all links, cameras, cables or the table.

Backups and detailed numerical evidence are in
`.local/trial6-separation-review/`. Trial 6 recordings and checkpoint remain
unchanged. No robot motion or automatic recovery was performed. A new runner is
required to load the revision; this change does not require a daemon restart.

# Camera, stopping and motion audit — 2026-11-13

The active prompt, embodiment/move descriptions, observation report, recheck
feedback, move rejection feedback and give_up schema now use the same contract:
YAM3 has main, left-wrist and right-wrist cameras, with no side camera or operator
photo input during a trial. The current adapter delivers main and right-wrist;
its main_comparison image is derived from main. No additional camera route was
invented or enabled.

Removed the side-oblique acquisition prerequisite and request from the prompt
and move feedback. Inconclusive evidence must be assessed together with joint
readback and previous motion results. Recheck exhaustion does not itself end a
trial. A geometry stop must identify the uncertainty or guard, the blocked next
action and why supported alternatives cannot progress. Existing hard stops for
tracking, freshness, limits, guard intervention and budgets remain in place.
The station schema adds this requirement to give_up's reason description; it
does not attempt to classify arbitrary model prose with a keyword filter.

Early clear-space actions must target 0.45–0.60 rad on selected joints when the
direction and whole-path clearance support them. A smaller early move requires
a specific uncertainty or guard explanation. Near the pink block, alignment,
descent, grasp and placement use 0.01–0.03 rad or smaller. The conditional
open_space_approach cap increased from 0.30 to 0.60 rad. The trusted clearance
gate, 0.15 fallback/fine cap, interpolation, gripper cap and controller settings
are preserved. The live constructor still lacks a trusted clearance checker,
so its effective cap remains 0.15 rad. These edits do not enable 0.60-rad hardware
motion without that verification.

Offline validation:

```sh
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -m unittest discover -s integrations/inspect_robots -p 'test_*.py' -q
```

Result: 47 tests passed. New regressions cover the active prompt, actual schemas,
observation report, exhausted recheck and oversized-move feedback; scripted
continuation to a supported move after recheck exhaustion; 0.45/0.60 acceptance
with simulated clearance, rejection above 0.60, absent/unknown/rejected clearance,
and fine-phase rejection. The existing suite covers explicit blocked-clearance
termination, saved images, tracking, guards, unchanged left-arm targets and
modified-path rejection.

New contract tests block sockets and replace the fake command method with a
dispatch failure. Saved-motion scenarios also reject dispatch. Existing rollout
tests execute only fake robot commands under network blocking. No hardware
episode, station connection or fresh model inference was performed. Scripted
responses verify contracts and control flow, not future model compliance or
physical clearance.

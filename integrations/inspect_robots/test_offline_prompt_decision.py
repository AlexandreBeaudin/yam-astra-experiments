"""Contract/transport tests with scripted decisions, NOT fresh Astra inference."""
import ast
import base64
import io
import json
import unittest

import astra_run as run
from offline_prompt_decision_audit import ARCHIVE, OUT, fixtures, restore_blobs, validate_proposal
from test_astra_run import make_arm
import httpx
import numpy as np
from PIL import Image


class OfflinePromptDecisionTests(unittest.TestCase):
    def test_original_narrative_and_preserved_feedback_checks(self):
        before = ast.parse((OUT / 'astra_run.before.py').read_text())
        after = ast.parse(run.Path(run.__file__).read_text())
        # Narrative and camera guidance intentionally change. Keep the actual
        # feedback, freshness and safety helpers identical to the baseline.
        for name in ('measured_feedback', 'check', 'close', 'checked_vector',
                     'camera_freshness', 'configuration'):
            old_nodes = [n for n in ast.walk(before)
                         if isinstance(n, ast.FunctionDef) and n.name == name]
            new_nodes = [n for n in ast.walk(after)
                         if isinstance(n, ast.FunctionDef) and n.name == name]
            self.assertEqual([ast.dump(n) for n in old_nodes],
                             [ast.dump(n) for n in new_nodes], name)

    def test_actual_policy_accepts_prompt_and_delivers_saved_pixels(self):
        requests, blobs = fixtures()
        request = restore_blobs(requests['step207'], blobs)
        observation = next(x for x in reversed(request['input']) if x.get('role') == 'user')
        urls = [part['image_url'] for part in observation['content'] if part.get('type') == 'input_image']
        events = [json.loads(line) for line in (ARCHIVE / 'station-events.jsonl').read_text().splitlines()]
        decision = next(e for e in events if e['kind'] == 'decision_observation' and e['step'] == 207)
        for view, url in zip(('main', 'right'), urls):
            pixels = np.asarray(Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))))
            expected = np.asarray(Image.open(ARCHIVE / ('replay-frames/' + decision['cameras'][view]['sha256'] + '.jpg')))
            np.testing.assert_array_equal(pixels, expected)
        self.assertIn(run.PROMPT.read_text().strip(), request['input'][0]['content'])
        self.assertLessEqual(len(run.PROMPT.read_text()), 8192)
        arm, robot = make_arm()
        def respond(req):
            body = json.loads(req.content)
            self.assertIn(run.PROMPT.read_text().strip(), body['input'][0]['content'])
            self.assertNotIn('Do not descend or close before review', json.dumps(body))
            return httpx.Response(200, json={'status': 'completed', 'output': [{
                'type': 'function_call', 'call_id': 'offline-contract', 'name': 'give_up',
                'arguments': json.dumps({'reason': 'Scripted contract check only', 'hindsight': 'No inference'})}]})
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)), arm.pre_check,
                                station=arm, env={'RHOS_API_KEY': 'offline-placeholder'})
        policy.bind(arm.info)
        scene = run.Scene(id='contract-only', instruction='No hardware')
        policy.reset(scene)
        from demo import offline_only
        with offline_only():
            policy.act(arm.reset(scene))
        self.assertFalse(robot.commands)

    def test_reviewed_step207_probe_passes_real_planner_and_guards(self):
        # Reviewer-selected candidate, NOT a generated Astra decision. Different
        # joint from the J1 tilt probe; small lateral alignment probe, fingers open.
        call = {'name': 'move_joints', 'arguments': json.dumps({
            'targets': {'right_j0': .5658},
            'note': 'Alignment probe: main shows the gripper to the right of the block, '
                    'with local table space between them; wrist identifies both fingers '
                    'and block. Test J0 +0.02 at the changed J1 pose: predict leftward '
                    'gripper/block displacement in main, compare wrist alignment after '
                    'settling and measured tracking. Do not infer vertical motion or descend.'})}
        result = validate_proposal(call)
        self.assertEqual(result['guard_interventions'], 0)
        self.assertEqual(result['robot_dispatches'], 0)
        self.assertAlmostEqual(result['displacement'][0], .02)
        self.assertTrue(all(abs(x) < 1e-12 for x in result['displacement'][1:]))
        (OUT / 'reviewed-probe.json').write_text(json.dumps(dict(
            source='manual saved-image review; not fresh Astra inference', call=call,
            numeric_validation=result), indent=2) + '\n')

    def test_no_clearance_fixture_and_scripted_stop_dispatch_nothing(self):
        requests, _ = fixtures()
        text = requests['no_supported_clearance']['input'][-1]['content'][0]['text']
        self.assertIn('every available joint and direction', text)
        arm, robot = make_arm()
        reason = ('Stable occlusion prevents establishing swept clearance. Smaller '
                  'increments and other joints/directions still have hidden swept '
                  'segments; reversal is no longer verified clear. Recheck already '
                  'showed the same occlusion; no other view is available. No safe '
                  'informative action remains within this experiment.')
        def respond(request):
            return httpx.Response(200, json={'status': 'completed', 'output': [{
                'type': 'function_call', 'call_id': 'offline-stop', 'name': 'give_up',
                'arguments': json.dumps({'reason': reason, 'hindsight': 'Clearance unresolved'})}]})
        policy = run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)), arm.pre_check,
                                station=arm, env={'RHOS_API_KEY': 'offline-placeholder'})
        policy.bind(arm.info)
        scene = run.Scene(id='no-clearance', instruction=text)
        policy.reset(scene)
        from demo import offline_only
        with offline_only():
            chunk = policy.act(arm.reset(scene))
        self.assertEqual(chunk.terminal.reason, reason)
        # Upstream give_up returns a stationary stop marker, not motion.
        for action in chunk.actions:
            np.testing.assert_array_equal(action.data, arm.last)
        self.assertFalse(robot.commands)
        (OUT / 'reviewed-stop.json').write_text(json.dumps(dict(
            source='scripted stop-path test; not fresh Astra inference',
            reason=reason, robot_dispatches=0), indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()

"""Offline task isolation, prompt rendering, recording and resume contracts."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import astra_run as run
import httpx
from demo import offline_only
from test_astra_run import FakeRobot, FakePreview
from hardware_replay import export_replay


class TaskSelectionTests(unittest.TestCase):
    def test_preflight_namespaces_and_no_attempt_consumption(self):
        with offline_only(), tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'experiments/usb-insertion').mkdir(parents=True)
            (root/'experiments/usb-insertion/prompt.txt').write_text('USB task')
            with patch.object(run, 'HERE', root), patch.object(run, 'configuration', return_value=({}, {}, {})):
                for task, expected in [('pick-place',root/'astra-yam3-run-001'),
                                       ('usb-insertion',root/'experiments/usb-insertion/runs/run-001')]:
                    with patch('sys.argv',['astra_run','--task',task,'--attempt','1']), patch('sys.stdout',new_callable=io.StringIO) as out:
                        run.main()
                    result=json.loads(out.getvalue())
                    self.assertEqual(result['output'],str(expected))
                    self.assertEqual(result['task_id'],task)
                    self.assertEqual(result['effective_cap_rad'],.8)
                    self.assertFalse(expected.exists())

    def test_cross_task_resume_is_rejected_before_checkpoint_or_hardware(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)
            (source/'attempt.json').write_text(json.dumps({'task_id':'usb-insertion'}))
            run.validate_resume_task(source,'usb-insertion')
            with patch('sys.argv',['astra_run','--resume',str(source)]), patch('sys.stderr',new_callable=io.StringIO), patch.object(run,'load_resume') as loader:
                with self.assertRaises(SystemExit):run.main()
                loader.assert_not_called()
            with self.assertRaises(ValueError):run.validate_resume_task(source,'pick-place')
            (source/'attempt.json').write_text('{}')
            run.validate_resume_task(source,'pick-place')  # Historical block trials.
            with self.assertRaises(ValueError):run.validate_resume_task(source,'usb-insertion')

    def test_usb_prompt_schema_and_recorded_replay_without_motion(self):
        with offline_only(), tempfile.TemporaryDirectory() as temp:
            robot=FakeRobot()
            arm=run.StationArm(robot,FakePreview(robot),{'joint_limits':[[-3.,3.]]*14},simulated=True,task_id='usb-insertion')
            spec=run.task_configuration('usb-insertion')
            calls=[]
            def respond(request):
                body=json.loads(request.content);calls.append(body)
                text=json.dumps(body)
                self.assertEqual(body['reasoning']['effort'],'medium')
                self.assertIn(spec['prompt'].read_text().strip(),body['input'][0]['content'])
                for forbidden in ['pink block','black X','white paper','0.10–0.20','Return to Rest','step-0 initial readback']:
                    self.assertNotIn(forbidden,text)
                move=next(t for t in body['tools'] if t['name']=='move_joints')
                self.assertIn('small observed moves along the insertion axis',move['description'])
                self.assertIn('Effective cap: 0.8',move['description'])
                self.assertIn('0.80',arm.action_guidance())
                terminal=next(t for t in body['tools'] if t['name']=='give_up')
                self.assertIn('insertion-axis back-out',json.dumps(terminal))
                return httpx.Response(200,json={'status':'completed','output':[{
                    'type':'function_call','call_id':'synthetic-usb-end','name':'done',
                    'arguments':json.dumps({'note':'Synthetic offline contract only; no physical insertion.'})}]})
            policy=run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)),arm.pre_check,
                env={'RHOS_API_KEY':'offline-placeholder'},station=arm,prompt_path=spec['prompt'])
            with patch('sys.stdout',new_callable=io.StringIO):
                log=run.execute(policy,arm,Path(temp),task_spec=spec)
            sample=log.to_dict()['samples'][0]
            self.assertEqual(sample['scene_id'],spec['scene_id'])
            self.assertEqual(sample['instruction'],spec['instruction'])
            self.assertEqual(sample['scene_metadata']['task_id'],'usb-insertion')
            # Framework terminal handling may issue an unchanged synthetic hold.
            for target in robot.commands:
                self.assertEqual(target.tolist(),arm.initial.tolist())
            self.assertEqual(len(calls),1)
            self.assertTrue((Path(temp)/'station-events.jsonl').exists())
            self.assertTrue(list((Path(temp)/'replay-frames').glob('*.jpg')))
            html=export_replay(temp,title=spec['title']).read_text()
            data=json.JSONDecoder().raw_decode(html.split('const data=',1)[1])[0]
            self.assertEqual(data['presentation']['title'],'USB-A cable insertion')


if __name__=='__main__':unittest.main()

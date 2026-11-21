"""Model/effort request contracts; synthetic station and mocked API only."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import astra_run as run
import httpx
from demo import offline_only
from test_plug_socket_attention import make_plug, fixture_run
import attention_analysis as analysis


class AstraModelTests(unittest.TestCase):
    def setUp(self):
        network=offline_only();network.__enter__();self.addCleanup(network.close)

    def test_next_trial_sends_astra_6_2_with_medium_reasoning(self):
        arm,robot,_,_,_=make_plug();spec=run.task_configuration('plug-socket');requests=[]
        def respond(request):
            body=json.loads(request.content);requests.append(body)
            self.assertEqual(body['model'],'gpt-6.2-astra')
            self.assertEqual(body['reasoning']['effort'],'medium')
            return httpx.Response(200,json={'status':'completed','output':[{
                'type':'function_call','call_id':'model-test','name':'done',
                'arguments':'{"note":"Synthetic request check only"}'}]})
        policy=run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)),arm.pre_check,
            station=arm,prompt_path=spec['prompt'],env={'RHOS_API_KEY':'offline-test'})
        with tempfile.TemporaryDirectory() as temp,patch('sys.stdout',io.StringIO()),patch.object(run.time,'sleep'):
            log=run.execute(policy,arm,Path(temp),task_spec=spec)
        self.assertEqual(log.status,'success');self.assertEqual(len(requests),1)
        self.assertTrue(all((command==arm.initial).all() for command in robot.commands))

    def test_preflight_reports_new_model_and_effort_without_consuming_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            spec=run.task_configuration('plug-socket');spec['output_root']=Path(temp)
            with patch.object(run,'task_configuration',return_value=spec),patch('sys.argv',['astra_run','--task','plug-socket','--attempt','1']),patch('sys.stdout',new_callable=io.StringIO) as out:
                run.main()
            data=json.loads(out.getvalue())
            self.assertEqual(data['model'],'gpt-6.2-astra');self.assertEqual(data['reasoning_effort'],'medium')
            self.assertFalse(Path(data['output']).exists())

    def test_historical_resumes_keep_their_original_model(self):
        self.assertEqual(run.experiment_model(),('gpt-6.2-astra','medium'))
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(run.experiment_model(temp),('gpt-6-astra','medium'))
            manifest=Path(temp)/'attempt.json'
            manifest.write_text(json.dumps({'model':'gpt-6.2-astra','reasoning_effort':'medium'}))
            self.assertEqual(run.experiment_model(temp),('gpt-6.2-astra','medium'))

    def test_counterfactual_replay_preserves_saved_model_for_both_versions(self):
        for model in ['gpt-6-astra','gpt-6.2-astra']:
            with self.subTest(model=model),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);fixture_run(root)
                wire=root/'wire/test/scene/calls.jsonl';row=json.loads(wire.read_text());row['request']['model']=model
                wire.write_text(json.dumps(row)+'\n');requests=[]
                def respond(request):
                    body=json.loads(request.content);requests.append(body)
                    self.assertEqual(body['model'],model);self.assertEqual(body['reasoning']['effort'],'medium')
                    if not body['tools']:
                        return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'Synthetic view summary'}]}]})
                    return httpx.Response(200,json={'output':[{'type':'function_call','name':'move_joints','arguments':'{"targets":{"right_j0":0.02}}'}]})
                report=analysis.causal_replay(root,[0],transport=httpx.MockTransport(respond),api_key='offline-test',
                    control_boxes={'0':{'main':[.6,.6,.8,.8]}})
                self.assertEqual(report['model'],model);self.assertEqual(report['steps'][0]['model'],model)
                self.assertEqual(len(requests),9)


if __name__=='__main__':unittest.main()

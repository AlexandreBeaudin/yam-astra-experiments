"""New-task conditions, telemetry, video and API counterfactuals; no hardware/network."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import astra_run as run
import httpx
import numpy as np
from PIL import Image, ImageDraw
from inspect_robots import Action, Scene
from inspect_robots.policy_adapter import ToolAdapter
from hardware_replay import EvidenceJournal
from attention_contract import attention_schema, validate_attention
import attention_analysis as analysis
from demo import offline_only
from observer_separation import RegisteredSeparation, HoldOnlyExclusion, load_guard
from test_active_perception import FakeBimanual, FakeFK


def attention(active=False):
    def view(visible):
        return {'visible':visible,'regions':[{'label':'plug_prongs','center_uv':[.3,.3],
            'box_uv':[.2,.2,.4,.4],'weight':1.}] if visible else []}
    return {'primary_view':'main','fusion_mode':'cross_view_parallel',
            'views':{'main':view(True),'left':view(active),'right':view(True)}}


class ColorPreview:
    def __init__(self):self.requested=[]
    def frame(self,view,timeout):
        self.requested.append(view)
        rgb={'main':(20,80,30),'left':(80,20,190),'right':(110,50,80)}[view]
        image=Image.new('RGB',(640,480),rgb);out=io.BytesIO();image.save(out,format='JPEG')
        return out.getvalue(),time.time_ns(),time.monotonic_ns()


def registered_guard(distance=.8,minimum=.16,margin=0.):
    left=np.eye(4);right=np.eye(4);right[0,3]=distance
    return RegisteredSeparation({'verified':True,'provenance':'Synthetic test fixture, not PS registration',
        'left_base_to_station':left,'right_base_to_station':right,'minimum_separation_m':minimum,'margin_m':margin},fk=FakeFK())


def make_plug(active=False,masked=None):
    robot=FakeBimanual();preview=ColorPreview()
    arm=run.StationArm(robot,preview,{'joint_limits':[[-3.,3.]]*14},simulated=True,
        task_id='plug-socket',active_perception=active,separation_guard=registered_guard() if active else None,mask_left_view=masked)
    observation=arm.reset(Scene(id='synthetic-plug',instruction='Synthetic test only'))
    adapter=ToolAdapter(pre_check=arm.pre_check);adapter.bind(arm.info)
    tools=run.station_motion_tools(adapter.tools,arm)
    return arm,robot,preview,observation,tools


def fixture_run(root,active=False):
    root=Path(root);images={};meta={};parts=[{'type':'input_text','text':'Current observation.\nStep budget: step 0/16200 (16200 env steps remaining).'}]
    blobs=root/'wire/test/blobs';blobs.mkdir(parents=True);frames=root/'replay-frames';frames.mkdir()
    for view,color in [('main',(80,100,60)),('left',(90,110,140)),('right',(120,60,80))]:
        image=Image.new('RGB',(160,120),color)
        if view=='left' and not active:image=Image.new('RGB',image.size,'black')
        else:ImageDraw.Draw(image).rectangle((32,24,63,47),fill=(230,40,20))
        png=io.BytesIO();image.save(png,format='PNG');digest=hashlib.sha256(png.getvalue()).hexdigest();(blobs/(digest+'.png')).write_bytes(png.getvalue())
        jpeg=io.BytesIO();image.save(jpeg,format='JPEG');sha=hashlib.sha256(jpeg.getvalue()).hexdigest();(frames/(sha+'.jpg')).write_bytes(jpeg.getvalue())
        meta[view]={'sha256':sha,'masked':view=='left' and not active}
        parts.extend([{'type':'input_text','text':f"camera '{view}' (step 0):"},
            {'type':'input_image','image_url':'data:image/png;base64,$blob:'+digest}])
        images[view]=image
    att=attention(active);reference=[0.]*(12 if active else 6)+[.9]
    decision={'kind':'decision_observation','step':0,'target':reference,'cameras':meta}
    distribution={'kind':'attention_distribution','step':0,'phase':'alignment','targets':{'right_j0':.02},'note':'Fixture only','attention':att}
    (root/'station-events.jsonl').write_text(json.dumps(decision)+'\n'+json.dumps(distribution)+'\n')
    (root/'attempt.json').write_text(json.dumps({'task_id':'plug-socket','active_perception':active,'mask_left_view':not active}))
    request={'model':'gpt-6-astra','input':[{'role':'system','content':'Saved fixture system prompt'},{'role':'user','content':parts}],
        'tools':[{'type':'function','name':'move_joints','parameters':{'type':'object','properties':{'attention':attention_schema()}}}],
        'reasoning':{'effort':'medium'},'store':False,'include':['reasoning.encrypted_content']}
    row={'call':0,'attempt':0,'endpoint':'/responses','status':200,'request':request,
        'response':{'output':[{'type':'function_call','name':'move_joints','arguments':json.dumps({'targets':{'right_j0':.02},'attention':att})}]}}
    path=root/'wire/test/scene/calls.jsonl';path.parent.mkdir();path.write_text(json.dumps(row)+'\n')
    return request,att,reference,images


class PlugSocketTests(unittest.TestCase):
    def setUp(self):
        net=offline_only();net.__enter__();self.addCleanup(net.close)

    def test_default_baseline_masks_left_and_exposes_only_right_actions(self):
        arm,robot,preview,obs,tools=make_plug()
        self.assertEqual(arm.dim,7);self.assertTrue(arm.mask_left_view)
        self.assertEqual(obs.images['left'].shape,(480,640,3));self.assertEqual(obs.images['left'].dtype,np.uint8)
        self.assertFalse(obs.images['left'].any());self.assertTrue(obs.extra['cameras']['left']['masked'])
        self.assertNotIn('left',preview.requested);self.assertTrue(obs.images['main'].any());self.assertTrue(obs.images['right'].any())
        self.assertIn('only main and right',arm.observation_guidance())
        fresh=arm.decision_observation(obs);self.assertEqual(set(fresh.images),{'main','left','right'})
        move=next(t for t in tools if t.name=='move_joints');schema=move.schema['function']['parameters']
        self.assertIn('attention',schema['required']);self.assertNotIn('left_j0',json.dumps(schema))
        self.assertFalse(robot.commands)

    def test_active_mode_exposes_thirteen_dimensions_and_preserves_left_gripper(self):
        arm,robot,preview,obs,tools=make_plug(True)
        self.assertEqual(arm.dim,13);self.assertFalse(arm.mask_left_view);self.assertTrue(obs.images['left'].any())
        result=tools.execute('move_joints',json.dumps({'targets':{'left_j0':.02,'right_j0':-.02},'note':'Fixture','attention':attention(True)}),obs)
        self.assertIsNone(result.error,result.error)
        with patch.object(run.time,'sleep'):
            for action in result.chunk.actions:arm.step(action)
        self.assertAlmostEqual(robot.joints[0],.02);self.assertAlmostEqual(robot.joints[7],-.02)
        self.assertTrue(all(c[6]==.37 for c in robot.commands))
        robot.joints[0]+=.081
        with self.assertRaisesRegex(RuntimeError,'0.08'):arm.step(Action(data=arm.last.copy()))

    def test_stricter_plug_separation_and_unverified_configuration(self):
        robot=FakeBimanual();preview=ColorPreview()
        with self.assertRaisesRegex(ValueError,'0.16'):
            run.StationArm(robot,preview,{'joint_limits':[[-3.,3.]]*14},task_id='plug-socket',active_perception=True,separation_guard=registered_guard(minimum=.14))
        g=registered_guard(distance=.165);full=robot.joints.copy();g.reset(full)
        blocked=full.copy();blocked[0]=.01
        self.assertIsNotNone(g.check_path(np.array([full,blocked])))
        # Exercise an explicitly unregistered fixture, independent of whether
        # the actual station has since acquired measured base registration.
        with tempfile.TemporaryDirectory() as temp:
            geometry=Path(temp)/'unregistered.json'
            geometry.write_text(json.dumps({'schema_version':1,'mode':'registered_fk','verified':False}))
            with patch('sys.argv',['astra_run','--task','plug-socket','--active-perception','--run','--observer-geometry',str(geometry)]),patch('sys.stdout',io.StringIO()),patch('sys.stderr',new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit):run.main()
                self.assertIn('observer motion excluded',err.getvalue())
        self.assertFalse(robot.commands)

    def test_staged_observer_trials_select_their_prompt_pose_and_guard(self):
        for attempt, prompt, minimum, margin in [(6, 'prompt-active-perception.txt', .16, .02),
                                                (7, 'prompt7.txt', .09, .015),
                                                (8, 'prompt8.txt', .09, .015),
                                                (9, 'prompt9.txt', .09, .015),
                                                (10, 'prompt10.txt', .0585, .015)]:
            args = ['astra_run', '--task', 'plug-socket', '--active-perception', '--attempt', str(attempt)]
            with patch('sys.argv', args), patch('sys.stdout', new_callable=io.StringIO) as out:
                run.main()
                data = json.loads(out.getvalue())
            self.assertEqual(Path(data['prompt_path']).name, prompt)
            self.assertEqual(data['separation_guard']['mode'], 'registered_fk_wrist_gripper')
            self.assertEqual(data['separation_guard']['minimum_separation_m'], minimum)
            self.assertEqual(data['separation_guard']['margin_m'], margin)
            self.assertEqual('left_observer_init_joints' in data, attempt in (7, 8, 9, 10))
            if attempt in (7, 8):
                self.assertEqual(data['left_observer_init_joints'], list(run.LEFT_OBSERVER_INIT_JOINTS))
            if attempt in (9, 10):
                captured_pose = json.loads(run.SAVED_OBSERVER_POSES[attempt].read_text())
                self.assertEqual(captured_pose['trial'], attempt)
                self.assertEqual(data['left_observer_init_joints'], captured_pose['joint_positions_rad'])
        geometry = Path(data['prompt_path']).with_name('ps-observer-geometry-trial7.json')
        with self.assertRaisesRegex(ValueError, '0.14'):
            load_guard(geometry)

    def test_trial7_initializes_only_left_arm_before_step_zero(self):
        robot = FakeBimanual()
        saved_pose = np.asarray(run.LEFT_OBSERVER_INIT_JOINTS)
        robot.joints[:6] = saved_pose
        robot.joints[0] -= .02
        robot.joints[6] = .999
        robot.joints[13] = .9991202990983066
        right_base = np.eye(4)
        right_base[0, 3] = .8
        geom = RegisteredSeparation(dict(verified=True, provenance='synthetic',
            left_base_to_station=np.eye(4), right_base_to_station=right_base,
            minimum_separation_m=.09, margin_m=.015), fk=FakeFK(), minimum_allowed=.09)
        arm = run.StationArm(robot, ColorPreview(), {'joint_limits': [[-3., 3.]] * 14},
            simulated=True, task_id='plug-socket', active_perception=True, separation_guard=geom, observer_trial=7)
        robot.joints[13] = .999193607506781  # One encoder-count change on the held open gripper.
        held = robot.joints[6:].copy()
        scene = Scene(id='trial7', instruction='Synthetic test only')
        arm.reset(scene)
        arm.observer_init_waypoints(saved_pose)
        self.assertFalse(robot.commands)
        arm.observer_init_joints = saved_pose
        with self.assertRaisesRegex(RuntimeError, 'supervised'):
            arm.reset(scene)
        arm.session = Mock()
        robot.joints[6] = .5
        with self.assertRaisesRegex(run.SafetyAbort, 'staged open'):
            arm.reset(scene)
        self.assertFalse(robot.commands)
        robot.joints[6] = held[0]
        with patch.object(run.time, 'sleep'):
            obs = arm.reset(scene)
        self.assertEqual(arm.t, 0)
        self.assertEqual(obs.extra['observation_id'], 'yam3-0')
        np.testing.assert_allclose(obs.state['joint_pos'][:6], saved_pose)
        self.assertTrue(robot.commands)
        self.assertTrue(all(np.array_equal(c[6:], held) for c in robot.commands))
        self.assertTrue(all(e['kind'] != 'command_dispatch' for e in arm.events))
        robot.commands.clear()
        arm.reset(scene)  # Matching the saved pose needs no motor command.
        self.assertFalse(robot.commands)

    def test_trial9_tool_and_observation_guidance_use_multiple_left_viewpoints(self):
        robot = FakeBimanual()
        arm = run.StationArm(robot, ColorPreview(), {'joint_limits': [[-3., 3.]] * 14},
            simulated=True, task_id='plug-socket', active_perception=True,
            separation_guard=registered_guard(margin=.015), observer_trial=9)
        obs = arm.reset(Scene(id='trial9', instruction='Synthetic test only'))
        adapter = ToolAdapter(pre_check=arm.pre_check)
        adapter.bind(arm.info)
        move = next(tool for tool in run.station_motion_tools(adapter.tools, arm) if tool.name == 'move_joints')
        description = move.schema['function']['description']
        self.assertIn('behind the brick', description)
        self.assertIn('finger bottoms', description)
        self.assertNotIn('only minor left adjustments', description)
        self.assertIn('multiple left-camera angles', '\n'.join(obs.extra['motion_report']['lines']))

    def test_trial10_separation_feedback_allows_retry_with_smaller_observer_move(self):
        robot = FakeBimanual()
        guard = registered_guard(distance=.25, margin=.015)
        arm = run.StationArm(robot, ColorPreview(), {'joint_limits': [[-3., 3.]] * 14},
            simulated=True, task_id='plug-socket', active_perception=True,
            separation_guard=guard, observer_trial=10)
        arm.reset(Scene(id='trial10', instruction='Synthetic test only'))
        endpoint = arm.last.copy()
        endpoint[0] += .12
        reason = arm.pre_check(np.linspace(arm.last, endpoint, 42)[1:])
        self.assertIn('13.0 cm; need 17.5 cm', reason)
        self.assertIn('smaller left-only angle change', reason)
        self.assertIsNone(arm.pre_check(np.linspace(arm.last, (arm.last + endpoint) / 2, 22)[1:]))
        self.assertFalse(robot.commands)

    def test_cli_conditions_mask_override_and_prompt_isolation(self):
        configuration=run.task_configuration
        def temporary_spec(*args,**kwargs):
            return {**configuration(*args,**kwargs),'output_root':Path(temp)}
        with tempfile.TemporaryDirectory() as temp,patch.object(run,'task_configuration',side_effect=temporary_spec):
            for flags,active,masked in [([],False,True),(['--mask-left-view'],False,True),(['--no-mask-left-view'],False,False),(['--active-perception'],True,False)]:
                with self.subTest(flags=flags),patch('sys.argv',['astra_run','--task','plug-socket','--attempt','1']+flags),patch('sys.stdout',new_callable=io.StringIO) as out:
                    run.main();data=json.loads(out.getvalue())
                    self.assertEqual(data['control_dimensions'],13 if active else 7);self.assertEqual(data['mask_left_view'],masked)
                    self.assertEqual(Path(data['prompt_path']).name,'prompt-active-perception.txt' if active else 'prompt.txt')
                    self.assertFalse(Path(data['output']).exists())
        self.assertEqual(run.task_configuration()['prompt'],run.PROMPT)
        self.assertEqual(run.task_configuration('usb-insertion')['prompt'],run.HERE/'experiments/usb-insertion/prompt.txt')
        for task in ['pick-place','usb-insertion']:
            arm=run.StationArm(FakeBimanual(),ColorPreview(),{'joint_limits':[[-3.,3.]]*14},task_id=task)
            self.assertFalse(arm.mask_left_view);self.assertEqual(arm.dim,7)
        for flags in [['--active-perception','--mask-left-view'],['--task','pick-place','--active-perception']]:
            with patch('sys.argv',['astra_run','--task','plug-socket']+flags),patch('sys.stderr',io.StringIO()):
                with self.assertRaises(SystemExit):run.main()

    def test_attention_bounds_regions_normalization_and_mask_validation(self):
        att=attention();self.assertEqual(validate_attention(att,masked_views={'left'}),att)
        multi=copy.deepcopy(att);r=multi['views']['main']['regions'][0];r['weight']=.6
        extra=copy.deepcopy(r);extra['label']='strip_clearance';extra['weight']=.4;multi['views']['main']['regions'].append(extra)
        validate_attention(multi,masked_views={'left'})
        bad=[]
        for field,value in [('center_uv',[-.1,.3]),('center_uv',[True,.3]),('center_uv',[float('nan'),.3]),('box_uv',[.4,.4,.2,.2]),('weight',0),('weight',.5)]:
            x=copy.deepcopy(att);x['views']['main']['regions'][0][field]=value;bad.append(x)
        x=copy.deepcopy(att);x['views']['main']['regions']=[];bad.append(x)
        x=copy.deepcopy(att);x['views']['left']=copy.deepcopy(x['views']['main']);bad.append(x)
        x=copy.deepcopy(att);x['primary_view']='left';bad.append(x)
        bad.extend([None,{}, {'primary_view':'main'}])
        for payload in bad:
            with self.subTest(payload=payload),self.assertRaises(ValueError):validate_attention(payload,masked_views={'left'})

    def test_malformed_attention_never_plans_or_logs_a_motion(self):
        arm,robot,preview,obs,tools=make_plug()
        for att in [None,{},attention(True)]:
            args={'targets':{'right_j0':.02},'note':'invalid','attention':att}
            result=tools.execute('move_joints',json.dumps(args),obs)
            self.assertIn('attention',result.error)
        self.assertFalse(robot.commands);self.assertFalse(any(e['kind']=='attention_distribution' for e in arm.events))

    def test_attention_journal_and_unchanged_adapter_interpolation(self):
        arm,robot,preview,obs,tools=make_plug();args={'targets':{'right_j0':.02},'note':'Compare fixture','phase':'alignment','attention':attention()}
        plain=ToolAdapter(pre_check=arm.pre_check);plain.bind(arm.info)
        expected=plain.tools.execute('move_joints',json.dumps({'targets':args['targets'],'note':args['note']}),obs)
        with tempfile.TemporaryDirectory() as temp:
            arm.journal=EvidenceJournal(temp);decision=arm.decision_observation(obs)
            result=tools.execute('move_joints',json.dumps(args),decision);self.assertIsNone(result.error,result.error)
            np.testing.assert_allclose(np.stack([a.data for a in result.chunk.actions]),np.stack([a.data for a in expected.chunk.actions]))
            rows=[json.loads(l) for l in (Path(temp)/'station-events.jsonl').read_text().splitlines()]
            event=next(e for e in rows if e['kind']=='attention_distribution')
            for key,value in [('step',0),('phase','alignment'),('note',args['note']),('targets',args['targets']),('attention',args['attention'])]:self.assertEqual(event[key],value)
            saved=next(e for e in rows if e['kind']=='decision_observation');self.assertTrue(saved['cameras']['left']['masked'])
            self.assertFalse(np.asarray(analysis.frame(Path(temp),saved,'left')).any())
        self.assertFalse(robot.commands)

    def test_actual_policy_receives_correct_views_schema_and_logs_both_modes(self):
        for active in [False,True]:
            with self.subTest(active=active):
                arm,robot,preview,obs,tools=make_plug(active);requests=[];spec=run.task_configuration('plug-socket',active_perception=active)
                def respond(request):
                    body=json.loads(request.content);requests.append(body)
                    move=next(t for t in body['tools'] if t['name']=='move_joints')
                    self.assertIn('attention',move['parameters']['required'])
                    images=analysis.current_images(body,0 if len(requests)==1 else arm.t)
                    self.assertEqual(bool(np.asarray(images['left']).any()),active)
                    self.assertNotIn('main_comparison',str(body))
                    if len(requests)==1:
                        targets={'right_j0':.02}
                        if active:targets['left_j0']=.02
                        name='move_joints';args={'targets':targets,'note':'Synthetic only','attention':attention(active)}
                    else:name='done';args={'note':'Synthetic finish only'}
                    return httpx.Response(200,json={'status':'completed','output':[{'type':'function_call','call_id':f'fixture-{len(requests)}','name':name,'arguments':json.dumps(args)}]})
                policy=run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)),arm.pre_check,station=arm,prompt_path=spec['prompt'],env={'RHOS_API_KEY':'test'})
                with tempfile.TemporaryDirectory() as temp,patch.object(run.time,'sleep'),patch('sys.stdout',io.StringIO()):
                    log=run.execute(policy,arm,Path(temp),task_spec=spec)
                    self.assertEqual(log.status,'success',json.dumps(log.to_dict()))
                    self.assertTrue(any(e['kind']=='attention_distribution' for e in arm.events))
                self.assertEqual(len(requests),2)

    def test_explicit_unmask_override_updates_policy_brief_without_moving_left(self):
        arm,robot,preview,obs,tools=make_plug(masked=False)
        spec=run.task_configuration('plug-socket',mask_left_view=False);seen=[]
        def respond(request):
            body=json.loads(request.content);seen.append(body)
            self.assertTrue(np.asarray(analysis.current_images(body,0)['left']).any())
            self.assertIn('left masking was explicitly disabled',body['input'][0]['content'])
            self.assertNotIn('left is parked and MASKED',body['input'][0]['content'])
            return httpx.Response(200,json={'status':'completed','output':[{'type':'function_call','call_id':'override-test','name':'done','arguments':'{"note":"Synthetic only"}'}]})
        policy=run.make_policy(run.GatewayTransport(httpx.MockTransport(respond)),arm.pre_check,station=arm,prompt_path=spec['prompt'],env={'RHOS_API_KEY':'test'})
        with tempfile.TemporaryDirectory() as temp,patch.object(run.time,'sleep'),patch('sys.stdout',io.StringIO()):
            log=run.execute(policy,arm,Path(temp),task_spec=spec)
        self.assertEqual(log.status,'success');self.assertEqual(len(seen),1)
        self.assertTrue(all(np.array_equal(c[:7],arm.initial[:7]) for c in robot.commands))


class AttentionAnalysisTests(unittest.TestCase):
    def setUp(self):
        net=offline_only();net.__enter__();self.addCleanup(net.close)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_heatmap_renders_real_mp4_and_labels_masked_view(self):
        if not shutil.which('ffmpeg'):self.skipTest('ffmpeg unavailable')
        fixture_run(self.root)
        result=analysis.render_heatmap(self.root)
        self.assertTrue(Path(result['output']).is_file());self.assertGreater(Path(result['output']).stat().st_size,1000)
        self.assertEqual(result['frames'][0]['step'],0)
        event=json.loads((self.root/'station-events.jsonl').read_text().splitlines()[0]);image=analysis.frame(self.root,event,'left')
        self.assertFalse(np.asarray(image).any())
        original=Image.new('RGB',(160,120),(40,80,90));rendered=analysis.overlay(original,attention()['views']['main']['regions'])
        self.assertTrue(np.any(np.asarray(original)!=np.asarray(rendered)))

    def test_causal_replay_restores_exact_request_and_masks_equal_area(self):
        captured,att,reference,images=fixture_run(self.root,active=True);requests=[]
        expected=analysis.restore_blobs(captured,self.root/'wire/test/blobs')
        def respond(request):
            body=json.loads(request.content);requests.append(body)
            if not body['tools']:
                return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'Synthetic view summary.'}]}]})
            image=analysis.current_images(body,0)['main'];center=image.getpixel((48,36));value=.04 if center==(128,128,128) else .02
            return httpx.Response(200,json={'output':[{'type':'function_call','name':'move_joints','arguments':json.dumps({'targets':{'right_j0':value},'note':'Counterfactual fixture','attention':att})}]})
        controls={'0':{'main':[.6,.6,.8,.8],'left':[.6,.6,.8,.8]}}
        result=analysis.causal_replay(self.root,[0],transport=httpx.MockTransport(respond),api_key='test-only',control_boxes=controls)
        self.assertEqual(requests[0],expected);self.assertEqual(result['requests'],9)
        conditions=result['steps'][0]['conditions'];self.assertEqual(conditions['reported-region-mask']['delta_from_baseline']['max_joint_delta_rad'],.02)
        self.assertEqual(conditions['equal-area-control']['delta_from_baseline']['max_joint_delta_rad'],0.)
        self.assertTrue(result['steps'][0]['region_vs_control']['control_background_verified'])
        for name in ['dropout-main','dropout-left','dropout-right','sequential-carryover','reported-region-mask','equal-area-control']:
            self.assertTrue((self.root/'causal-replay/step-0'/f'{name}.png').exists(),name)
        for view in ['main','left']:
            original=np.asarray(images[view]);roi=np.asarray(analysis.current_images(requests[-2],0)[view]);control=np.asarray(analysis.current_images(requests[-1],0)[view])
            self.assertEqual(int(np.any(roi!=original,axis=-1).sum()),int(np.any(control!=original,axis=-1).sum()))
        self.assertIn('main: Synthetic view summary.',requests[6]['input'][-1]['content'][0]['text'])
        self.assertIn('left: Synthetic view summary.',requests[6]['input'][-1]['content'][0]['text'])
        self.assertEqual(list(conditions['baseline']['action']['resolved_targets']),[f'left_j{i}' for i in range(6)]+[f'right_j{i}' for i in range(6)]+['right_gripper'])
        self.assertTrue((self.root/'causal-replay/causal_ablation_report.json').exists())

    def test_causal_baseline_mask_stays_masked_and_nonmotion_is_not_zero_delta(self):
        captured,att,reference,images=fixture_run(self.root)
        body,_=analysis.saved_request(self.root,0,att)
        self.assertFalse(np.asarray(analysis.current_images(body,0)['left']).any())
        modified=analysis.intervene(body,drop=['right']);self.assertTrue(np.all(np.asarray(analysis.current_images(modified,0)['right'])==128))
        original=json.dumps(body);analysis.intervene(body,drop=['main']);self.assertEqual(json.dumps(body),original)
        value=analysis.action({'output':[{'type':'function_call','name':'give_up'}]},reference,False)
        self.assertFalse(analysis.deltas(value,value)['comparable'])
        with patch.dict('os.environ',{},clear=True),self.assertRaisesRegex(ValueError,'RHOS_API_KEY'):analysis.causal_replay(self.root,[0])

    def test_control_candidates_are_explicitly_unverified_and_missing_space_fails(self):
        image=Image.new('RGB',(160,120),'white');roi=[.2,.2,.4,.4]
        box,verified=analysis.control_patch(image,roi,[roi]);self.assertFalse(verified)
        a,b,c,d=analysis.pixels(box,image.size);x,y,x1,y1=analysis.pixels(roi,image.size)
        self.assertEqual((c-a)*(d-b),(x1-x)*(y1-y))
        with self.assertRaises(ValueError):analysis.control_patch(image,[0,0,1,1],[[0,0,1,1]])
        with self.assertRaises(ValueError):analysis.control_patch(image,roi,[roi],provided=[.6,.6,.9,.9])

    def test_partial_targets_preserve_reference_and_gripper_units_are_separate(self):
        reference=[.1]*6+[.9]
        base=analysis.action({'output':[{'type':'function_call','name':'move_joints','arguments':json.dumps({'targets':{}})}]},reference,False)
        other=analysis.action({'output':[{'type':'function_call','name':'move_joints','arguments':json.dumps({'targets':{'right_j2':.4,'right_gripper':.2}})}]},reference,False)
        delta=analysis.deltas(base,other)
        self.assertAlmostEqual(delta['l2_norm_rad'],.3);self.assertAlmostEqual(delta['max_joint_delta_rad'],.3)
        self.assertAlmostEqual(delta['right_gripper_delta_normalized'],-.7)

    def test_dropout_removes_historical_images_and_region_mask_only_changes_current_step(self):
        fixture_run(self.root,active=True)
        body,_=analysis.saved_request(self.root,0)
        old=copy.deepcopy(body['input'][-1])
        for part in old['content']:
            if part.get('type')=='input_text':part['text']=part['text'].replace('(step 0)','(step 1)')
        body['input'].insert(1,old)
        drop=analysis.intervene(body,drop=['left'])
        self.assertTrue(all(np.all(np.asarray(analysis.decode_image(part))==128)
            for part,view,step in analysis.image_parts(drop) if view=='left'))
        region=analysis.intervene(body,boxes={'main':[.2,.2,.4,.4]},step=0)
        for part,view,step in analysis.image_parts(region):
            if view=='main':self.assertEqual(analysis.decode_image(part).getpixel((48,36))==(128,128,128),step==0)

    def test_region_proposal_is_retained_when_no_background_control_fits(self):
        fixture_run(self.root)
        path=self.root/'station-events.jsonl';rows=[json.loads(line) for line in path.read_text().splitlines()]
        region=rows[1]['attention']['views']['main']['regions'][0];region.update(center_uv=[.5,.5],box_uv=[0.,0.,1.,1.])
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        def respond(request):
            body=json.loads(request.content)
            if not body['tools']:return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'Synthetic summary'}]}]})
            return httpx.Response(200,json={'output':[{'type':'function_call','name':'move_joints','arguments':'{"targets":{"right_j0":0.02}}'}]})
        report=analysis.causal_replay(self.root,[0],transport=httpx.MockTransport(respond),api_key='test')
        record=report['steps'][0];self.assertEqual(report['requests'],8)
        self.assertIn('reported-region-mask',record['conditions'])
        self.assertIn('no equal-area',record['region_vs_control']['error'])


if __name__=='__main__':unittest.main()

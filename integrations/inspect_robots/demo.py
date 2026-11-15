#!/usr/bin/env python3
"""Saved-frame paper-X evaluation using the pinned, unmodified Inspect Robots core."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent.parent
TRIAL = WORKSPACE / 'gpt_policy_trial_004'
sys.path.insert(0, str(HERE / 'vendor/src'))
import numpy as np
from PIL import Image
from inspect_robots import (Action, ActionChunk, ActionSemantics, Box, CameraSpec,
    Embodiment, EmbodimentInfo, Observation, ObservationSpace, Policy, PolicyConfig,
    PolicyInfo, Scene, Score, StateField, StateSpec, StepResult, Target, Task, eval)

REVISION = '772019598e42ccc7921a043dee3a2ea2692020ea'
IMAGE_SHA = 'dd5b3634b293b5fa88cde2b24a2f49a687c531aeee8bdf9fef9fb9be01de6822'
SPACE = Box(shape=(4,), low=np.array([-.2, -.2, -.2, 0.]),
    high=np.array([.2, .2, .2, 1.]), semantics=ActionSemantics(
        control_mode='eef_delta_pos', gripper='continuous', frame='world',
        dim_labels=('mock_dx', 'mock_dy', 'mock_dz', 'mock_gripper')))
OBS_SPACE = ObservationSpace(cameras=(CameraSpec('main', 480, 640),),
    state=StateSpec((StateField('eef_pos', (3,), 'm'),
                     StateField('gripper', (1,), 'normalized'))))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_trial():
    """Reuse only the read-only validator; never invoke the existing runner."""
    spec = importlib.util.spec_from_file_location('paper_x_trial004_validator', TRIAL / 'dry_run.py')
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    manifest = validator.strict_json((TRIAL / 'manifest.json').read_text())
    if (manifest['execution_enabled'] is not False or not manifest['image_reviewed']
            or manifest['selected_arm'] != 'right' or len(manifest['images']) != 1):
        raise ValueError('Trial 004 reviewed offline contract changed')
    image = manifest['images'][0]
    raw = (TRIAL / image['path']).read_bytes()
    if image['sha256'] != IMAGE_SHA or sha(raw) != IMAGE_SHA:
        raise ValueError('reviewed Trial 004 image mismatch')
    with Image.open(TRIAL / image['path']) as frame:
        rgb = np.array(frame.convert('RGB'))
    if rgb.shape != (480, 640, 3):
        raise ValueError('unexpected saved-frame shape')
    return validator, manifest, rgb


def describe(obs):
    """JSON-safe exact input identity; pixels live in framework frame sidecars."""
    return {'observation_id': obs.extra['observation_id'],
            'image_sha256': IMAGE_SHA, 'image_status': 'reviewed_saved_frame_not_fresh',
            'rgb_sha256': obs.extra['rgb_sha256'],
            'instruction': obs.instruction,
            'state': {k: v.tolist() for k, v in obs.state.items()},
            'mock_state': deepcopy(obs.extra['mock_state']),
            'mock_data': True, 'task_success': 'unverified'}


def compile_mock(proposal, valid, gripper):
    """Toy displacements only: no image geometry or physical target mapping."""
    if not valid or proposal['action'] == 'request_observation':
        return np.array([0., 0., 0., gripper])
    if proposal['action'] == 'pick':
        return np.array([.1, 0., .1, 0.])
    return np.array([.1, .1, -.1, 1.])


class SimulatedDecisionPolicy:
    """Actual Policy protocol consuming fixture responses conditioned on observation."""
    info = PolicyInfo(name='paper-x-simulated-decision', action_space=SPACE,
                      observation_space=OBS_SPACE, checkpoint='offline-fixture-v1')
    config = PolicyConfig(action_horizon=1, replan_interval=1)

    def __init__(self, validator, manifest, decisions):
        self.validator, self.manifest = validator, manifest
        self.decisions = deepcopy(decisions)

    def reset(self, scene):
        self.cursor = 0
        self.audit = []

    def act(self, observation):
        fixture = self.decisions[self.cursor]
        # A canned list cannot silently pass if updated feedback stops reaching act().
        expected = fixture['expected_mock_state']
        if observation.extra['mock_state'] != expected:
            raise ValueError('simulated model received unexpected feedback')
        proposal = self.validator.strict_json(json.dumps(fixture['proposal']))
        result = self.validator.validate(proposal, self.manifest)
        audit = {'decision_id': f'mock-decision-{self.cursor}',
                 'input': describe(observation), 'proposal': proposal,
                 'validator': result, 'model_source': 'simulated_fixture',
                 'api_calls': 0, 'robot_commands': 0}
        self.audit.append(deepcopy(audit))
        self.cursor += 1
        return ActionChunk(actions=[Action(
            data=compile_mock(proposal, result['valid'], observation.state['gripper'][0]),
            meta=audit)])

    def transcript(self):
        return deepcopy(self.audit)

    def on_trial_end(self, record, log_dir, run_id):
        # StepResult.info is not otherwise persisted in the canonical EvalLog.
        record.metadata['paper_x_transitions'] = [
            {**deepcopy(step.action.meta), 'mock_outcome': deepcopy(step.result.info),
             'next_input': describe(step.result.observation), 'step': step.t}
            for step in record.steps]
        record.metadata.update(framework_revision=REVISION, mock_data=True,
            api_calls=0, robot_connections=0, robot_commands=0,
            execution_enabled=False, task_success='unverified')


class SavedFrameMockArm:
    """Actual Embodiment protocol: an in-memory arm and immutable saved camera."""
    info = EmbodimentInfo(name='paper-x-saved-frame-mock-arm', action_space=SPACE,
        observation_space=OBS_SPACE, is_simulated=True,
        environment_id='shurui-offline-toy-world', environment_revision=IMAGE_SHA,
        docs='Synthetic coordinates only; no geometry derived from the saved frame.')

    def __init__(self, validator, manifest, rgb):
        self.validator, self.manifest, self.rgb = validator, manifest, rgb.copy()
        self.rgb.flags.writeable = False

    def reset(self, scene, *, seed=None):
        self.scene = scene
        self.t = 0
        self.position = np.zeros(3)
        self.gripper = 1.
        self.world = {'held': False, 'block_location': 'table', 'revision': 0}
        return self.observe()

    def observe(self):
        return Observation(images={'main': self.rgb.copy()},
            state={'eef_pos': self.position.copy(), 'gripper': np.array([self.gripper])},
            instruction=self.scene.instruction,
            # Deliberately no claim of a fresh camera acquisition or wall-clock sync.
            extra={'observation_id': f'mock-observation-{self.t}',
                   'mock_state': deepcopy(self.world), 'mock_data': True,
                   'rgb_sha256': sha(self.rgb.tobytes())})

    def step(self, action):
        proposal = action.meta['proposal']
        validation = self.validator.validate(proposal, self.manifest)
        expected = compile_mock(proposal, validation['valid'], self.gripper)
        if not np.array_equal(action.data, expected):
            raise ValueError('mock action does not match validated semantic proposal')
        applied = False
        detail = 'rejected_by_semantic_validator'
        if validation['valid']:
            kind = proposal['action']
            if kind == 'pick' and not self.world['held'] and self.world['block_location'] == 'table':
                self.world.update(held=True, block_location='mock_gripper')
                applied, detail = True, 'mock_pick_applied'
            elif kind == 'place' and self.world['held']:
                self.world.update(held=False, block_location='paper_x')
                applied, detail = True, 'mock_release_applied'
            elif kind == 'request_observation':
                detail = 'mock_observation_requested'
            else:
                detail = 'rejected_by_mock_state_precondition'
        if applied:
            self.position += action.data[:3]
            self.gripper = float(action.data[3])
        self.t += 1
        self.world['revision'] = self.t
        return StepResult(observation=self.observe(), info={
            'applied': applied, 'detail': detail, 'mock_state': deepcopy(self.world),
            'mock_data': True, 'physical_success': 'unverified',
            'execution_enabled': False, 'robot_commands': 0})

    def close(self):
        pass


class UnverifiedScorer:
    name = 'physical_task_success'

    def __call__(self, record, target):
        return Score(None, 'Saved image and toy state cannot verify post-release physical success.')


def blocked_network(*args, **kwargs):
    raise RuntimeError('Network disabled in paper-X offline integration')


def offline_only():
    """Block socket connections even if an imported dependency attempts one."""
    stack = ExitStack()
    for owner, name in [(socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                        (socket.socket, 'sendto'), (socket, 'create_connection'),
                        (socket, 'getaddrinfo')]:
        stack.enter_context(patch.object(owner, name, blocked_network))
    return stack


def run(output, decisions=None):
    output = Path(output).resolve()
    # Restrict all artifacts to this integration, preventing accidental trial/latch writes.
    if HERE not in output.parents or output == HERE / 'vendor' or HERE / 'vendor' in output.parents:
        raise ValueError('output must be a new directory inside integrations/inspect_robots, outside vendor')
    output.mkdir(exist_ok=False, parents=True)
    with offline_only():
        validator, manifest, rgb = load_trial()
        if decisions is None:
            decisions = json.loads((HERE / 'decisions.mock.json').read_text())
        policy = SimulatedDecisionPolicy(validator, manifest, decisions)
        arm = SavedFrameMockArm(validator, manifest, rgb)
        assert isinstance(policy, Policy) and isinstance(arm, Embodiment)
        scene = Scene(id='paper-x-trial004-mock', instruction=manifest['task'],
            target=Target('paper_x', {'success_criterion': manifest['success_criterion']}),
            metadata={'mock_data': True, 'framework_revision': REVISION,
                'source_bundle': 'gpt_policy_trial_004', 'saved_image_sha256': IMAGE_SHA,
                'saved_image_path': manifest['images'][0]['path'],
                'manifest_sha256': sha((TRIAL / 'manifest.json').read_bytes()),
                'validator_runner_sha256': sha((TRIAL / 'dry_run.py').read_bytes()),
                'geometry_verified': False, 'execution_enabled': False})
        # Snapshot only allowlisted reusable inputs, never runner outputs or latches.
        (output / 'trial004.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (output / 'trial004.prompt.txt').write_bytes((TRIAL / 'prompt.txt').read_bytes())
        task = Task(name='paper-x-offline-integration', scenes=[scene],
                    scorer=UnverifiedScorer(), max_steps=len(decisions))
        try:
            log = eval(task, policy, arm, log_dir=str(output),
                       store_frames=True, store_actions=True, store_records=True,
                       environment_revision=REVISION)[0]
        finally:
            arm.close()
        if log.status != 'success':
            raise RuntimeError(f'framework evaluation failed: {log.error or log.samples[0].error}')
        # Stable entry point alongside the framework's canonical randomly named JSON sink.
        (output / 'framework-log.json').write_text(json.dumps(log.to_dict(), indent=2) + '\n')
        from replay import export_replay
        export_replay(output / 'framework-log.json', output / 'replay')
        summary = {'framework_revision': REVISION, 'steps': log.stats.total_steps,
                   'log': str(output / 'framework-log.json'), 'mock_data': True,
                   'api_calls': 0, 'robot_connections': 0, 'robot_commands': 0,
                   'task_success': 'unverified'}
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2))

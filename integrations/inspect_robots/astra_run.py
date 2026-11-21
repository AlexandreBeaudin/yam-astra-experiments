#!/usr/bin/env python3
"""One Astra episode using upstream agent/tools and the existing YAM safety owner.

Default: offline preflight. --check-station reads services only. --run consumes
the selected one-episode latch and can move hardware. No direct CAN/IK driver.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
GELLO = ROOT / 'YAM/gello_software'
sys.path[:0] = [str(HERE / 'vendor/src'), str(HERE / 'vendor-agent/src'),
               str(HERE / 'runtime-deps'), str(GELLO)]

import httpx
import numpy as np
from PIL import Image
from hardware_replay import EvidenceJournal, PreviewRecorder, export_replay
from inspect_robots import (ActionSemantics, Box, CameraSpec, EmbodimentInfo,
    Observation, ObservationSpace, Scene, Score, StateField, StateSpec,
    StepResult, Task, eval)
from inspect_robots_agent import LLMAgentPolicy
from inspect_robots import Action, ActionChunk
from inspect_robots.guard import ClampGuard, DeltaLimitGuard, GuardChain, _GUARD_TRACE_KEY
from inspect_robots.errors import SafetyAbort
from inspect_robots.tools import Tool, ToolLibrary, ToolResult
from planned_motion import goal_tools, pose_tool_available
from experiment_resume import atomic_json, install_checkpoint, load_resume, validate_pose, validate_observer_guard
from experiment_budgets import MAX_CALLS, MAX_SECONDS, MAX_STEPS
from observer_separation import load_guard
from attention_contract import attention_schema, validate_attention


def motion_guard(space):
    return GuardChain(ClampGuard(space), DeltaLimitGuard(space))


class PauseOnIntervention:
    name = 'pause-for-review'

    def __init__(self, arm):
        self.arm = arm

    def review(self, action, store):
        causes = [key for key in ('clamped', 'delta_clamped') if action.meta.get(key)]
        if causes:
            self.arm.record(dict(kind='guard_intervention', causes=causes,
                                 guards=store.get(_GUARD_TRACE_KEY, []),
                                 proposed_after_guard=action.data.tolist()))
            raise SafetyAbort('Guard intervention: ' + ', '.join(causes) +
                              '; motion paused for review; confirmed hold required')
        return action

PROFILE = ROOT / 'profiles/shurui-mcap.yaml'
PROMPT = HERE / 'astra_exploration.txt'
URL = 'https://api.rhos.cc/v1/responses'
MODEL = 'gpt-6.2-astra'
REASONING_EFFORT = 'medium'
LABELS = tuple(f'right_j{i}' for i in range(6)) + ('right_gripper',)
MODEL_TIMEOUT_S = 600.
MODEL_ATTEMPTS = 4  # Total attempts, including the first request.
MAX_MOVE_RAD = 0.80  # Per-call ceiling; use fine steps near contact. Interpolation unchanged.
MAX_GRIPPER_DELTA = 1.0
APPROACH_MOVE_RAD = 0.80  # Optional attended path review uses the same ceiling.
STAGED_OBSERVER_TRIALS = (7, 8, 9, 10)
MULTI_VIEW_OBSERVER_TRIALS = (9, 10)
STAGED_OBSERVER_MINIMUM_SEPARATION_M = .09
STAGED_OBSERVER_MARGIN_M = .015
TRIAL10_MINIMUM_SEPARATION_M = .0585
OBSERVER_INIT_TOLERANCE_RAD = .008
# Replace these six radians (left_j0..left_j5) with the next calibrated wrist pose.
# Source: left-wrist-20261122-013244-281376.json, can_follower_l.
LEFT_OBSERVER_INIT_JOINTS = (
    0.12340733958953187, 2.454222934309911, 1.1045624475471119,
    1.354810406652934, 1.3410772869459073, 0.09250782024872173,
)
SAVED_OBSERVER_POSES = {
    9: HERE / 'experiments/plug-socket/trial9-left-wrist-20261122-032518-338907.json',
    10: HERE / 'experiments/plug-socket/trial10-left-wrist-20261122-040939-660496.json',
}
MOTION_PHASES = ('open_space_approach', 'alignment', 'descent', 'grasp', 'placement', 'fine')
OBSERVATION_GUIDANCE = (
    'Use right for approach depth relative to the finger pads and XY centering; '
    'use main for overall table/arm clearance. '
    'main_comparison shows previous/current main crops. '
    'Use fresh observations after each move. On possible contact stop descent, '
    'then assess a small upward withdrawal or reversal with clear space and valid feedback. '
    'Recoverable contact alone is not a reason to give_up. Never bypass guard trips.'
)
MOVE_DESCRIPTION = (
    ' Per-call ceiling: 0.80 rad per arm joint and 1.0 normalized gripper. '
    'In visibly clear workspace, combine active axes in 0.40–0.80 rad transit moves. '
    'Use 0.10–0.20 rad during descent and 0.02–0.05 rad for final XY centering. '
    'Absolute named targets; omitted joints retain their current target. '
    + OBSERVATION_GUIDANCE
)



USB_OBSERVATION_GUIDANCE = (
    'Use main for overall layout and clearance; use right for finger-pad depth, '
    'plug orientation and port alignment. Check fresh images and measured joints after each move. '
    'On binding, hub motion or possible contact, stop advancing; assess a clear, '
    'guard-permitted back-out before revising alignment, grasp or port. Never bypass hardware guards.'
)
USB_MOVE_DESCRIPTION = (
    ' Absolute named right-arm targets; omitted joints retain their current target. '
    'Per-call ceiling: 0.80 rad per arm joint and 1.0 normalized gripper. '
    'Large coupled moves are for clear free-space transit only. Near the hub, '
    'align keyed plug orientation first and use small observed moves along the insertion axis. '
    + USB_OBSERVATION_GUIDANCE
)


def task_configuration(task_id='pick-place', *, active_perception=False, mask_left_view=None, observer_trial=None):
    if task_id == 'plug-socket':
        root = HERE / 'experiments/plug-socket'
        instruction = ('Use the right arm to grasp the rear half of the charger brick and seat both flat prongs '
            'in any of the eight parallel two-prong slot pairs on the unplugged white power strip. '
            'Release and withdraw clear. ')
        masked = not active_perception if mask_left_view is None else mask_left_view
        instruction += ('Use left_j0..left_j5 as an observer camera boom; preserve the left gripper opening.'
            if active_perception else 'Keep the left arm parked; left is masked, and main/right are the active views.' if masked else 'Keep the left arm parked; main, left and right views are available (mask explicitly disabled).')
        prompt_name = 'prompt-active-perception.txt' if active_perception else 'prompt.txt'
        if observer_trial is not None:
            prompt_name = f'prompt{observer_trial}.txt'
            instruction = ('Use the right arm to insert both prongs of the charger brick fully into an upper '
                'two-prong socket on the unplugged, braced power strip. Release and withdraw clear. '
                'Use the pre-positioned left wrist for the side view and keep its gripper open.')
            if observer_trial in MULTI_VIEW_OBSERVER_TRIALS:
                instruction += ' Move the left wrist to additional viewpoints during fine alignment.'
        return dict(id=task_id, active_perception=active_perception,
            prompt=root / prompt_name,
            output_root=root / 'runs', output_prefix='run-', scene_id='yam3-plug-socket-one-episode',
            task_name='yam3-plug-socket', title='Charger brick plug-in-socket insertion', instruction=instruction)
    if task_id == 'usb-insertion':
        root = HERE / 'experiments/usb-insertion'
        spec = dict(id=task_id, active_perception=active_perception, prompt=root / ('prompt-active-perception.txt' if active_perception else 'prompt.txt'), output_root=root / 'runs',
            output_prefix='run-', scene_id='yam3-usb-insertion-one-episode',
            task_name='yam3-usb-insertion', title='USB-A cable insertion', instruction=
            "Use only YAM3's logical right arm to pick up the loose cable and fully seat its "
            "USB-A plug in any one of the four ports on the hub. Leave the plug seated with "
            "the gripper clear. " + ("Use the left arm as an auxiliary observer camera; keep its gripper at its initial opening."
            if active_perception else "Keep the left arm at its initial pose."))
        if active_perception:
            spec['instruction'] = ('Use the right arm to pick up the loose USB-A cable and seat its plug in a top-facing hub port. '
                'Use left_j0..left_j5 to position the left eye-in-hand observer camera for depth and plug-angle views; '
                'keep the left gripper at its initial opening. Leave the plug seated with the right gripper clear.')
        return spec
    if task_id != 'pick-place':
        raise ValueError(f'Unknown task: {task_id}')
    return dict(id=task_id, prompt=PROMPT, output_root=HERE, output_prefix='astra-yam3-run-',
        scene_id='yam3-astra-one-episode', task_name='yam3-astra-gradual-spatial-exploration',
        title='Pink block pick-and-place', instruction=
        'Use the right arm to place the pink block centered on the black X on the white paper, '
        'then return to its initial resting pose before calling done.')


def validate_resume_task(source, task_id):
    manifest = Path(source) / 'attempt.json'
    saved = json.loads(manifest.read_text()).get('task_id', 'pick-place') if manifest.exists() else 'pick-place'
    if saved != task_id:
        raise ValueError(f'Resume task mismatch: saved {saved}, selected {task_id}. '
                         'Use the matching --task; never carry task history into another experiment.')


def station_motion_tools(library, arm):
    """Wrap the pinned planner without changing interpolation or joint selection."""
    tools = []
    for tool in library:
        if tool.name == 'give_up':
            schema = copy.deepcopy(tool.schema)
            schema['function']['description'] += ' ' + arm.observation_guidance()
            schema['function']['parameters']['properties']['reason']['description'] = (
                'State the exact blocking uncertainty or guard and blocked next action; '
                'explain why delivered views, feedback, previous moves and safe alternatives '
                'cannot support progress. Missing side imagery or pose-tool prerequisites alone '
                'are not blockers while another supported action is available. '
                'For contact, explain why a small upward withdrawal or reversal cannot '
                'safely separate the pad before re-centering. Contact alone is not terminal; '
                'all hardware guards remain authoritative.')
            if arm.task_id in ('usb-insertion', 'plug-socket'):
                reason = schema['function']['parameters']['properties']['reason']
                reason['description'] = reason['description'].replace(
                    'small upward withdrawal or reversal', 'clear insertion-axis back-out or reversal')
            tools.append(replace(tool, schema=schema))
            continue
        if tool.name != 'move_joints':
            tools.append(tool)
            continue
        schema = copy.deepcopy(tool.schema)
        schema['function']['description'] += (arm.action_guidance() if arm.active_perception or arm.task_id == 'plug-socket' else USB_MOVE_DESCRIPTION if arm.task_id == 'usb-insertion' else MOVE_DESCRIPTION) + ' Current station allowance: ' + arm.motion_allowance()
        schema['function']['parameters']['properties']['phase'] = {
            'type': 'string', 'enum': list(MOTION_PHASES),
            'description': 'Optional stage label; all phases share the per-call ceiling.'}

        if arm.task_id == 'plug-socket':
            parameters = schema['function']['parameters']
            parameters['properties']['attention'] = attention_schema()
            parameters['required'] = list(dict.fromkeys(parameters.get('required', []) + ['attention']))

        def move(args, observation, original=tool.run):
            phase = args.get('phase', 'fine')
            if phase not in MOTION_PHASES:
                return ToolResult(error='invalid motion phase')
            if arm.task_id == 'plug-socket':
                try:
                    validate_attention(args.get('attention'), masked_views={'left'} if arm.mask_left_view else set())
                except ValueError as exc:
                    return ToolResult(error='invalid attention: ' + str(exc))
                arm.record(dict(kind='attention_distribution', step=arm.t, phase=phase,
                    targets=copy.deepcopy(args.get('targets')), note=args.get('note'),
                    attention=copy.deepcopy(args['attention'])))
                args = {key: value for key, value in args.items() if key != 'attention'}
            prior = arm.motion_phase
            arm.motion_phase = phase
            try:
                return original(args, observation)
            finally:
                arm.motion_phase = prior
        tools.append(replace(tool, schema=schema, run=move))
    return ToolLibrary(tools)


class StationPolicy(LLMAgentPolicy):
    """Refresh and compare evidence at decision boundaries, outside interpolation."""
    def __init__(self, *, station=None, resume_state=None, **kwargs):
        self.station = station
        self.resume_state = resume_state
        super().__init__(**kwargs)

    def bind(self, info):
        info = replace(info, docs=info.docs + ' ' + self.station.action_guidance()) if self.station is not None else info
        super().bind(info)
        if self.station is not None:
            self.bind_tools(ToolLibrary([Tool('observation_recheck', {
                'type': 'function', 'function': {'name': 'observation_recheck',
                'description': 'Observation only: refresh delivered main/left/right cameras and measured state without dispatch. At most two rechecks per motion. No operator input.',
                'parameters': {'type': 'object', 'properties': {'note': {'type': 'string'}},
                               'required': ['note'], 'additionalProperties': False}}},
                self._recheck)]))
            if not self.station.active_perception:
                self.bind_tools(goal_tools(self.station))
            self._adapter._tools = station_motion_tools(self._adapter.tools, self.station)

    def reset(self, scene):
        super().reset(scene)
        # Match the station task policy without modifying the pinned agent.
        self._messages[0]['content'] = self._messages[0]['content'].replace(
            'small, deliberate motions', 'decisive, coupled moves in clear workspace')
        if self.resume_state is not None:
            state = self.resume_state
            # Preserve the unanswered observation too, then supply live views
            # before any new decision. No saved action chunk is replayed.
            self._messages = copy.deepcopy(state['messages'])
            self._messages.append(dict(role='user', content=
                'Experiment resumed after interruption. Any interrupted move is described in history. '
                'Prior actions are already executed. Do not replay them. The next '
                'observation is fresh; reassess the current scene before choosing a new action. '
                'Right-arm action bounds now use nominal model limits instead of the SDK expanded limits. '
                'If the starting wrist is just outside a nominal bound, move inward with observed clearance. '
                'Never repeat a target that caused a tracking stop.'))
            self._client._raw_items_by_call_id = copy.deepcopy(state['raw_items'])
            self._calls_used = state['calls_used']
            self._delta_cursor = 0

    def _recheck(self, args, observation):
        if not isinstance(args.get('note'), str) or not args['note'].strip():
            return ToolResult(error='note required')
        if self.station.rechecks >= 2:
            return ToolResult(error='observation recheck budget exhausted; no further recheck until motion. '
                'This does not itself require stopping: assess delivered views, measured feedback and '
                'previous moves for supported progress within guards. If none exists, state the exact '
                'blocker and blocked action. Do not request other cameras or operator images.')
        self.station.rechecks += 1
        return ToolResult(chunk=ActionChunk(actions=[Action(
            data=self.station.last.copy(), meta={'observation_only': True})]),
            note='Observation-only recheck requested; no station command will be dispatched.')

    def act(self, observation):
        if self.station is not None:
            # Refresh the tool's allowance as well as observation text; approval
            # can expire or be consumed after the policy was bound.
            available_goals = set() if self.station.active_perception else {tool.name for tool in goal_tools(self.station)}
            self._adapter._tools = ToolLibrary([
                replace(tool, schema={**tool.schema, 'function': {
                    **tool.schema['function'], 'description':
                    tool.schema['function']['description'].split(' Current station allowance: ')[0]
                    + ' Current station allowance: ' + self.station.motion_allowance()}})
                if tool.name == 'move_joints' else tool for tool in self._adapter.tools
                if tool.name not in {'move_gripper_to_pose', 'set_gripper'}
                or tool.name in available_goals])
            observation = self.station.decision_observation(observation)
            reference = observation.extra.get('guard_reference', self.station.last)
            if not np.array_equal(reference, self.station.last):
                raise SafetyAbort('guard reference differs from station setpoint; pause for review')
            observation = replace(observation, extra={**observation.extra,
                'guard_reference': np.asarray(reference).tolist()})
        while True:
            chunk = super().act(observation)
            if not chunk.actions or not chunk.actions[0].meta.get('observation_only'):
                return chunk
            self._pending_motion = None
            observation = self.station.decision_observation(observation)


def observer_module():
    spec = importlib.util.spec_from_file_location('yam3_observer', ROOT / 'yam3-sunday/capture_observations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GatewayTransport(httpx.BaseTransport):
    """Use the upstream wire client, adding only the station's required header.

    Restrict every request to the approved URL; retries are owned by the wire client.
    A failed/uncertain request consumes budget too.
    """
    def __init__(self, inner=None, *, deadline=None, timing_sink=None, check=None):
        self.inner = inner if inner is not None else httpx.HTTPTransport(retries=0)
        self.deadline = deadline
        self.calls = 0
        self.timing_sink = timing_sink
        self.check = check
        self.attempt_sink = None

    def handle_request(self, request):
        if str(request.url) != URL or request.method != 'POST':
            raise RuntimeError('unexpected gateway destination or method')
        if self.calls >= MAX_CALLS or (self.deadline and time.monotonic() >= self.deadline):
            raise RuntimeError('one-episode request/time budget exhausted')
        if self.check is not None:
            self.check()
        if self.deadline:
            remaining = max(.001, self.deadline - time.monotonic())
            request.extensions['timeout'] = {k: min(v, remaining) if v is not None else remaining
                for k, v in request.extensions.get('timeout', {}).items()}
        self.calls += 1
        if self.attempt_sink is not None:
            self.attempt_sink(self.calls)
        request.headers['x-openai-actor-authorization'] = 'local-image-extension'
        started = time.monotonic()
        outcome = 'error'
        try:
            response = self.inner.handle_request(request)
            response.read()  # Include full body reception, not only headers.
            outcome = str(response.status_code)
            return response
        finally:
            if self.timing_sink is not None:
                self.timing_sink(dict(kind='model_round_trip', call=self.calls,
                    duration_s=time.monotonic() - started, outcome=outcome))

    def close(self):
        self.inner.close()


def make_policy(transport, pre_check, *, env=None, station=None, resume_state=None,
                timeout_s=MODEL_TIMEOUT_S, model_attempts=MODEL_ATTEMPTS, prompt_path=None,
                model=MODEL, reasoning_effort=REASONING_EFFORT):
    brief = (prompt_path or PROMPT).read_text()
    if station is not None and station.task_id == 'plug-socket' and not station.active_perception and not station.mask_left_view:
        brief = brief.replace('Only main and right are active. left is parked and MASKED; report left visible=false, regions=[].',
            'Main, left and right are active; left masking was explicitly disabled. The left arm stays parked.')
    policy = StationPolicy(station=station, resume_state=resume_state, model=model, base_url='https://api.rhos.cc/v1',
        api_key_env='RHOS_API_KEY', wire='responses', effort=reasoning_effort,
        max_llm_calls=MAX_CALLS, max_retries=model_attempts, timeout_s=timeout_s,
        images='always', image_horizon=3, depth='off', max_speed_frac=0.1,
        eval_notes=brief, transport=transport, pre_check=pre_check,
        transcript_echo=True, env=env)
    # The pinned plugin enables optional explicit cache extensions for GPT-6.
    # RHOS has only been exercised with standard Responses fields here. Keep
    # upstream translation/history intact while omitting those optional fields.
    policy._client._cache_anchors = False
    return policy


def checked_vector(value, shape):
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f'expected finite vector {shape}')
    return array.copy()


def observer_minimum_separation_m(observer_trial):
    if observer_trial == 10:
        return TRIAL10_MINIMUM_SEPARATION_M
    return STAGED_OBSERVER_MINIMUM_SEPARATION_M if observer_trial is not None else .14


def validate_plug_observer_guard(guard, *, observer_trial):
    descriptor = guard.describe()
    minimum = descriptor.get('minimum_separation_m', 0.)
    margin = descriptor.get('margin_m', 0.)
    staged_observer = observer_trial is not None
    minimum_allowed = observer_minimum_separation_m(observer_trial)
    threshold = minimum_allowed + STAGED_OBSERVER_MARGIN_M if staged_observer else .16
    if (descriptor.get('mode') != 'registered_fk_wrist_gripper'
            or not descriptor.get('metric_separation_verified')
            or minimum + margin < threshold
            or staged_observer and (minimum < minimum_allowed or margin < STAGED_OBSERVER_MARGIN_M)):
        raise ValueError(f'plug-socket observer requires registered FK separation >= {threshold:g} m')


def camera_freshness(acquisition, frame_id, received):
    if acquisition is not None:
        age = (received - acquisition) / 1e9
        return dict(exposure_age_s=age, status='recent' if 0 <= age <= 1. else 'stale_or_clock_inconsistent')
    return dict(status='unknown', source_frame_id=frame_id,
                reason='frame ID identifies a source frame but does not establish exposure age')


class StationArm:
    """Expose right-only control by default, or opt-in left observer plus right manipulation.

    Injected RPC/preview objects make the actual adapter testable without sockets.
    Reset observes the station and can position the left observer for a fresh staged trial.
    """
    def __init__(self, robot, preview, status, *, session=None, deadline=None, simulated=False, path_clearance_check=None, pose_planner=None, pose_inputs_available=False, task_id='pick-place', active_perception=False, separation_guard=None, mask_left_view=None, observer_trial=None):
        self.mask_left_view = (task_id == 'plug-socket' and not active_perception) if mask_left_view is None else bool(mask_left_view)
        if active_perception and self.mask_left_view:
            raise ValueError('active observer mode needs the unmasked left view')
        self.active_perception = active_perception
        self.control_mode = 'active_perception' if active_perception else 'right_only'
        self.indices = np.r_[0:6,7:14] if active_perception else np.arange(7,14)
        self.labels = (tuple(f'left_j{i}' for i in range(6)) + LABELS) if active_perception else LABELS
        self.dim = len(self.labels)
        self.arm_indices = np.arange(self.dim-1)
        self.gripper_index = self.dim-1
        self.step_limits = np.array([.003]*(self.dim-1)+[.02])
        self.separation_guard = (separation_guard or load_guard()) if active_perception else None
        self.observer_trial = observer_trial
        staged_observer = observer_trial is not None
        self.observer_init_joints = None
        if staged_observer and (task_id != 'plug-socket' or not active_perception or not self.separation_guard.ready):
            raise ValueError(f'Trial {observer_trial} requires the active plug-socket observer and registered geometry')
        if task_id == 'plug-socket' and active_perception and self.separation_guard.ready:
            validate_plug_observer_guard(self.separation_guard, observer_trial=observer_trial)
        self.task_id = task_id
        self.robot, self.preview, self.session = robot, preview, session
        self.deadline = deadline
        # Trusted integration only, never model text/meta. Must verify the
        # continuous swept arm/fingers against table, objects and stationary
        # left arm, with tracking uncertainty, for this exact path.
        # Optional attended review records and tracks an exact approach path.
        if active_perception and (path_clearance_check is not None or pose_planner is not None or pose_inputs_available):
            raise ValueError('observer mode cannot reuse right-only pose or swept-path helpers')
        self.path_clearance_check = path_clearance_check
        self.verified_approach = None
        self.motion_phase = 'fine'
        self.pose_planner = pose_planner
        # Trusted integration attests grounded right_base position/orientation inputs.
        # Never infer this from model notes, pixels, or an IK solution.
        self.pose_inputs_available = pose_inputs_available
        self._path_attended = False
        self._approved_path = []
        limits = np.asarray(status['joint_limits'], dtype=float)
        if limits.shape != (14, 2) or not np.all(np.isfinite(limits)):
            raise ValueError('daemon must publish finite 14x2 joint limits')
        low, high = limits[self.indices, 0].copy(), limits[self.indices, 1].copy()
        # The normalized range includes held readback near the fully open endpoint.
        # pre_check and dispatch_waypoint enforce the tighter limit on commanded openings.
        low[-1], high[-1] = 0., 1.
        if np.any(low >= high):
            raise ValueError('invalid daemon limits')
        self.space = Box((self.dim,), low=low, high=high, semantics=ActionSemantics(
            control_mode='joint_pos', gripper='continuous', dim_labels=self.labels,
            max_step=tuple(self.step_limits)))
        self.info = EmbodimentInfo(name='yam3-existing-daemon-right',
            action_space=self.space, control_hz=30., is_simulated=simulated,
            capabilities=frozenset({'self_paced'}),
            observation_space=ObservationSpace(
                cameras=(CameraSpec('main', 480, 640), CameraSpec('left', 480, 640), CameraSpec('right', 480, 640),
                         CameraSpec('main_comparison', 366, 1280)),
                state=StateSpec((StateField('joint_pos', (self.dim,), 'rad'),))),
            docs='Right joints J0-J5 in radians; right_gripper normalized 1=open, 0=closed. '
                'Left has no exposed action dimensions. No camera calibration. '
                'Preview acquisition times/IDs unknown. Only supervised visual exploration. '
                'Pose frame is right_base; nominal grasp_site is not a calibrated physical TCP. '
                'Never invent a block/paper pose from pixels.' if task_id == 'pick-place' else
                'Right joints J0-J5 in radians; right_gripper normalized 1=open, 0=closed. '
                'Left has no exposed action dimensions. No camera calibration. '
                'Never invent a plug or port pose from pixels.',
            environment_id='YAM3', morphology='yam-pro-existing-safety-daemon')
        if active_perception:
            self.info = replace(self.info, name='yam-observer-and-right-manipulator',
                docs='Left joints left_j0..left_j5 position the eye-in-hand observer camera (left view). '
                'Left gripper is inaccessible and remains at its initial opening. Right joints right_j0..right_j5 '
                'and right_gripper manipulate. Right opening maximum 0.90. Both arms share bounds, caps, '
                'interpolation and tracking checks. Separation guard: '+json.dumps(self.separation_guard.describe()))
        if task_id == 'plug-socket':
            self.info = replace(self.info, observation_space=ObservationSpace(
                cameras=(CameraSpec('main', 480, 640), CameraSpec('left', 480, 640), CameraSpec('right', 480, 640)),
                state=StateSpec((StateField('joint_pos', (self.dim,), 'rad'),))),
                docs=self.info.docs + ' ' + self.observation_guidance())
        self.events = []
        self.last = None
        self.journal = None
        self.recorder = None
        self.rechecks = 0

    def record(self, event):
        if self.journal is not None:
            self.journal.append(event)
        self.events.append(event)

    def check(self):
        if self.recorder is not None:
            self.recorder.check()
        if self.deadline and time.monotonic() >= self.deadline:
            raise RuntimeError('episode wall-clock budget exhausted')
        if self.session:
            self.session.check()

    def full_target(self, target):
        full = self.initial.copy()
        full[self.indices] = checked_vector(target, (self.dim,))
        return full

    def tracking_error(self, position):
        return float(np.max(np.abs(np.asarray(position)[self.arm_indices] - self.last[self.arm_indices])))

    def reset(self, scene, *, seed=None):
        self.check()
        self.scene, self.t = scene, 0
        self.initial = checked_vector(self.robot.get_joint_state(), (14,))
        self.last = self.initial[self.indices].copy()
        if self.verified_approach is not None:
            approval = self.verified_approach
            if np.max(np.abs(self.initial - approval.initial)) > .008:
                raise SafetyAbort('station moved since attended path check; repeat station check')
            # Preserve the exact reviewed planner reference, allowing only the
            # existing small tracking residual around that reference.
            self.initial = approval.initial.copy()
            self.last = approval.path[0].copy()
        if getattr(self, 'resume_state', None) is not None:
            state = self.resume_state
            validate_pose(state, self.initial)
            self.initial = np.asarray(state['initial']).copy()
            self.last = np.asarray(state['target']).copy()
            self.t = state['step']
            self.rechecks = state['rechecks']
        if self.active_perception:
            self.separation_guard.reset(self.initial)
            self.record(dict(kind='observer_guard', **self.separation_guard.describe()))
        if self.observer_init_joints is not None and getattr(self, 'resume_state', None) is None:
            self.position_left_observer()
        self.previous_decision = None
        self._approved_path = []
        return self.observe()

    def observer_init_waypoints(self, joints):
        if self.observer_trial is None:
            raise RuntimeError('observer initialization requires a staged plug-socket trial')
        if self.initial[6] < .95:
            raise SafetyAbort(f'Trial {self.observer_trial} needs the left gripper staged open before initialization')
        target = self.last.copy()
        target[:6] = checked_vector(joints, (6,))
        delta = np.abs(target - self.last)
        outside = np.flatnonzero((target < self.space.low) | (target > self.space.high))
        if outside.size:
            details = ', '.join(f'{self.labels[i]}={target[i]:.5f} '
                                f'(allowed [{self.space.low[i]:.5f}, {self.space.high[i]:.5f}])'
                                for i in outside)
            raise SafetyAbort(f'Trial {self.observer_trial} observer initialization target is outside station limits: {details}')
        if np.max(delta) > MAX_MOVE_RAD:
            raise SafetyAbort(f'Stage the left arm closer to the saved pose before starting Trial {self.observer_trial}')
        if np.max(delta) <= OBSERVER_INIT_TOLERANCE_RAD:
            points = self.last[np.newaxis, :]
        else:
            count = int(np.ceil(np.max(delta / self.step_limits)))
            points = np.linspace(self.last, target, count + 1)[1:]
        if reason := self.pre_check(points):
            raise SafetyAbort(f'Trial {self.observer_trial} observer initialization rejected: {reason}')
        return points

    def position_left_observer(self):
        if self.session is None:
            raise RuntimeError('observer initialization requires an active supervised motion session')
        points = self.observer_init_waypoints(self.observer_init_joints)
        moved = not np.array_equal(self.last, points[-1])
        self.record(dict(kind='observer_init_start', step=0, target=list(self.observer_init_joints),
                         left_gripper=self.initial[6], waypoints=len(points) if moved else 0))
        if moved:
            for point in points:
                self.dispatch_waypoint(point, initializing=True)
        deadline = time.monotonic() + 3.
        desired = np.asarray(self.observer_init_joints)
        while True:
            sample = self.measured_feedback()
            position = np.asarray(sample['position'])[:6]
            if np.max(np.abs(position - desired)) <= OBSERVER_INIT_TOLERANCE_RAD:
                break
            if time.monotonic() >= deadline or self.tracking_error(sample['position']) > .08:
                raise SafetyAbort(f'Trial {self.observer_trial} left observer did not settle at the saved pose')
            time.sleep(.05)
        self.initial = self.full_target(self.last)
        self.record(dict(kind='observer_init_complete', step=0, measured_left_joints=position.tolist(),
                         left_gripper=self.initial[6], moved=moved))

    def decision_observation(self, rollout_observation):
        """Hold the existing setpoint, then obtain paired images/readback.

        Never dispatch a target here or compensate a persistent residual. The
        daemon owns the hold. The timeout is bounded and checked like motion.
        """
        started = time.monotonic()
        previous = self.previous_decision
        moved = previous is not None and not np.array_equal(previous['target'], self.last)
        residual = None
        if moved:
            # A changed command needs a full second before judging image motion.
            # Even a small residual is not proof of mechanical settling.
            for _ in range(61):
                sample = self.measured_feedback()
                residual = self.tracking_error(sample['position'])
                if residual > .08:
                    raise RuntimeError('arm tracking residual exceeds 0.08 rad at decision boundary')
                elapsed = time.monotonic() - started
                if elapsed >= 1. and residual <= .008:
                    break
                if elapsed >= 3.:
                    break
                time.sleep(.05)
        fresh = self.observe()
        measured = np.asarray(fresh.state['joint_pos'])
        residual = self.tracking_error(measured)
        if residual > .08:
            raise RuntimeError('arm tracking residual exceeds 0.08 rad at decision boundary')
        # Fixed display crop only: never used to infer geometry or choose motion.
        def detail(pixels):
            return np.asarray(Image.fromarray(pixels).crop((96, 192, 544, 448))
                              .resize((640, 366), Image.Resampling.LANCZOS))
        before = previous['main'] if previous is not None else fresh.images['main']
        comparison = np.concatenate((detail(before), detail(fresh.images['main'])), axis=1)
        lines = list(fresh.extra['motion_report']['lines'])
        lines += [
            f'Decision-boundary passive settling wait: {time.monotonic() - started:.2f} s; '
            f'arm residual {residual:.5f} rad; within 0.008 rad tolerance: {residual <= .008}. '
            'Tolerance is not proof of clearance or contact; never chase a residual.',
            'main_comparison is a derived display, not another camera: LEFT previous decision, '
            'RIGHT current decision; both crop main pixels x=96:544, y=192:448. '
            'Use the full main/left/right views for clearance and anything outside that crop.',
        ]
        if previous is not None:
            for view in ('main', 'left', 'right'):
                prior = previous.get('camera_meta', {}).get(view, {})
                current_meta = fresh.extra['cameras'][view]
                prior_id, current_id = prior.get('frame_id'), current_meta.get('frame_id')
                changed = None if prior_id is None or current_id is None else prior_id != current_id
                lines.append(f'{view} source frame ID changed: {changed}; source exposure metadata: {current_meta["freshness"]}')
            delta = measured - previous['measured']
            lines.append('Measured change since previous decision: ' + ', '.join(
                f'{label}={value:+.5f}' for label, value in zip(self.labels, delta)))
            lines.append('JPEG content changed since previous decision: ' + ', '.join(
                f'{view}={fresh.extra["cameras"][view]["sha256"] != previous["hashes"][view]}'
                for view in ('main', 'right')) + '. Content change alone is not exposure freshness or useful motion.')
        if self.task_id == 'plug-socket':
            lines = [line for line in lines if not line.startswith('main_comparison is a derived display')]
        # Preserve rollout metadata, but replace stale sensor fields with refresh.
        extra = {**rollout_observation.extra, **fresh.extra,
                 'motion_report': {'lines': lines}, 'decision_observation': True}
        images = fresh.images if self.task_id == 'plug-socket' else {**fresh.images, 'main_comparison': comparison}
        if self.journal is not None and self.task_id != 'plug-socket':
            output = io.BytesIO()
            Image.fromarray(comparison).save(output, format='JPEG')
            self.journal.frame('main_comparison', output.getvalue(), time.time_ns(),
                               time.monotonic_ns(), source='derived_decision_comparison')
        self.record(dict(kind='decision_observation', step=self.t, **extra,
                         measured_joints=measured.tolist(), target=self.last.tolist()))
        self.previous_decision = dict(main=fresh.images['main'].copy(), measured=measured.copy(),
            target=self.last.copy(), camera_meta=fresh.extra['cameras'], hashes={v: fresh.extra['cameras'][v]['sha256'] for v in ('main', 'left', 'right')})
        return replace(fresh, images=images, extra=extra)

    def observation_guidance(self):
        if self.observer_trial in MULTI_VIEW_OBSERVER_TRIALS:
            return ('Use main for overall clearance, right for top-down prong and slot alignment, '
                'and the initial left side view to check finger depth, prong height and insertion depth. '
                'Use an upper two-prong socket nearer the left camera. Change the left viewpoint during '
                'alignment, including a clear view from behind the brick, to check for tilt from multiple angles. '
                + ('When close to the brick or socket, hold the right arm steady for small left-only viewpoint changes. '
                   if self.observer_trial == 10 else '') +
                'Report attention on every move_joints call; coordinates use the full original view, origin top-left.')
        if self.observer_trial is not None:
            return ('Use main for overall clearance, right for top-down prong and slot alignment, '
                'and the pre-positioned left side profile for height, pitch and insertion depth. '
                'Use an upper two-prong socket. Make only minor left adjustments if it leaves frame. '
                'Report attention on every move_joints call; coordinates use the full original view, origin top-left.')
        if self.task_id == 'plug-socket':
            views = ('left is masked/disabled: only main and right are available. '
                if self.mask_left_view else 'main, left and right are available. ')
            return (views + 'Use main for lateral clearance; right for visible slot/prong alignment. '
                'The wrist view can look horizontally when prongs are vertical; keep the insertion posture. '
                + ('Left is the movable side-profile observer. ' if self.active_perception else '')
                + 'Keep both prongs visible beyond the pads; avoid the angled three-prong slots. '
                'Report attention on every move_joints call; coordinates use the full original view, origin top-left.')
        if self.active_perception:
            return (USB_OBSERVATION_GUIDANCE + ' Left is the eye-in-hand observer camera on left_j0..left_j5. '
                'Use its oblique view for depth and plug angle; confirm clearance in main. '+json.dumps(self.separation_guard.describe()))
        return USB_OBSERVATION_GUIDANCE if self.task_id == 'usb-insertion' else OBSERVATION_GUIDANCE

    def action_guidance(self):
        if self.task_id == 'plug-socket':
            grasp = ('Before closing, verify that the finger bottoms are level with the bottom of the brick; '
                     'both prongs must extend past the pads. ' if self.observer_trial in MULTI_VIEW_OBSERVER_TRIALS else
                     'Grasp the rear rim; both prongs must extend past the pads. ')
            return ('Absolute named ' + ', '.join(self.labels) + '. Omitted targets hold. '
                'Right gripper 0=closed, maximum opening 0.90; left gripper fixed. '
                '0.80 rad/joint per call, 0.003 rad per waypoint; 0.08 rad arm tracking guard. '
                + grasp + self.observation_guidance())
        if self.active_perception:
            return ('Absolute named left_j0..left_j5 and right_j0..right_j5 targets; right_gripper 0=closed, '
                'maximum opening 0.90. Omitted targets hold. Left gripper stays fixed at its initial opening. '
                'Both arms: 0.80 rad/joint per call, 0.003 rad per waypoint, and 0.08 rad tracking guard. '
                'The left arm is an observer camera boom. '+self.observation_guidance())
        if self.task_id == 'usb-insertion':
            return USB_MOVE_DESCRIPTION.strip()
        if pose_tool_available(self):
            return ('Use move_gripper_to_pose for grounded goal-level travel; local IK and timing '
                    'generate waypoints, subject to trusted whole-path clearance checks. '
                    'Use set_gripper for closure/release. ' + OBSERVATION_GUIDANCE)
        return ('Control mode: move_joints, absolute named right-arm targets in radians; '
                'right_gripper 1=open, 0=closed; full open or close in one call '
                f'(maximum delta {MAX_GRIPPER_DELTA:.1f}). '
                f'Arm per-call ceiling {MAX_MOVE_RAD:.2f} rad/joint in clear space; '
                'use 0.10–0.20 rad descent and 0.02–0.05 rad final XY steps. ' + OBSERVATION_GUIDANCE)

    def motion_allowance(self):
        approval = self.verified_approach
        available = approval is not None and approval.available(self.last)
        detail = (f' Optional verified path SHA256 {approval.digest}; start {approval.path[0].tolist()}; '
                  f'absolute target {dict(zip(LABELS, approval.path[-1].tolist()))}; '
                  'name all seven dimensions; one use, forward only, no gripper change.'
                  if available else '')
        return (f'Effective cap: {MAX_MOVE_RAD} rad/joint per move_joints call; '
                f'{MAX_GRIPPER_DELTA:.2f} gripper per move_joints call. '
                'Use coupled transit moves in clear workspace and fine steps near contact. '
                + detail + ' ' + self.action_guidance()
                + (' Planned goals require trusted whole-path acceptance and have no total displacement cap.'
                   if callable(self.path_clearance_check) else ''))


    def pre_check(self, waypoints, *, planned=False):
        self.check()
        self._approved_path = []  # Clear even when a replacement plan is invalid.
        self._path_attended = False
        points = np.asarray(waypoints)
        if self.last is None or points.ndim != 2 or points.shape[0] == 0 or points.shape[1] != self.dim or not np.all(np.isfinite(points)):
            return 'invalid waypoints or no measured start'
        if (self.active_perception or self.task_id == 'plug-socket') and points[-1,-1] > .90 + 1e-9 and abs(points[-1,-1]-self.last[-1]) > 1e-9:
            return 'right gripper maximum commanded opening is 0.90 (initial opening may only hold or decrease)'
        delta = np.abs(points - self.last)
        cap, clearance = MAX_MOVE_RAD, 'unknown'
        if planned:
            if self.path_clearance_check is None:
                return ('missing trusted swept-path clearance checker for planned motion: '
                        'registered table, block/paper, fixtures and stationary left arm; '
                        'include gripper/cables and tracking uncertainty')
            path = np.vstack((self.last, points)).copy()
            path.flags.writeable = False
            if self.path_clearance_check(path) is not True:
                return 'whole-path clearance check rejected or could not validate planned motion'
            cap, clearance = None, 'planned_verified'
        if not planned and self.motion_phase == 'open_space_approach' and self.path_clearance_check is not None:
            path = np.vstack((self.last, points)).copy()
            path.flags.writeable = False
            verdict = self.path_clearance_check(path)
            if verdict is False:
                return 'whole-path clearance check rejected motion'
            if verdict is True:
                cap, clearance = APPROACH_MOVE_RAD, 'verified'
        if not planned and self.motion_phase == 'open_space_approach' and self.verified_approach is not None:
            if self.verified_approach.matches(np.vstack((self.last, points))):
                cap, clearance = APPROACH_MOVE_RAD, 'attended_verified'
        self.record(dict(kind='move_allowance', phase=self.motion_phase,
                         cap_rad=cap, clearance=clearance,
                         requested_rad=float(np.max(delta[:, self.arm_indices]))))
        if not planned and (np.any(delta[:, self.arm_indices] > cap + 1e-9) or np.any(delta[:, -1] > MAX_GRIPPER_DELTA + 1e-9)):
            return (f'split the motion: maximum {cap} rad/joint and '
                    f'{MAX_GRIPPER_DELTA:.2f} gripper per move; clearance {clearance}. '
                    'Choose a target within these per-call limits.')

        if self.active_perception:
            if planned:
                return 'active-perception mode exposes guarded move_joints only; right-only pose tools are unavailable'
            full_path = np.vstack([self.full_target(self.last)] + [self.full_target(point) for point in points])
            if reason := self.separation_guard.check_path(full_path):
                certificate = getattr(self.separation_guard, 'last_check', None)
                self.record(dict(kind='inter_arm_rejection', guard=self.separation_guard.mode, reason=reason,
                    phase='trajectory_preflight',step=self.t, certificate=copy.deepcopy(certificate)))
                if (self.observer_trial == 10 and certificate
                        and certificate['min_sampled_distance_m'] is not None
                        and certificate['min_sampled_distance_m'] < certificate['threshold_m']):
                    closest = certificate['min_sampled_distance_m'] * 100
                    required = certificate['threshold_m'] * 100
                    return (f'{reason}. Closest modeled separation on the proposed path is {closest:.1f} cm; '
                            f'need {required:g} cm. Hold the right arm and try a smaller left-only angle change, '
                            'or retract the left arm for a wider approach before changing views.')
                return reason
        # Dry-run the same bounds and delta guards with an isolated store.
        guard, store = motion_guard(self.space), {}
        guard.review(Action(data=self.last.copy()), store)
        for index, point in enumerate(points):
            action = Action(data=point.copy())
            reviewed = guard.review(action, store)
            if reviewed is not action:
                cause = f'guard intervention at planned waypoint {index}: {dict(reviewed.meta)}; pause for review'
                self.record(dict(kind='guard_intervention', phase='trajectory_preflight', cause=cause,
                                 proposed=action.data.tolist(), approved=reviewed.data.tolist()))
                raise SafetyAbort(cause)
        sample = self.measured_feedback()
        measured = checked_vector(sample['position'], (self.dim,))
        if self.tracking_error(measured) > .08:
            return 'measured arm is over 0.08 rad behind its setpoint; stop and inspect'
        if planned or clearance in ('verified', 'attended_verified'):
            self._approved_path = [point.copy() for point in points]
            if clearance == 'attended_verified':
                self.verified_approach.used = True
                self._path_attended = True
        return None

    def measured_feedback(self):
        self.check()
        obsmod = observer_module()
        diagnostics = self.robot.get_diagnostics()
        if self.active_perception:
            followers = diagnostics.get('followers', diagnostics)
            sides = {side: obsmod.measured_right({'right': followers.get(side)}) for side in ['left', 'right']}
            for side, values in sides.items():
                age = (time.time_ns() - values['source_time_ns']) / 1e9
                if not 0 <= age <= .25:
                    raise RuntimeError(f'{side} measured feedback is stale or clock is inconsistent')
            sample = dict(source_time_ns=min(v['source_time_ns'] for v in sides.values()),
                position=sides['left']['position'][:6]+sides['right']['position'])
        else:
            sample = obsmod.measured_right(diagnostics)
        age = (time.time_ns() - sample['source_time_ns']) / 1e9
        if not 0 <= age <= .25:
            raise RuntimeError('right measured feedback is stale or clock is inconsistent')
        joints = checked_vector(self.robot.get_joint_state(), (14,))
        locked = [6] if self.active_perception else list(range(7))
        if np.max(np.abs(joints[locked] - self.initial[locked])) > .05:
            raise RuntimeError('locked left dimensions drifted over their 0.05 tolerance; stop' if self.active_perception else 'left arm/gripper drifted over its 0.05 tolerance; stop')
        return sample

    def check_model_wait(self):
        sample = self.measured_feedback()
        if self.tracking_error(sample['position']) > .08:
            raise RuntimeError('arm tracking residual exceeds 0.08 rad during model wait')

    def observe(self):
        sample = self.measured_feedback()
        images, camera_meta = {}, {}
        for view in ('main', 'left', 'right'):
            if view == 'left' and self.mask_left_view:
                output = io.BytesIO()
                Image.fromarray(np.zeros((480, 640, 3), dtype=np.uint8)).save(output, format='JPEG')
                frame = (output.getvalue(), time.time_ns(), time.monotonic_ns(), {})
            else:
                frame = self.preview.frame(view, 1.)
            raw, wall, mono = frame[:3]
            source = frame[3] if len(frame) > 3 else {}
            acquisition = source.get('acquisition_time_ns')
            frame_id = source.get('frame_id')
            if self.journal is not None:
                self.journal.frame(view, raw, wall, mono, source='policy_observation',
                    acquisition_time_ns=acquisition, frame_id=frame_id)
            with Image.open(io.BytesIO(raw)) as image:
                rgb = np.array(image.convert('RGB'))
            if rgb.shape != (480, 640, 3):
                raise RuntimeError('unexpected preview shape')
            images[view] = rgb
            camera_meta[view] = dict(sha256=hashlib.sha256(raw).hexdigest(),
                received_time_ns=wall, received_monotonic_ns=mono,
                acquisition_time_ns=acquisition, frame_id=frame_id,
                freshness=camera_freshness(acquisition, frame_id, wall))
            if view == 'left' and self.mask_left_view:
                images[view] = np.zeros((480, 640, 3), dtype=np.uint8)
                camera_meta[view]['masked'] = True
        measured = checked_vector(sample['position'], (self.dim,))
        report = {'lines': [
            self.observation_guidance(),
            self.motion_allowance(),
            'Camera source metadata: ' + json.dumps(camera_meta),
            'Camera images are preview receive-time-only; exposure freshness is unverified.',
            'Measured motion: encoder readback and residual only; not visual displacement or clearance.',
            'Visible displacement: requires comparison of main/left/right views; unknown until reviewed.',
            'Camera freshness: unknown for receive-time-only previews; JPEG change does not prove freshness.',
            'Guard intervention: any modification pauses motion for review, independently of station acceptance.',
            ('Pre-grasp: establish approach direction, relative depth, finger alignment and swept clearance from supported observations before descent or closure. Grasp examples, calibration and routine supervisor review are optional.'
             if self.task_id == 'pick-place' else
             'Grasp the plug housing with its metal tip exposed. Confirm visual lift before approaching the hub; align the tip and keyed orientation before small insertion-axis moves.'),
            'For plausible settling/frame delivery issues, use observation_recheck (at most two per motion). For geometric ambiguity, consider a different informative bounded probe with supported clearance. Missing metadata or one inconclusive recheck alone does not require stopping.',
            f'Measured arm residual maximum: {self.tracking_error(measured):.5f} rad.',
        ]}
        if self.task_id == 'plug-socket':
            alignment = ('Grasp the rear rim with both prongs exposed; verify lift and use both wrist views to align '
                         'the horizontal prongs with an upper two-prong socket.' if self.observer_trial is not None else
                         'Grasp the rear rim with both prongs exposed; verify lift and align to one of eight parallel two-prong slot pairs.')
            if self.observer_trial in MULTI_VIEW_OBSERVER_TRIALS:
                alignment = ('Check finger depth in the left view before closing and check prong exposure in the right view. '
                             'Verify lift, then use multiple left-camera angles and the right view to align the prongs.')
            report['lines'] = [line.replace('Grasp the plug housing with its metal tip exposed. Confirm visual lift before approaching the hub; align the tip and keyed orientation before small insertion-axis moves.', alignment) for line in report['lines']]
        observation = Observation(images=images, state={'joint_pos': measured},
            instruction=self.scene.instruction, extra=dict(
                observation_id=f'yam3-{self.t}', cameras=camera_meta,
                measured_source_time_ns=sample['source_time_ns'], motion_report=report,
                geometry_verified=False, physical_success='unverified'))
        self.record(dict(kind='observation', step=self.t,
            **observation.extra, measured_joints=measured.tolist()))
        return observation

    def step(self, action):
        self.dispatch_waypoint(action.data)
        return StepResult(observation=self.observe(), info={'accepted': True,
            'measured_completion': None, 'physical_success': 'unverified'})

    def dispatch_waypoint(self, point, *, initializing=False):
        self.check()
        target = checked_vector(point, (self.dim,))
        prefix = 'observer_init_' if initializing else ''
        next_step = self.t if initializing else self.t + 1
        if (self.active_perception or self.task_id == 'plug-socket') and target[-1] > .90 + 1e-9 and target[-1] > self.last[-1] + 1e-9:
            raise RuntimeError('right gripper may not increase above 0.90')
        if np.any(target < self.space.low) or np.any(target > self.space.high):
            raise RuntimeError('target outside daemon limits')
        if np.any(np.abs(target - self.last) > self.step_limits + 1e-8):
            raise RuntimeError('waypoint exceeds declared per-step change')
        if (self._approved_path and self._path_attended and self.verified_approach is not None
                and self.verified_approach.used and time.monotonic() >= self.verified_approach.expires):
            raise SafetyAbort('attended approach authorization expired; stop for review')
        if self._approved_path and not np.array_equal(target, self._approved_path[0]):
            self._approved_path = []
            raise SafetyAbort('dispatch differs from clearance-checked path; pause for review')
        # Compare fresh feedback with the last accepted setpoint, not the next
        # waypoint. A blocked joint must stop the chunk before another RPC;
        # checking only at the next policy decision lets the error accumulate.
        sample = self.measured_feedback()
        measured = checked_vector(sample['position'], (self.dim,))
        errors = np.abs(measured[self.arm_indices] - self.last[self.arm_indices])
        worst = int(np.argmax(errors))
        if errors[worst] > .08:
            cause = 'arm tracking residual exceeds 0.08 rad before waypoint dispatch'
            self._approved_path = []
            self.record(dict(kind='guard_intervention', phase='waypoint_tracking',
                cause=cause, step=self.t, blocked_step=next_step,
                joint=self.labels[worst], residual_rad=float(errors[worst]), limit_rad=.08,
                measured_joints=measured.tolist(), reference_target=self.last.tolist(),
                proposed_target=target.tolist(), measured_source_time_ns=sample['source_time_ns']))
            raise RuntimeError(cause)
        full = self.full_target(target)
        if self.active_perception:
            measured_full = checked_vector(self.robot.get_joint_state(), (14,))
            if reason := self.separation_guard.check_path(np.vstack([measured_full, full])):
                self.record(dict(kind='inter_arm_rejection', guard=self.separation_guard.mode, reason=reason,
                    phase='waypoint_dispatch',step=self.t,blocked_step=next_step,
                    measured_full_joints=measured_full.tolist(),proposed_full_target=full.tolist(),
                    certificate=copy.deepcopy(getattr(self.separation_guard,'last_check',None))))
                raise SafetyAbort(reason)

        start = time.monotonic()
        command_id = uuid.uuid4().hex
        self.record(dict(kind=prefix+'command_dispatch', command_id=command_id,
            step=next_step, target=full.tolist(), measured_completion=None))
        self.check()  # Recheck deadline/session after synchronous disk writes.
        # A dispatch without acceptance remains uncertain after an interrupted RPC.
        acceptance = self.robot.command_joint_state(full)
        if not isinstance(acceptance, dict) or 'applied_joint_state' not in acceptance:
            raise RuntimeError('missing command acceptance')
        applied = checked_vector(acceptance['applied_joint_state'], (14,))
        self.record(dict(kind=prefix+'command_acceptance', command_id=command_id, step=next_step,
            target=full.tolist(), applied=applied.tolist(), accepted=True,
            limited=bool(acceptance.get('limited')),
            accepted_time_ns=acceptance.get('accepted_time_ns'), measured_completion=None))
        if acceptance.get('limited') or not np.allclose(applied, full, atol=1e-8, rtol=0):
            raise RuntimeError('daemon modified command; stop rather than continue stale trajectory')
        if self._approved_path:
            self._approved_path.pop(0)
        self.last = target.copy()
        self.rechecks = 0
        self.t = next_step
        time.sleep(max(0., 1 / 30 - (time.monotonic() - start)))
        self.record(dict(kind=prefix+'motion_time', command_id=command_id, step=self.t,
                         duration_s=time.monotonic() - start))

    def close(self):
        pass  # The outer SupervisedMotionSession owns confirmed SAFE HOLD.


class VerifiedApproach:
    """In-memory, one-use authorization for one exact planner output on YAM3."""
    def __init__(self, path, initial):
        self.path = np.array(path, dtype=float, copy=True)
        self.path.flags.writeable = False
        self.initial = initial.copy()
        self.digest = hashlib.sha256(self.path.tobytes()).hexdigest()
        self.expires = time.monotonic() + 300
        self.used = False

    def available(self, start):
        return (not self.used and time.monotonic() < self.expires and
                start is not None and np.array_equal(start, self.path[0]))

    def matches(self, path):
        return self.available(path[0]) and np.array_equal(path, self.path)


def attended_approach_check(arm, target, directory):
    """Read services and plan only. Never dispatch, acquire control or call Astra."""
    if not sys.stdin.isatty():
        raise RuntimeError('optional path review requires an attended station terminal')
    from inspect_robots.policy_adapter import ToolAdapter
    target = checked_vector(target, (7,))
    delta = np.abs(target - arm.last)
    if (np.max(delta[:6]) <= 0 or np.max(delta[:6]) > APPROACH_MOVE_RAD + 1e-9
            or target[6] != arm.last[6]):
        raise ValueError('review target must change an arm joint by >0 and <=0.80 rad; keep gripper fixed')
    captured = []
    def capture(points):
        captured.append(np.vstack((arm.last, points)))
        return None
    adapter = ToolAdapter(pre_check=capture, max_speed_frac=.1)
    adapter.bind(arm.info)
    observation = arm.observe()
    observation = replace(observation, extra={**observation.extra, 'guard_reference': arm.last.tolist()})
    result = adapter.tools.execute('move_joints',
        json.dumps({'targets': dict(zip(LABELS, target.tolist())), 'note': 'attended path review only'}), observation)
    if result.error or not captured:
        raise RuntimeError(f'cannot plan review path: {result.error}')
    path = captured[0]
    # Validate the same controller bounds, interpolation and tracking checks.
    old_checker, old_phase = arm.path_clearance_check, arm.motion_phase
    try:
        arm.path_clearance_check = lambda candidate: np.array_equal(candidate, path)
        arm.motion_phase = 'open_space_approach'
        rejection = arm.pre_check(path[1:])
        if rejection:
            raise RuntimeError(rejection)
    finally:
        arm.path_clearance_check, arm.motion_phase = old_checker, old_phase
        arm._approved_path = []
    approval = VerifiedApproach(path, arm.initial)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    for view in ('main', 'left', 'right'):
        Image.fromarray(observation.images[view]).save(directory / f'{view}.jpg')
    manifest = dict(station='YAM3', cap_rad=APPROACH_MOVE_RAD,
        labels=LABELS, initial_full_state=arm.initial.tolist(), path_sha256=approval.digest,
        right_waypoints_including_start=path.tolist(),
        interpolation='pinned move_joints planner; max 0.003 rad/step at 30 Hz',
        approved=False, camera_metadata=observation.extra['cameras'])
    review_file = directory / 'path.json'
    review_file.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'YAM3 exact path: {review_file.resolve()}\n'
          f'Start: {path[0].tolist()}\nTarget: {target.tolist()}\n'
          f'{len(path)-1} waypoints; max joint change {np.max(delta[:6]):.6f} rad; SHA256 {approval.digest}\n'
          f'Inspect {directory.resolve()}/main.jpg, left.jpg, right.jpg and live three-camera views.\n'
          'Operator must verify physical right arm can_follower_r, unchanged stationary left arm, '
          'camera identity/liveness, and lab-verified clearance of the entire interpolated swept '
          'links, wrist, fingers and cables (including tracking uncertainty up to 0.08 rad) '
          'and left-arm drift tolerance 0.05 rad, from table, block, paper, fixtures, other arm and people. '
          'The whole path must remain open space; no descent/contact or gripper change. '
          'Confirm stop control is reachable, supervision continuous, and scene unchanged. '
          'Images alone do not prove clearance. Decline if any portion is unverified.\n'
          'Authorization lasts 5 minutes, one forward path only, within this process.')
    answer = input(f'Type VERIFIED {approval.digest} after the lab check, or Enter to decline: ')
    if answer != f'VERIFIED {approval.digest}' or time.monotonic() >= approval.expires:
        raise RuntimeError('attended approach not verified or check expired; review cancelled')
    arm.measured_feedback()
    if np.max(np.abs(checked_vector(arm.robot.get_joint_state(), (14,)) - approval.initial)) > .008:
        raise RuntimeError('station moved during review; repeat attended station check')
    manifest.update(approved=True, operator_attestation_time_ns=time.time_ns())
    review_file.write_text(json.dumps(manifest, indent=2) + '\n')
    arm.verified_approach = approval
    print(arm.motion_allowance())


class UnverifiedScorer:
    name = 'physical_success'

    def __call__(self, record, target):
        return Score(None, 'Operator must inspect post-release frames; model done is not independent success.')



def nominal_station_status(status, current, *, side='right', command_reference=None):
    """Constrain task targets for one arm to the configured nominal model.

    Permit holding a starting pose at most .03 rad outside a nominal limit so
    recovery can move inward. Never extend to the previously unreachable target.
    This does not change daemon limits, motor configuration, or tracking guards.
    """
    import xml.etree.ElementTree as ET
    from gello.robots.yam import YAMRobot
    root = ET.parse(YAMRobot._arm_type().get_xml_path()).getroot()
    nominal = np.array([list(map(float, root.find(f".//joint[@name='joint{i}']").attrib['range'].split()))
                        for i in range(1, 7)])
    joints = {'left': slice(0, 6), 'right': slice(7, 13)}[side]
    pose = checked_vector(current, (14,))[joints]
    outside = np.flatnonzero((pose < nominal[:, 0] - .03) | (pose > nominal[:, 1] + .03))
    if outside.size:
        details = ', '.join(f'J{i + 1}={pose[i]:.3f} rad (nominal [{nominal[i, 0]:.3f}, {nominal[i, 1]:.3f}])'
                            for i in outside)
        raise RuntimeError(f'Current {side} arm is more than 0.03 rad outside nominal model limits: {details}. '
                           'Operator review required.')
    reference = pose if command_reference is None else checked_vector(command_reference, (7,))[:6]
    if np.max(np.abs(reference - pose)) > .008:
        raise RuntimeError('restored command reference differs from live pose by more than 0.008 rad')
    if np.any(reference < nominal[:, 0] - .03) or np.any(reference > nominal[:, 1] + .03):
        raise RuntimeError('restored command reference outside recovery allowance')
    result = copy.deepcopy(status)
    limits = np.asarray(result['joint_limits'], dtype=float)
    limits[joints, 0] = np.maximum(limits[joints, 0], np.minimum(nominal[:, 0], np.minimum(pose, reference)))
    limits[joints, 1] = np.minimum(limits[joints, 1], np.maximum(nominal[:, 1], np.maximum(pose, reference)))
    result['joint_limits'] = limits.tolist()
    return result


def execute(policy, arm, output, *, task_spec=None):
    spec = task_spec or task_configuration()
    scene = Scene(id=spec['scene_id'], instruction=spec['instruction'],
        metadata={'geometry_verified': False, 'calibration_required': False,
                  'grasp_example_required': False, 'episodes': 1, 'task_id': spec['id']})
    task = Task(name=spec['task_name'], scenes=[scene],
                epochs=1, max_steps=getattr(arm, 'max_total_steps', MAX_STEPS) - (getattr(arm, 'resume_state', None) or {}).get('step', 0), scorer=UnverifiedScorer())
    if arm.journal is None:
        arm.journal = EvidenceJournal(output)
    log = eval(task, policy, arm, guard=GuardChain(motion_guard(arm.space), PauseOnIntervention(arm)),
        log_dir=str(output), store_frames=True, store_actions=True,
        store_records=True, fail_on_error=True)[0]
    payload = log.to_dict()
    (output / 'framework-log.json').write_text(json.dumps(payload, indent=2) + '\n')
    for sample in payload['samples']:
        arm.record(dict(kind='policy_termination',
            termination_reasons=sample['termination_reasons'],
            termination_details=sample['termination_details'],
            physical_success='unverified'))
    return log


def configuration():
    import yaml
    profile = yaml.safe_load(PROFILE.read_text())
    configs = {name: Path(path) for name, path in profile['arms'].items()}
    channels = {name: yaml.safe_load(path.read_text())['robot']['channel']
                for name, path in configs.items()}
    if channels != {'left': 'can_follower_l', 'right': 'can_follower_r'}:
        raise RuntimeError('Local station profile channel mapping changed')
    return profile, configs, channels


def experiment_model(resume_source=None):
    """Upgrade fresh trials; preserve the model tied to saved reasoning/history."""
    if resume_source is None:
        return MODEL, REASONING_EFFORT
    manifest = Path(resume_source) / 'attempt.json'
    saved = json.loads(manifest.read_text()) if manifest.exists() else {}
    return saved.get('model', 'gpt-6-astra'), saved.get('reasoning_effort', 'medium')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=['pick-place', 'usb-insertion', 'plug-socket'], default='pick-place',
        help='Task prompt, goal and recording namespace (default: pick-place)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-station', action='store_true')
    mode.add_argument('--run', action='store_true')
    parser.add_argument('--active-perception', action='store_true', help='Enable the left observer camera arm for USB or plug-socket insertion')
    parser.add_argument('--mask-left-view', action=argparse.BooleanOptionalAction, default=None, help='Mask left observation; defaults on for right-only plug-socket. Override with --no-mask-left-view.')
    parser.add_argument('--observer-geometry', type=Path, help='Verified registered bases or reviewed joint exclusion envelope JSON')
    parser.add_argument('--max-total-steps', type=int, default=MAX_STEPS,
        help=f'Cumulative motion-step budget including steps already used before resume (default: {MAX_STEPS})')
    parser.add_argument('--recover-rejected-motion', action='store_true',
        help='Recover a recorded waypoint rejection only when no command was dispatched')
    parser.add_argument('--recover-separation-stop', action='store_true',
        help='With observer --resume, reconcile an evidenced interpolation-certificate stop; request a fresh decision, never replay motion')
    parser.add_argument('--recover-tracking-stop', action='store_true',
        help='Reconcile a recorded decision-boundary tracking stop; never replay the failed target')
    parser.add_argument('--tracking-stop-clearance-reviewed', action='store_true',
        help='Operator visually checked the stopped arm, plug and cable and found no contact or binding')
    parser.add_argument('--recover-interrupted-motion', action='store_true',
        help='With --resume, reconcile a wall-clock interruption using recorded command evidence; never replay motion')
    parser.add_argument('--resume', type=Path, help='Resume an interrupted run directory; defaults to offline assessment')
    parser.add_argument('--model-timeout', type=float, default=MODEL_TIMEOUT_S, help='HTTP timeout in seconds (default: 600)')
    parser.add_argument('--model-attempts', type=int, default=MODEL_ATTEMPTS, help='Total attempts per model request (default: 4)')
    parser.add_argument('--attempt', type=int, default=2,
        help='Explicit episode number (default: 2); existing attempts cannot be reused')
    parser.add_argument('--right-channel-confirmed', choices=['can_follower_r'],
        help='Operator has identified the physical task arm as the configured right follower')
    parser.add_argument('--approach-target', type=float, nargs=7,
        metavar=('J0', 'J1', 'J2', 'J3', 'J4', 'J5', 'GRIPPER'),
        help='Absolute lab-verified right-arm target; attended exact-path check before any trial')
    parser.add_argument('--station-review-dir', type=Path,
        help='New directory for exact path JSON and all three camera images')
    args = parser.parse_args()
    observer_trial = None
    if args.resume:
        try:
            validate_resume_task(args.resume, args.task)
            manifest = args.resume / 'attempt.json'
            recorded = json.loads(manifest.read_text()) if manifest.is_file() else {}
            observer_trial = next((number for number in STAGED_OBSERVER_TRIALS
                                   if recorded.get(f'trial{number}') is True), None)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    elif args.task == 'plug-socket' and args.attempt in STAGED_OBSERVER_TRIALS:
        observer_trial = args.attempt
    staged_observer = observer_trial is not None
    if staged_observer and (args.task != 'plug-socket' or not args.active_perception):
        parser.error(f'plug-socket Trial {observer_trial} requires --active-perception')
    init_joints = LEFT_OBSERVER_INIT_JOINTS if staged_observer and not args.resume else None
    if observer_trial in SAVED_OBSERVER_POSES and not args.resume:
        try:
            saved_pose = json.loads(SAVED_OBSERVER_POSES[observer_trial].read_text())
            if (saved_pose['trial'] != observer_trial or saved_pose['channel'] != 'can_follower_l'
                    or saved_pose['joint_names'] != [f'left_j{i}' for i in range(6)]):
                raise ValueError(f'pose must identify Trial {observer_trial} and left_j0..left_j5 on can_follower_l')
            init_joints = checked_vector(saved_pose['joint_positions_rad'], (6,)).tolist()
        except (OSError, KeyError, ValueError, TypeError) as exc:
            parser.error(f'invalid saved observer pose for Trial {observer_trial}: {exc}')
    observer_metadata = {f'trial{observer_trial}': True, 'left_observer_init_joints': init_joints} if staged_observer else {}
    if args.active_perception and args.task not in ('usb-insertion', 'plug-socket'):
        parser.error('active perception is supported for usb-insertion and plug-socket')
    mask_left = (args.task == 'plug-socket' and not args.active_perception) if args.mask_left_view is None else args.mask_left_view
    if args.active_perception and mask_left:
        parser.error('active observer mode requires the unmasked left view')
    if args.task == 'plug-socket' and args.active_perception and args.observer_geometry is None:
        name = ('ps-observer-geometry-trial10.json' if observer_trial == 10 else
                'ps-observer-geometry-trial7.json' if staged_observer else 'ps-observer-geometry.json')
        args.observer_geometry = HERE / 'experiments/plug-socket' / name
    if args.observer_geometry and not args.active_perception:
        parser.error('--observer-geometry requires --active-perception')
    if args.active_perception and (args.approach_target is not None or args.recover_interrupted_motion or args.recover_tracking_stop or args.recover_rejected_motion):
        parser.error('active perception does not reuse right-only attended paths or motion-recovery records; model-boundary resume is supported')
    if args.recover_separation_stop and (not args.resume or not args.active_perception
            or args.recover_interrupted_motion or args.recover_tracking_stop or args.recover_rejected_motion):
        parser.error('--recover-separation-stop requires --resume --active-perception and no other recovery mode')
    try:
        separation = load_guard(args.observer_geometry,
            minimum_allowed=observer_minimum_separation_m(observer_trial)) if args.active_perception else None
        if staged_observer and not separation.ready:
            raise ValueError(f'Trial {observer_trial} requires registered observer geometry')
        if args.task == 'plug-socket' and separation is not None and separation.ready:
            validate_plug_observer_guard(separation, observer_trial=observer_trial)
    except (ValueError, OSError) as exc:
        parser.error(f'invalid observer geometry: {exc}')
    if args.recover_rejected_motion and (not args.resume or args.recover_tracking_stop or args.recover_interrupted_motion):
        parser.error('--recover-rejected-motion requires --resume and no other recovery mode')
    if args.recover_tracking_stop and (not args.resume or args.recover_interrupted_motion):
        parser.error('--recover-tracking-stop requires --resume and cannot combine with --recover-interrupted-motion')
    if args.run and args.recover_tracking_stop and not args.tracking_stop_clearance_reviewed:
        parser.error('tracking-stop recovery requires --tracking-stop-clearance-reviewed after physical inspection')
    if args.recover_interrupted_motion and not args.resume:
        parser.error('--recover-interrupted-motion requires --resume')
    if args.resume and args.approach_target is not None:
        parser.error('resume cannot inherit or add an attended approach authorization')
    if args.approach_target is not None and (not (args.check_station or args.run) or args.station_review_dir is None):
        parser.error('--approach-target needs --check-station or --run and a new --station-review-dir')
    if args.max_total_steps < 1:
        parser.error('--max-total-steps must be positive')
    if args.attempt < 1:
        parser.error('--attempt must be a positive integer')
    if not np.isfinite(args.model_timeout) or args.model_timeout <= 0 or args.model_attempts < 1:
        parser.error('model timeout and attempts must be positive')
    spec = task_configuration(args.task, active_perception=args.active_perception, mask_left_view=mask_left,
                              observer_trial=observer_trial)
    prompt_path = args.resume / 'prompt.txt' if staged_observer and args.resume else spec['prompt']
    try:
        resume_state = load_resume(args.resume, recover_interrupted_motion=args.recover_interrupted_motion,
            max_total_steps=args.max_total_steps, recover_tracking_stop=args.recover_tracking_stop,
            recover_rejected_motion=args.recover_rejected_motion,
            recover_separation_stop=args.recover_separation_stop) if args.resume else None
    except (ValueError, KeyError, IndexError, OSError) as exc:
        parser.error(f'cannot recover saved run: {exc}')
    output = spec['output_root'] / f"{spec['output_prefix']}{args.attempt:03d}"
    if args.resume:
        output = args.resume.resolve() / ('resume-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    if resume_state and (resume_state.get('control_mode', 'right_only') == 'active_perception') != args.active_perception:
        parser.error('resume control mode mismatch; right-only and observer histories cannot be mixed')
    if resume_state and args.task == 'plug-socket' and resume_state.get('mask_left_view') != mask_left:
        parser.error('resume camera mask condition mismatch')
    if resume_state and args.active_perception:
        try:
            validate_observer_guard(resume_state, separation.describe())
        except ValueError as exc:
            parser.error(str(exc))
    profile, configs, channels = configuration()
    model, reasoning_effort = experiment_model(args.resume)
    print(json.dumps(dict(mode='offline_preflight', model=model, reasoning_effort=reasoning_effort, url=URL,
        episodes=1, max_llm_calls=MAX_CALLS, max_retries=args.model_attempts,
        model_timeout_s=args.model_timeout, resume_source=str(args.resume) if args.resume else None,
        recovered_calls=resume_state['calls_used'] if resume_state else 0, max_seconds=MAX_SECONDS,
        motion_recovery=resume_state.get('motion_recovery') if resume_state else None,
        active_perception=args.active_perception, mask_left_view=mask_left, control_dimensions=13 if args.active_perception else 7,
        **observer_metadata,
        separation_guard=separation.describe() if separation else None,
        effective_cap_rad=MAX_MOVE_RAD, attended_verified_path_cap_rad=APPROACH_MOVE_RAD,
        max_gripper_delta=MAX_GRIPPER_DELTA,
        task_id=spec['id'], instruction=spec['instruction'],
        prompt_path=str(prompt_path), prompt_sha256=hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
        max_steps=args.max_total_steps, remaining_steps=args.max_total_steps - (resume_state or {}).get('step', 0), output=str(output),
        calibration_required=False, grasp_example_required=False,
        channels=channels, attempt_already_consumed=output.exists(),
        credential_present=bool(os.environ.get('RHOS_API_KEY'))), indent=2))
    if not args.check_station and not args.run:
        return
    if args.run and args.active_perception and not separation.ready:
        parser.error('observer motion excluded until verified base geometry or a reviewed joint envelope is configured')
    if args.run and output.exists():
        parser.error(f'attempt already consumed: {output}. Preserve its logs; '
                     'use --attempt with a new episode number to start a separate episode.')
    if socket.gethostname().lower().split('.')[0] != 'ps':
        parser.error('station operations must run on ps')
    if args.run and (args.right_channel_confirmed is None or not os.environ.get('RHOS_API_KEY')):
        parser.error('--run needs RHOS_API_KEY and --right-channel-confirmed can_follower_r')
    from gello.safety.rpc import SafetyClient
    from gello.safety.daemon_client import validate_daemon_status
    from gello.safety.identity import daemon_identity, validate_daemon_identity
    from gello.safety.motion_session import SupervisedMotionSession
    from gello.zmq_core.robot_node import ZMQClientRobot
    from contextlib import ExitStack
    with ExitStack() as stack:
        # Kernel lock is released on process exit, including unexpected death.
        # Serialize all hardware runners without modifying the preserved source.
        import fcntl
        lock = stack.enter_context(open('/tmp/yam3-astra-run.lock', 'a'))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        safety = SafetyClient(port=profile['network']['safety_port'])
        stack.callback(safety.close)
        robot = ZMQClientRobot(port=profile['network']['hardware_port'])
        stack.callback(robot.close)
        status = safety.status()
        validate_daemon_identity(status, daemon_identity(GELLO, configs))
        validate_daemon_status(status, channels, profile['network']['hardware_port'])
        if status.get('state') != 'safe_hold' or not status.get('hold_confirmed'):
            raise RuntimeError('existing daemon must be in confirmed SAFE HOLD')
        preview = observer_module().PreviewClient()
        preview.bootstrap(1.)
        current = checked_vector(robot.get_joint_state(), (14,))
        task_status = nominal_station_status(status, current,
            command_reference=resume_state['target'][-7:] if resume_state else None)
        if args.active_perception:
            # Apply the same nominal-model constraints to the left arm as well.
            task_status = nominal_station_status(task_status, current, side='left',
                command_reference=np.r_[resume_state['target'][:6],current[6]] if resume_state else None)
        arm = StationArm(robot, preview, task_status, task_id=args.task,
            active_perception=args.active_perception, separation_guard=separation, mask_left_view=mask_left,
            observer_trial=observer_trial)
        arm.reset(Scene(id='preflight', instruction='Observe only'))
        if init_joints is not None:
            points = arm.observer_init_waypoints(init_joints)
            count = len(points) if not np.array_equal(points[-1], arm.last) else 0
            print(json.dumps({f'trial{observer_trial}_observer_init_waypoints': count,
                'left_gripper': arm.initial[6], 'requested_left_joints_rad': init_joints}))
        if resume_state:
            comparison = validate_pose(resume_state, robot.get_joint_state())
            print(json.dumps(dict(resume_pose_check=comparison)))
            arm.resume_state = resume_state
        if args.approach_target is not None:
            attended_approach_check(arm, args.approach_target, args.station_review_dir)
        print(arm.motion_allowance())
        print('Station observations and daemon checks passed. No calibration prerequisite.')
        if not args.run:
            return
        try:
            output.mkdir(parents=True, exist_ok=False)  # Exclusive segment latch; original evidence is preserved.
        except FileExistsError:
            parser.error(f'attempt already consumed: {output}; no episode started')
        deadline = time.monotonic() + MAX_SECONDS
        (output / 'attempt.json').write_text(json.dumps(dict(
            started_time_ns=time.time_ns(), channels=channels,
            model=model, reasoning_effort=reasoning_effort,
            **observer_metadata,
            active_perception=args.active_perception, mask_left_view=mask_left, separation_guard=separation.describe() if separation else None,
            task_id=spec['id'], instruction=spec['instruction'], replay_title=spec['title'],
            prompt_sha256=hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
            runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            max_llm_calls=MAX_CALLS, max_seconds=MAX_SECONDS, max_steps=args.max_total_steps,
            model_timeout_s=args.model_timeout, model_attempts=args.model_attempts,
            resume_source=resume_state['source'] if resume_state else None,
            motion_recovery=resume_state.get('motion_recovery') if resume_state else None,
            calibration_required=False, grasp_example_required=False), indent=2))
        (output / 'prompt.txt').write_bytes(prompt_path.read_bytes())
        transport = GatewayTransport(deadline=deadline)
        if resume_state:
            transport.calls = resume_state['http_attempts']
        stack.callback(transport.close)
        arm.max_total_steps = args.max_total_steps
        arm.deadline = deadline
        transport.check = arm.check_model_wait
        policy = make_policy(transport, arm.pre_check, station=arm, resume_state=resume_state,
                             timeout_s=args.model_timeout, model_attempts=args.model_attempts, prompt_path=prompt_path,
                             model=model, reasoning_effort=reasoning_effort)
        install_checkpoint(policy, arm, output, transport)
        if resume_state:
            atomic_json(output / 'resume-source.json', dict(source=resume_state['source'],
                calls_used=resume_state['calls_used'], step=resume_state['step'],
                motion_recovery=resume_state.get('motion_recovery')))
        completed = False
        termination_reasons, termination_details = [], []
        session = None
        arm.journal = EvidenceJournal(output)
        transport.timing_sink = arm.record
        # Use a separate HTTP preview client; no shared opener across threads.
        recording_preview = observer_module().PreviewClient()
        recording_preview.bootstrap(1.)
        arm.recorder = PreviewRecorder(recording_preview, arm.journal)
        try:
            arm.recorder.start()
            with SupervisedMotionSession(safety, uuid.uuid4().hex,
                    operation='astra-yam3-one-episode') as session:
                arm.session = session
                arm.observer_init_joints = init_joints
                log = execute(policy, arm, output, task_spec=spec)
                if log.status != 'success':
                    raise RuntimeError('evaluation failed; preserved checkpoint may be resumed: ' +
                                       str([sample.get('error') for sample in log.to_dict().get('samples', []) if sample.get('error')]
                                           or log.to_dict().get('error')))
                for sample in log.to_dict()['samples']:
                    termination_reasons.extend(sample['termination_reasons'])
                    termination_details.extend(sample['termination_details'])
            completed = True
        finally:
            # The motion context exits (and requests hold) before replay work.
            try:
                arm.recorder.close()
            except Exception as exc:
                print(f'Recorder shutdown failed: {type(exc).__name__}', file=sys.stderr)
            (output / 'attempt-result.json').write_text(json.dumps(dict(
                http_attempts=transport.calls, physical_success='unverified',
                evaluation_completed=completed,
                model_termination_reasons=termination_reasons,
                model_termination_details=termination_details,
                final_hold_confirmed=bool(session and session.held),
                ended_time_ns=time.time_ns()), indent=2))
            try:
                print(f'Replay saved: {export_replay(output, title=spec["title"])}', flush=True)
            except Exception as exc:
                # Export failures must not hide the episode error or its hold result.
                print(f'Replay export failed ({type(exc).__name__}); rebuild offline with '
                      f'hardware_replay.py --run-dir {output}', file=sys.stderr)


if __name__ == '__main__':
    main()

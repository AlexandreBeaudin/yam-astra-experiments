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
from experiment_resume import atomic_json, install_checkpoint, load_resume, validate_pose


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
LABELS = tuple(f'right_j{i}' for i in range(6)) + ('right_gripper',)
MODEL_TIMEOUT_S = 600.
MODEL_ATTEMPTS = 4  # Total attempts, including the first request.
MAX_CALLS = 180
MAX_SECONDS = 2400
MAX_STEPS = 5400
MAX_MOVE_RAD = 0.45  # Per-call ceiling; use fine steps near contact. Interpolation unchanged.
MAX_GRIPPER_DELTA = 1.0
APPROACH_MOVE_RAD = 0.45  # Optional attended path review uses the same ceiling.
MOTION_PHASES = ('open_space_approach', 'alignment', 'descent', 'grasp', 'placement', 'fine')
OBSERVATION_GUIDANCE = (
    'Use main for height above the table and right for XY alignment; left is also '
    'delivered. main_comparison shows previous/current main crops. '
    'Use fresh observations after each move. Stop on unexpected contact or guard trips.'
)
MOVE_DESCRIPTION = (
    ' Per-call ceiling: 0.45 rad per arm joint and 1.0 normalized gripper. '
    'In visibly clear workspace, combine active axes in 0.25–0.45 rad transit moves. '
    'Use 0.03–0.06 rad for terminal alignment near the block/table. '
    'Absolute named targets; omitted joints retain their current target. '
    + OBSERVATION_GUIDANCE
)



def station_motion_tools(library, arm):
    """Wrap the pinned planner without changing interpolation or joint selection."""
    tools = []
    for tool in library:
        if tool.name == 'give_up':
            schema = copy.deepcopy(tool.schema)
            schema['function']['description'] += ' ' + OBSERVATION_GUIDANCE
            schema['function']['parameters']['properties']['reason']['description'] = (
                'State the exact blocking uncertainty or guard and blocked next action; '
                'explain why delivered views, feedback, previous moves and safe alternatives '
                'cannot support progress. Missing side imagery or pose-tool prerequisites alone '
                'are not blockers while another supported action is available.')
            tools.append(replace(tool, schema=schema))
            continue
        if tool.name != 'move_joints':
            tools.append(tool)
            continue
        schema = copy.deepcopy(tool.schema)
        schema['function']['description'] += MOVE_DESCRIPTION + ' Current station allowance: ' + arm.motion_allowance()
        schema['function']['parameters']['properties']['phase'] = {
            'type': 'string', 'enum': list(MOTION_PHASES),
            'description': 'Optional stage label; all phases share the per-call ceiling.'}

        def move(args, observation, original=tool.run):
            phase = args.get('phase', 'fine')
            if phase not in MOTION_PHASES:
                return ToolResult(error='invalid motion phase')
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
                'Experiment resumed after interruption at an unanswered model request. '
                'Prior actions are already executed. Do not replay them. The next '
                'observation is fresh; reassess the current scene before choosing a new action.'))
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
            available_goals = {tool.name for tool in goal_tools(self.station)}
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
                timeout_s=MODEL_TIMEOUT_S, model_attempts=MODEL_ATTEMPTS):
    policy = StationPolicy(station=station, resume_state=resume_state, model='gpt-6-astra', base_url='https://api.rhos.cc/v1',
        api_key_env='RHOS_API_KEY', wire='responses', effort='medium',
        max_llm_calls=MAX_CALLS, max_retries=model_attempts, timeout_s=timeout_s,
        images='always', image_horizon=3, depth='off', max_speed_frac=0.1,
        eval_notes=PROMPT.read_text(), transport=transport, pre_check=pre_check,
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


def camera_freshness(acquisition, frame_id, received):
    if acquisition is not None:
        age = (received - acquisition) / 1e9
        return dict(exposure_age_s=age, status='recent' if 0 <= age <= 1. else 'stale_or_clock_inconsistent')
    return dict(status='unknown', source_frame_id=frame_id,
                reason='frame ID identifies a source frame but does not establish exposure age')


class StationArm:
    """Expose only seven right-arm dimensions; left setpoint stays fixed.

    Injected RPC/preview objects make the actual adapter testable without sockets.
    reset is observational: it never homes, calibrates or parks either arm.
    """
    def __init__(self, robot, preview, status, *, session=None, deadline=None, simulated=False, path_clearance_check=None, pose_planner=None, pose_inputs_available=False):
        self.robot, self.preview, self.session = robot, preview, session
        self.deadline = deadline
        # Trusted integration only, never model text/meta. Must verify the
        # continuous swept arm/fingers against table, objects and stationary
        # left arm, with tracking uncertainty, for this exact path.
        # Optional attended review records and tracks an exact approach path.
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
        low, high = limits[7:, 0].copy(), limits[7:, 1].copy()
        low[6], high[6] = 0., 1.
        if np.any(low >= high):
            raise ValueError('invalid daemon limits')
        self.space = Box((7,), low=low, high=high, semantics=ActionSemantics(
            control_mode='joint_pos', gripper='continuous', dim_labels=LABELS,
            max_step=(0.003,) * 6 + (0.02,)))
        self.info = EmbodimentInfo(name='yam3-existing-daemon-right',
            action_space=self.space, control_hz=30., is_simulated=simulated,
            capabilities=frozenset({'self_paced'}),
            observation_space=ObservationSpace(
                cameras=(CameraSpec('main', 480, 640), CameraSpec('left', 480, 640), CameraSpec('right', 480, 640),
                         CameraSpec('main_comparison', 366, 1280)),
                state=StateSpec((StateField('joint_pos', (7,), 'rad'),))),
            docs='Right joints J0-J5 in radians; right_gripper normalized 1=open, 0=closed. '
                'Left has no exposed action dimensions. No camera calibration. '
                'Preview acquisition times/IDs unknown. Only supervised visual exploration. '
                'Pose frame is right_base; nominal grasp_site is not a calibrated physical TCP. '
                'Never invent a block/paper pose from pixels.',
            environment_id='YAM3', morphology='yam-pro-existing-safety-daemon')
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

    def reset(self, scene, *, seed=None):
        self.check()
        self.scene, self.t = scene, 0
        self.initial = checked_vector(self.robot.get_joint_state(), (14,))
        self.last = self.initial[7:].copy()
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
        self.previous_decision = None
        self._approved_path = []
        return self.observe()

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
                residual = float(np.max(np.abs(np.asarray(sample['position'])[:6] - self.last[:6])))
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
        residual = float(np.max(np.abs(measured[:6] - self.last[:6])))
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
                f'{label}={value:+.5f}' for label, value in zip(LABELS, delta)))
            lines.append('JPEG content changed since previous decision: ' + ', '.join(
                f'{view}={fresh.extra["cameras"][view]["sha256"] != previous["hashes"][view]}'
                for view in ('main', 'right')) + '. Content change alone is not exposure freshness or useful motion.')
        # Preserve rollout metadata, but replace stale sensor fields with refresh.
        extra = {**rollout_observation.extra, **fresh.extra,
                 'motion_report': {'lines': lines}, 'decision_observation': True}
        images = {**fresh.images, 'main_comparison': comparison}
        if self.journal is not None:
            output = io.BytesIO()
            Image.fromarray(comparison).save(output, format='JPEG')
            self.journal.frame('main_comparison', output.getvalue(), time.time_ns(),
                               time.monotonic_ns(), source='derived_decision_comparison')
        self.record(dict(kind='decision_observation', step=self.t, **extra,
                         measured_joints=measured.tolist(), target=self.last.tolist()))
        self.previous_decision = dict(main=fresh.images['main'].copy(), measured=measured.copy(),
            target=self.last.copy(), camera_meta=fresh.extra['cameras'], hashes={v: fresh.extra['cameras'][v]['sha256'] for v in ('main', 'left', 'right')})
        return replace(fresh, images=images, extra=extra)

    def action_guidance(self):
        if pose_tool_available(self):
            return ('Use move_gripper_to_pose for grounded goal-level travel; local IK and timing '
                    'generate waypoints, subject to trusted whole-path clearance checks. '
                    'Use set_gripper for closure/release. ' + OBSERVATION_GUIDANCE)
        return ('Control mode: move_joints, absolute named right-arm targets in radians; '
                'right_gripper 1=open, 0=closed; full open or close in one call '
                f'(maximum delta {MAX_GRIPPER_DELTA:.1f}). ' + OBSERVATION_GUIDANCE)

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
        if self.last is None or points.ndim != 2 or points.shape[0] == 0 or points.shape[1] != 7 or not np.all(np.isfinite(points)):
            return 'invalid waypoints or no measured start'
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
                         requested_rad=float(np.max(delta[:, :6]))))
        if not planned and (np.any(delta[:, :6] > cap + 1e-9) or np.any(delta[:, 6] > MAX_GRIPPER_DELTA + 1e-9)):
            return (f'split the motion: maximum {cap} rad/joint and '
                    f'{MAX_GRIPPER_DELTA:.2f} gripper per move; clearance {clearance}. '
                    'Choose a target within these per-call limits.')

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
        measured = checked_vector(sample['position'], (7,))
        if np.max(np.abs(measured[:6] - self.last[:6])) > .08:
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
        sample = obsmod.measured_right(diagnostics)
        age = (time.time_ns() - sample['source_time_ns']) / 1e9
        if not 0 <= age <= .25:
            raise RuntimeError('right measured feedback is stale or clock is inconsistent')
        joints = checked_vector(self.robot.get_joint_state(), (14,))
        if np.max(np.abs(joints[:7] - self.initial[:7])) > .05:
            raise RuntimeError('left arm/gripper drifted over its 0.05 tolerance; stop')
        return sample

    def check_model_wait(self):
        sample = self.measured_feedback()
        if np.max(np.abs(np.asarray(sample['position'])[:6] - self.last[:6])) > .08:
            raise RuntimeError('arm tracking residual exceeds 0.08 rad during model wait')

    def observe(self):
        sample = self.measured_feedback()
        images, camera_meta = {}, {}
        for view in ('main', 'left', 'right'):
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
        measured = checked_vector(sample['position'], (7,))
        report = {'lines': [
            OBSERVATION_GUIDANCE,
            self.motion_allowance(),
            'Camera source metadata: ' + json.dumps(camera_meta),
            'Camera images are preview receive-time-only; exposure freshness is unverified.',
            'Measured motion: encoder readback and residual only; not visual displacement or clearance.',
            'Visible displacement: requires comparison of main/left/right views; unknown until reviewed.',
            'Camera freshness: unknown for receive-time-only previews; JPEG change does not prove freshness.',
            'Guard intervention: any modification pauses motion for review, independently of station acceptance.',
            'Pre-grasp: establish approach direction, relative depth, finger alignment and swept clearance from supported observations before descent or closure. Grasp examples, calibration and routine supervisor review are optional.',
            'For plausible settling/frame delivery issues, use observation_recheck (at most two per motion). For geometric ambiguity, consider a different informative bounded probe with supported clearance. Missing metadata or one inconclusive recheck alone does not require stopping.',
            f'Measured arm residual maximum: {float(np.max(np.abs(measured[:6] - self.last[:6]))):.5f} rad.',
        ]}
        observation = Observation(images=images, state={'joint_pos': measured},
            instruction=self.scene.instruction, extra=dict(
                observation_id=f'yam3-{self.t}', cameras=camera_meta,
                measured_source_time_ns=sample['source_time_ns'], motion_report=report,
                geometry_verified=False, physical_success='unverified'))
        self.record(dict(kind='observation', step=self.t,
            **observation.extra, measured_joints=measured.tolist()))
        return observation

    def step(self, action):
        self.check()
        self.measured_feedback()  # Recheck source freshness before every dispatch.
        target = checked_vector(action.data, (7,))
        if np.any(target < self.space.low) or np.any(target > self.space.high):
            raise RuntimeError('target outside daemon limits')
        if np.any(np.abs(target - self.last) > np.array([.003] * 6 + [.02]) + 1e-8):
            raise RuntimeError('waypoint exceeds declared per-step change')
        if (self._approved_path and self._path_attended and self.verified_approach is not None
                and self.verified_approach.used and time.monotonic() >= self.verified_approach.expires):
            raise SafetyAbort('attended approach authorization expired; stop for review')
        if self._approved_path and not np.array_equal(target, self._approved_path[0]):
            self._approved_path = []
            raise SafetyAbort('dispatch differs from clearance-checked path; pause for review')
        full = np.concatenate((self.initial[:7], target))
        start = time.monotonic()
        command_id = uuid.uuid4().hex
        self.record(dict(kind='command_dispatch', command_id=command_id,
            step=self.t + 1, target=full.tolist(), measured_completion=None))
        self.check()  # Recheck deadline/session after synchronous disk writes.
        # A dispatch without acceptance remains uncertain after an interrupted RPC.
        acceptance = self.robot.command_joint_state(full)
        if not isinstance(acceptance, dict) or 'applied_joint_state' not in acceptance:
            raise RuntimeError('missing command acceptance')
        applied = checked_vector(acceptance['applied_joint_state'], (14,))
        self.record(dict(kind='command_acceptance', command_id=command_id, step=self.t + 1,
            target=full.tolist(), applied=applied.tolist(), accepted=True,
            limited=bool(acceptance.get('limited')),
            accepted_time_ns=acceptance.get('accepted_time_ns'), measured_completion=None))
        if acceptance.get('limited') or not np.allclose(applied, full, atol=1e-8, rtol=0):
            raise RuntimeError('daemon modified command; stop rather than continue stale trajectory')
        if self._approved_path:
            self._approved_path.pop(0)
        self.last = target.copy()
        self.rechecks = 0
        self.t += 1
        time.sleep(max(0., 1 / 30 - (time.monotonic() - start)))
        self.record(dict(kind='motion_time', command_id=command_id, step=self.t,
                         duration_s=time.monotonic() - start))
        return StepResult(observation=self.observe(), info={'accepted': True,
            'measured_completion': None, 'physical_success': 'unverified'})

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
        raise ValueError('review target must change an arm joint by >0 and <=0.45 rad; keep gripper fixed')
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


def execute(policy, arm, output):
    scene = Scene(id='yam3-astra-one-episode', instruction=
        'Use the right arm to place the pink block fully inside the white paper marked X.',
        metadata={'geometry_verified': False, 'calibration_required': False,
                  'grasp_example_required': False, 'episodes': 1})
    task = Task(name='yam3-astra-gradual-spatial-exploration', scenes=[scene],
                epochs=1, max_steps=MAX_STEPS - (getattr(arm, 'resume_state', None) or {}).get('step', 0), scorer=UnverifiedScorer())
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-station', action='store_true')
    mode.add_argument('--run', action='store_true')
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
    if args.resume and args.approach_target is not None:
        parser.error('resume cannot inherit or add an attended approach authorization')
    if args.approach_target is not None and (not (args.check_station or args.run) or args.station_review_dir is None):
        parser.error('--approach-target needs --check-station or --run and a new --station-review-dir')
    if args.attempt < 1:
        parser.error('--attempt must be a positive integer')
    if not np.isfinite(args.model_timeout) or args.model_timeout <= 0 or args.model_attempts < 1:
        parser.error('model timeout and attempts must be positive')
    resume_state = load_resume(args.resume) if args.resume else None
    output = HERE / f'astra-yam3-run-{args.attempt:03d}'
    if args.resume:
        output = args.resume.resolve() / ('resume-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    profile, configs, channels = configuration()
    print(json.dumps(dict(mode='offline_preflight', model='gpt-6-astra', url=URL,
        episodes=1, max_llm_calls=MAX_CALLS, max_retries=args.model_attempts,
        model_timeout_s=args.model_timeout, resume_source=str(args.resume) if args.resume else None,
        recovered_calls=resume_state['calls_used'] if resume_state else 0, max_seconds=MAX_SECONDS,
        effective_cap_rad=MAX_MOVE_RAD, attended_verified_path_cap_rad=APPROACH_MOVE_RAD,
        max_gripper_delta=MAX_GRIPPER_DELTA,
        prompt_path=str(PROMPT), prompt_sha256=hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
        max_steps=MAX_STEPS, output=str(output),
        calibration_required=False, grasp_example_required=False,
        channels=channels, attempt_already_consumed=output.exists(),
        credential_present=bool(os.environ.get('RHOS_API_KEY'))), indent=2))
    if not args.check_station and not args.run:
        return
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
        arm = StationArm(robot, preview, status)
        arm.reset(Scene(id='preflight', instruction='Observe only'))
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
            output.mkdir(exist_ok=False)  # Exclusive segment latch; original evidence is preserved.
        except FileExistsError:
            parser.error(f'attempt already consumed: {output}; no episode started')
        deadline = time.monotonic() + MAX_SECONDS
        (output / 'attempt.json').write_text(json.dumps(dict(
            started_time_ns=time.time_ns(), channels=channels,
            prompt_sha256=hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
            runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            max_llm_calls=MAX_CALLS, max_seconds=MAX_SECONDS, max_steps=MAX_STEPS,
            model_timeout_s=args.model_timeout, model_attempts=args.model_attempts,
            resume_source=resume_state['source'] if resume_state else None,
            calibration_required=False, grasp_example_required=False), indent=2))
        (output / 'prompt.txt').write_bytes(PROMPT.read_bytes())
        transport = GatewayTransport(deadline=deadline)
        if resume_state:
            transport.calls = resume_state['http_attempts']
        stack.callback(transport.close)
        arm.deadline = deadline
        transport.check = arm.check_model_wait
        policy = make_policy(transport, arm.pre_check, station=arm, resume_state=resume_state,
                             timeout_s=args.model_timeout, model_attempts=args.model_attempts)
        install_checkpoint(policy, arm, output, transport)
        if resume_state:
            atomic_json(output / 'resume-source.json', dict(source=resume_state['source'],
                calls_used=resume_state['calls_used'], step=resume_state['step']))
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
                log = execute(policy, arm, output)
                if log.status != 'success':
                    raise RuntimeError('evaluation failed; preserved checkpoint may be resumed: ' +
                                       str(log.to_dict().get('error')))
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
                print(f'Replay saved: {export_replay(output)}', flush=True)
            except Exception as exc:
                # Export failures must not hide the episode error or its hold result.
                print(f'Replay export failed ({type(exc).__name__}); rebuild offline with '
                      f'hardware_replay.py --run-dir {output}', file=sys.stderr)


if __name__ == '__main__':
    main()

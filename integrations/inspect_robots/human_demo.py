#!/usr/bin/env python3
"""Human camera-only joint commands using the unchanged Astra station adapter.

Default is offline preflight. --run connects to an existing SAFE HOLD daemon;
only explicit terminal moves dispatch trajectories. No LLM, homing or CAN owner.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import ExitStack
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import select
import socket
import sys
import threading
import time
import uuid

import astra_run as station
import numpy as np
from inspect_robots import Scene
from inspect_robots.policy_adapter import ToolAdapter

OUTPUT_ROOT = station.HERE / 'experiments/plug-socket/human-runs'
HELP = """Commands (radians, absolute unless +=/-=; no Python expressions):
  j4=-1.10 ; square the brick face
  j1+=0.02 j2+=0.02 ; advance while holding height
  g=0.90 ; open the gripper
  {"targets":{"right_j4":-1.1},"note":"square face","phase":"alignment"}
  state                  fresh joints/targets; no command
  mark <observation>     record a milestone, contact, vibration, or protocol break
  done <note>            finish with operator-reported success and confirmed hold
  give_up <reason>       finish without success and request confirmed hold
  quit                   finish without labeling success; confirmed hold
Ctrl+C/EOF exits through the same hold context. No automatic back-out, reset or undo.
"""


def parse_move(line, reference):
    """Resolve shorthand to named absolute targets; never eval operator input."""
    if line.lstrip().startswith('{'):
        args = json.loads(line)
        if not isinstance(args, dict) or set(args) - {'targets', 'note', 'phase'}:
            raise ValueError('JSON requires targets, optional note and phase only')
    else:
        command, _, note = line.partition(';')
        targets = {}
        for token in command.split():
            match = re.fullmatch(r'(right_j[0-5]|j[0-5]|right_gripper|g)(\+=|-=|=)([^=]+)', token)
            if not match:
                raise ValueError('Use j0=0.2, j1+=0.02, or g=0.90; see help')
            name, operator, raw = match.groups()
            name = 'right_gripper' if name == 'g' else 'right_' + name if name.startswith('j') else name
            if name in targets:
                raise ValueError('Duplicate joint in one command')
            number = float(raw)
            index = station.LABELS.index(name)
            targets[name] = float(reference[index]) + (number if operator == '+=' else -number) if operator != '=' else number
        args = dict(targets=targets, note=note.strip() or 'Human command; intent not annotated.')
    targets = args.get('targets')
    if not isinstance(targets, dict) or not targets:
        raise ValueError('targets must be a nonempty object')
    for name, number in targets.items():
        if name not in station.LABELS:
            raise ValueError('Only right_j0..right_j5 and right_gripper are available')
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not np.isfinite(float(number)):
            raise ValueError('Joint values must be finite numbers')
        if name == 'right_gripper' and not 0 <= number <= .90:
            raise ValueError('Right gripper command range is 0.0..0.90')
        if name == 'right_j3' and number < -1.65:
            raise ValueError('Task prompt prohibits right_j3 below -1.65')
        if name == 'right_j4' and abs(number) > 1.35:
            raise ValueError('Task prompt limits right_j4 to [-1.35, 1.35]')
    if not isinstance(args.get('note', ''), str) or not args.get('note', '').strip():
        args['note'] = 'Human command; intent not annotated.'
    if args.get('phase', 'fine') not in station.MOTION_PHASES:
        raise ValueError('Invalid phase')
    return args


class TwoViewRecorder(station.PreviewRecorder):
    """Record only the two baseline views; supply cached frames to a read-only viewer."""
    def __init__(self, preview, journal):
        super().__init__(preview, journal, hz=5)
        self.cache = {}
        self.cache_lock = threading.Lock()

    def capture(self):
        for view in ('main', 'right'):
            raw, wall, mono, *meta = self.preview.frame(view, 1.)
            source = meta[0] if meta else {}
            self.journal.frame(view, raw, wall, mono, source='human_preview_recording',
                acquisition_time_ns=source.get('acquisition_time_ns'), frame_id=source.get('frame_id'))
            with self.cache_lock:
                self.cache[view] = raw, mono


VIEWER = b'''<!doctype html><meta charset="utf-8"><title>Human demo: main + right</title>
<style>body{background:#101923;color:#eee;font:16px sans-serif;margin:20px}section{display:flex;gap:16px}
article{width:50%}img{width:100%;background:#222}small{color:#bbb}</style>
<h2>Human camera-only demonstration</h2><p>MAIN + RIGHT only. Enter moves in the terminal.</p>
<section><article><h3>MAIN</h3><img id="main"><p id="main-age"></p></article>
<article><h3>RIGHT</h3><img id="right"><p id="right-age"></p></article></section>
<small>Preview receive age is shown; sensor exposure timestamps are unavailable. Red means stale/unavailable.</small>
<script>for(const view of ['main','right']){let last;
async function refresh(){const label=document.getElementById(view+'-age');try{
const r=await fetch('frame/'+view,{cache:'no-store',signal:AbortSignal.timeout(1500)});if(!r.ok)throw Error('unavailable');
const age=Number(r.headers.get('X-Receive-Age'));const url=URL.createObjectURL(await r.blob());
document.getElementById(view).src=url;if(last)URL.revokeObjectURL(last);last=url;
label.textContent='Receive age '+age.toFixed(2)+' s';label.style.color=age>1.5?'#ff6868':'#a9deb2';
}catch(e){label.textContent='UNAVAILABLE / STALE';label.style.color='#ff6868'}setTimeout(refresh,200)}refresh()}
</script>'''


class Viewer:
    """Capability URL, loopback only, GET-only cached pixels; cannot move hardware."""
    def __init__(self, recorder, port):
        self.prefix = '/' + secrets.token_urlsafe(24) + '/'
        prefix = self.prefix

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never log the capability URL.

            def do_GET(self):
                if self.path == prefix:
                    body, kind, age = VIEWER, 'text/html; charset=utf-8', None
                elif self.path in (prefix + 'frame/main', prefix + 'frame/right'):
                    view = self.path.rsplit('/', 1)[1]
                    with recorder.cache_lock:
                        value = recorder.cache.get(view)
                    if value is None:
                        self.send_error(503)
                        return
                    body, mono = value
                    kind, age = 'image/jpeg', (time.monotonic_ns() - mono) / 1e9
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                if age is not None:
                    self.send_header('X-Receive-Age', str(age))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        print(f'TWO-VIEW CAMERA PAGE: http://127.0.0.1:{self.server.server_port}{self.prefix}', flush=True)

    def close(self):
        if self.thread.ident is not None:
            self.server.shutdown()
            self.thread.join(timeout=2)
        self.server.server_close()


class HumanController:
    def __init__(self, arm):
        self.arm = arm
        self.adapter = ToolAdapter(pre_check=arm.pre_check)
        self.adapter.bind(arm.info)
        self.move = next(t for t in self.adapter.tools if t.name == 'move_joints')
        self.guard = station.motion_guard(arm.space)
        self.store = {}
        self.guard.review(station.Action(data=arm.last.copy()), self.store)
        self.pause = station.PauseOnIntervention(arm)

    def observe(self):
        self.observation = self.arm.decision_observation(self.arm.observe())
        # Match StationPolicy.act: plan from the last accepted setpoint, while
        # retaining measured readback solely as feedback. Encoder sag must not
        # create a first-waypoint jump or replace unnamed joint targets.
        reference = self.observation.extra.get('guard_reference', self.arm.last)
        if not np.array_equal(reference, self.arm.last):
            raise RuntimeError('guard reference differs from station setpoint; pause for review')
        self.observation = replace(self.observation, extra={**self.observation.extra,
            'env_step': self.arm.t, 'guard_reference': self.arm.last.tolist()})
        measured = self.observation.state['joint_pos']
        print('MEASURED ' + ' '.join(f'{k}={v:.5f}' for k, v in zip(station.LABELS, measured)), flush=True)
        print('TARGET   ' + ' '.join(f'{k}={v:.5f}' for k, v in zip(station.LABELS, self.arm.last)), flush=True)
        print(f'Max arm lag: {self.arm.tracking_error(measured):.5f} rad', flush=True)
        return self.observation

    def move_line(self, line):
        args = parse_move(line, self.arm.last)
        return self.move_args(args, line)

    def move_args(self, args, line):
        # Obtain a fresh paired decision image and enforce the same boundary guard.
        self.observe()
        self.arm.record(dict(kind='human_decision', step=self.arm.t, raw_command=line,
            targets=args['targets'], note=args['note'], phase=args.get('phase', 'fine'),
            camera_only_operator_declared=True, attention=None))
        prior = self.arm.motion_phase
        self.arm.motion_phase = args.get('phase', 'fine')
        try:
            result = self.move.run(args, self.observation)
        finally:
            self.arm.motion_phase = prior
        if result.error:
            self.arm.record(dict(kind='human_command_rejected', step=self.arm.t, error=result.error))
            print('REJECTED: ' + result.error, flush=True)
            return False
        if result.chunk is None:
            raise RuntimeError('Motion tool returned no trajectory')
        if self.arm.t + len(result.chunk.actions) > station.MAX_STEPS:
            raise RuntimeError('Human episode motion-step budget exhausted')
        print(f'Executing {len(result.chunk.actions)} waypoints at the existing 30 Hz.', flush=True)
        for action in result.chunk.actions:
            reviewed = self.guard.review(action, self.store)
            self.pause.review(reviewed, self.store)
            self.arm.step(reviewed)
        self.observe()
        return True

    def idle_feedback(self):
        sample = self.arm.measured_feedback()
        if self.arm.tracking_error(sample['position']) > .08:
            raise RuntimeError('arm tracking residual exceeds 0.08 rad while awaiting human input')
        self.arm.record(dict(kind='human_feedback', step=self.arm.t,
            measured_joints=list(sample['position']), measured_source_time_ns=sample['source_time_ns'],
            target=self.arm.last.tolist(), feedback=station.observer_module().plain(sample)))

    def interact(self):
        print(HELP, flush=True)
        self.observe()
        while True:
            print('human> ', end='', flush=True)
            while not select.select([sys.stdin], [], [], .1)[0]:
                self.idle_feedback()
            line = sys.stdin.readline()
            if not line:
                return 'eof', ''
            line = line.strip()
            if not line:
                continue
            name, _, detail = line.partition(' ')
            if name in ('done', 'give_up', 'quit'):
                return name, detail
            if name == 'help':
                print(HELP)
            elif name in ('state', 'observe'):
                self.observe()
            elif name == 'mark':
                self.arm.record(dict(kind='human_annotation', step=self.arm.t, note=detail))
            else:
                try:
                    args = parse_move(line, self.arm.last)
                except (ValueError, OverflowError) as exc:
                    print('INPUT REJECTED: ' + str(exc), flush=True)
                    continue
                # Hardware/sensor/guard exceptions must leave the session and hold,
                # even when they use the same ValueError type as bad input.
                self.move_args(args, line)


def export_csv(run_dir, *, nominal_fk=False):
    """Export measured samples and accepted targets; FK is optional and offline."""
    run_dir = Path(run_dir)
    planner = None  # FK is never constructed during control.
    if nominal_fk:
        from planned_motion import PosePlanner
        planner = PosePlanner()
    path = run_dir / ('trajectory-nominal-fk.csv' if nominal_fk else 'trajectory.csv')
    fields = ['recorded_time_ns', 'measured_source_time_ns', 'step', 'source']
    fields += [f'measured_{k}' for k in station.LABELS] + [f'accepted_{k}' for k in station.LABELS]
    if nominal_fk:
        fields += ['nominal_right_base_x_m', 'nominal_right_base_y_m', 'nominal_right_base_z_m']
        fields += [f'nominal_R{i}{j}' for i in range(3) for j in range(3)]
    target = None
    with (run_dir / 'station-events.jsonl').open() as stream, path.open('w') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # Complete rows remain useful after an interrupted process.
            if event.get('kind') == 'command_acceptance':
                target = event['applied'][7:14]
            if event.get('kind') not in ('observation', 'human_feedback'):
                continue
            q = event['measured_joints']
            reference = event.get('target', target)
            row = dict(recorded_time_ns=event.get('recorded_time_ns'),
                measured_source_time_ns=event.get('measured_source_time_ns'),
                step=event['step'], source=event['kind'])
            row.update(zip((f'measured_{k}' for k in station.LABELS), q))
            if reference is not None:
                row.update(zip((f'accepted_{k}' for k in station.LABELS), reference))
            if nominal_fk:
                position, rotation = planner.fk(np.asarray(q, dtype=float))
                row.update(zip(fields[-12:], [*position, *rotation.ravel()]))
            writer.writerow(row)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--check-station', action='store_true')
    mode.add_argument('--export-only', type=Path, metavar='HUMAN_RUN_DIR')
    parser.add_argument('--nominal-fk', action='store_true', help='Add model XYZ/rotation to --export-only; offline only')
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--right-channel-confirmed', choices=['can_follower_r'])
    parser.add_argument('--viewer-port', type=int, default=8766)
    args = parser.parse_args(argv)
    if args.nominal_fk and args.export_only is None:
        parser.error('--nominal-fk is available only with --export-only')
    if args.export_only is not None:
        print(export_csv(args.export_only, nominal_fk=args.nominal_fk))
        return
    if args.attempt < 1 or not 1 <= args.viewer_port <= 65535 or args.viewer_port == 8765:
        parser.error('Use a positive attempt and a viewer port other than 8765')
    output = OUTPUT_ROOT / f'demo-{args.attempt:03d}'
    print(json.dumps(dict(mode='human_offline_preflight', output=str(output),
        attempt_already_consumed=output.exists(), control_dimensions=7,
        views=['main', 'right'], left_arm='parked', gripper_max=.90,
        per_call_joint_cap_rad=station.MAX_MOVE_RAD, tracking_stop_rad=.08,
        prompt_only_j3_min=-1.65, prompt_only_j4_range=[-1.35, 1.35],
        api_key_required=False), indent=2))
    if not (args.run or args.check_station):
        return
    if args.run and output.exists():
        parser.error('Human attempt already exists; choose a new --attempt (never overwrite)')
    if args.run and (args.right_channel_confirmed is None or not sys.stdin.isatty()):
        parser.error('--run requires --right-channel-confirmed can_follower_r and an interactive terminal')
    if socket.gethostname().lower().split('.')[0] != 'ps':
        parser.error('Live station operations must run on ps')
    # Hardware imports and sockets occur only beyond explicit station/run flags.
    import fcntl
    from gello.safety.rpc import SafetyClient
    from gello.safety.daemon_client import validate_daemon_status
    from gello.safety.identity import daemon_identity, validate_daemon_identity
    from gello.safety.motion_session import SupervisedMotionSession
    from gello.zmq_core.robot_node import ZMQClientRobot
    profile, configs, channels = station.configuration()
    with ExitStack() as stack:
        lock = stack.enter_context(open('/tmp/yam3-astra-run.lock', 'a'))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        safety = SafetyClient(port=profile['network']['safety_port'])
        stack.callback(safety.close)
        robot = ZMQClientRobot(port=profile['network']['hardware_port'])
        stack.callback(robot.close)
        status = safety.status()
        validate_daemon_identity(status, daemon_identity(station.GELLO, configs))
        validate_daemon_status(status, channels, profile['network']['hardware_port'])
        if status.get('state') != 'safe_hold' or not status.get('hold_confirmed'):
            raise RuntimeError('Existing daemon must be in confirmed SAFE HOLD; do not take over another session')
        preview = station.observer_module().PreviewClient()
        preview.bootstrap(1.)
        arm = station.StationArm(robot, preview, station.nominal_station_status(status, robot.get_joint_state()),
            task_id='plug-socket', mask_left_view=True)
        arm.reset(Scene(id='human-camera-demo', instruction=station.task_configuration('plug-socket')['instruction']))
        arm.check_model_wait()
        if not args.run:
            print('Station observations and daemon checks passed; no session activated or command dispatched.')
            return
        output.mkdir(parents=True, exist_ok=False)
        prompt = station.task_configuration('plug-socket')['prompt']
        (output / 'prompt.txt').write_bytes(prompt.read_bytes())
        station.atomic_json(output / 'attempt.json', dict(task_id='plug-socket', operator='human',
            control_mode='right_only', active_perception=False, mask_left_view=True, channels=channels,
            started_time_ns=time.time_ns(), initial_full_joints=arm.initial.tolist(),
            prompt_sha256=hashlib.sha256(prompt.read_bytes()).hexdigest(),
            runner_sha256=hashlib.sha256(Path(station.__file__).read_bytes()).hexdigest(),
            controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            daemon_code_fingerprint=status.get('code_fingerprint'),
            daemon_config_fingerprint=status.get('config_fingerprint'),
            camera_only_operator_declared=True, attention_annotation_required=False,
            comparison_limit='Human sees continuous preview; Astra sees decision images and has API latency.'))
        arm.journal = station.EvidenceJournal(output)
        recording_preview = station.observer_module().PreviewClient()
        recording_preview.bootstrap(1.)
        recorder = arm.recorder = TwoViewRecorder(recording_preview, arm.journal)
        viewer = Viewer(recorder, args.viewer_port)
        session = None
        outcome, note, error = 'interrupted', '', None
        arm.deadline = time.monotonic() + station.MAX_SECONDS
        try:
            recorder.start()
            viewer.start()
            with SupervisedMotionSession(safety, uuid.uuid4().hex, operation='human-camera-demo') as session:
                arm.session = session
                # No reset/homing motion; establish the guarded reference at the held pose.
                controller = HumanController(arm)
                print(f'RECORDING: {output}', flush=True)
                outcome, note = controller.interact()
                arm.record(dict(kind='human_termination', outcome=outcome, note=note,
                    physical_success='operator_reported' if outcome == 'done' else 'unverified'))
        except BaseException as exc:
            error = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            # Motion context requests confirmed hold before recorder/export shutdown.
            try:
                viewer.close()
            finally:
                try:
                    recorder.close()
                finally:
                    station.atomic_json(output / 'attempt-result.json', dict(operator='human',
                        outcome=outcome, note=note, error=error, ended_time_ns=time.time_ns(),
                        final_hold_confirmed=bool(session and session.held),
                        physical_success='operator_reported' if outcome == 'done' else 'unverified'))
            try:
                print('CSV saved:', export_csv(output), flush=True)
                print('Replay saved:', station.export_replay(output, title='Human plug-socket camera-only demonstration', harness_label='Human terminal control'), flush=True)
            except Exception as exc:
                print(f'Offline export failed ({type(exc).__name__}); raw journal/frames remain in {output}', file=sys.stderr)


if __name__ == '__main__':
    main()

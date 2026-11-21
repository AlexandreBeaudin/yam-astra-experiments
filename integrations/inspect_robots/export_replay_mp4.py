#!/usr/bin/env python3
"""Offline deterministic browser replay export. No robot or API connections.

Install playwright and its Chromium browser once. FFmpeg must be on PATH.
The shared hardware_replay exporter also upgrades runner-generated HTML.
"""
from __future__ import annotations
import argparse
import base64
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from hardware_replay import export_replay


def render_video(html, out, *, speed=4., fps=30, width=None, height=None, browser_path=None, layout='auto'):
    from playwright.sync_api import sync_playwright
    if not math.isfinite(speed) or speed <= 0 or fps <= 0:
        raise ValueError('speed and fps must be positive')
    if layout not in ('auto', 'two', 'three'):
        raise ValueError('layout must be auto, two or three')
    if any(value is not None and (value <= 0 or value % 2) for value in (width, height)):
        raise ValueError('video dimensions must be positive even integers')
    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg is required for MP4 export')
    auto_height = height is None
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    # Atomic final publication; keep an existing valid export on failure.
    with tempfile.TemporaryDirectory(prefix='.replay-render-', dir=out.parent) as tmp, sync_playwright() as pw:
        video = Path(tmp) / 'video.mp4'
        browser = pw.chromium.launch(headless=True, executable_path=browser_path)
        try:
            page = browser.new_page(viewport={'width': width or 1920, 'height': height or 1440}, device_scale_factor=1)
            capture = page.context.new_cdp_session(page)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(Path(html).resolve().as_uri() + '?export=1', wait_until='load')
            if layout != 'auto':
                page.evaluate('value => window.replay.setLayout(value)', layout)
            camera_layout = page.evaluate('window.replay.state().layout')
            width = width or (1920 if camera_layout == 'three' else 1600)
            height = height or (1440 if camera_layout == 'three' else 1080)
            page.set_viewport_size({'width': width, 'height': height})
            page.evaluate('document.fonts.ready')
            fonts = page.evaluate("async () => {await Promise.all([document.fonts.load('400 15px Inter'),document.fonts.load('500 14px \"JetBrains Mono\"')]);return [...document.fonts].every(f=>f.status==='loaded');}")
            if not fonts:
                raise RuntimeError('Bundled publication fonts failed to load')
            if auto_height:
                # A 13-joint/three-view attention payload can be taller than a
                # seven-joint tool card. Fit all cards without clipping text;
                # camera layout and the shared presentation clock stay fixed.
                height = max(height, page.evaluate('window.replay.requiredHeight()'))
                page.set_viewport_size({'width': width, 'height': height})
            duration = page.evaluate('window.replay.duration')
            count = max(1, math.ceil(duration / speed * fps))
            if page.evaluate('document.documentElement.scrollHeight') > height:
                raise ValueError('layout exceeds viewport; increase --height')
            command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'image2pipe',
                       '-vcodec', 'png', '-framerate', str(fps), '-i', '-', '-an',
                       '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                       '-movflags', '+faststart', str(video)]
            started = time.monotonic()
            with tempfile.TemporaryFile() as stderr:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=stderr)
                try:
                    for i in range(count):
                        # Last frame includes the final state; all earlier frames use
                        # the exact presentation clock, independent of render speed.
                        t = duration if i == count-1 else i * speed / fps
                        page.evaluate('([t,s]) => window.replay.setTime(t,s)', [t, speed])
                        if page.evaluate('document.documentElement.scrollHeight') > height:
                            raise ValueError('layout exceeds viewport at this decision; increase --height')
                        if errors:
                            raise RuntimeError('; '.join(errors))
                        shot = capture.send('Page.captureScreenshot', {'format': 'png', 'optimizeForSpeed': True})
                        process.stdin.write(base64.b64decode(shot['data']))
                        if i % 300 == 0:
                            print(f'Rendering {i+1}/{count} frames ({time.monotonic()-started:.1f}s elapsed)', flush=True)
                    process.stdin.close()
                    if process.wait() != 0:
                        stderr.seek(0)
                        raise RuntimeError(stderr.read().decode(errors='replace'))
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
            video.replace(out)
            report = dict(output=str(out), duration_s=count/fps, episode_duration_s=duration,
                          speed=speed, fps=fps, frames=count, width=width, height=height,
                          camera_layout=camera_layout, camera_views=['main', 'left', 'right'] if camera_layout == 'three' else ['main', 'right'],
                          codec='h264', pixel_format='yuv420p', crf=18, browser=browser.version)
            out.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n')
            return report
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--combine-resumes', action='store_true',
        help='Join the single resume lineage into <run-dir>/combined/replay.html and export one video')
    parser.add_argument('--title')
    parser.add_argument('--harness-label', default='Inspect Robots')
    parser.add_argument('--layout', choices=['auto', 'two', 'three'], default='auto',
        help='Camera layout: auto follows the recorded control mode; two=main/right, three=main/left/right')
    parser.add_argument('--speed', type=float, default=4,
        help='Playback multiplier: any positive number, e.g. 1, 2, 4, 8, 16, 32 or 2.5 (default: 4)')
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--width', type=int, help='Default: 1600 for two cameras, 1920 for three')
    parser.add_argument('--height', type=int, help='Default: 1080 for two cameras, 1440 for three')
    parser.add_argument('--browser-path', help='Optional Chromium executable override')
    parser.add_argument('--html-only', action='store_true')
    args = parser.parse_args()
    if not args.run_dir.is_dir():
        parser.error('--run-dir must exist')
    if not math.isfinite(args.speed) or args.speed <= 0 or args.fps <= 0:
        parser.error('--speed and --fps must be positive')
    if any(value is not None and (value <= 0 or value % 2) for value in (args.width, args.height)):
        parser.error('--width and --height must be positive even integers')
    if args.combine_resumes:
        from combine_replays import export_combined_replay
        html = export_combined_replay(args.run_dir, title=args.title, harness_label=args.harness_label, layout=args.layout)
    else:
        html = export_replay(args.run_dir, title=args.title, harness_label=args.harness_label, layout=args.layout)
    print(f'Viewer: {html}', flush=True)
    if not args.html_only:
        # Explicit layout exports coexist; preserve the historical auto filename.
        suffix = '' if args.layout == 'auto' else f'_{args.layout}-camera'
        out = args.out or html.parent / f'replay_{args.speed:g}x{suffix}.mp4'
        print(json.dumps(render_video(html, out, speed=args.speed, fps=args.fps,
              width=args.width, height=args.height, browser_path=args.browser_path, layout=args.layout), indent=2))


if __name__ == '__main__':
    main()

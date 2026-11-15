"""Operator-facing teleop text; protocol values and raw logs stay unchanged."""

import argparse
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar

_LANGUAGE = ContextVar("yam_terminal_language", default="en")


@contextmanager
def terminal_language(language):
    token = _LANGUAGE.set(language)
    try:
        yield
    finally:
        _LANGUAGE.reset(token)


def tr(message):
    return ZH.get(message, message) if _LANGUAGE.get() == "zh" else message


def language():
    return _LANGUAGE.get()


def label(value):
    value = str(value).upper()
    if value == "MODEL" and language() == "zh":
        return "型号不符"
    return tr(value)


def diagnostic(value):
    text = str(value)
    if _LANGUAGE.get() != "zh":
        return text
    if "warning, comm failed: -3001" in text:
        return "主臂串口接收超时（-3001）；请检查电源与连接线"
    if "warning, comm failed: -3002" in text:
        return "主臂串口收到损坏的数据包（-3002）；请检查连接线和 USB 连接"
    match = re.search(
        r"saved action jump ([\d.]+) rad exceeds ([\d.]+) rad after camera misses", text
    )
    if match:
        return f"相机缺帧后，保存动作的间隔 {match[1]} rad 超过 {match[2]} rad，数据连续性检查停止了采集"
    patterns = (
        (
            r"(LEFT|RIGHT|THIRD|FOURTH) enabling motor (\d+)/(\d+)",
            lambda m: f"{label(m[1])}从臂正在使能电机 {m[2]}/{m[3]}",
        ),
        (r"camera warmup timed out(.*)", lambda m: f"相机等待出帧超时{m[1]}"),
        (r"(\w+) camera is not connected: (.*)", lambda m: f"{label(m[1])}相机未连接：{m[2]}"),
        (
            r"(\w+) model mismatch: expected (.*), got (.*)",
            lambda m: f"{label(m[1])}相机型号不符：需要 {m[2]}，实际 {m[3]}",
        ),
        (
            r"(\w+) USB link must be 3.x, got (.*)",
            lambda m: f"{label(m[1])}相机需要 USB 3.x，实际 {m[2]}",
        ),
        (
            r"([\d.]+) GB free \(minimum ([\d.]+) GB\)",
            lambda m: f"剩余空间 {m[1]} GB（至少需要 {m[2]} GB）",
        ),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    replacements = {
        "SAFE HOLD not confirmed": "安全保持未确认",
        "saved; temporary file cleanup pending": "数据已保存；临时文件待清理",
        "safely stop the old daemon and restart (do not kill or take over)": "请按安全停机流程退出旧守护进程后重新启动，不要强制结束或接管",
    "GELLO feedback unavailable on ": "主臂反馈不可用，串口：",
        "GELLO feedback reader failed on ": "主臂反馈读取失败，串口：",
        "GELLO feedback reader stopped on ": "主臂反馈读取线程已停止，串口：",
        "GELLO feedback stale on ": "主臂反馈已过期，串口：",
        "GELLO first feedback timed out on ": "等待主臂首次反馈超时，串口：",
        "data root is not writable": "数据目录不可写",
        "data root is not a directory:": "数据目录不存在：",
        "workspace data path is not a symlink:": "工作区数据路径不是软链接：",
        "broken data symlink:": "数据软链接失效：",
        "hardware initialization timed out": "硬件初始化超时",
        "collector exited before reaching a ready state": "采集程序在准备就绪前退出",
        "parent heartbeat failed:": "主进程心跳失败：",
        "machine profile ": "本机配置 ",
        "cannot read profile ": "无法读取配置 ",
        "Migrate ~/.config/yam or supply --profile.": "请迁移 ~/.config/yam 或使用 --profile 指定配置。",
        "unrecognized arguments:": "无法识别的参数：",
        "invalid choice:": "无效选项：",
        "not allowed with argument ": "不能同时使用参数 ",
        "argument ": "参数 ",
    }
    for source, translated in replacements.items():
        text = text.replace(source, translated)
    return text


class TerminalArgumentParser(argparse.ArgumentParser):
    def format_help(self):
        result = super().format_help()
        return result.replace("usage: ", "用法：", 1) if _LANGUAGE.get() == "zh" else result

    def format_usage(self):
        result = super().format_usage()
        return result.replace("usage: ", "用法：", 1) if _LANGUAGE.get() == "zh" else result

    def error(self, message):
        if _LANGUAGE.get() == "en":
            return super().error(message)
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}：错误：{diagnostic(message)}\n")


ZH = {
    "Diagnostic log: {0}": "关节诊断日志：{0}",
    "Diagnostic logging incomplete: {0}; dropped={1}": "诊断日志不完整：{0}；丢弃条数={1}",
    "USB WARNING": "USB 带宽提示",
    "Warning: {0} camera uses USB {1}; bandwidth is limited. Continuing stream checks and startup; stable FPS is not guaranteed.": "提示：{0}相机使用 USB {1}，带宽受限；继续出流检查与启动，不保证稳定达到目标帧率。",
    "Camera preview: {0}": "相机实时预览：{0}",
    "SSH: on your computer run {0}, then open the preview URL in your browser.": "SSH 用户：请在自己电脑的终端执行 {0}（替换用户名和机器人主机地址），然后在自己电脑的浏览器打开上述预览地址。",
    "Camera preview unavailable; capture continues: {0}": "相机预览不可用，采集继续：{0}",
    "disable browser camera preview": "关闭浏览器相机预览",
    "local preview port (default: 8765)": "本机预览端口（默认：8765）",
    "preview port must be between 1 and 65535": "预览端口必须在 1 到 65535 之间",
    "YAM {0}-Arm Teleoperation": "YAM {0} 臂遥操作",
    "SIDE": "侧别",
    "GELLO": "主臂",
    "FOLLOWER": "从臂",
    "STATE": "状态",
    "[green]READY[/green]": "[green]就绪[/green]",
    "[red]FTDI CHECK[/red]": "[red]检查串口延迟[/red]",
    "[red]GELLO CHECK[/red]": "[red]检查主臂[/red]",
    "[red]CAN CHECK[/red]": "[red]检查 CAN[/red]",
    "target {0} Hz  ·  control 127.0.0.1:{1}": "目标频率 {0} Hz  ·  控制地址 127.0.0.1:{1}",
    "RealSense Capture": "RealSense 相机采集",
    "VIEW": "视角",
    "MODEL": "型号",
    "SERIAL": "序列号",
    "PROFILE": "分辨率/帧率",
    "RGB ON  ·  depth {0}": "彩色图像：开启  ·  深度：{0}",
    "Episode {0:04d} recording": "第 {0:04d} 条数据录制中",
    "Synchronizing followers to GELLO leaders · not recording": "从臂正在对齐主臂 · 尚未录制",
    "{0} J{1} · ": "{0} 关节{1} · ",
    "Synchronizing followers · {0}arm error {1:.3f} rad": "正在对齐从臂 · {0}关节误差 {1:.3f} rad",
    "Followers held · ready for leader synchronization": "从臂保持中 · 等待对齐主臂",
    "Connecting GELLO leaders": "正在连接主臂",
    "GELLO leader connected": "主臂已连接",
    "Preparing follower arms": "正在准备从臂",
    "LEFT follower prepared · waiting for RIGHT": "左从臂已准备 · 等待右从臂",
    "RIGHT follower prepared · waiting for LEFT": "右从臂已准备 · 等待左从臂",
    "Both followers prepared · starting control": "双从臂已准备 · 正在启动控制",
    "Starting follower control server": "正在启动从臂控制服务",
    "Followers connected": "从臂已连接",
    "Follower home pose captured": "已记录从臂初始姿态",
    "Moving followers to start pose": "从臂正在移动到初始姿态",
    "Starting control loop": "正在启动控制循环",
    "Teleoperation live": "遥操作已启动",
    "[bold bright_red]● REC[/bold bright_red]": "[bold bright_red]● 录制中[/bold bright_red]",
    "[dim red]○ REC[/dim red]": "[dim red]○ 录制中[/dim red]",
    "{0}  [bold]EPISODE {1:04d}[/bold]  [bold]{2:02d}:{3:02d}[/bold]  [bold green]Enter[/bold green] finish  ·  [bold red]Esc/Ctrl+C[/bold red] exit  [bright_green]{4}[/bright_green][bold green]▌[/bold green]": "{0}  [bold]第 {1:04d} 条[/bold]  [bold]{2:02d}:{3:02d}[/bold]  [bold green]Enter[/bold green] 结束本条  ·  [bold red]Esc/Ctrl+C[/bold red] 退出  [bright_green]{4}[/bright_green][bold green]▌[/bold green]",
    "[bold red]CAM FAULT {0}[/bold red]": "[bold red]相机异常 {0}[/bold red]",
    "[yellow]CAM MISS {0}/10[/yellow]": "[yellow]相机缺帧 {0}/10[/yellow]",
    "[cyan]CAM {0} · {1}[/cyan]": "[cyan]相机 {0} FPS · 帧间偏差 {1}[/cyan]",
    "[bold cyan]↝ SYNC · NOT RECORDING[/bold cyan]  {0}arm {1:.3f} rad  ·  gripper {2:.3f}  ·  {3:04.1f}s  ·  [bold red]Esc/Ctrl+C[/bold red] SAFE HOLD": "[bold cyan]↝ 对齐中 · 尚未录制[/bold cyan]  {0}关节误差 {1:.3f} rad  ·  夹爪误差 {2:.3f}  ·  {3:04.1f}秒  ·  [bold red]Esc/Ctrl+C[/bold red] 安全保持",
    "[bold cyan]INIT[/bold cyan]  {0}": "[bold cyan]初始化[/bold cyan]  {0}",
    "[bold yellow]STOPPING[/bold yellow]  EPISODE {0:04d}  ·  entering SAFE HOLD": "[bold yellow]正在结束[/bold yellow]  第 {0:04d} 条  ·  正在进入安全保持",
    "[bold yellow]REVIEW[/bold yellow]  EPISODE {0:04d}  ·  [bold green]s[/bold green] success  ·  [bold red]f[/bold red] fail  ·  [bold yellow]d[/bold yellow] discard": "[bold yellow]请标注[/bold yellow]  第 {0:04d} 条  ·  [bold green]s[/bold green] 成功  ·  [bold red]f[/bold red] 失败  ·  [bold yellow]d[/bold yellow] 废弃",
    "[bold cyan]READY[/bold cyan]  EPISODE {0:04d}  ·  position GELLO  ·  [bold green]Enter[/bold green] sync + wait for triggers  ·  [bold red]Esc/Ctrl+C[/bold red] exit": "[bold cyan]准备就绪[/bold cyan]  下一条 {0:04d}  ·  摆好主臂  ·  [bold green]Enter[/bold green] 对齐后等待扳机  ·  [bold red]Esc/Ctrl+C[/bold red] 退出",
    "[bold cyan]SAFE HOLD[/bold cyan]  exit requested · waiting for confirmation": "[bold cyan]安全保持[/bold cyan]  已请求退出 · 等待确认",
    "[bold cyan]SAFE HOLD[/bold cyan]  followers actively supported": "[bold cyan]安全保持[/bold cyan]  电机正在主动支撑从臂",
    "[bold yellow]SAFETY CHECK[/bold yellow]  clear workspace · emergency stop ready  ·  [bold green]Enter[/bold green] arm + slow sync  ·  [bold red]Ctrl+C[/bold red] cancel": "[bold yellow]使能前确认[/bold yellow]  运动范围无人无障碍 · 急停可用  ·  [bold green]Enter[/bold green] 使能并慢速对齐  ·  [bold red]Ctrl+C[/bold red] 取消",
    "[bold green]SAVED SUCCESS[/bold green]  {0}": "[bold green]已保存：成功[/bold green]  {0}",
    "[bold red]SAVED FAIL[/bold red]  {0}": "[bold red]已保存：失败[/bold red]  {0}",
    "[bold yellow]DISCARDED[/bold yellow]  episode not saved": "[bold yellow]已废弃[/bold yellow]  本条数据未保存",
    "[bold cyan]SESSION DATA[/bold cyan]  {0} episodes · [green]{1} success[/green] / [yellow]{2} fail[/yellow] · {3} frames": "[bold cyan]本次采集统计[/bold cyan]  {0} 条数据 · [green]{1} 条成功[/green] / [yellow]{2} 条失败[/yellow] · {3} 帧",
    "[bold]USABLE {0}[/bold]  ·  recorded span {1}": "[bold]可用时长 {0}[/bold]  ·  时间戳跨度 {1}",
    "[red]DURATION ERROR[/red] {0}: {1}": "[red]时长统计失败[/red] {0}: {1}",
    "[green]\\[Enter][/green] SAFE HOLD · [magenta]\\[g][/magenta] gravity · [bold red]\\[o][/bold red] motors off": "[green]\\[Enter][/green] 安全保持 · [magenta]\\[g][/magenta] 重力补偿 · [bold red]\\[o][/bold red] 关闭电机",
    "Safety action: ": "请选择退出后的状态：",
    "[bold red]Support every follower first. Press o again to disable motors.[/bold red]": "[bold red]请先可靠托住所有从臂。再次按 o 才会关闭电机。[/bold red]",
    "Confirm [o]: ": "确认关闭电机 [o]：",
    "[yellow]Motor disable cancelled · SAFE HOLD remains active.[/yellow]": "[yellow]已取消关闭电机 · 从臂继续安全保持。[/yellow]",
    "[yellow]Invalid key · use Enter, g, or o.[/yellow]": "[yellow]无效按键 · 请按 Enter、g 或 o。[/yellow]",
    "Preflight and launch YAM two- or four-arm teleoperation.": "检查并启动 YAM 双臂或四臂遥操作与数据采集。",
    "machine profile (defaults to YAM_PROFILE/XDG)": "本机配置文件（默认从 YAM_PROFILE/XDG 路径读取）",
    "number of YAM arms to use (default: 2)": "使用的机械臂数量（默认：2）",
    "deprecated alias for --arm-count 2": "--arm-count 2 的兼容别名",
    "task directory name for episode collection": "采集任务名称，用作数据目录名",
    "run preflight only": "仅检查设备、存储与相机，不使能从臂",
    "fixed RealSense rig configuration": "相机配置文件",
    "record aligned depth for all cameras": "同时录制各相机对齐后的深度图",
    "disable all camera capture": "关闭相机采集",
    "skip arming prompt": "跳过使能前确认提示",
    "third/fourth configs cannot be used with --arm-count 2": "双臂模式不能指定第三、第四臂配置",
    "third and fourth configs must be supplied together": "第三、第四臂配置必须同时提供",
    "--arm-count 4 requires calibrated third and fourth configs in the profile or via --third-config/--fourth-config": "四臂模式需要在本机 profile 或 --third-config/--fourth-config 中提供已标定的第三、第四臂配置",
    "camera capture requires cameras.config in the profile or --camera-config": "开启相机需要在 profile 中配置 cameras.config 或指定 --camera-config",
    "[red]Storage preflight failed:[/red] {0}": "[red]存储检查失败：[/red] {0}",
    "[bold red]Preflight failed:[/bold red] {0}": "[bold red]启动前检查失败：[/bold red] {0}",
    "[yellow]CAMERAS OFF[/yellow]  robot-only debugging mode": "[yellow]相机已关闭[/yellow]  仅机械臂调试模式",
    "{0} GELLO is not connected: {1}": "{0}主臂未连接：{1}",
    "{0} GELLO permission denied; add {1} to the dialout group, then log in again": "{0}主臂无串口权限；请将 {1} 加入 dialout 组并重新登录。旧远程终端可用 sg dialout -c 运行命令",
    "{0} GELLO FTDI latency_timer could not be read": "无法读取{0}主臂的 FTDI latency_timer",
    "{0} GELLO FTDI latency is {1} ms; set it to 1 ms: echo 1 | sudo tee {2}": "{0}主臂 FTDI 延迟为 {1} ms，需要设置为 1 ms：echo 1 | sudo tee {2}",
    "{0} GELLO bus check failed: {1}; verify leader power and the cable to Dynamixel ID 1": "{0}主臂总线检查失败：{1}；请检查主臂电源及 Dynamixel 1 号电机连接线",
    "{0} CAN {1} is {2}": "{0}侧 CAN {1} 状态：{2}",
    "{0} CAN bitrate must be 1 Mbps": "{0}侧 CAN 波特率必须为 1 Mbps",
    "Checking sustained RealSense streams": "正在检查相机是否稳定出流",
    "[bold red]Camera stream check failed:[/bold red] {0}": "[bold red]相机出流检查失败：[/bold red] {0}",
    "[yellow]Capture is blocked before arming.[/yellow] Restore every camera stream, then run [bold]scripts/teleop --check[/bold].": "[yellow]已在从臂使能前停止采集启动。[/yellow] 修复相机后，请重新运行 [bold]scripts/teleop --check[/bold]。",
    "[green]✓ Camera streams stable[/green] · {0} FPS": "[green]✓ 相机出流稳定[/green] · {0} FPS",
    "[green]✓ Preflight passed[/green]": "[green]✓ 启动前检查通过[/green]",
    "Task name: ": "请输入任务名称：",
    "Incomplete episode {0}: [f] save as fail · [d] discard: ": "发现未完成的数据 {0}：[f] 保存为失败 · [d] 废弃：",
    "[yellow]Recovered as fail:[/yellow] {0}": "[yellow]已恢复并标记为失败：[/yellow] {0}",
    "[yellow]Discarded incomplete:[/yellow] {0}": "[yellow]已废弃未完成数据：[/yellow] {0}",
    "[bold cyan]TASK {0}[/bold cyan]  ·  next [bold green]{1:04d}[/bold green]  ·  [dim]{2}[/dim]": "[bold cyan]任务 {0}[/bold cyan]  ·  下一条 [bold green]{1:04d}[/bold green]  ·  [dim]{2}[/dim]",
    "[bold red]Data preflight failed:[/bold red] {0}": "[bold red]数据准备失败：[/bold red] {0}",
    "[yellow]Cancelled.[/yellow]": "[yellow]已取消。[/yellow]",
    "Verifying watchdogs · preparing safety daemon": "正在核验电机看门狗并准备安全守护进程",
    "[bold red]Safety daemon unavailable:[/bold red] {0}": "[bold red]安全守护进程不可用：[/bold red] {0}",
    "Safety log: {0}": "安全日志：{0}",
    "[cyan]Attached to safety daemon PID {0} · state {1}[/cyan]": "[cyan]已连接安全守护进程 PID {0} · 状态 {1}[/cyan]",
    "[green]✓ {0} motor watchdogs verified at 400 ms[/green]": "[green]✓ 已核验 {0} 个电机的 400 ms 看门狗[/green]",
    "Initializing collector": "正在初始化采集程序",
    "[red]ERROR[/red] {0}": "[red]错误详情[/red] {0}",
    "{0} J{1}": "{0} 关节{1}",
    "[bold red]Synchronization failed:[/bold red] {0}": "[bold red]主从臂对齐失败：[/bold red] {0}",
    "Cameras streaming · preparing control": "相机已出流 · 正在准备控制",
    "[bold red]Collection error:[/bold red] {0}": "[bold red]采集异常：[/bold red] {0}",
    "[bold red]Save failed:[/bold red] {0} · choose s/f/d to retry": "[bold red]保存失败：[/bold red] {0} · 请按 s/f/d 重试",
    "[bold red]SAFE HOLD RPC failed:[/bold red] {0}": "[bold red]安全保持请求失败：[/bold red] {0}",
    "Log: {0}": "日志：{0}",
    "[red]Teleoperation stopped unexpectedly (exit {0}).[/red]": "[red]遥操作异常停止（退出码 {0}）。[/red]",
    "\n[bold cyan]SAFE HOLD ACTIVE[/bold cyan]\nFollowers remain actively supported by the safety daemon.": "\n[bold cyan]安全保持已生效[/bold cyan]\n电机仍在主动支撑从臂，并未断电。",
    "status unavailable": "无法获取状态",
    "\n[bold red]ACTIVE HOLD NOT CONFIRMED[/bold red] · state={0}: {1}\nThe 400 ms motor watchdog/damping is the fallback. Physically support every selected arm.": "\n[bold red]未确认主动保持[/bold red] · 状态={0}：{1}\n400 ms 电机看门狗/阻尼保护作为后备。请立即可靠托住所有从臂。",
    "[bold red]Safety action rejected:[/bold red] {0}": "[bold red]安全操作被拒绝：[/bold red] {0}",
    "[yellow]Followers explicitly disabled.[/yellow]": "[yellow]从臂电机已按确认关闭。[/yellow]",
    "[cyan]Gravity compensation remains active in the safety daemon.[/cyan]": "[cyan]重力补偿仍在运行，电机未关闭。[/cyan]",
    "[green]SAFE HOLD remains active.[/green] Re-run scripts/teleop to attach.": "[green]安全保持仍在运行。[/green] 再次运行 scripts/teleop 可重新连接。",
    "[bold red]Follower safety state is {0}, not confirmed HOLD.[/bold red]": "[bold red]从臂状态为 {0}，尚未确认安全保持。[/bold red]",
    "Log: {0} · Safety log: {1}": "日志：{0} · 安全日志：{1}",
    "LEFT": "左",
    "RIGHT": "右",
    "THIRD": "第三",
    "FOURTH": "第四",
    "MAIN": "主视角",
    "READY": "就绪",
    "MISSING": "未连接",
    "PERMISSION": "无权限",
    "USB CHECK": "检查 USB",
    "ON": "开启",
    "OFF": "关闭",
    "SAFE_HOLD": "安全保持",
    "GRAVITY": "重力补偿",
    "ACTIVE": "运行中",
    "FAULT": "故障",
    "DISARMED": "电机已关闭",
    "INITIALIZING": "初始化中",
    "Initializing hardware": "正在初始化硬件",
    "show this help message and exit": "显示帮助并退出",
    "terminal language (default: zh)": "终端语言（默认：zh 中文；en 英文）",
    "Options": "选项",
}

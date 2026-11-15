# rhos-nuc-1：双主从臂与三台 D405

部署日期：2026-10-14。系统 Ubuntu 22.04.5，内核 6.8，普通用户 `rhos`。
GitHub 仓库 `ChenyuanLiu92/YAM`，`main` 基线 `12a4c6094ea1cf8bfa45371d3c70f16cfd440b2a`。
局域网参考主机仅用于读取 Git 对象和配置参考，最终代码已与 GitHub `origin/main` 对齐。

> 后续已完成网页真机采集及 ABC 模拟摆放流程测试；当前入口为 http://127.0.0.1:8770/ 。下文保留最初的检查证据与当时未完成项，不代表今天仍未试动。正式相机标定另行提供。

## 初始部署状态（2026-10-14 历史记录）

软件安装与非运动检查已完成。没有使能从臂、修改电机零点、写入电机寄存器或启动安全守护进程。
主从臂左右映射已由操作者拔掉左侧两根 USB 线确认；操作者通过画面确认 A=main、B=left、C=right，并明确要求三路不旋转。
主臂零位与扳机两端已采样，配置采用仓库 π/2 网格零偏和标准 YAM 关节符号；首次物理方向、零位准确性、跟随及录制验收仍待完成。
参考机的辅助臂配置虽然出现相同主臂串口序列号，仍不能替代本机实测标定。

- 项目：`/home/rhos/workspace/YAM`。
- Python：`gello_software/.venv/bin/python`，由 uv 安装 Python 3.11.17 并按 `uv.lock` 重建环境。
- 已安装 extras：`yam realsense mcap guidance dev`；I2RT 使用本仓库源码，Real2Sim 使用仓库固定子模块版本。
- 数据：`~/data/YAM`；`~/workspace/YAM/data` 是其符号链接；保留至少 50 GB 空闲空间。
- 私有设备清单：`~/.config/yam/inventory.json`。实际序列号和标定保留在用户目录，不加入 Git。
- 默认配置：`~/.config/yam/profiles/default.yaml`，引用 `arms/yam_left.yaml`、`arms/yam_right.yaml` 和 `realsense_bimanual.yaml`。
  已通过严格校验；原草稿保存在 `~/.config/yam/backups/measured-profile-*`。配置校验不代替物理方向与跟随验收。
- 参考配置和检查结果：`~/.local/state/yam-deploy/`。

`main/left/right` 三路均使用 D405，RGB 640×480、30 FPS、初始自动曝光，深度默认关闭。
三路 `rotation` 均为 **0**；即使主相机原始画面呈倒置，也按操作者要求保留，不自动修正保存方向。
相机对应关系保存在 `inventory.json` 和正式相机配置中；本次确认不代表完成相机几何标定。
不要套用参考机 D435 内参、外参、指导包或曝光参数。
RealSense 配置使用 **SDK 返回的序列号**，它可能与 sysfs/USB 描述符里的 serial 不同。

### 主臂配置依据与验收范围

操作者按零位照片摆好两台主臂、松开扳机后，已分别采集 59 组只读编码器数据。
每路所有关节的采样变化范围不超过约 0.088°。按仓库生成器的 π/2 网格计算，两臂六关节零偏候选均为 π。
最大摆位残差为左臂第 4 关节约 -6.42°、右臂第 4 关节约 -4.31°；照片摆位不是精确机械测量，候选仍需方向及跟随核验。
零偏候选和实测扳机两端已写入正式 arm 配置。关节符号采用仓库 README 的标准 YAM 值 `[1, -1, -1, -1, 1, 1]`，不是从静态零位样本推导或声称已物理验证。
原始数据与计算依据保存于 `~/.local/state/yam-deploy/calibration/zero-released/leaders.json` 和 `zero-summary.json`。
操作者确认两个扳机按到底后又分别读取 20 组，左侧端点采样变化为 0°，右侧约 0.615°。
按下/松开实测角度分别为：左 145.546875° / 202.236328125°（行程 56.689453125°）；右 146.162109375° / 200.91796875°（行程 54.755859375°）。
配置按 `[7, 按下, 松开]` 保存，使按到底映射为 0、松开映射为 1；未使用参考机的夹爪行程。
证据在 `calibration/pressed/leaders.json` 与 `calibration/calibration-summary.json`。以上为配置建立依据，仍需首次现场试动核对各关节方向和零位。

## 已验证

- 仓库测试：361 passed（`pytest.log`）。
- 两个 GELLO 主臂都识别到 ID 1–7；按生产驱动的同步读包连续读取位置成功（`leaders.json`）。
- 两条 CAN 总线为 1 Mbps、ERROR-ACTIVE；14 个从臂电机均响应，timeout 寄存器全部为 8000 / 400 ms（`watchdogs.json`）。
- 三台 D405 均为 USB 3.2，同时采集 15 秒，分别约 29.99、30.00、30.00 FPS（`cameras.json`）。
- H.264 `libx264` 编码器可用；数据目录 HDF5 写入、读回、重命名和清理成功。
- 默认 profile 校验通过；`yam doctor --json` 为 22 PASS、0 FAIL、0 WARN、1 SKIP（守护进程未运行）。
- 使用默认配置的 MCAP 200 Hz 目标 `scripts/teleop --check` 通过，三路相机短窗口估计 30/30/31 FPS。
  最终证据：`~/.local/state/yam-deploy/doctor-final.json`、`teleop-check-final.log`。

上述结果不等于标定完成、200 Hz 实机控制验收、三相机长期录制验收或深度流验收。

## 设备权限与自动配置

已由管理员执行 `~/.local/state/yam-deploy/setup-host.sh`：安装 FFmpeg、can-utils、ACL，
将 `rhos` 加入 `dialout`/`video`，将两条 FTDI 延迟设为 1 ms，并配置 D405 USB 访问权限。
CAN 适配器按各自序列号固定为 `can_yam_a`、`can_yam_b`；拔插确认 **left = can_yam_b，right = can_yam_a**。
主臂串口配对已写入 `inventory.json` 和两份 arm 配置；保留 a/b 名称以维持稳定的设备标识。

持久配置在 `/etc/udev/rules.d/90-yam-nuc.rules` 与 `/etc/systemd/system/yam-can@.service`。
初始规则旨在设备插入时配置 1 Mbps，不发送运动指令。旧配置备份放在 `/var/backups/yam-nuc-*`。
初次连接设备已增加临时 ACL；重新登录终端后获得持久组权限。

### 拔插复查与 CAN 修复

左从臂接回后已恢复 `can_yam_b` 名称，但接口处于 DOWN，udev 的 `SYSTEMD_WANTS` 没有保留在最终设备事件中，服务未启动。
管理员已运行 `~/.local/state/yam-deploy/fix-can-autostart.sh`。它通过
`WantedBy=sys-subsystem-net-devices-%i.device` 建立持久依赖，提供检查适配器序列号和当前速率的
`/usr/local/sbin/yam-can-up`，并启动已连接的接口。已 UP 且为 1 Mbps 的接口保持原状；
若接口已 UP 但速率不符，拒绝自动中断其通信。配置备份在 `/var/backups/yam-can-autostart-*`。
`setup-host.sh` 已接入该修复安装器，后续重装使用同一套服务。
两份服务均已 enabled/active，设备单元的 Wants 已指向对应服务，两路 CAN 均 UP / ERROR-ACTIVE / 1 Mbps。
14 个从臂电机 timeout 寄存器复查均为 8000。修复后的再次热插拔自动启动仍待现场复验。

同次拔插中，左主臂 USB 曾在枚举阶段反复断开，内核报告 `error -32/-71`。
更换连接后已于 17:01:59 恢复，当前通过另一条集线器路径 `3-7.4` 接入；右主臂仍在原路径 `3-5.3`。
两台主臂各连续同步读取 10 秒（各 197 组），ID 1–7 均正常，FTDI 延迟均为 1 ms，测试期间无新掉线。
结果表明更换连接后通信恢复，尚不能独立归因于旧线缆或原集线器端口。保留当前可用连接，不因端口变化修改已确认的序列号配对。
当前代理会话未继承新增的 dialout 组，复查通过 `sg dialout -c '<检查命令>'` 运行；操作者重新登录终端后可直接使用。

## 不使能电机的复查

在没有采集或安全守护进程占用设备时执行：

```bash
cd ~/workspace/YAM/gello_software
.venv/bin/python scripts/inspect_nuc.py cameras --seconds 15
.venv/bin/python scripts/inspect_nuc.py leaders --seconds 5
.venv/bin/python scripts/inspect_nuc.py watchdogs
```

工具没有电机写寄存器、扭矩模式切换或位置控制入口。相机检查会打开 RGB 流并设置自动曝光。
相机拼图为 `~/.local/state/yam-deploy/d405-contact-sheet.jpg`，每幅图标出 SDK 序列号。
`leaders` 先后采样两台主臂，输出关节角度和变化范围；`watchdogs` 使用仓库原有只读校验器。
不要在其他进程占用串口或 CAN 时并行运行同类检查。

## 初始现场接续步骤（历史记录）

1. 已按操作者指认的左侧完成 USB 拔插识别，并将主从臂配对记录到 `inventory.json`。
   接回设备后检查权限、FTDI 延迟和 CAN 速率；相机就位时再核对其画面左右与该命名一致。
2. 已通过原始画面确认 A=main、B=left、C=right，三路保持原方向。配置后样张和报告在
   `~/.local/state/yam-deploy/camera-mapping/confirmed/`；几何标定另行进行。
3. 零位及扳机端点已采集，默认 profile 已建立；首次试动仍需核对各关节方向和零位。
   零位参考 [仓库照片](../gello_software/imgs/yam_default.JPG)，不要把配置通过等同于实机标定验收通过。
4. 完整退出并重新登录用户会话以获得 dialout/video 组权限。仍在旧终端时，可临时使用
   `sg dialout -c '/home/rhos/workspace/YAM/gello_software/scripts/teleop --recording-format mcap --check'`。
5. 每次开始前执行：

   ```bash
   yam profile validate
   yam doctor --deep
   cd ~/workspace/YAM/gello_software
   scripts/teleop --recording-format mcap --check
   ```

6. 现场确保急停可用、运动空间清空，按原安全流程进行首次小幅、缓慢跟随，逐关节核对方向和配对，再测试 MCAP 录制。
   启动命令会进入原有使能确认流程；部署时尚未执行此命令，未验证真实 200 Hz 控制效果。
   完成现场验收后的正式运行入口：

   ```bash
   scripts/teleop --recording-format mcap --control-hz 200 --state-hz 200 --task-name YOUR_TASK
   ```

## 重建环境

```bash
cd ~/workspace/YAM
git submodule update --init real2sim
cd gello_software
uv sync --frozen --extra yam --extra realsense --extra mcap --extra guidance --extra dev
.venv/bin/python -m pytest -q
```

当前遥操作使用 pip 安装的 Dynamixel SDK 和仓库内 YAM 模型，无需 MuJoCo Menagerie 的其他机器人资源。
两个公共 `third_party` 子模块尚未初始化；若后续使用相应仿真示例，再执行 `git submodule update --init --recursive`。

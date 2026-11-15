> **2026-09-15 仿真版本更新：** Xtrainer/YAM统一Isaac Sim 5.1.0 + Python 3.11 + PhysX。之前先用MuJoCo搭建YAM仿真场景的建议撤销；MuJoCo仅保留模型FK/IK与标定用途。请先读 [统一运行环境交接](YAM_SIMULATION_RUNTIME.md)。

> **当前接手入口已更新：** [YAM 下一阶段交接](YAM_NEXT_PHASE_HANDOFF.md)。其中记录固定标定、三个实施方向和已经迁移验证的BOS/飞书凭证。下文早期“未标定/未迁移凭证”等状态已被后续记录替代。

# YAM Real2Sim2Real 项目交接

更新时间：2026-09-15。此文档是接手本项目的 agent / 开发者入口，包含产品目标、已落地代码、验证证据和未完成事项。**当前完成的是软件迁移和无硬件验证，不是已完成 YAM 现场标定或真实抓取仿真。**

## 1. 用户要做什么

面向数采管理者与算法人员建立可扩展的 Real2Sim2Real 流程：

1. 用一条真实遥操示范、机械臂模型和相机标定作为场景重建依据。
2. 从任务表关联物料库，确定正确物料、数量和资产版本。
3. 重建任务环境与物体，在仿真/几何场景中生成广泛的初始摆放分布。
4. 用机器人运动学、桌面边界、相机视野和后续碰撞检查验证候选；VLM 可以提出摆放方案，不能替代验证器。
5. 用标定相机把目标布局投影到真实相机画面，默认约 50% 透明度，指导采集员摆放。
6. 把每条轨迹对应的指导目标记录下来，再和真实数据第一帧核对，管理实际采集分布。
7. 后续继续做真实接触、抓取和 replay 验证。不能直接根据视频估计物体轨迹再强行让物体跟随夹爪，作为物理抓取成功的替代。

平台必须可扩展：Xtrainer 是首个已开发平台，YAM 是此次迁移目标，不能把某个平台的相机外参、DH 参数、工具长度、桌面高度或关节约定写入通用层。

## 2. 已确认、不要反复推翻的产品决策

### 2.1 连续数采操作

用户不接受“每条都手工点击物体关键点才能开始录制”的操作负担。**已取消人工标点、两分钟核对有效期、一次核对只能用一次等限制。不要重新加入。**

期望流程是：

> 启动一次 collect/teleop → 选择指引 → 摆放 → 原按钮/扳机开始录制 → 结束并保存 → 手动遥操到大致还原位置 → 电脑上切换上一条/下一条指引 → 再摆放并录制。

- 逐条采集不重启程序；不自动替用户跳到下一指引。
- 同一指引可以重复录多条。
- 开始录制时固定目标快照；之后切换只影响后续轨迹。
- 指引与原有相机可视化合并，不另开程序争抢相机。
- 当前只保存目标，不声称真实物体已经摆对；实际分布与质检通过数需后续首帧核对。
- YAM 必须保留原有使能、双扳机确认、结束、成功/失败保存、废弃和权限控制。不要把 Xtrainer 的按钮状态机搬过来。

### 2.2 物料重建路线

用户计划建立物料库，后续提供专门环拍，可能提供深度。可用两条路径：

- 快速路径：根据图片、登记尺寸与 GPT 辅助判断做参数化估计模型，显式保留假设。
- 精细路径：多视图/图生三维建模，结合深度、测量尺寸矫正尺度与几何，再生成碰撞体并验证。

视觉网格与碰撞模型分开，记录来源、单位、版本、尺寸冲突、未测物理参数。当前并未实际运行通用图生三维模型或深度融合系统；已经有的是参数化候选资产与相关测试流程。

借鉴 SimFoundry 的阶段化处理、资产来源记录、几何与物理验证思想。不能称 SimFoundry 全流程已经安装并运行。**物体关节拆分/铰链建模已由用户明确暂缓**，当前优先初始摆放指导。

### 2.3 版本与数据

- 用户要求 Git 管理版本；本轮没有发布到 GitHub。
- 保留已有用户修改，不把它们混入迁移提交。
- 代码/配置模板进 Git；凭据、整段数据集、虚拟环境和生成的大资产分开管理。
- 不向飞书写回数据或改变原记录，除非得到新的明确授权。

## 3. 本机目录、仓库与版本

| 内容 | 路径 / 版本 |
|---|---|
| YAM 原项目，当前正式接入位置 | `/home/chenyuan/workspace/YAM` |
| YAM 分支 | `migration/real2sim-guidance` |
| YAM 功能迁移提交 | `593e432`，本交接文档另有后续文档提交 |
| YAM 迁移前基线 | `dc0a893` |
| 临时验证 worktree | `/home/chenyuan/workspace/YAM-real2sim-migration`，detached 在功能提交；不是第二套正在运行的数采进程 |
| 通用 workbench | `/home/chenyuan/workspace/real2sim` |
| workbench 分支/交接时版本 | `refactor/platform-adapters` / `928c7cc` |
| 原 Xtrainer 电脑 workbench | `/mnt/sdb/rhos_collector/real2sim`，同一通用 Git 历史 |
| Xtrainer 原数采项目 | `/mnt/sdb/rhos_collector/dobot_xtrainer20250912/dobot_xtrainer`，重构后提交 `94e44e4` |
| YAM 数据 | `YAM/data` 是指向 `/mnt/sdb/YAM/data` 的软链接 |

迁移通过 Git bundle + SSH 完成。YAM workbench 的 origin 指向本地归档 `.git/migration-source.bundle`，并不是 GitHub 仓库；本地 Git pull 不会自动获得 Xtrainer 电脑的更新。后续可继续发送新 bundle 并显式 fetch/fast-forward，或另行配置真正的代码远程库。

`YAM/feishu.md` 在迁移前就有用户未提交修改，目前仍保留为工作区差异。备份位置：`.git/migration-backups/feishu.before-real2sim.md`，另有名为 `pre-real2sim-migration-user-feishu` 的 stash。**不要随意 restore、reset --hard、stash pop 或把这些修改作为自己的提交。**

先读根目录 `AGENTS.md`：用户可见改动必须同时更新根 `/README.md` 与 `/feishu.md`。本次功能提交遵守此要求；后续也需遵守。

## 4. 通用层与平台层边界

通用 workbench 中：

| 模块 | 职责 |
|---|---|
| `src/real2sim/camera.py` | 已保存图像坐标约定下的相机投影与反投影 |
| `calibration/board.py` | ChArUco 检测；默认板规格保留旧平台兼容，YAM 必须核对实物后显式配置 |
| `calibration/selection.py`、`handeye.py` | 姿态多样性、eye-to-hand 求解、残差与稳定性诊断 |
| `guidance.py:GuidanceSession` | 载入布局包、前后选择、不可变快照、投影校验与首帧对照导出 |
| `collection.py` | 目录/JPEG 录制桥接；Xtrainer 通过薄封装使用，YAM MCAP 不强行复用其文件目录生命周期 |
| `platforms/xtrainer.py` | Nova2 与已有 Xtrainer 场景的运动学检查 |
| `platforms/yam.py` | 用本机 YAM MuJoCo 模型做 FK/IK；不连接硬件 |
| `platforms/profile.py` | 显式站位配置；未标定不能继承其他平台标定 |
| `feishu.py`、资产重建与 USD 辅助代码 | 可复用的物料信息、资产几何与场景能力 |

历史 stack-cups/goal-scoring 脚本仍有 Xtrainer 专用参数；这是历史任务入口，不意味着这些脚本可直接在 YAM 运行。当前并非“任意机器人、任意任务零配置”。

## 5. YAM 已实现的接入

关键文件位于 `YAM/gello_software`：

- `scripts/run_bimanual_teleop.py`：新增可选 `--guidance-bundle`，未指定时行为保持原样；先做离线包验证，再进入设备启动。
- `experiments/launch_yaml.py`：用实际相机配置进一步核对图像尺寸、主相机序列号和保存方向，向原预览和 MCAP writer 注入同一个 session。
- `gello/data_utils/real2sim_guidance.py`：YAM 适配与原网页指引控件。
- `gello/cameras/preview.py`：在原带 token 的网页增加上一条/下一条和主视角投影；原相机流和录制控制不替换。选指引接口也校验 token/Origin。
- `gello/data_utils/mcap_recording.py`：在实际 `writer.start()` 时，把 `/guidance/initial` 入队，再打开相机/状态录制 gate。不是准备阶段，不把扳机确认动作算进指引轨迹。
- `scripts/export_guidance.py`：从 MCAP 读取目标快照及真正的第一张 main 图像，离线输出对照。

MCAP 中的 `/guidance/initial` 包含任务、布局、相机参数、所需物料、透明度、原始 PNG 的 base64、哈希和 `placement_validation=pending_first_frame_review`。投影随 MCAP 保存、失败保留、废弃和未完成恢复，不另造一个不一致的保存状态机。

完成标定并生成有效包后的使用方式：

```bash
cd /home/chenyuan/workspace/YAM/gello_software
scripts/teleop --recording-format mcap --control-hz 200 --state-hz 200 \
  --task-name TASK-Y-0001 --guidance-bundle /absolute/path/to/verified_yam_bundle
```

这里的 `TASK-Y-0001` 是命令示例，**尚未从飞书确认本次 YAM 验证任务**。不要默认等同于 Xtrainer 的 TASK-X-0001。

导出已录轨迹的对照：

```bash
python scripts/export_guidance.py /path/to/episode.mcap --output /new/review_directory
```

输出：`placement_guidance.json`、`guidance_projection.png`、`first_main.png`、`guidance_overlay_first_frame.jpg`。输出目录必须是新目录。

## 6. 当前已验证与未验证

已完成无硬件验证：

- Xtrainer 本地 workbench 47 项单元测试通过；原标定 selftest 在 Python 3.8 通过。
- Xtrainer bridge 5 项测试，以及连续三条轨迹、前后切换、开始后固定投影的浏览器联调通过。
- YAM 新接入、原预览和 MCAP：29 项通过；数采/相机/安全相关回归：57 项通过，共 86 项。
- 部署回 YAM 原目录后，新增 4 项接入测试再次通过。
- YAM 模型 6 关节加载、FK、一个目标的 IK 回代通过。此结果不代表工作区或整段无碰撞验证通过。
- YAM 通用 core 3 项测试通过；ChArUco 检测器可初始化、空白图返回无检测。

**尚未完成：YAM 现场标定、真实布局包、物体实际摆放核对、YAM 接触抓取/replay、完整碰撞或动态可达性验证。没有启动真实机械臂或相机进行迁移测试。**

## 7. 环境与资产状态

原数采 venv：`YAM/gello_software/.venv`，本轮没有改其依赖。

workbench 环境：`real2sim/.venv`，Python 3.11。由于在线下载停滞，采用只读复用 `YAM/i2rt/.venv` 的依赖，并把缓存中的 OpenCV 复制到 `real2sim/.runtime/opencv`，通过 .pth 引用。它不是完全自包含环境：删除/升级 i2rt venv 可能影响 workbench。环境来源记录见 `real2sim/runs/migration/environment.json`。

当前验证版本：NumPy 2.2.6、SciPy 1.17.1、MuJoCo 3.8.1、OpenCV 5.0.0。无需把这些版本强行装入原数采环境。以后网络可用时可另行建立独立、锁版本的 workbench 环境。

现有候选物料资产已放到 `real2sim/assets/materials`，包括 MAT-00004/00010/00012/00019/00033/00035/00036，另有估计纸杯 `assets/cups`。它们有来源和测试记录，但不是经过 YAM 抓取验证的资产。图生三维/深度矫正/物体关节拆分仍属后续能力。飞书认证凭据和 Xtrainer 标定没有复制到 YAM。

## 8. 首要阻塞：YAM 没有标定，需要单独采集

用户已明确确认“没有，需要单独标定”。

本机 `real2sim/configs/yam_station.local.json` 已记录从一个历史 MCAP `/session` 读取的相机信息：

| 角色 | 型号 | 序列号 | 记录中的大小/方向 |
|---|---|---|---|
| main | D435 | 408122072021 | 640×480，rotation=0 |
| left | D405 | 260322274929 | 640×480，rotation=0 |
| right | D405 | 260422272011 | 640×480，rotation=0 |

这些是**历史记录，不是本次现场安装验证**。配置中的 K、畸变、外参、右基座变换、工具变换、桌面参数故意留空；`calibration_status=missing`。目前不能启用真实投影，不得为了演示手动改成 verified 或重命名 Xtrainer 包来绕过。

## 9. 下一位 agent 应按什么顺序做

### A. 先读代码和现场信息，别重新做迁移

1. 确认两个仓库当前分支/提交，保留 feishu.md 用户差异。
2. 阅读本文件、根 AGENTS.md、原 GUIDELINE.md、共享 `docs/YAM_CALIBRATION_PLAN.md`。
3. 与用户确认实际标定板规格、当前工具安装、相机固定情况。不要再次询问 SSH 账号密码或“是否已有标定”；这些已有结论。

### B. 补标定输入链路

1. 按实际图像流读取并保存内参、畸变、分辨率与旋转/裁剪约定；现有 MCAP 样例仅有相机设置，不能据此猜 K。
2. 准备按原 MCAP 流程采集标定数据：左臂持板、右臂持板分别观察固定主相机；每批约 20–30 个多轴、多位置、停稳姿态，另留独立验证姿态。
3. 对 MCAP 主相机图片做板检测，用**从臂实测状态**经 YAM 模型 FK 生成工具姿态，按时间戳配对。不要用主臂命令替代实测，不照搬 Nova2 参数。
4. 目前共享 `scripts/calibrate_transforms.py` 只接受准备好的 NPZ，字段为 `base_T_tool`、`camera_T_target`（N×4×4、米制）。**自动从任意 YAM MCAP 生成该 NPZ 的完整标定预处理器尚未实现**；需要结合本次板规格和流内参完成它。

### C. 求解、验证、再生成场景

固定相机模型：`base_T_tool[i] @ tool_T_board = base_T_camera @ camera_T_board[i]`。

分别求出左右基座相对于同一主相机的关系后：

`left_base_T_right_base = left_base_T_camera @ inverse(right_base_T_camera)`。

检查角点重投影误差、姿态多样性、留出误差和分半稳定性；板到工具的未知变换独立估计。腕相机做单独 eye-in-hand 标定；现有 eye-to-hand CLI 不能直接冒充腕相机外参求解器。

再处理桌面平面/工作区、实际工具接触点和机器人模型关节映射。YAM 模型默认 `link6` 是法兰，不是已经标定的夹爪尖端。

### D. 完成一个 YAM 任务闭环

1. 从 YAM 数采任务表确定任务 ID、关联物料及本次用量。不要沿用 Xtrainer 的任务 ID、世界坐标或桌面边界。
2. 生成正确平台、正确相机配置的布局包；分别记录几何、视野、IK、遮挡与碰撞检查的范围和结果。
3. 开启原数采网页指引，实物摆放，连续录制、手动还原和切换，至少验证不同指引与重复同一指引。
4. 导出首帧对照，确认画面和目标配准后，再讨论自动核对、实际覆盖统计与更广的分布生成。
5. 完整抓取/接触仿真另行验收，不与“投影看起来对齐”混为一谈。

## 10. 常用无硬件验证命令

```bash
# YAM 新接入（不会启动设备）
cd /home/chenyuan/workspace/YAM/gello_software
PYTHONPATH=. .venv/bin/python -m unittest tests.test_real2sim_guidance -v

# 原预览与 MCAP 回归
PYTHONPATH=. .venv/bin/python -m unittest tests.test_camera_preview tests.test_mcap_recording -v

# 共享 core（YAM 机器）
cd /home/chenyuan/workspace/real2sim
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_platform_core.py -v
```

完整 workbench 测试中部分用到了 Xtrainer 本机 `runs/` 资产，不要在 YAM 缺少这些离线数据时宣称全部测试通过。不要将无硬件单元测试替代现场标定验收。

## 11. 接手后向用户汇报的口径

可以说：“通用层和 YAM 原生 MCAP/网页接入已经迁移并验证，现在要做 YAM 首次现场标定和一个实际任务的投影验收。”

不能说：“YAM 全套 real2sim 已完成、真实摆放已通过、抓取仿真已成功。” 当前这些结论都没有证据。


## 12. 首批现场标定数据审查补充（2026-09-15）

用户已录完五组 MCAP，并确认标定板与 Xtrainer 为同一块（15 mm 方格、11 mm marker）。已执行筛选；前文“无标定数据”是迁移时状态，现已被本节更新。标定仍未求解或验收。

审查目录：`/home/chenyuan/workspace/real2sim/runs/yam_calibration_20260915/selection_v1`，打开 `index.html`。按完整姿态留出，train/validation 分别为：左 main 5/1，右 main 1/0，左 wrist 0/0，右 wrist 1/0，table 0/0。8 张 PNG 与 MCAP 原解码像素一致，准备段保护和分组不交叉已检查。当前样本不足，不得声称独立验证通过。

发现视频任务/相机的 left 对应实际运动 `/right/state`，right 对应 `/left/state`，manifest 已明确映射；不要擅自修改原硬件配置。主要不足为持续运动、主相机板较小导致检测不足，以及 MCAP 仍缺内参。下一步先补内参和在线有效帧反馈，再少量试采验证，补充分散姿态与停稳数据。

完整规则和重现命令：`/home/chenyuan/workspace/real2sim/docs/YAM_CALIBRATION_REVIEW_20260915.md`；筛帧入口 `scripts/prepare_yam_calibration.py`，仅离线处理，不启动设备。没有更新站位配置为 verified。


### 2026-09-15 左右命名修复注意

9 月 13 日起 MCAP 修复方案见根 README 的“左右臂命名修正”。完成修复且 `/session.data.arm_side_repair.id = yam-physical-arm-sides-v1` 的文件，应按同名相机/state 配对；上文历史的 `left camera → /right/state` 仅适用于未修复原件。先前提取的关节观测、NPZ 及标定缓存不能直接与修复后数据混用，需从对应 MCAP 重新生成。视频帧未改变。本站尚未完成标定求解，不能据此标记 calibration_status=verified。


### 新板标定候选结果（2026-09-15）

已使用 `_new` 五组数据求出四组手眼候选变换、完成独立姿态留出验证并按约 35mm 板厚估计桌面。参见 [新板标定报告](docs/YAM_CALIBRATION_NEW_20260915.md)。结果尚未启用为正式站位，原图与预测对照入口为 `/home/chenyuan/workspace/real2sim/runs/yam_calibration_new_20260915/index.html`。


## 当前固定标定版本

用户实测确认后，当前固定使用 `yam_chenyuan_20260915_v1`。主相机采用左侧原候选和右侧重新固定板后的角点优化结果，腕相机保留各自 link6 安装变换。共享项目的 `configs/yam_station.local.json` 已激活。见 [当前标定与清理说明](docs/YAM_CALIBRATION_CURRENT.md)。旧候选/优化 runs 目录已清理，历史段落中的候选状态和路径不再代表当前入口；原始 MCAP 和最终验证证据保留。

# YAM 机械臂数采

> **rhos-nuc-1 初始部署记录（2026-10-14）**：部署目录为 `~/workspace/YAM`，使用两套主从臂及 **3 台 D405**。
> 软件与默认配置已部署，主臂零位和扳机端点已采样，启动前检查通过。主从臂左右映射和三相机用途已确认，三路画面均保留原方向（rotation=0）。首次运动方向及跟随验收尚未完成。先阅读
> [NUC 部署与现场接续](docs/NUC_DEPLOYMENT.md)。下文的 `chenyuan` 路径和旧站点标定记录属于参考主机。
> CAN 服务修复已应用，两路 1 Mbps 通信及 14 个从臂看门狗复查通过；关节方向采用仓库标准 YAM 符号，尚未做真实跟随核验。部署过程没有使能从臂。
> 以上为初始部署时的状态；后续已完成网页真机采集及模拟摆放流程测试，见文末工作台说明。正式相机几何标定仍待提供。

本机使用两个主臂控制两个从臂，录制主、左、右三路相机。
当前已验证流程：**MCAP 录制，三路相机 640×480 / 30 FPS，action 目标 200 Hz，state 保存原生反馈，视频采用 H.264 压缩。**

- 数采员：[完整操作指南](GUIDELINE.md)
- 飞书发布：[简明操作卡](feishu.md)

## 本机 YAM Pro 控制配置（2026-11-12）

本机 `192.168.77.38 / rhos-nuc-1` 左右从臂均已确认为 YAM Pro。
`gello_software/gello/robots/yam.py` 的 `YAMRobot.ARM_TYPE = "yam_pro"`
让单臂、双臂和多臂入口显式选择 Pro 的电机映射、动力学模型和增益，避免落入标准 YAM 默认值。

- 关节增益：`i2rt/i2rt/robots/config/yam_pro.yml`；实验跟踪参数 Kp 为 `[300,450,450,120,30,30]`，Kd 为 `[6,6,6,1.8,1.8,1.8]`，顺序为 J1–J6。
- 夹爪增益：`i2rt/i2rt/robots/config/linear_4310.yml`；Kp=30、Kd=1.8。该文件也影响所有使用 linear_4310 且未显式覆盖增益的入口。
- 本次修改前的 250/400 配置另存为 `.before-large-motion-20261112`，校验清单位于 `../integrations/inspect_robots/gain-backups-20261112.json`。实验结束并正常关闭硬件进程后，在工作区根目录运行 `python3 integrations/inspect_robots/restore_experiment_gains.py --restore` 恢复最初配置；`--baseline prior --restore` 则恢复本次修改前配置。
- 两份完整旧配置保存在各原文件旁的 `.before-20261112` 文件中；旧关节 Kp=`[80,80,80,40,20,20]`，Kd 不变，旧夹爪 Kp/Kd=`20/0.5`。恢复时将两份备份复制回原文件并正常重启。
- 当前 Dynamixel 主臂不使用这组 YAM PD 增益；导师提供的四组参数和适用范围见 [增益变更说明](docs/PD_GAINS_20261112.md)。增益影响目标跟踪力度与阻尼，不会自动增大 GPT 请求的动作幅度。
- 启动命令不变：在 `/home/rhos/workspace/YAM/gello_software` 下执行 `scripts/teleop --recording-format mcap --control-hz 200 --state-hz 200 --task-name pick_banana`。
- 不会热更新旧进程。先结束录制、可靠支撑双臂，按现有安全停机流程退出旧守护进程，再启动遥操作。不可强杀仍在托举双臂的守护进程。
- 本次只做离线测试；重新启动后，须从新的 diagnostics 日志确认实际下发的 Kp/Kd，再完成现场跟随验收。

## 启动

在机器人电脑终端执行：

```bash
cd /home/chenyuan/workspace/YAM/gello_software
scripts/teleop --recording-format mcap --check
```

看到“启动前检查通过”后启动。将 `pick_banana` 换成负责人给定的任务名：

```bash
scripts/teleop --recording-format mcap --control-hz 200 --state-hz 200 --task-name pick_banana
```

## 每条数据怎么录

1. 摆好物品，确认机械臂运动范围无人无障碍，按 **Enter** 准备。
2. 等从臂对齐。显示“未录制”时，拿稳两个主臂，按提示完成：**两侧松开 → 两侧同时按到底 → 两侧全部松开**。
3. 看到 **“录制中”**、计时开始后，再做任务动作。
4. 完成动作，按 **Enter** 结束本条，再按 **s** 保存成功或 **f** 保存失败；误录、无效数据按 **d** 废弃。
5. 等到“就绪”，重新摆好物品，按 Enter 准备下一条，每条都要完成扳机确认。

等待期间从臂会慢速跟随，但准备、按下和松开扳机的过程不保存。录制后扳机用于控制夹爪，松开不会结束录制。
网页也可操作准备、结束、标注和停止，具体按钮见操作指南。

**收工：先保存最后一条，再停止任务。直接停止会废弃未保存数据。** 退出后按终端提示处理电机；关闭电机前先支撑所有从臂。遇到危险使用硬件急停。

## 数据与技术说明

MCAP 数据保存在 `/home/chenyuan/workspace/YAM/data/任务名/mcap/` 的 `success/`、`fail/` 目录，`.in_progress/` 为未完成文件。数据入口指向共享数据盘，不是备份。

- [安装、频率统计、质量检查和导出](gello_software/docs/multirate_mcap.md)
- [环境与配置](gello_software/docs/foundation.md) · [迁移说明](gello_software/docs/migration.md)
- [HDF5 数据格式](gello_software/docs/data_collection.md) · [开发工具](gello_software/README.md)

程序不带 `--recording-format mcap` 时仍使用旧版 30 Hz HDF5。本页明确指定 MCAP；采集时沿用完整命令。

## 运行诊断与升级

录制资源会在从臂保持时提前准备；保持未确认时显示故障并保留未完成数据。MCAP 提示“数据已保存；临时文件待清理”时，该条已发布，不需再录一遍。

启动时会校验守护进程的用户、代码目录及控制代码／硬件配置。升级后旧进程不匹配时，先支撑从臂并按正常停机流程关闭电机、退出旧守护进程，再重新启动。关节诊断日志路径会显示在终端，详见 [诊断说明](gello_software/docs/teleop_diagnostics.md)。

回放取消时，只有守护进程确认后才显示 SAFE HOLD；未确认会明确报错。视频导出只接受 `.mp4` 输出，不能覆盖输入数据，即使使用 `--force`。未完成 MCAP 恢复为失败后，如提示临时文件待清理，该条已恢复，下次启动会重试清理，无需重复恢复。


## Real2sim 连续采集指引（可选迁移功能）

保留原使能、双扳机确认、录制、结束、成功/失败保存与废弃流程，不新增机械臂动作。可选 `--guidance-bundle /absolute/path/to/yam_bundle` 将“上一条/下一条指引”叠加到原相机网页；默认不启用。

布局包必须声明 `task.platform=yam`、`calibration_status=verified`，主相机图像大小、序列号及保存方向必须匹配本机相机配置。Xtrainer 布局/标定禁止直接使用。当前尚无本机已验证布局包：需要补 YAM 相机内外参、双臂基座、工具及桌面标定后生成。

启用示例（完成上述标定之后）：

```bash
scripts/teleop --recording-format mcap --control-hz 200 --state-hz 200 --task-name TASK-Y-0001 --guidance-bundle /absolute/path/to/yam_bundle
```

摆放 → 原扳机流程开始 → 结束并保存 → 手动还原 → 网页切换指引 → 下一条。无需人工标点，无两分钟有效期。录制开始时将不可变布局和原始投影写入 MCAP 的 `/guidance/initial`，随后切换不影响已开始的一条；目标不等于实际摆放已核对。成功/失败保存、废弃和未完成恢复沿用原 MCAP 生命周期。

离线导出首帧对照：

```bash
python scripts/export_guidance.py /path/to/episode.mcap --output /new/review_directory
```

产物包含 `placement_guidance.json`、`guidance_projection.png`、`first_main.png`、`guidance_overlay_first_frame.jpg`。默认使用仓库内 `real2sim/` 固定版本子模块，可通过 `REAL2SIM_ROOT` 显式选择其他工作副本，不复制账号密钥或 Xtrainer 标定。

指引选择不控制机器人；原网页操作权限与原录制控制保持原样。选择指引需当前预览会话 token，同一预览会话中的观看者共享一个选中目标。


## Real2Sim2Real 迁移交接

项目目标、已完成的软件接入、Git 版本与验证结果，以及 YAM 首次现场标定和任务验收的下一步，见 [YAM Real2Sim2Real 项目交接](docs/REAL2SIM_HANDOFF.md)。当前尚未完成 YAM 现场标定；启用真实摆放投影前请按交接文档完成标定和验收。


### 2026-09-15 首批标定视频审查

已完成五组视频的候选帧筛选，尚未完成标定求解。审查入口：`/home/chenyuan/workspace/real2sim/runs/yam_calibration_20260915/selection_v1/index.html`。原图、角点图、时间戳和排除原因已保存。结果及左右状态通道映射见 [交接文档补充](docs/REAL2SIM_HANDOFF.md#12-首批现场标定数据审查补充2026-09-15)。


### 左右臂命名修正（2026-09-15）

左右以主视角画面和同名腕部相机为准。本站正确配对：`left = can_follower_l + FTBSX7UI`，`right = can_follower_r + FTC2FNQP`。必须连同主臂串口、关节/夹爪标定参数交换整套配置，保持原来的主从控制关系；不能只交换 CAN 通道。实际生效配置是 `~/.config/yam/arms/yam_left.yaml`、`yam_right.yaml`，不是仓库里的示例文件。

配置改好后，如旧安全守护进程仍在运行，先结束录制、支撑好从臂并通过原安全菜单关闭电机，再按原流程重启遥操作；不要强杀仍在托举机械臂的守护进程。也可运行 `scripts/safety off` 并输入 `off` 正常关闭电机（支撑好机械臂后操作）。先执行 `scripts/teleop --check`，再录一小段单侧运动，确认 `left` 相机对应 `/left/state` 和 action 前 7 维、`right` 对应后 7 维。

9 月 13 日起的旧数据需交换 `/left/state`、`/right/state`，以及 action 的两个 7 维分段（含 `requested_position`），同时交换 session 的两套 `arm_configs` 和质量统计中的 state 通道。相机、时间戳与动作数值本身不变。修正文件在 `/session.data.arm_side_repair` 标记 `yam-physical-arm-sides-v1`；读取器不要对有此标记的数据再次交换。修正前导出的 NPZ/HDF5 或标定观测缓存应重新生成。

离线修复工具（在 `gello_software` 下执行，先生成清单再应用；北京时间日期包含当日）：

```bash
.venv/bin/python scripts/repair_arm_sides.py --root ../data --since 2026-09-13 --plan /tmp/arm-side-plan.json
.venv/bin/python scripts/repair_arm_sides.py --apply --plan /tmp/arm-side-plan.json --backup-root /mnt/sdb/yam-arm-side-backups/UNIQUE
```

工具只处理已确认映射的双臂 MCAP，逐消息验证后原路径替换，原文件保存在同盘、数据目录以外的备份中；未知格式/映射停止处理。备份内 `plan.json` 和 `completed.jsonl` 记录原始及修正文件哈希。中断后先核对日志及文件，再重新生成清单、使用新的备份目录继续，不能直接再次应用旧清单。近期 HDF5 不会被静默忽略，会要求另行检查。

本次实际修复已完成：北京时间 2026-09-13 起共 205 条 MCAP（`pick_huotuichang` 100 条、小方块入槽 100 条、标定 5 条），逐消息核对 4,342,972 条消息；全部原件及修正文件 SHA-256 核对通过，重新扫描剩余 0 条待修复。原件备份：`/mnt/sdb/yam-arm-side-backups/20260915-v1/`；用户配置备份：`~/.config/yam/backups/arm-side-repair-20260915-v1/`。当前原数据路径已是修正版，无需再执行上述迁移命令。


### 新板标定候选结果（2026-09-15）

已使用 `_new` 五组数据求出四组手眼候选变换、完成独立姿态留出验证并按约 35mm 板厚估计桌面。参见 [新板标定报告](docs/YAM_CALIBRATION_NEW_20260915.md)。结果尚未启用为正式站位，原图与预测对照入口为 `/home/chenyuan/workspace/real2sim/runs/yam_calibration_new_20260915/index.html`。


## 当前固定标定版本

用户实测确认后，当前固定使用 `yam_chenyuan_20260915_v1`。主相机采用左侧原候选和右侧重新固定板后的角点优化结果，腕相机保留各自 link6 安装变换。共享项目的 `configs/yam_station.local.json` 已激活。见 [当前标定与清理说明](docs/YAM_CALIBRATION_CURRENT.md)。旧候选/优化 runs 目录已清理，历史段落中的候选状态和路径不再代表当前入口；原始 MCAP 和最终验证证据保留。


## YAM 下一阶段：仿真、分布监督与云端读写

请从 [下一阶段交接](docs/YAM_NEXT_PHASE_HANDOFF.md) 继续实施。固定标定版本已就绪；BOS与飞书凭证/业务配置已由Xtrainer机器通过SSH迁移到本用户私有目录，无需重新输入。YAM实测飞书读取52条物料、BOS HEAD/GET成功；本轮未执行云端上传或飞书回写。


## 按飞书编号管理正式数据（2026-09-15）

两批正式数据的任务目录已按「YAM 数采记录」表的「编号」统一命名：

| 编号 | 任务 | 成功 MCAP | 本地目录 |
|---|---|---:|---|
| TASK-YAM-0004 | 拿起香肠，放进泡沫箱中 | 100 | `/home/chenyuan/workspace/YAM/data/TASK-YAM-0004/` |
| TASK-YAM-0006 | 拿起小方块，放入相应形状的凹槽中 | 100 | `/home/chenyuan/workspace/YAM/data/TASK-YAM-0006/` |

原目录 `pick_huotuichang`、`pick_up_the_small_square_and_place_it_in_the_groove` 已重命名，历史文档中的旧任务数据路径需按上表替换。数据仍位于共享盘 `/mnt/sdb/YAM/data/`，每条文件的名称及内容保持不变；MCAP 内 `session.task` 保留录制时原任务名，通过发布清单关联正式编号。后续采集同一任务时，使用对应编号作为 `--task-name`。

云端目标根目录：`bos://rhos-baige-confidential-beijing-prod/S2/TeleopData/YAM/`，其下按 `<编号>/mcap/success/<原文件名>.mcap` 存放，并在 `<编号>/manifest.json` 保存文件大小、SHA-256 和原任务名映射。

本次发布已完成：200 条成功 MCAP（3,681,176,930 字节）与两个完成清单上传并校验通过，飞书两条记录的「本地路径」「云上路径」已更新并读回核对。核验与回执见 [本次发布记录](docs/YAM_DATA_PUBLICATION_20260915.md)。


## YAM 关联物料重建与资产发布（2026-09-15）

共享 `real2sim` 已补齐读取「YAM 数采记录」→ 按物料 record ID 解析关联 → 照片/登记尺寸参数化重建 → 几何与 MuJoCo 静置/渲染检查 → BOS 上传及物料表回写的执行入口。

本轮范围：TASK-YAM-0001 的足球网 MAT-00011、足球 MAT-00012；TASK-YAM-0002 的印泥 MAT-00033、印章 MAT-00034；TASK-YAM-0004 的火腿肠 MAT-00052、泡沫箱 MAT-00053。按用户要求跳过空物料关联的 TASK-YAM-0003、0006、0007。库存数量不作为任务用量。

资产发布沿用 `bos://rhos-baige-general-beijing-prod/project/handzero-project/real2sim/`，逐物料回写「仿真资产地址」「资产版本」；上一轮 MCAP 的 confidential 桶和任务数据路径保持有效。每个资产含 USD、GLB、MJCF、独立碰撞体、证据、配方和验证记录。6 件几何与 18 次静置检查通过，但质量/摩擦仍为假设、图案和细节为近似；未完成真实抓取或 YAM 指导布局验收，闭合印泥候选尚不能用于蘸印泥任务。

运行：在 `/home/chenyuan/workspace/real2sim` 使用 `.venv/bin/python scripts/reconstruct_yam_materials.py sync|build|publish --run <运行目录>`（将 `sync|build|publish` 替换为对应阶段）。本轮记录为 `runs/yam_materials_20260915/`，详见 [共享项目的完整说明](../real2sim/docs/YAM_LINKED_MATERIALS.md)。原数采解释器和控制流程未修改。

本轮最终结果：6 件资产已全部上传 BOS；物料主表 6 条资产地址与版本已更新并读回核对。发布回执：`/home/chenyuan/workspace/real2sim/runs/yam_materials_20260915/publication.json`。


## 仿真统一版本 · 2026-09-15

Xtrainer与YAM统一使用 **Isaac Sim 5.1.0 / Python 3.11 / PhysX**，不再分别选择仿真器或版本。
[统一环境与下一步](docs/YAM_SIMULATION_RUNTIME.md)；共享代码验收提交 `968b08e`。
本机Xtrainer离线32阶段/54项单元测试通过；不代表YAM已安装或完成场景/现场验收。
YAM当前驱动550.163.01，需先安排5.1驱动兼容/升级检查；数采和标定环境保持独立。


## 两批成功数据的初始摆放分布

已离线提取 TASK-YAM-0004/0006 共 200 条主相机首帧并生成位置分布、图像朝向和时长统计。香肠任务为箱左1根/右2根56条、左2根/右1根44条；方块任务96条位于凹槽画面右侧、4条位于左侧。数据为图像平面估计，包含5张香肠首帧的人工补充；箱体反光会影响定位，尚未输出米制三维位姿。见 [方法、分布及产物](docs/YAM_DATA_DISTRIBUTION_20260915.md)。原始数据未修改。


## TASK-YAM-0008 / 0010 全量发布（2026-09-16）

已按用户要求上传全部200条到 `bos://rhos-baige-confidential-beijing-prod/S2/TeleopData/YAM/`：TASK-YAM-0008 为91成功/9失败，TASK-YAM-0010 为99成功/1失败，共4,747,103,724字节。保留 `<编号>/mcap/success/` 和 `<编号>/mcap/fail/` 分类，原文件名和内容不变。200条及2份完成清单均校验通过，飞书两条记录的「本地路径」「云上路径」已更新并读回核对。

当前逻辑数据入口仍是 `/home/chenyuan/workspace/YAM/data/`，实际指向 `/mnt/sdb/YAM/TeleopData/`；此前文档中的 `/mnt/sdb/YAM/data/` 属于迁移前位置。采集命令继续使用原入口和任务编号。见 [发布与核验记录](docs/YAM_DATA_PUBLICATION_20260916.md)。


## 存储路径检查修复（2026-09-17）

若启动提示“存储检查失败：…/data → /mnt/sdb/YAM/TeleopData”，需核对当前 profile 的 `storage.root` 是否与 `storage.workspace_link` 的实际目标一致；该提示也可能表示两者不一致，并非磁盘满。本站迁移数据入口后，`~/.config/yam/profiles/default.yaml`、`original-rig.yaml` 曾仍配置旧根目录 `/mnt/sdb/YAM/data`，现已同步为 `/mnt/sdb/YAM/TeleopData`。逻辑入口仍为 `/home/chenyuan/workspace/YAM/data`。

修改前配置备份：`~/.config/yam/backups/storage-root-sync-20260917-142513/`。两个 profile 的目录、空间、写权限、链接一致性检查及临时文件写读均通过；未启动机器人，未移动或修改采集数据。重新执行原启动命令即可，现场仍先运行 `scripts/teleop --recording-format mcap --check`。以后调整数据入口时，应同时同步实际使用 profile 的 `storage.root`，不要绕过存储检查。


## 全任务 BOS 同步（2026-09-17）

当前 TASK-YAM-0001、0002、0004、0006、0007、0008、0010 共702条MCAP（691成功、11失败）已同步到 `bos://rhos-baige-confidential-beijing-prod/S2/TeleopData/YAM/<编号>/`，保持 `mcap/success/`、`mcap/fail/` 分类。总量14,109,797,254字节，本次新增302条、复用已有400条；文件及完成清单校验通过，飞书7条任务记录的「本地路径」「云上路径」已确认并读回核对。原始录制内容、其他飞书字段未修改。详见 [同步记录](docs/YAM_DATA_SYNC_20260917.md)。


## TASK-YAM-0015 随机三物品摆放指引（2026-09-19）

上层抽屉、初始关闭；从6种关联物料中每条随机选3种各1件。已生成60组均衡候选，原预览页显示本条物料清单，并随MCAP保存。候选包仍需现场投影、边界与TCP验收。启动、管理者预览、重新生成和验证见 [TASK-YAM-0015 指引说明](docs/YAM_TASK_0015_GUIDANCE.md)。


## 固定预览地址与实体投影（2026-09-19）

预览固定打开 http://127.0.0.1:8765/ ，保留页面可在teleop重启后自动重连，不再复制会话token地址。TASK-YAM-0015新增60条Isaac 5.1实体透明渲染图，使用新bundle路径启用；原线框包保留。操作和验收范围见 [实体指引说明](docs/YAM_RENDERED_GUIDANCE_20260919.md)。


## 2026-09-20：TASK-YAM-0015 尺寸复核与旧指导包停用

抽屉长宽轴向正在复核，候选为正面250、进深314、高254 mm；把手方向待确认。旧60条布局不能沿用，两个旧包已标记 RECALLED.json，新会话拒绝加载。已运行的旧指导请结束使用；不自动中断采集。尚未发布替代指导包。原图＋三视图在共享项目 runs/yam15_dimension_review_20260920_v2/index.html。详见 [尺寸复核说明](docs/YAM_DIMENSION_AUDIT_20260920.md)。


## recording_timer 测试数据 BOS 上传（2026-09-20）

已将 `/mnt/sdb/recording_timer` 下的49个测试文件（2,613,980,041字节，约2.61 GB）上传到 `bos://rhos-baige-confidential-beijing-prod/S3/EgoData/recording_timer/`，保留 `device11251/2026-07-13/0003_1783957824/` 原有层级。49个对象均完成 SHA-256、大小及 BOS HEAD 校验；完成清单为 `.../recording_timer/manifest.json`。详见 [上传记录](docs/BOS_RECORDING_TIMER_20260920.md)。


## 通用分布管理页（2026-09-20，第一阶段）

real2sim 新增独立的离线管理页：http://127.0.0.1:8780/ 。支持布局缩略图/筛选、物料组合次数、设计位置散点、俯视及可旋转三维包络，以及录制首帧和保存目标对照。YAM读取器已实现；Xtrainer按本轮要求只预留接口。没有接管遥操、相机或仿真器。

```bash
cd /home/chenyuan/workspace/real2sim
.runtime/manager/bin/python scripts/collection_manager.py serve --port 8780
```

本机目录已导入60个历史布局和TASK-YAM-0015的35条真实轨迹。布局明确标记“已停用”；35条均未保存指导目标，因此不能判断是否按图摆放。940条已完成MCAP的topic扫描也未发现 `/guidance/initial`，不据此补造关联。

当前版本只提供浏览与证据对照，人工审核提交、自动核验、批准发布和实际分布统计留待后续阶段。不能把“录过某条”或“存在目标图”统计为摆放合格。用法、公共格式、环境与验收见 [管理页说明](../real2sim/docs/COLLECTION_MANAGER.md)。本功能更新了文档，没有更改现有录制流程。


## 开发与测试：real2sim 子模块

real2sim 代码由本仓库根目录的 `real2sim/` Git 子模块管理，来源为私有仓库 `yuz1wan/real2sim-guidance`。YAM 提交固定其版本，更新 YAM 后同步子模块；拉取者需要两个仓库的读取权限。

```bash
# 在 YAM 根目录执行；已有克隆也使用这两条同步命令
git submodule sync --recursive
git submodule update --init --recursive
cd gello_software
uv sync --frozen --extra yam --extra realsense --extra mcap --extra guidance --extra dev
uv run --frozen python -m pytest -q
```

pytest 同时运行原 unittest 用例和 pytest 用例，默认只收集 `gello_software/tests` 中的离线测试。CI 使用只读 deploy key（`REAL2SIM_DEPLOY_KEY`）读取固定的私有子模块版本，不依赖服务器旁边的工作目录。轮子安装需单独安装此固定版本的 real2sim；普通遥操未启用指引时不依赖它。

现有上一级 real2sim 工作副本、标定结果、`runs/`、凭证和管理页运行环境保留原处，不会随子模块同步迁移。代码的新默认入口为 `YAM/real2sim`；本文历史记录中的上一级绝对路径仍指原始产物。需要使用原工作副本时可显式设置 `REAL2SIM_ROOT=/home/chenyuan/workspace/real2sim`。更新子模块时，先在 real2sim 仓库提交并推送，再在 YAM 中提交 `real2sim` 的版本指针。


## NUC 摆放对齐与网页数采（2026-10-20）

本机管理页：http://127.0.0.1:8780/ 。已部署为用户服务，并导入 commissioning 试采首帧；历史数据未修改。

在 `~/workspace/YAM/gello_software` 运行 `scripts/collect --task commissioning --no-guidance`，使用终端打印的 8765/token 链接操作采集网页。仍需终端使能确认和每条双扳机确认；关闭电机前托稳从臂。

本机指导包就绪后，通过 `~/.config/yam/guidance.json` 配置、`scripts/collect --task TASK --bundle /ABS/BUNDLE --publish` 发布，再用 `scripts/collect --task TASK` 自动选版。尚未提供本机标定/指导包路径，真实摆放投影待接入；不会使用 Xtrainer 标定代替。

共享 real2sim 已同步 Xtrainer 的分布审计/版本管理，YAM 原生网页支持目标投影、透明度、前后切换及 MCAP 固定留档。生成器按任务适配，YAM 全场景 Isaac 渲染未在 NUC 部署。完整步骤和能力边界见 [本机部署说明](docs/YAM_GUIDED_COLLECTION_NUC.md)。

### 无硬件指引／采集演示

运行 `gello_software/.venv/bin/python gello_software/scripts/mock_guided_collection.py`，
在本机打开 http://127.0.0.1:8766/ ，点击页面入口后取得操作权限。
演示复用正式网页、对齐/扳机/采集状态机和 MCAP 写入器；三路画面、双臂和扳机均为合成输入，
不连接硬件。选择指引 → 准备下一条 → 等待自动模拟扳机 → 结束本条 → 成功/失败保存或废弃。
数据单独放在 `~/.local/share/yam/mock-guidance/data/MOCK-DEMO`。
六个布局是固定种子的像素示例，并非真实模型生成结果；`SYNTHETIC-ONLY` 标定只用于演示。
mock 的单线程状态频率不能作为真机 200 Hz 验收结果。真实任务仍需模型、标定和摆放现场验收。
部署中的演示可用 `systemctl --user start|stop|restart yam-mock-guidance` 管理（不随开机启动）。
浏览器流程回归：用安装了 Playwright 的 Python 运行 `gello_software/scripts/check_mock_guided_browser.py`；
需先启动一个就绪的 mock 服务，每次会新增两条模拟数据，并检查废弃流程。

本机 mock 回看页：http://127.0.0.1:8781/ ，已导入本次测试的六个布局和两条记录。
它使用独立 catalog；后续新增记录需用 `real2sim/scripts/collection_manager.py --workspace ~/.local/share/yam/mock-guidance/catalog import-episode ~/.local/share/yam/mock-guidance/data --platform yam --recursive` 重新导入（使用 manager 环境 Python）。
测试截图、首帧叠加及频率报告在 `~/.local/share/yam/mock-guidance/verification/`。

### 数采员全网页操作（NUC）

固定入口：**http://127.0.0.1:8770/**。`yam-operator.service` 已作为用户服务启用，
登录后自动运行；打开网页不会自动使能。数采员无需在终端操作：

1. 输入任务名，点击「设备检查」。若使用真实指引，勾选「使用本任务已发布的真实摆放指引」。
2. 点击「上使能」，在网页确认运动范围、急停。设备和看门狗检查通过后进入安全保持，**不自动对齐**。
3. 页面自动获取操作权限，按指引摆放，点击「开始采集」；对齐通过后按提示完成双扳机动作。
4. 点击「结束采集」，再选择成功或失败保存。只有工作台显示「已保存」和文件路径才代表成功落盘；「废弃」不保存。
5. 点击工作台「停止任务／安全保持」，等待任务进程结束与保持确认。此操作废弃未保存记录。
6. 支撑或放稳全部从臂，点击「下使能」并确认。安全保持仍有力矩，下使能会撤去支撑。
   下一次上使能会重新启动安全守护进程、核验看门狗。

该入口代理原有摆放与采集页面，并复用安全守护进程、MCAP、浏览器控制所有权和状态机。
关闭网页不会停止任务；软件停止不是硬件急停。服务启动不会自动开启相机或电机。
故障不会自动恢复、也不会跳过对齐。未完成记录可在停止任务后，按网页按钮恢复为失败记录；原始数据不自动删除。
指导包仍须由工程部署提供 `~/.config/yam/guidance.json`，无有效真实指导包时可以不勾选指引进行采集，不能用 mock 标定替代。

维护入口：`systemctl --user status yam-operator`；实现 `gello_software/scripts/operator_web.py`。
工作台日志和保存事件审计在 `~/.local/state/yam-operator/`；真实数据仍保存在 `~/data/YAM/<任务>/mcap/`。
仅绑定本机回环地址，跨站请求和无令牌写请求会被拒绝。远程使用需转发 8770 端口。


### 工作台操作体验更新

8770 工作台按「检查设备 → 上使能 → 开始采集 → 保存本条 → 结束工作」显示步骤，
底部固定显示当前主要操作。绿色代表检查通过／完成，蓝色代表处理中，红色代表错误，黄色代表废弃或需要注意。
检查摘要独立显示，完整输出可展开查看。保存结果单独显示成功／失败标签和文件路径。
页面自动取得空闲的采集操作权限，不需要手动点击；另一页面持有权限时显示占用提示，不抢占控制。
“开始采集”仍先执行慢速对齐和双扳机确认，只有显示“录制中”才写入数据。
录制时主按钮变为“结束采集”，结束后出现成功保存／失败保存／废弃；保存后回到“开始采集”。
停止任务与下使能位于独立的“结束工作”区域，保留现场确认。
此体验适配仅作用于 8770 工作台，底层采集控制与电机安全逻辑不变。


### ABC 场景模拟标定与摆放测试

工作台 http://127.0.0.1:8770/ 的「ABC 模拟标定 · 摆放测试」直接打开三路真实相机，
只预览，不使能、不运动、不录制。切换六组布局并调整透明度，核对主视角中的 10 cm 桌面网格和 5 cm 测试方块；
底部「结束摆放测试」释放相机，之后可以继续常规采集流程。相机图像保持 0°，不旋转。

参数来源为 [amazon-far/abc 标准双臂场景](https://github.com/amazon-far/abc/blob/d0832d12651d1b260a652861a14648dc5f3660c7/abc_sim/models/yam_bimanual_empty.xml)，
不是旧工位标定。世界坐标 Z 向上、桌面高 0.75 m，左右臂底座分别为 (0.2525, ±0.31, 0.75) m。
选择场景 top/left/right 相机：主相机光心 (0.08600512, 0, 1.70432053) m；
640×480、垂直视场角 58° 推得 fx=fy=432.97146、cx=320、cy=240，理想畸变为零。
这些是场景模型的理想参数，并非 D405 实测内参。外参转换到光学坐标（X 向右、Y 向下、Z 向前）；
腕部保存相对各侧 link_6 的安装变换，实际投影还需要关节状态和 FK，当前轮廓叠加仅用于主相机。

本机参数：`~/.local/share/yam/simulated-placement/abc-d405-v1/calibration.json`，
同目录保留源 XML、许可证、`validation.json` 和 `bundle/` 六组测试布局。
运行 `gello_software/scripts/build_abc_calibration.py --source-repo ~/.local/share/yam/abc-source --output <新目录> --station-id <工位ID> --main-serial <主相机序列号> --left-serial <左腕序列号> --right-serial <右腕序列号>` 可重新生成（需 numpy、Pillow）。
生成过程仅解析模型并进行针孔投影，不运行物理仿真；布局不是经过物理验证的任务分布。
参数明确标记 `simulated`，不发布为 `verified`，正式采集的真实标定校验保持不变。
此前旧工位派生的 `d405-v1` 已标记撤回，本入口使用 `abc-d405-v1`。

本机虚拟环境、日志、MCAP、浏览器测试输出和私有 `.env` 不纳入同步；设备配置与标定保留在用户目录。Git 仅保存示例配置、生成工具和离线测试。浏览器回归使用对应 Python 环境的 Playwright Chromium（先执行 `python -m playwright install chromium`），不固定本机浏览器路径。


### 三视角布局

8770 工作台的相机区域随内容展开，两层内嵌页面不再单独纵向滚动。主视角独占画面区整行，左右腕视角缩小并排置于下方；摆放切换与透明度控件紧邻主画面上方，说明信息压缩显示。图像保持完整比例，不裁剪、不旋转，仍可点击放大。摆放指引与画面随工作台统一滚动，底部操作栏保持可见。


### 顶部任务栏与检查结果确认

工作台取消左右分栏，任务名称和指引选择集中在顶部，主画面占据整行，腕部画面在下方。检查过程和结果在弹出卡片中显示，完成后确认返回；顶部「检查与操作结果」可以重新查看，错误也会弹出提示。底部固定区区分当前采集操作与「停止任务 → 支撑双臂 → 下使能」，模拟预览时隐藏电机结束操作。重新检查、模拟摆放及记录恢复收纳在「更多操作」中。

### RDP 数采工作台接入（本地候选）

新增可选的 `platform` extra 和 HTTPS 设备接口。RDP 内登录、查看本人任务及摆放图；浏览器直连 NUC 查看三路画面并执行检查、上下使能、开始/结束/保存采集。采集数据留在本机，不上传到 RDP；平台任务与参考图快照写入每条 MCAP 的 `/guidance/initial`。图片尚无可验证标定清单时只作为未标定参考，不旋转、不宣称精确对齐。

安装时在原有 extras 上增加 `--extra platform`，为 `yam-operator` 设置 `YAM_DEVICE_CONFIG` 后启用；默认不开启该接口。配置和部署要求见 [RDP 接入说明](docs/rdp-device.md)。目前只完成本地模拟验收，未部署正式平台、未通过此接口驱动真机；Linear/PR 按当前要求暂缓。

本机已准备独立 RDP 联调实例：`https://rhos-nuc-1.tailae29ac.ts.net:8843/tel-workbench`，虚拟任务 `YAM-LIVE-TEST-001`。使用真实 PostgreSQL/登录/设备接口，测试账号与运行资料保存在 `~/.local/state/rdp-yam-live-test/`，不连接线上平台。真机使能须现场确认；完成后先停止、支撑双臂并下使能。准备检查通过不等于运动和数据质量已验收。

### RDP任务与采集进度同步

工作台每10秒自动刷新本人任务，任务转派/关闭后禁止新采集但保留结束和下使能。NUC在MCAP实际发布后回写成功/失败条数与策略关联，不传视频或轨迹；断网事件保存在本机SQLite队列，后台自动重试、平台去重。成功才完成策略，废弃不计数。“完成平台任务”需本机停止且进度同步完成，再由数采员确认；与停止和下使能分开。需配置专用设备回写凭据，升级设备服务前必须安全停止。详见[设备接入说明](docs/rdp-device.md)。

### 异常断电后的未完成记录清理

RDP工作台现在显示未完成MCAP数量，并提供“清理未完成记录”。只允许当前设备会话在任务已停止且电机offline/disarmed时确认操作；旧revision、运行中、保持中均拒绝。清理仅处理当前任务，将未保存残片移到`mcap/.discarded/`，原文件保留；成功/失败目录不受影响。对应孤立started事件补记discarded，未发布保存意图撤销，不虚增成功/失败计数。文件移动与队列事务中断后可重复执行，不会重复计数。清理后必须重新执行设备检查。

### 工作台按当前阶段展示操作

连接后只显示当前阶段所需的主操作：设备检查→上使能→开始采集→结束本条→保存/废弃。运行时保留停止任务；停止后优先提示支撑并下使能，下使能后再完成平台任务或断开设备。未完成记录清理仅在存在异常遗留且可处理时出现；正常会话隐藏重复授权，辅助操作和运行记录收进“更多操作与运行记录”，异常日志自动展开。不修改电机控制、确认门禁或数据库。

### 完成平台任务后的会话收尾

平台任务完成后进入独立完成态，停止设备授权续期及已完成任务查询，不再显示授权过期/任务丢失警告；保留停止、下使能和断开设备能力。完成状态在当前会话保存，刷新后恢复；旧版本页面也可从平台待入库/已入库状态识别完成。未完成但被转派/关闭的任务仍按原权限限制处理。完成后不会自动使能、重复完成或改变数据库。浏览器测试覆盖完成后列表移除、详情404、授权到期、刷新页面且不再请求device-grant，随后正常断开。

### 操作提示、逐条摆放与连续预览（2026-10-22）

操作提示优先显示正在执行的检查、上电、保存、停止等命令。初始化期间忽略旧采集器状态；慢速对齐完成后才按扳机阶段显示按下/松开提示。下使能后，未完成的平台任务在主操作区直接提供设备检查，检查通过才可重新上使能。

每个摆放策略对应一条成功记录。MCAP实际保存成功后，自动选择并授权下一条未完成策略；失败保存或废弃保持原策略。本机持久化成功记录先于平台同步也可触发切换；尚未发布的文件不会推进。已完成策略不能再次开始，全部成功后提示停止并下使能；切换授权失败时保留手动应用摆放入口，禁止继续录制旧的已完成策略。

三路预览由浏览器直连NUC的连续MJPEG流，在Canvas绘制并显示实际预览FPS，复用本机JPEG缓存、丢弃已被更新的帧，断流隐藏旧画面并重连。取代原150ms逐帧请求（约6FPS上限），源预览最多30FPS；实际帧率取决于相机、USB、CPU与浏览器。RDP不接收视频；NUC本机浏览器走本机服务，远程浏览器会接收NUC传来的预览流。预览不改变本地MCAP录制频率。

摆放参考图默认开启叠加到主视角，初始透明度40%；主视角就绪且与参考图宽高比匹配时显示。操作员可关闭叠加或调节透明度，刷新页面恢复默认开启。

### 网页注册采集工位

RDP管理员现在可在“系统管理 → 采集工位”新增、编辑、停用工位，下载接入配置并检查连接。目标工控机在 `http://127.0.0.1:8772/` 导入接入文件，扫描并确认主从臂和三台D405、拍照核对视角、上传本机标定后检查应用；无须编辑配置源码。向导仅本机可访问，应用需停止、下使能及进度同步完成，不自动使能。首次安装驱动/权限/证书与向导服务仍由部署人员完成，步骤及限制见[设备接入说明](docs/rdp-device.md)。

### 默认本机识别与连接（2026-10-22）

工作台新会话默认“本机（自动识别）”。浏览器只探测固定的 `http://127.0.0.1:8773/v1/challenge`，将设备ID和工位ID同时与平台启用工位列表核对；匹配后显示本机工位名称，选择已发布且分配给本人的任务后自动建立签名设备会话。不会自动检查、上使能或开始采集。未注册、已停用、服务不可达或浏览器拒绝本地网络访问时明确提示，绝不自动连接列表里的其他机器。可手动重新识别或切换“远程工位（手动选择）”。不自动扫描局域网。

本机模式的状态、相机预览和控制全部经127.0.0.1:8773，不使用工位登记的远程URL；浏览器首次可能要求允许该平台访问本地网络。回环HTTP是浏览器可信本地地址的特殊情况，客户端仍拒绝普通局域网HTTP及其他HTTP端口，远程连接继续要求可信HTTPS。设备接口只监听loopback、严格校验Host/平台Origin、独占会话和签名授权；与8771共享同一个DeviceBridge，不能从另一个入口抢占控制。现有安装仍保留HTTPS远程入口及其证书配置，未改平台注册字段。

刷新后按会话保存的连接方式恢复。老版本的远程会话保留远程连接供安全收尾，不在录制期间自动切换传输；停止并断开后可以选择本机。主动断开不会立即自动重连，切换新任务或显式重试才重新连接。

同时修复sg权限包装进程下的systemd监督：operator服务使用Type=notify/NotifyAccess=all，由Python报告实际MAINPID，保留KillMode=process以免连带终止安全守护进程。安装脚本和本机服务已更新；纯回环入口仅做会话和元数据鉴权，不放宽机械臂控制门禁。

RDP 工作台只展示本人已领取的任务；请先到 TEL 任务页面领取。设备未启动且安全状态为 offline/disarmed 时，可以“取消选择”或直接改选任务，不必先检查。此操作只释放本机会话，不退回平台任务、不删除记录。设备服务同时检查独立采集进程，运行中不能释放控制会话。

RDP工作台按浏览器可用宽度布局，长任务说明换行，不再随文案改变画面宽度。相机SN及左右主从臂映射可在NUC本机打开 http://127.0.0.1:8772/ 配置：扫描、按角色选择、拍照确认、检查配置，下使能并支撑双臂后应用。已有工位可保持原平台接入配置；硬件SN与平台设备ID/工位ID是不同标识。

## NUC 按任务手动上传 BOS（2026-11-02）

本机上传入口 `gello_software/scripts/upload_task_bos.py`，配置位于 `~/.config/yam/bos-upload.json`；本机独立 Python 环境 `~/.local/share/yam-bos-venv` 安装 `bce-python-sdk==0.9.79`，不修改采集运行环境。快捷命令 `~/.local/bin/yam-bos-configure` 在终端隐藏输入 AK/SK，保存为 `~/.config/real2sim/bos.json`（0600）；凭据不进 Git。

```bash
# 首次配置凭据
~/.local/bin/yam-bos-configure
# 预检：只读取本地文件，不访问 BOS
~/.local/bin/yam-upload-task SCRIPT-DARM-000002 --source ~/data/YAM/RDP-实际任务UUID --dry-run
# 目标任务结束后上传；其他任务可继续采集，目标任务不可重新开始
~/.local/bin/yam-upload-task SCRIPT-DARM-000002 --source ~/data/YAM/RDP-实际任务UUID
```

若本地目录本身就是 `~/data/YAM/SCRIPT-DARM-000002`，可省略 `--source`。云端任务编号须与本地目录人工核对，程序不会把 RDP UUID 自动当作剧本编号，也不会自动上传历史任务。同一云端任务只能由一台工位发布，不支持跨主机并发发布或自动合并。

固定目标 `bos://rhos-baige-confidential-beijing-prod/S2/TeleopData/YAM/<任务编号>/`。仅上传 `mcap/success/*.mcap` 和 `mcap/fail/*.mcap`，保留相对路径；排除隔离文件与其他本机资料。未完成录制、符号链接、MCAP首尾标记异常、源文件变化、云端多余对象或内容冲突都会阻止完成信号。首尾标记检查不替代帧率/内容质检。

逐文件计算SHA-256，分片上传后HEAD核对大小和SHA-256元数据；相同文件跳过，失败可用同一命令重试。先发布并校验 `manifest.json`，最后上传 **零字节** `_UPLOAD_COMPLETE`，例如 `SCRIPT-DARM-000002/_UPLOAD_COMPLETE`。已有完成标记的任务只允许原清单原文件复核，不允许追加/覆盖；追加数据应使用独立批次编号并由下游协调。单机锁防止重复执行。上传完成后本地回执位于 `~/.local/state/yam-bos-upload/<任务编号>.json`；不会删除源文件、不回写飞书、不经过 RDP 传输媒体。

真实验证（2026-11-03）：凭据配置完成，临时对象上传/读回及清理通过。SCRIPT-DARM-000002 的30个MCAP共590,350,893字节已真实上传并逐文件HEAD校验；manifest读回核对，最后发布的_UPLOAD_COMPLETE确认0字节。回执位于 ~/.local/state/yam-bos-upload/SCRIPT-DARM-000002.json。

2026-11-03 上传并行修正：仅拒绝目标任务仍在采集的情况，其他任务可继续采集，无需为上传下使能。核对网页任务、实际采集进程（含残留进程）、RDP任务上下文及本地回写记录中的任务映射；身份不明确时拒绝上传。每个文件及发布标记前重新检查，文件增删/内容变化仍阻止完成标记。默认串行上传、CPU nice +10，简单上传与分片均通过BOS traffic_limit限速2 MiB/s，可用 --limit-mib 调整。上传期间不要重新开始目标任务；本工具不修改或重启机械臂服务。限速参数参考 https://intl.cloud.baidu.com/zh/doc/BOS/s/bl5v4vpwl-intl 。15项离线测试通过。

2026-11-03 批量手动上传支持 --quiet-minutes 30：以任务目录及文件的mtime（非访问时间）判断静置窗口，且保留进程占用和文件变化检查。依据用户指示，对本机16个SCRIPT-DARM目录建立固定队列，末次修改均为2026-10-28；每项单独发布完成标记，失败项记入汇总、不阻塞其他任务。16项离线测试通过。后台单元 yam-bos-batch-20261103，计划/日志/状态位于 ~/.local/state/yam-bos-upload/batch-20261103/，只针对NUC1。

### 上传清单文件大小兼容修复（2026-11-05）

本机 `yam-upload-task` 生成的每条文件记录现同时包含 `size_bytes`（RDP 入库字段）和 `size`（BOS 传输辅助字段），两者均取已关闭文件的真实字节数。新增字段不会修改 MCAP。历史已发布清单由单独维护流程在备份、核对 BOS 实际对象大小后补齐；普通上传命令仍拒绝覆盖不一致的完成批次。本次仅修复大小字段，outcome、任务身份、策略和大文件限制另行处理，不能把此项通过视为完整入库验收通过。

### 上传清单录制结果修复（2026-11-05）

本机 `yam-upload-task` 现在将原始保存目录 `mcap/success/` 或 `mcap/fail/` 写入每条清单记录的 `outcome`，分别为 `success`、`fail`。这是采集时保存的结果，不是人工质检结论；失败录制不会改成成功。历史清单维护只补缺失字段，已有字段与目录冲突或目录不明确时停止；先备份和核对对象，再更新清单并回读。不改写 MCAP，不对原本缺少完成标记的批次补标记，不发送入库通知。

### RDP 本地增量归整

`yam-normalize-datasets` 将已保存且至少 120 秒未修改的 `~/data/YAM/RDP-*/mcap/{success,fail}/*.mcap` 归整到 `data/datasets/TASK-TEL-*/mcap/{success,fail}/`。身份来自录制内 `/guidance/initial.task_id`，并校验 batch UUID 与源目录一致；测试编号、任务冲突和异常文件会登记问题，不猜测编号。原文件保留，输出为独立副本，原录制 UUID 保持不变；只修改 `/session.data.task`，不会把无法确认的策略或剧本信息补进 session。

每条记录读取完整 MCAP、校验 CRC、Summary/Statistics，并比较源与目标的全部消息内容、时间戳、Schema、Metadata 和附件，除 session.task 外应完全一致；这属于容器和数据保持验证，不冒充视频完整解码或 QC 验收。SQLite 目录索引保存录制 UUID、源/目标 SHA256 和文件指纹；重跑跳过未变文件，新保存录制增量加入。相同 UUID 不同内容或不同结果会阻断，不覆盖旧副本。中断后的已发布副本会重新核验，完成后的索引和 manifest 可恢复，不重复统计。

默认保留至少 40 GiB 可用磁盘供数采，处理前及 MCAP 写入时检查空间；不足时状态为 `paused_low_disk`，不删除源或备份。`--watch` 每 300 秒扫描新增录制，空间恢复后继续。默认源、目标、索引路径可分别用 `--source`、`--target`、`--state` 指定；`--reserve-gib` 可调整预留量。该命令使用本机独立上传环境的 Python 3.11+ 和 mcap/zstandard/lz4 依赖，不修改控制程序运行环境。

```bash
yam-normalize-datasets             # 单次增量扫描
yam-normalize-datasets --watch     # 持续扫描（不要与已有服务同时运行）
yam-datasets-status               # 查看累计数量、问题和剩余磁盘
```

后台服务为 `yam-dataset-normalizer.service`；查看日志用 `journalctl --user -u yam-dataset-normalizer.service -n 30`，停止归整用 `systemctl --user stop yam-dataset-normalizer.service`，不影响采集。索引和状态保存在 `~/.local/state/yam-dataset-normalizer/`，其中 `catalog.sqlite3` 的 records/sources/issues 可追溯逐文件归整。

每个目标任务包含完整的当前本地 manifest（size_bytes、sha256、outcome）及 `_LOCAL_INCREMENTAL` 标记，不生成 `_UPLOAD_COMPLETE`，不上传、不调用 RDP。普通 `yam-upload-task` 会拒绝直接发布此类未冻结增量目录。后续必须核实平台任务完成并另外生成冻结快照，再进入上传流程；不能通过删除标记把正在追加的数据当作完整批次。

2026-11-06 本机部署：指定路径 `/home/rhos/workspace/YAM/data/datasets` 通过仓库原有 `data` 链接解析到 `/home/rhos/data/YAM/datasets`。原 `/home/rhos/data/datasets` 中 3791 条归整结果已通过同文件系统重命名迁入，旧路径保留兼容链接，原 RDP 数据未移动或删除。首次扫描依据旧 `.aggregation-state.json` 核对源/目标 SHA256，再逐消息比较后纳入新索引；不会再复制这批约 83 GiB 数据。旧清单在其全部条目校验完成前保留不改，索引统计只包含已核验条目，因此首次扫描时数量会逐步增长。旧溯源文件保留作历史记录，后续增量以 SQLite 索引和新 manifest 为准。后台服务以低 CPU/IO 优先级运行，最多使用一个 CPU 核的配额；40 GiB 是归整停止阈值，不能阻止采集程序或其他进程继续消耗空间。

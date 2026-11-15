# Real2Sim

**统一仿真环境：Isaac Sim 5.1.0 / Python 3.11 / PhysX（Xtrainer、YAM 共用）。**
使用 `bash scripts/isaac_python.sh ...`，禁止用原 `.venv` 启动仿真或按平台另选版本。
[安装、兼容修复和测试范围](docs/ISAACSIM_5_1_MIGRATION.md) · [验收报告](docs/ISAACSIM_5_1_TEST_REPORT.md)。

跨机器人平台的真实遥操数据 → 场景重建 → 多视角回放与评估项目。

**2026-09-14：新增 PhysX 接触抓取原型，使用动态夹爪和自由杯子；完整叠杯尚未成功。**
项目独立位于 `/mnt/sdb/rhos_collector/real2sim`，不在 Xtrainer 项目内。原始采集数据和标定通过路径引用，未修改采集系统。

## 查看结果

- [新的接触回放：真机轨迹、抓取与失败](runs/stack_cups_contact/nominal/review/index.html)
- [独立接触测试：合成居中夹取](runs/stack_cups_contact/grasp_bench_nominal/review/index.html)
- [接触实现、参数及运行方法](docs/CONTACT_REPLAY.md)
- [碰撞/摩擦对照与步长检查](runs/stack_cups_contact/contact_evaluation.json)

以下为上一阶段的视觉动画原型，包含人为估计的杯子轨迹：

- [交互验收页面](runs/stack_cups/review/index.html)：上排真机，下排仿真；支持播放、拖动、半透明叠加。
- [对比视频](runs/stack_cups/review/comparison.mp4)：无需仿真环境即可观看。
- [验收说明与局限](docs/REVIEW_STATUS.md)
- [动画 USD 场景](runs/stack_cups/scene.usda)
- [可复用空心杯资产](assets/cups/paper_cup/v1/README.md)

本地用浏览器打开 HTML；页面图片和视频均为本地产物，无需外部服务。分享时打包 `runs/stack_cups/review/` 即可观看，报告链接另需其父目录内的 JSON/JPG；USD 则依赖项目内官方资产。

已盘点 100 段、36,118 帧观测；本轮选取 `20260912135045` 的 410 帧构建连续动画，每隔 4 帧并包含末帧渲染，共 104 组 × 3 视角。6 FPS 视频是展示速度，原始采集时间戳尚未可靠验证。

## 已实现与边界

已复用固定版本的官方机器人 USD，在 Isaac Sim RTX 中回放实测臂关节（首版 4.5，当前统一运行版本 5.1.0），接入完整相机内外参，生成参数化空心杯、桌面和估计抓放轨迹。三个视角共享同一个 3D 场景。

上一阶段完成的是**视觉回放原型**，完整视觉相似度尚未验收。杯子尺寸、抓取变换、落杯轨迹、持杯夹爪开度和部分外观是估计值；右基座外参仍为暂定。没有用物理接触自然生成叠杯动作，也未验证控制器动作回放。

## 运行

已有环境可直接使用，不需要启动真机：

```bash
cd /mnt/sdb/rhos_collector/real2sim
.venv/bin/python scripts/prepare_episode.py --source-root /mnt/sdb/rhos_collector/dobot_xtrainer20250912/dobot_xtrainer
.venv/bin/python scripts/build_scene.py --source-root /mnt/sdb/rhos_collector/dobot_xtrainer20250912/dobot_xtrainer
.venv/bin/python scripts/validate_scene.py
bash scripts/isaac_python.sh scripts/render_isaac.py --stride 4
.venv/bin/python scripts/evaluate_landmarks.py
.venv/bin/python scripts/build_review.py
```

依赖安装与资源获取见 [实现记录](docs/IMPLEMENTATION.md)。`scripts/no_proxy.sh` 清除大小写代理变量，默认清华 PyPI、HF 镜像；NVIDIA 专有依赖及 Kit 扩展由供应商源直连补全。环境在独立 `.venv` 内。

## 扩展结构

```text
src/real2sim/contracts.py  平台、资产、后端与评估接口草案
src/real2sim/geometry.py   通用坐标变换
src/real2sim/usd_robot.py  通用 USD 关节图与运动学
src/real2sim/scene.py      通用相机、材质和几何构建
src/real2sim/xtrainer.py   Xtrainer 数据与运动学适配
configs/                  平台关节约定、任务配方、来源版本
assets/                   官方资产与独立生成的道具
scripts/                  数据处理、场景构建、渲染、评估
runs/                     实际回放与报告，不进入版本控制
```

通用模块不导入 Xtrainer 原项目。新增平台应适配中立数据、机器人运动学、关节映射和相机链；新任务更换资产与轨迹配方。当前只有一个平台端到端跑通，接口尚未通过第二个平台验证；任务装配脚本仍含叠杯专用逻辑。

## 计划与设计

2026-09-15 新增飞书首批物料重建测试：7 件图片/尺寸驱动的参数化候选、21 次 PhysX 静置、21 张 RTX 预览。见 [重建方法、限制与复现](docs/MATERIAL_RECONSTRUCTION_BATCH.md) 和 [实物/模型对照页](runs/material_reconstruction_batch/index.html)。这些为待复核资产，尚未通过任务场景或真实抓取认证。

2026-09-14 新增初始摆放与采集指导测试：纸杯 v1 物料登记，54 个几何/IK 合格候选，49 个通过静置及 RTX 遮挡检查，覆盖 9 类源杯深度/间距组合。默认显示录制首帧，可切换布局并作 50% 物体叠加、人工核验摆放；尚未进行实时采集员试用，也不代表新布局抓取成功。

运行 `.venv/bin/python scripts/serve_guidance.py` 后在本机打开 `http://127.0.0.1:8765`。完整命令、VLM 候选 JSON 和实时 JPEG 接入见 [摆放指导流程](docs/DISTRIBUTION_GUIDANCE.md)，本轮数据见 [测试结果](runs/stack_cups_distribution/RESULTS.md)。

- [实施计划与验收](docs/PLAN.md)
- [架构和数据契约](docs/ARCHITECTURE.md)
- [仿真环境选择](docs/SIMULATORS.md)
- [Xtrainer 输入盘点](docs/XTRAINER_INPUTS.md)
- [模型辅助道具建模流程](docs/ASSET_WORKFLOW.md)
- [官方环境参考](docs/UPSTREAM_XTRAINER.md)

初始计划文档保留设计背景，当前实施状态以本页和验收说明为准。路线依次为视觉回放、接触回放、控制器动作回放；每份结果都应区分 measured / commanded / estimated / assumed。

### BOS 结果上传

独立对象存储适配器支持 AK/SK 本机配置、目录清单、分片上传和文件级重试。用 `scripts/configure_bos_credentials.py` 保存密钥，配置 `configs/bos.local.json`，再运行 `scripts/upload_bos.py`。支持 `--dry-run` 离线预览；详细用法与边界见 [BOS_STORAGE.md](docs/BOS_STORAGE.md)。

### TASK-X-0001 击球进门摆放指导

已通过飞书任务关联选取足球网 MAT-00011 和足球 MAT-00012，生成 36 组按距离/入射角分层的初始摆放。支持人工选料确认、标定投影、标点核对与独立的测试/真实覆盖统计。运行 `.venv/bin/python scripts/serve_goal_supervision.py --port 8767`，访问 `http://127.0.0.1:8767`。当前使用历史标定离线验证；详见 [说明与边界](docs/TASK_X_0001_SUPERVISION.md)。

实时数采已增加可选桥接：原 `collect.sh` 选 **5**，合并预览访问 `http://127.0.0.1:8770`，每条轨迹写入 `placement_guidance.json`。详见 [采集员步骤与接入说明](docs/LIVE_COLLECTION_GUIDANCE.md)。已做无硬件联调，尚未现场运行设备。

当前连续采集流程：上一条／下一条指引 → 摆放 → 原按钮开始／结束。无需人工标点或两分钟有效期，每条轨迹保存 guidance_projection.png、guidance_overlay_first_frame.jpg 和 placement_guidance.json；实际摆放待后续首帧核对。见 [操作说明](docs/LIVE_COLLECTION_GUIDANCE.md)。

### Platform extraction / YAM

Shared camera projection, calibration math, immutable guidance snapshots and the directory recording bridge now live under `src/real2sim`; Xtrainer-specific IK is isolated under `platforms/xtrainer.py`. YAM's model-only MuJoCo adapter and an explicitly uncalibrated station template are provided. See [migration boundaries, Git workflow and prerequisites](docs/PLATFORM_MIGRATION.md). Historical Xtrainer scripts remain platform examples, not YAM calibration sources.

YAM native MCAP/preview integration has been migrated and tested without hardware. See [YAM migration status, Git commits, environment provenance and missing calibration](docs/YAM_MIGRATION_STATUS.md). Live YAM projection remains gated on YAM-specific calibration and a matching layout bundle.

YAM 用户确认尚无标定数据，下一步按 [首次现场标定准备与采集计划](docs/YAM_CALIBRATION_PLAN.md) 进行；当前物理投影保持未启用。


YAM 首批标定数据的筛帧结果、左右通道映射及待补录项见 [2026-09-15 标定审查](docs/YAM_CALIBRATION_REVIEW_20260915.md)。本轮未完成标定验收。


新板 `_new` 五组数据已得到候选标定与留出验证指标，见 [YAM 新批次标定结果](docs/YAM_CALIBRATION_NEW_20260915.md)。尚未启用正式站位。


右主相机两帧排除后的重标定与验证退化记录见 [重标定对比](docs/YAM_CALIBRATION_REFIT_20260915.md)。正式站位未变更。


右臂重新固定板后得到新候选与角点优化对照，仍有跨批外参差异，见 [重新固定板诊断](docs/YAM_RIGHT_REMOUNT_20260915.md)。


## 当前 YAM 标定入口

用户已实测确认并固定 `yam_chenyuan_20260915_v1`。使用 `configs/yam_station.local.json`，版本参数和来源记录在 `configs/stations/`。后续重建以 [当前固定标定](docs/YAM_CALIBRATION_CURRENT.md) 为准，之前各批优化报告仅作历史记录。


## YAM 下一阶段开发交接

仿真环境重建、数采分布监督、BOS/飞书读写的实施入口和当前凭证配置，见 [YAM 下一阶段交接](docs/YAM_NEXT_PHASE_HANDOFF.md)。凭证已安全迁移，YAM飞书读取及BOS只读连通已验证。


## YAM 任务关联物料重建与发布

新增 `scripts/reconstruct_yam_materials.py sync|build|publish --run <运行目录>`：读取 YAM 数采表真实物料关联，按已检查照片/尺寸的配方生成 USD、GLB、MuJoCo 模型，完成几何/静置/渲染检查，再上传现有 BOS 资产目录并回写物料表地址和版本。本轮 3 个已关联任务覆盖 6 件物料，空关联任务按用户要求跳过；新版本保留旧资产，候选仍需任务场景及抓取验收。详见 [实施、命令与验证记录](docs/YAM_LINKED_MATERIALS.md)。


## YAM TASK-YAM-0015 摆放候选（2026-09-19）

新增任务关联读取、随机三物料上层抽屉布局和逐条物料快照支持。60条候选及YAM原生MCAP离线验证已完成，现场验收待完成。详见 [使用及限制](docs/YAM_TASK_0015_GUIDANCE.md)。


## YAM 实体指引渲染（2026-09-19）

TASK-YAM-0015 新增Isaac 5.1逐条独立渲染RGBA指引及验证工具，数采端只需读取PNG。固定预览地址与用法见 [说明](docs/YAM_RENDERED_GUIDANCE_20260919.md)。


## 2026-09-20：TASK-YAM-0015 尺寸复核与旧指导包停用

抽屉长宽轴向正在复核，候选为正面250、进深314、高254 mm；把手方向待确认。旧60条布局不能沿用，两个旧包已标记 RECALLED.json，新会话拒绝加载。已运行的旧指导请结束使用；不自动中断采集。尚未发布替代指导包。原图＋三视图在共享项目 runs/yam15_dimension_review_20260920_v2/index.html。详见 [尺寸复核说明](docs/YAM_DIMENSION_AUDIT_20260920.md)。


## 通用分布管理页（2026-09-20，第一阶段）

新增 `scripts/collection_manager.py import-bundle|import-episode|inspect|serve`，默认管理地址 http://127.0.0.1:8780/ 。提供跨平台公共证据格式、布局目录/筛选、物料组合与设计位置分布、可旋转包络和实际录制首帧对照。YAM MCAP 已接入；按用户本轮要求，Xtrainer仅保留入口，尚未实现读取。

使用独立 `.runtime/manager` 环境，安装依赖见 `requirements-manager.txt`，不修改数采或Isaac环境。60个历史布局已入目录并显示停用状态；35条真实YAM轨迹均未关联目标，不计为摆放达标。人工审核、发布与自动核验尚未实现。完整命令、架构及验收见 [分布管理说明](docs/COLLECTION_MANAGER.md)。


## 2026-09-21：YAM 斜放试采布局

新增6条试采模式（原60条全组合模式保留）。使用确认的柜体正面宽250、进深314、高254 mm及用户允许的60°–75°斜放，保持原视野/间隔/工作区/200mm拉出门限；各物料出现3次。产物是标定线框投影，非实体渲染或物理验证。新包已导入本机管理目录，旧包保持停用。见 [试采指南及复现](docs/YAM_TRIAL_GUIDANCE_20260921.md)。


## 本机管理员微调与发布（2026-09-23）

Xtrainer TASK-X-0001 已增加版本化数值微调/剔除、几何和 IK 重验、Isaac 5.1 双臂与环境预览、发布及采集默认选择最新匹配版本。原数采按钮流程保持不变。使用、测试和跨平台边界见 [管理员编辑与发布](docs/MANAGER_EDIT_PUBLISH.md)。

管理分布：见 [鸟瞰拖动与100训练＋30验证](docs/DISTRIBUTION_SPLITS_AND_BIRDVIEW.md)，含采样依据、覆盖审计和录制标签说明。

Xtrainer TASK-X-0003：见 [研杵交接/捣压初始分布](docs/TASK_X_0003_DISTRIBUTION.md)，使用飞书 STL，100训练＋30验证，管理员审阅与发布；[实时采集和现场试采](docs/TASK_X_0003_FIELD_TEST.md)。

实时投影显示：[TASK-X-0003 透明资产渲染](docs/RENDERED_LIVE_GUIDANCE.md)。

## RDPC 测试 Worker

新增 `integrations/rdpc/`：使用原版 RDPC Worker bridge 对接 `tel.placement_strategy`，生成显式 MOCK 测试图，验证进度和结果回传。无需运行 Isaac Sim 或硬件，不使用已确认分布。

[配置、启动与同事交接](integrations/rdpc/README.md) · [本机验证范围](integrations/rdpc/VALIDATION.md) · [操作说明](feishu.md)。目前已通过本机隔离测试，跨电脑 Scheduler/BOS 联调等待双方地址和授权配置。

## RDPC 三种任务联调

测试 Worker 现支持 `tel.simulation_asset`（占位OBJ）、`tel.placement_strategy`（MOCK分布图）、`tel.placement_supplement`（human_mock补充图），全部 mock=true。共享并发容量1，不运行真实仿真或硬件。16项本机测试通过；请按 [三类型联调说明](integrations/rdpc/README.md) 分别下发成功/失败任务，检查中心确认与BOS结果。测试补充结果不能当真人确认的分布使用。

资产 MOCK 输入校验：描述字段仅要求尺寸说明 `size_text` 为非空字符串；材质 `material_kind`、类别 `category`、尺寸来源 `size_source` 可为空或省略。保留 material_uuid/material_code/material_name、图片、封套身份与摘要校验。缺少尺寸的任务仍失败，历史失败任务需新建 job 重试。


管理页在无匹配发布版时提示使用本平台入口发布指引，或显式选择无指引模式；不再暗示所有平台都会自动退回普通采集。YAM 指引入口缺少发布版时会拒绝启动。

# 布局微调、全场景预览与采集默认版本

本机 Xtrainer / TASK-X-0001 的闭环入口。通用发布目录按平台、任务、工位和相机标定分开；YAM 轨迹证据读取保持原接口。本次未部署到 YAM 采集电脑。

## 启动管理员

```bash
cd /mnt/sdb/rhos_collector/real2sim
.runtime/manager/bin/python scripts/collection_manager.py serve --port 8780
```

打开 http://127.0.0.1:8780/ 。已运行旧管理页时先结束旧服务，重新启动并刷新网页。SSH 转发仍适用：`ssh -N -L 8780:127.0.0.1:8780 用户名@主机`；本地映射到其他端口也支持。

管理服务使用 `.runtime/manager`；布局计算调用独立 `.venv`；渲染经 `scripts/isaac_python.sh` 使用 Isaac Sim 5.1。不会调用采集器、访问相机或运动机械臂。依赖或资产不足时直接报告失败，不切换仿真后端。

## 管理者流程

1. 选择布局版本，查看整批材料组合、数量和设计位置散点。
2. 点击单条布局，查看标定主相机画面、含双臂/桌面的鸟瞰和 IK 接触姿态。可以点击“生成整批全场景预览”，也可以只渲染单条。每条布局使用独立 Isaac 进程，缓存按布局、相机、场景和渲染代码绑定。
3. 在正交鸟瞰图拖动球门/球、拖动旋转柄调整朝向；也可在数值表单调整 X/Y/朝向、执行臂（1左/2右）。支持撤销与 Shift 吸附。
4. 点击“保存新草稿并重新校验”。自动切换到新版本，保留整批其他布局；越界或 IK 失败的草稿可以继续修改，但不能发布。
5. 对修改过的布局重新生成全场景预览，检查整体分布。点击“发布所选版本供下次采集使用”。发布只意味着选择了一个指导版本，不等于通过了真机任务验收。

重复上述步骤可修改多条布局。已支持鼠标拖动和旋转；暂不支持在线调节采样分箱权重、自动判定真实物体位置或自动审核。调整生成范围/分箱仍使用任务生成器；页面支持已有分布的逐条位置/朝向修正与剔除。新版默认 100 训练＋30 验证，剔除导致配额不足时会拒绝发布。详细操作和分布判读见 [鸟瞰与分组说明](DISTRIBUTION_SPLITS_AND_BIRDVIEW.md)。

全场景预览里的初始机械臂姿态来自历史演示首帧；接触姿态来自该布局的 IK，不是实时机械臂姿态。视角遮挡会如实显示；可见像素很少时报告提示。Xtrainer 包络根据显式尺寸保守计算，只辅助看分布，精确可见外观以全场景图为准。

当前仍只有关键点可达校验，没有机械臂/环境全路径碰撞检测，也没有击球/进球物理验收。管理员应结合画面和数值判断，不能把单张可达姿态当作一条可执行路径。

## 采集员流程

原 `collect.sh` 选项 2 启动节点；选项 3 默认检查最新已发布指导，选项 5 保留为同一入口。原录制按钮、前后切换、首帧事后核对方式不变。

- 自动匹配平台 + 任务 + 工位 + 相机标定哈希；只按发布次序选版，不按目录修改时间猜测。
- 草稿不参与；已停用、校验失败或发布后文件变化的版本会跳过并提示。派生版本的父包被停用或改变时同样拒绝。
- 找不到可用发布版本时，明确提示后使用普通数采。
- 启动时固定该会话版本，后来发布的新版本只对下次启动生效。录制开始时继续固定当前指引，图像和 manifest 在指导服务启动时载入内存，历史记录不会被新版本覆盖。
- `python -m scripts.run_guided_collection --no-guidance` 可显式普通数采；`--bundle /path` 可显式选择经过同一任务/标定及几何校验的包。
- `--check-only` 只检查选择与启动路径，不启动硬件。

## 工位配置和持久化

`configs/manager.local.json`（不提交 Git）定义 station_id、platform、task_id、calibration_bundle、registry、utility_python 和 scene。参考 `configs/manager.example.json`。`calibration_bundle` 是已接受相机参数的本机参考包，不能为了加载异地包随意替换。

- 目录：`runs/collection_manager`。
- 编辑版本：该目录下 `drafts/<uuid>/`；原图和原 manifest 不覆盖。
- 任务与失败日志：`jobs/<uuid>/`。
- Isaac 场景、图片、运行版本、姿态和哈希报告：`previews/`。
- 发布记录：`runs/guidance_releases/*.json`，包含 manifest 与全部投影哈希。

不要手工移动/删除已发布源目录及其父版本；不可变来源用于完整性校验。不会自动把模拟审核结果导入真实轨迹统计。新版发布记录不上传云端、不回写飞书。

YAM 编辑器可使用 `station` 与 `model` 配置调用现有 DrawerPlanner 重算桌面 u/v/yaw、几何和位置代理 IK，但本机没有接入 YAM 全场景构建与采集启动器的默认选版。因此本次可实际使用的编辑→全场景→发布→自动采集闭环是 Xtrainer；YAM 不会加载 Xtrainer 的模型或标定。

## 验证入口

```bash
.runtime/manager/bin/python -m unittest discover -s tests -p test_management.py -v
.runtime/manager/bin/python -m unittest discover -s tests -p test_manager_workflows.py -v
.runtime/manager/bin/python -m unittest discover -s tests -p test_releases.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_manager_revisions.py -v
PYTHONPATH=src .venv/bin/python scripts/check_collector_integration.py
```

离线测试不启动机器人/相机。全场景渲染报告的 physics_stepped=false，不据此宣称接触仿真通过。

## 2026-09-23 初始 36 条版本验证记录（新 130 条版本见上述说明）

- 管理器原有 8 项、版本选择 5 项、工作流边界 2 项、实际布局微调 3 项、会话文件固定 1 项测试通过，共 19 项。
- 浏览器完成错误布局拒绝发布、剔除生成 35 条新版本、有效移动 1 mm 后重新渲染和发布、最新版本读取；无 JS 错误。测试发布目录与正式目录隔离。报告：`runs/manager_acceptance_20260923/report.json`。
- 36 条原布局各生成主相机、鸟瞰、IK 接触姿态，共 108 张 Isaac Sim 5.1.0.0 图片；报告 `runs/collection_manager/full_scene_batch_report.json`。7 条存在至少一个视角的可见像素不足提示，真实遮挡保留；不是布局丢失或视野/碰撞验收通过。
- Xtrainer 历史三相机连续三条录制、开始时投影固定、前后切换、腕相机来源、过期画面拒绝等离线联调通过。
- 原有36条作为初始发布版本登记，未改变布局/标定。最新选版与 `--no-guidance` 的 `--check-only` 均通过。
- 未启动机械臂/相机；未进行真机可达性或无碰撞路径验收。未经 YAM 工位测试的能力不视为已部署到 YAM。

发布记录引用本机不可变路径；迁移到另一电脑应重新导入并按该工位标定发布，不能直接复制路径失效的发布记录。

# SimFoundry 与本项目的关系

核查日期：2026-09-14。参考仓库 https://github.com/NVlabs/SimFoundry ，本地只读检出提交 `9e34ebefcd020583fbb755a8b57268dce78eca26`，位于 `external/SimFoundry`。此次为 README、论文和关键源码阅读，未安装模型、调用云服务或验证其完整运行。

SimFoundry 主要自动生成可用于策略训练和评估的场景，并扩展为不同物体、布局及任务的 digital cousins。本项目当前主要验证已有真机遥操数据在相同机器人、相机和操作物条件下能否产生一致的视觉及接触结果。场景重建高度相关，验收目标有所不同。

## 实现链路

A 重建：视频/双目输入 → 深度与相机信息 → 代表帧和支撑平面 → 物体分割及逐物体移除补全 → 物体图片增强 → 图生 3D → 尺度/位姿配准 → 可选关节结构 → 碰撞与物理属性 → 稳定化 → USD / OmniGibson 场景。

当前默认图生 3D 为 Hunyuan3D-2.1，另有 TRELLIS.2/Pixal3D 后端。姿态阶段读取深度后端的 K，进行尺度和点云配准，再支持 FoundationPose 精化。基础 RGB 视频路径并非消费本项目的手眼标定和双臂关节数据。

B 增强：生成保持操作功能的物体变体、场景布局变体及任务提议。

C 应用：场景加载、随机动作 smoke test、策略评估、遥操等。仓库已有部分 demo/waypoint/replay 脚本，但 README 仍明确指出论文完整数据生成/训练流程尚未发布，不能据目录名声称全部可复现。

## 与接触真实性有关的源码事实

- `scripts/pipeline/A_reconstruction/stages/11_make_objects_sim_ready.py` 的 `import_rigid_scene_object` 把对象图片、包围盒、体积发给 VLM，解析质量和摩擦；碰撞网格默认 CoACD。这是参数先验估计，不能当成由真实受力/滑动实验辨识出的参数。
- `12_stabilize_physics.py` 在 PyBullet 中处理初始稳定性，并有每步清零速度的去穿透/稳定化逻辑。该处理用于场景初始化，不能搬进我们的真实抓取时间步。
- `scripts/cfg/real2sim_cfg.yaml` 中 `s14_og.robot_config.grasping_mode` 和 `s17_generate.grasping_type` 为 `assisted`；`5_generate_demos.py` 也有 assisted 默认回退，并支持 physical 覆盖。不能把这些默认抓取结果视为纯摩擦抓取；也不能由默认配置推断论文所有实验都用了辅助抓取。
- OmniGibson 官方说明中 assisted 会在满足接触/手指范围条件后增加抓取约束，physical 则依赖接触摩擦和手指力。

## 对本项目的建议（工程判断，尚未集成）

2026-09-15 实施更新：已自主实现飞书图片下载、显式米制配方、7 件候选资产编译、21 次 PhysX 静置和 RTX 三视图复核，见 `docs/MATERIAL_RECONSTRUCTION_BATCH.md`。这体现了部分分阶段资产处理方法，并不代表已接入 SimFoundry 代码。其分割、图生三维、深度配准和 CoACD 后端仍未执行。

1. 将 SimFoundry 作为可替换的场景/资产生成前端，保留我们的平台数据适配、已知相机标定和机器人模型。
2. 先使用静态场景采集或任务开始前静止视图，不将有移动杯子和机器人遮挡的整段遥操视频直接当静态扫描。
3. 生成式模型适合外观和复杂道具候选；对套杯任务的杯口、内腔、锥度、壁厚和 PLA 夹持面，继续以测量与 CAD 为几何约束。
4. 使用分割、深度/点云、位姿估计改善初始场景；检查其尺度和世界坐标到机器人基座的变换。
5. 保留现有自由杯子 + 动态有限力夹爪，不引入 assisted 抓取作为物理验收。通过真实标定、接触对照和留出片段拟合/验证物理参数。
6. 数字孪生基线可靠后，再接入 digital cousins，扩展物体、布局和任务以进行训练/泛化评估。

## 主要来源

- [项目 README](https://github.com/NVlabs/SimFoundry)
- [流水线及采集要求](https://github.com/NVlabs/SimFoundry/blob/main/scripts/pipeline/README.md)
- [论文](https://arxiv.org/html/2606.28276)
- [物理资产编译](https://github.com/NVlabs/SimFoundry/blob/main/scripts/pipeline/A_reconstruction/stages/11_make_objects_sim_ready.py)
- [稳定化](https://github.com/NVlabs/SimFoundry/blob/main/scripts/pipeline/A_reconstruction/stages/12_stabilize_physics.py)
- [默认配置](https://github.com/NVlabs/SimFoundry/blob/main/scripts/cfg/real2sim_cfg.yaml)
- [OmniGibson 抓取模式](https://behavior.stanford.edu/getting_started/examples.html)

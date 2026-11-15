# 架构与数据契约（草案 v0.1）

```text
平台原始数据 ── DatasetAdapter ──> Episode
URDF/其他模型 ─ RobotAdapter ───> RobotDescription
标定/安装关系 ─ FrameGraph ─────> CameraRig
关键帧/尺寸 ─── AssetBuilder ───> AssetPackage
                                      │
                         SceneSpec + 时间轴
                                      │
                       Backend（选定后实现）
                                      │
                  Replay → Render → Evaluate → RunReport
```

## 扩展边界

- DatasetAdapter：转换一种数据格式；不负责仿真，也不把 action 当 state。
- RobotAdapter：描述任意数量的机器人、关节组、末端、夹爪、移动基座、模型帧对应关系。
- CameraRig：任意数量的固定/随动相机，以 frame graph 绑定；不在核心写死 top/left/right。
- AssetBuilder：参数化几何、导入网格或后续重建工具，统一输出视觉/碰撞/物理描述。
- Backend：导入场景、设状态、施加控制、步进和渲染；必须声明能力与不支持项。
- Evaluator：消费统一图像、轨迹、遮罩、深度与可见性，不读取引擎私有对象。

新增平台需要 dataset adapter + robot profile + camera rig；任务资产与评估器复用。
机器人运动学的简单近似可用于占位验证，不能冒充完整 URDF 或真实碰撞几何。

## 统一约定

- SI：长度 m、时间 s、旋转 rad；normalized gripper command 不属于 rad。
- 右手坐标系；`T_parent_child` 是 child → parent 的 4×4 齐次变换。
- 存储姿态优先矩阵；使用四元数时固定 `xyzw`，引擎适配器负责转换。
- 相机光学系：x 向右、y 向下、z 向前；引擎 camera frame 必须显式转换。
- 世界坐标系由场景配置选择，首版可选左基座但不能写死到核心；标明重力方向。
- 每个变换记录 parent、child、来源、单位、时间有效期、质量状态和可选协方差。
- 先检查旋转正交、det=+1、逆变换和坐标图闭环；图中不允许矛盾的重复父边。

Xtrainer 示例：

```text
world = left_base
T_leftbase_rightbase = T_leftbase_top @ inv(T_rightbase_top)
T_world_wrist(t) = T_world_base @ FK(q(t)) @ T_flange_wrist
```

两份 top 外参是同一个物理相机的两次测量，不能在场景里生成两个 top 相机。

## Episode

建议目录：manifest.json + states.npz + actions.npz + streams/ + provenance.json。
规范化存储不使用 pickle；原始 Xtrainer pickle 只在受信任的本地数据适配层读取。

- schema_version、episode_id、task_id、robot_profile、源文件哈希。
- 状态：timestamp_s、joint_names、position、可选 velocity/effort/base_pose；每列有 unit/source/valid mask。
- 动作：独立时间轴、命名通道、控制模式、单位、目标语义与有效性，不默认与状态同一采样时刻。
- 每路图像：frame_id、文件、采集时间/估计时间、clock domain、与状态的关联及同步不确定度。
- 无时间戳时保留 ordinal frame_id；如假设 FPS，记录 assumed_fps，输出时间明确标记 inferred。
- camera：分辨率、K、畸变模型/系数、父连杆、安装外参、flip/crop/resize 历史。
- object track：object_id、时间、T_world_object、置信度、可见度、来源（实测/跟踪/人工/仿真）。

关节按名字映射，索引仅存在于源适配器。夹爪命令、实测开度与物理指关节分别保存。
重采样需要 unwrap 连续关节、旋转插值、缺口处理；固定物理步长与渲染采样时间解耦。

## RobotDescription / SceneSpec

机器人：原始模型 URI/hash/格式、visual/collision/inertial、命名关节、控制器参数、夹爪映射、模型帧与标定帧差异。
模型 URI 暂缺时用 null，不伪造文件路径或关节名匹配成功。
场景：世界坐标系、机器人实例与基座位姿、相机、对象实例、桌面、灯光、重力。
资产的模型坐标原点与任务抓取点/支撑面分开定义。

保留引擎中立描述作为主记录；USD/MJCF 是后端产物，转换会记录损失项，不能承诺 URDF 无损转换。
复杂闭链、mimic joints、软体、驱动器和材质需要后端能力检查。

## 产物与可复现性

每次 run 保存：配置快照、数据/模型/标定/资产哈希、软件版本、种子、后端设置、原始先验和优化 delta、
回放模式、估计/强制轨迹标记、校准/评估帧列表、视频与指标、失败原因。
状态分为 missing / unvalidated / provisional / accepted，不以文件存在代替验收通过。

## 建议实现布局

`core/`、`adapters/datasets/xtrainer.py`、`adapters/robots/`、`backends/`、`assets/`、`reconstruction/`、`evaluation/`。
现在仅提供 `src/real2sim/contracts.py` 协议草案；接口会在 M1/M2 中验证，不代表功能已实现。

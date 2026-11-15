# 官方 X-Trainer 环境复用评估

2026-09-13 阅读官方仓库并检查本地源码；没有启动仿真。

- 源码：[embodied-dobot/x-trainer](https://github.com/embodied-dobot/x-trainer)，提交 `5862c3ba4997ae0d4c41f69c73981353af3a8346`。
- 运行框架：Isaac Lab / LeIsaac，依赖 Isaac Sim。README 报告验证过 Isaac Sim 4.5，不代表当前机器已验证。
- 源码 LICENSE 为 Apache-2.0；USD 资产独立分发于 [Hugging Face](https://huggingface.co/dstx123/xtrainer-leisaac/tree/main)。
- 本次 Hugging Face 元数据请求连接失败，资产版本、大小、许可及所有引用依赖尚未核实，也未下载。

## 可以复用的内容

| 源码位置（相对官方仓库） | 已核对内容 | real2sim 中的用途 |
| --- | --- | --- |
| source/leisaac/leisaac/assets/robots/xtrainer.py | USD articulation、关节组、执行器配置 | 首个平台机器人资产入口 |
| source/leisaac/leisaac/tasks/template/xtrainer_arm_env_cfg.py | 左右腕与 top 相机、任务场景模板 | 新建本地叠杯任务配置，覆盖标定和布局 |
| source/leisaac/leisaac/devices/lerobot/xtrainer_leader.py | 14 通道到双臂 16 关节的候选映射 | 参考命名/夹爪语义，不连接主手 |
| scripts/environments/teleoperation/replay.py | HDF5 数据回放、episode 初始化 | 参考加载与控制循环；本地 pickle/JPG 仍需适配 |
| source/leisaac/leisaac/utils/constant.py | 命名关节、LEISAAC_ASSETS_ROOT | 固定资产根目录，避免运行目录影响路径 |

期望资产路径为 `$LEISAAC_ASSETS_ROOT/robots/x_trainer.usd`。仓库 README 提到 URDF 导入，但当前运行配置实际读取 USD；不能假设已经拿到可独立复用的 URDF。
先验证这份 USD 和依赖能完整加载；本地 STEP 作为硬件版本差异核对、修补夹爪与外壳的备用来源。

## 必须适配，不能照搬的部分

1. **相机**：上游模板使用固定 offset、焦距/孔径和 30 Hz，不能代替本机实测 K、畸变、外参及数据时间轴。左右腕为 D405，top 为 D435i，各用各自标定。
2. **模型坐标**：源码中的 J1_6/J2_6 连杆不自动等于本机控制器法兰。要建立 `T_model_link_flange`，再接腕部标定；先验证零位与多姿态 FK。
3. **基座间距与安装**：官方装配可能是另一版本。用本机标定与 CAD 尺寸检查基座布局，不能用微调相机掩盖机器人安装差异。
4. **关节**：本地状态 14 列，官方模型 12 个臂关节 + 4 个指关节共 16 个。按名字映射，核对零位、符号、范围和夹爪宽度；不能直接将 14 维数组塞入引擎。
5. **夹爪**：上游 leader 用 `-(1-g)` / `1-g` 生成两个归一化通道，再经后续映射转换到物理关节；不能将该中间值直接当米制指位移。上游限位约 ±0.04 的定义还需与实际 USD 和本机开合方向核对。
6. **回放语义**：上游 `replay_mode=state` 从记录取机器人 joint_position 后，仍送入 `env.step(actions)`。它不是逐帧把所有机器人/道具状态强制设定。因此本项目的 visual_state 模式需单独实现状态写入与渲染。
7. **任务**：上游有 PickCube/PickOrange；本项目要新建 StackCups，并补充真实杯子、桌面、轨迹和任务事件。
8. **物理**：现成 stiffness/damping/effort limit 是仿真配置，不是本机辨识结果；先服务于视觉/轨迹试验，再独立验证接触。

## 首版路径

官方源码与 USD → 离线场景加载 → 关节/FK 验证 → 替换三路标定 → 导入一段标准化真机数据 → 空心杯资产与布局 → 三路状态回放与留出评估。

优先做 Isaac Lab 小规模试验，用户最终选型仍待定。若硬件或版本成本不合适，再启用 MuJoCo 备选，并专门预算资产/关节转换工作。
不为了抽象而重写官方框架；在本项目 backend 和 robot adapter 中包裹上游依赖。其他机器人不必依赖该仓库。

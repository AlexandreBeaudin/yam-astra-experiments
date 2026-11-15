# Xtrainer 首个平台盘点

核对日期：2026-09-13。以下是当前父项目的证据，不是通用平台假设。

| 输入 | 已确认 | 缺口/处理 |
| --- | --- | --- |
| 遥操数据 | `datasets/.../collect_data/<episode>/{topImg,leftImg,rightImg,observation}` | JPG 和 pickle 按数字编号；需解读可信本地数据并审计重复、缺失和采样 |
| 动作与状态 | `format_obs.save_frame` 将 action 写成 `control`；状态包含 joint_positions 等 | 两者语义不同；禁止把 control 当实测轨迹 |
| 同步 | 图像从相机线程的最新缓存写入；编号对齐 | 当前保存路径未见显式传感器采集时间戳；采样率不能直接认定 30/60 Hz |
| 保存顺序 | run_control 先保存 obs/action，再 env.step | 需要核对状态、动作与相机的实际时序；可能存在延迟，不能直接宣称差一整帧 |
| 夹爪 | joint_positions 的列 6/13 被 action 对应列覆盖 | 属于命令派生，不能当实测开度；另有 gripper_position，需核对驱动来源/单位/可靠性 |
| 内参 | calibration/intrinsics/{top,left,right}.json | 原始流内参；top/right 180° 翻转，应用一次；保留像素处理历史 |
| top ←→ 左基座 | 20260912154818 compact，4.26 mm / 0.363°，stable=true | 前后半段差 9.38 mm，通过内部阈值不等于绝对精度认证 |
| top ←→ 右基座 | 20260912161728 compact，8.32 mm / 0.718°，stable=false | 前后半段差 21.04 mm；作为 provisional 先验，M0/M1 优先验证 |
| 腕相机 | 20260912175145_wrist_cameras_fixed.json | 使用 fixed 版本；与 top 外参有关联，需独立投影验证 |
| 机器人 FK | calibration/fk.py、控制器 FK 缓存可用 | 不等于完整机器人视觉/碰撞/惯性模型 |
| 仿真模型 | 官方 embodied-dobot/x-trainer 已有 Isaac Lab / LeIsaac 环境，运行配置引用 robots/x_trainer.usd | 源码已固定提交；USD 另从 Hugging Face 获取，未下载，故本地模型路径仍为 null |
| CAD 备用 | 用户指定 Assets/2D尺寸图 & 3D模型，含从臂+夹爪+相机及平台 STEP | 标准 STEP 可用于尺寸核对和几何修补；不是带关节的 URDF |
| GPU | 本环境 nvidia-smi 无法连接驱动 | 待选择实际执行仿真的机器并验证兼容性 |

## 首条样板数据

`datasets/dataset_hejialong_stack_cups_20260912/collect_data/20260912135045`

用户指定的 collect_data 共 100 段、36,118 条 observation 文件；各段三路 JPG 数量与观测数量一致（只代表数量一致，不代表硬件同步或图像内容有效）。
样板 episode 有 410 条观测及各 410 张图片；抽查第 200 帧 pickle 包含 joint_positions[14]、joint_velocities[14]、ee_pos_quat[14]、gripper_position[2]、control[14]，无顶层时间戳。
盘点输出保存于 runs/collection_inventory.json 与 runs/cad_inventory.json。

## CAD 内容检查

`../Assets/2D尺寸图 & 3D模型` 相对父项目根目录；文件仅检查文本结构，尚未用 CAD 内核加载。

- 从手 nova2+夹爪+相机：标准 STEP P21，约 40 MB，128 条装配使用记录、60 个 MANIFOLD_SOLID_BREP 声明，包含毫米单位。
- 轻量版完整平台：标准 STEP，约 118 MB；不含臂平台约 55 MB。
- 夹爪手指：标准 STEP，约 216 KB，包含毫米单位。
- nova2_asm.stp、左右主手 stp：起始为 `%TSD-Header-###%`，不是普通明文 STEP 文件头。原因未确认，不能声称直接可解析。
- 还有增高底座/主手尺寸 PDF。实体与装配记录数量不等于可直接驱动的机器人 link 数量。

优先官方 USD；若需 CAD 补充，则执行：装配解析 → mm 转 m → 按刚性连杆分组 → 建立关节/零位/轴 → 网格化视觉与碰撞 → 多姿态 FK 验证。无需在第一版重建整套机器人。

现有投影验证已使用过此 episode，便于对照；原始三视角质量、任务完成情况与物体身份仍需 M0 审核。

## 源数据索引（仅 Xtrainer adapter 使用）

- 左臂：joint_positions[0:6]，rad。
- 右臂：joint_positions[7:13]，rad。
- 列 6/13：夹爪命令派生值，独立通道，不能加入机器人转动关节数组。
- 与导入模型的真实 joint name 对应关系待模型到位后填写；不得只按 URDF 文件顺序猜。

源代码证据：`scripts/format_obs.py`、`experiments/run_control.py` 保存与 env.step 附近、
`dobot_control/env.py:get_obs`、`calibration/dataset.py`、`calibration/intrinsics.py`。

## 后续采集建议

新增数据保留相机曝光/采集时间、机器人状态时间、动作下发时间与 clock domain；可选记录 RGB-D。
记录实测夹爪状态及单位，保留相机流配置/序列号、安装版本、模型和标定哈希。
本次只规划这些改进，不修改原采集程序。

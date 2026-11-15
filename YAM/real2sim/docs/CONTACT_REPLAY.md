# 接触抓取原型

本轮新增 `runs/stack_cups_contact/`，原有视觉动画保留在 `runs/stack_cups/`。两类产物不能混为物理验证。

## 数据怎样进入物理引擎

1. 从原始片段读取实测双臂关节角，转换为官方模型约定，按假定 24 Hz 时间轴在 240 Hz 物理子步中插值。
2. 双臂连杆与法兰按运动学轨迹移动。当前未建立力矩驱动的完整机械臂动力学，也未启用臂连杆碰撞。
3. 每个手指是独立动态刚体，通过移动副连接到运动学法兰。夹爪命令映射为移动副目标，弹簧阻尼驱动具有最大推力，接触阻力会阻止手指达到目标。
4. 三只杯子仅初始化位姿，之后由 PhysX 计算重力、碰撞、摩擦和惯性。没有杯子目标轨迹，没有抓取/释放事件表，没有杯子到法兰的固定关节。
5. 保存 PhysX 求解得到的杯子与手指状态，再离线渲染三路相机。渲染阶段读取轨迹是为了展示求解结果；该轨迹不参与物理求解输入。

## 改进夹爪与物理参数

使用用户提供的 `xtrainer夹爪底板-改进版.stl` 和暂选的 `夹具.stl`。两个夹具的外包尺寸一致，仅安装区域局部圆柱面半径相差约 0.1 mm；不能据此确认实装版本。原始 STL 按毫米解释；装配变换由定位特征间距与官方手指坐标推估，尚未实测确认。替换视觉几何，同时加入底板凸包与夹持件凸分解碰撞几何。

| 参数 | 当前值 | 来源 |
|---|---:|---|
| 杯子质量 | 8 g | 假设 |
| 单个移动手指质量 | 80 g | 假设 |
| 静/动摩擦系数 | 0.8 / 0.6 | 假设，另有零摩擦测试 |
| 单手指最大驱动力 | 15 N | 假设 |
| 驱动刚度 | 1500 N/m | 假设 |
| 驱动阻尼 | 10 N·s/m | 假设 |
| 单手指闭合行程 | 40 mm | 上游映射候选 |
| 杯子高度、口径、底径 | 80 / 75 / 52 mm | 图像估计 |
| 物理步频 | 240 Hz | 数值设置，另测 480 Hz |
| 采集帧率 | 24 Hz | 未确认时间戳，暂定 |

杯子和夹持件均按刚体模拟，不包含纸杯压溃、TPU/泡棉弹性或形变。这个模型可以检查接触过程，但不是已辨识的真机物理模型。

## 验证与结果入口

- `nominal/review/index.html`：原始遥操状态驱动的接触回放，对比真实图像。
- `grasp_bench_nominal/review/index.html`：合成居中夹取、抬升、张开测试；机械臂动作不是遥操数据。
- `contact_evaluation.json`：实际高度、接触、禁用碰撞对照、时间步长与零摩擦测试。
- 每个运行目录中的 `physics_input.usda`：物理求解输入，不含杯子位置动画。
- `physics_tracks.npz`：物理引擎输出，包含杯子和动态手指位姿。
- `contacts.json`：接触事件及冲量证据。
- `effective_config.json`、`physics_report.json`：实际参数与来源哈希。

应区分“被接触抬起”“稳定保持”“成功释放”“完成叠杯”。完整遥操任务尚未通过验收，合成测试中的释放也可能发生卡杯；任何单次抬升都不能直接标为任务成功。动态刚体的休眠设置对早期结果产生了明显影响，最终测试关闭休眠，以持续检查接触；当前仍需要更广泛的数值敏感性验证。

## 重建

```bash
cd /mnt/sdb/rhos_collector/real2sim
.venv/bin/python scripts/build_cup_asset.py
.venv/bin/python scripts/build_contact_scene.py
bash scripts/isaac_python.sh scripts/run_contact_replay.py --frames 410
bash scripts/isaac_python.sh scripts/run_contact_replay.py --variant no_finger_contact --frames 410
bash scripts/isaac_python.sh scripts/run_contact_replay.py --scenario grasp_bench --frames 180
bash scripts/isaac_python.sh scripts/run_contact_replay.py --scenario grasp_bench --physics-hz 480 --frames 180
bash scripts/isaac_python.sh scripts/run_contact_replay.py --scenario grasp_bench --variant zero_friction --frames 180
.venv/bin/python scripts/evaluate_contact.py
.venv/bin/python scripts/bake_contact_result.py --variant nominal
bash scripts/isaac_python.sh scripts/render_isaac.py --run runs/stack_cups_contact/nominal --stride 4
.venv/bin/python scripts/build_contact_review.py --variant nominal
.venv/bin/python scripts/bake_contact_result.py --variant grasp_bench_nominal
bash scripts/isaac_python.sh scripts/render_isaac.py --run runs/stack_cups_contact/grasp_bench_nominal --stride 3
.venv/bin/python scripts/build_contact_review.py --variant grasp_bench_nominal
```

仍使用独立虚拟环境，下载绕过系统代理。整个流程离线执行，不连接真机。

## 后续收敛顺序

夹持件材料已由用户确认为 PLA（见 configs/xtrainer_contact_materials.json）。用户于 2026-09-15 确认：改进底板和夹爪在 9 月 12 日叠杯数据采集及相机标定时均已安装，硬件配置时间对应关系已确认（见 configs/xtrainer_hardware_provenance.json）。仍需确认具体夹具型号、实际安装位姿和最大开度，以及杯子尺寸和材质。此确认不改变右臂外参精度待验证的结论。然后确认时间轴，修正初始场景与镜头畸变，辨识驱动和接触参数；最后再推进完整臂动力学、柔顺夹持和成功叠杯。禁止通过杯子轨迹绑定掩盖失败。

实现参考：[Omni Physics 刚体与运动学驱动](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/104.0/source/extensions/omni.physx/docs/index.html)、[Isaac Sim 4.5 物理步进](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/physics/simulation_fundamentals.html)。

## 本轮最终结果

最终采用关闭刚体休眠的配置，保留全部接触事件：

- 遥操片段：右侧杯子最大抬升约 196.5 mm；杯子离初始高度超过 30 mm 且双指均报告接触的最长连续时间为 4 秒。随后放置发生碰撞、杯子倒下，未完成叠杯；左侧杯子本次只短暂移动。
- 禁用夹爪碰撞、保持其他命令不变：两只目标杯子均没有抬升。
- 合成居中测试：240 Hz 和 480 Hz 均能抬升并保持约 100 mm，两次峰值相差约 1.36 mm；张开后仍发生卡杯，因此完整释放检查均失败。
- 合成测试将摩擦设为零：杯子没有抬升。这支持本轮抬升依赖物理接触/摩擦，而非绑定动画。

这些结果替代早期调试运行的高度与成败记录。抬升/保持检查通过不等于完整抓放通过；最终结果以 `contact_evaluation.json` 为准。

## 插入失败定位补充

当前物理回放第一次杯间接触时，杯底中心相对接收杯轴线偏离约 22 mm，杯轴倾斜约 33°；第 139 帧仍有约 33 mm 偏移及 26° 倾角。按当前估计几何，竖直同轴插入时杯口内半径 36.3 mm、杯底外半径 26 mm，仅有约 10.3 mm 单侧余量。第 145 帧接收杯已倾斜约 58°。直接失败表现为偏心、倾斜接触后推倒接收杯，而不是已经对准后单纯摩擦过大。更上游的误差来源仍包括装配、初始布局、外参、杯尺寸和持杯接触姿态，不能仅凭一次回放唯一归因。细节见 nominal/insertion_diagnosis.json。历史运行摩擦系数未因确认 PLA 而擅自更改。

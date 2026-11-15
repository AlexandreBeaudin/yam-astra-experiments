# 实现记录与运行方法

项目已迁到 `/mnt/sdb/rhos_collector/real2sim`，Xtrainer 原目录中不再保留项目或符号链接。
平台源数据通过 `--source-root` 传入，机器人来源与关节约定独立配置。

## 已实现

- `xtrainer.py`：可信本地 pickle → NPZ/manifest，分离实测臂关节、动作和命令派生夹爪列。
- `usd_robot.py`：从官方 USD 读取通用关节图并计算运动学。
- `identify_joints.py`：用合成姿态辨识模型坐标约定；不使用图像拟合机器人。
- `scene.py`：通用材质、空心杯、几何体及完整内参的 USD 相机。
- `build_scene.py`：将平台数据和任务配方组合成 410 帧动画场景。
- `render_isaac.py`：Isaac Sim RTX 三视角视觉状态回放。
- `validate_scene.py`：独立随机姿态 FK、相机投影和场景帧完整性检查。
- `build_review.py`：真实渲染产物 → 可拖动的本地页面、叠加图与视频。

上游关节同名不等于直接可映射：左/右分别辨识符号和零位，结果在 `configs/xtrainer_usd_conventions.json`。
这是模型坐标约定转换，不是根据真机图像修正轨迹；100 个新的随机姿态另作验证。

## 独立环境

数据准备、离线 USD 构建与指导服务使用工具环境 `.venv/bin/python`。仿真/渲染统一使用独立 Python 3.11 / Isaac Sim 5.1.0 环境 `.runtime/isaacsim-5.1`，入口为 `bash scripts/isaac_python.sh`，不依赖激活 Xtrainer 的 conda 环境。
本机实测 GPU 是 RTX 4090，驱动 580.178.04。工具依赖见 `requirements.txt`；仿真依赖见 `requirements-isaacsim.txt` 和完整 Linux 锁文件。详见 [5.1 迁移说明](ISAACSIM_5_1_MIGRATION.md)。

网络命令使用 `bash scripts/no_proxy.sh <command...>` 清除系统代理；Python 使用清华镜像，
Hugging Face 资源脚本使用 hf-mirror。Isaac 专有依赖可通过 NVIDIA 索引直连；首次启动由 Kit
从供应商扩展源直连补全运行时缓存，目前没有为该扩展源配置第三方镜像。

```bash
cd /mnt/sdb/rhos_collector/real2sim
bash scripts/no_proxy.sh .venv/bin/python -m pip install -r requirements.txt
bash scripts/install_isaac.sh
.venv/bin/python scripts/fetch_robot_assets.py
```

## 重建验收产物

```bash
cd /mnt/sdb/rhos_collector/real2sim
.venv/bin/python scripts/prepare_episode.py --source-root /mnt/sdb/rhos_collector/dobot_xtrainer20250912/dobot_xtrainer
.venv/bin/python scripts/build_scene.py --source-root /mnt/sdb/rhos_collector/dobot_xtrainer20250912/dobot_xtrainer
.venv/bin/python scripts/validate_scene.py
bash scripts/isaac_python.sh scripts/render_isaac.py --stride 4
.venv/bin/python scripts/evaluate_landmarks.py
.venv/bin/python scripts/build_review.py
```

输出：`runs/stack_cups/review/index.html`、`comparison.mp4`、三路原始仿真 PNG、场景 USD、
原始数据 manifest、模型对齐/实现验证报告。页面和视频仅在渲染成功后生成。

## 本轮验收范围

视觉回放原型：同一个 3D 场景、真实关节轨迹、标定相机、可复现杯子资产与多视角对比。
道具初始几何、持杯/落杯轨迹、持杯夹爪开度是估计值，必须在报告中保留标记。
不能把运动学误差很小当成视觉或物理精度已经通过；真实图像上的轮廓、尺寸、时间对齐仍需评估。

本机存在两个重复的 NVIDIA Vulkan ICD 描述文件，运行脚本仅在子进程中固定一个 ICD，未修改系统驱动。首次 Kit 扩展下载与 shader 编译较慢，后续完整 104 帧 × 3 视角渲染约 53 秒（本机一次实测）。腕部相机近裁剪设为 30 mm，以排除 CAD 中没有镜头开孔的相机外壳；这是一项可见性近似。

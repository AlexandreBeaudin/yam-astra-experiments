> 历史记录：当前参数已由用户确认并固定，见 [当前 YAM 标定](YAM_CALIBRATION_CURRENT.md)。旧 runs 优化目录已清理。

# YAM 首次现场标定

用户已确认 YAM 尚未标定。当前软件迁移完成了接入口；以下实测完成前，不生成或启用声称已校准的真实摆放投影。

## 采集前准备

确认实物 ChArUco 板的横纵格数、方格边长、marker 边长、字典与 legacyPattern，记录单位为米。通用检测器保留的 Xtrainer 默认值只是旧入口兼容，不能替代核对 YAM 使用的实际标定板。

记录主、左、右相机序列号、分辨率、图像保存方向，以及当前夹爪/工具安装方式。相机内参应按这一次录制的实际流配置读取并保存；现有 MCAP 样例仅含相机设置，没有足以完成本次标定的内参/外参。

## 建议的独立数据批次

保持相机、机械臂基座和桌面位置固定。沿用原数采的启动、使能、双扳机开始、结束和成功/失败保存流程。不要启用尚未标定的 guidance-bundle。

```bash
cd /home/chenyuan/workspace/YAM/gello_software
scripts/teleop --recording-format mcap --control-hz 200 --state-hz 200 --task-name calib_left_main
```

- `calib_left_main`：板刚性固定在左末端，主相机观察；采 20–30 个可清晰识别板的不同位置/姿态，尤其覆盖多个旋转轴。每个姿态停稳约 1 秒。另保留 5–10 个独立姿态验证。
- `calib_right_main`：板刚性固定在右末端，按相同方法采集，用于估计右基座与主相机关系。换装后板到工具的变换重新估计，不假定与左臂相同。
- 腕部相机若固定在运动末端：另做 eye-in-hand 数据，板固定在场景内，移动对应腕相机并覆盖不同姿态。不能直接拿固定相机 eye-to-hand 结果当腕相机外参。
- 桌面：在左右基座关系与主相机外参确定后，测量/估计桌面平面、工作区边界，记录实际工具到接触点变换。机械模型 link6 不是已校准的夹爪接触点。

## 求解与检查

固定主相机的模型为 `base_T_tool[i] @ tool_T_board = base_T_camera @ camera_T_board[i]`。基座到工具来自 YAM 从臂实测关节与其 FK，不用主臂命令或 Nova2 的 DH 参数。

共享离线入口接受已经完成时间匹配和板检测的 NPZ：

```bash
/home/chenyuan/workspace/real2sim/.venv/bin/python \
  /home/chenyuan/workspace/real2sim/scripts/calibrate_transforms.py \
  --observations /path/to/yam_left_observations.npz \
  --output /path/to/yam_left_candidate.json
```

NPZ 必须含 `base_T_tool`、`camera_T_target`，形状均为 N×4×4，米制。这个命令不自动把任意 MCAP 变成标定输入：还需要根据实际板规格、流内参和同步数据完成板检测与姿态匹配。它不连接设备，也不发送机械臂动作。

由同一固定主相机分别标定得到 `left_base_T_camera` 与 `right_base_T_camera` 后，计算 `left_base_T_right_base = left_base_T_camera @ inverse(right_base_T_camera)`。应报告留出姿态误差、分半稳定性、旋转多样性及角点重投影误差，不仅看训练残差。

## 标定后验收

1. 更新本机 `configs/yam_station.local.json` 的相机内外参、双臂基座关系、工具和桌面信息，保留数据来源及误差报告。
2. 用独立的桌面参照点检查实物与投影重合。不得仅把 calibration_status 字符串改为 verified 来绕过验收。
3. 选定 YAM 任务及物料，生成其布局包，执行 YAM 运动学、视野与后续碰撞检查。不能复制 Xtrainer 的世界坐标或布局坐标。
4. 连续录制数条带 `/guidance/initial` 的 MCAP，导出 `guidance_overlay_first_frame.jpg` 核对起始摆放。

物理抓取/接触 replay 是独立验收项，投影对齐不意味着动力学已经通过。

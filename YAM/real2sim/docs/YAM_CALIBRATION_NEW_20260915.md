> 历史记录：当前参数已由用户确认并固定，见 [当前 YAM 标定](YAM_CALIBRATION_CURRENT.md)。旧 runs 优化目录已清理。

# YAM 新标定板五组数据：候选标定结果（2026-09-15）

已对 `_new` 五组新 MCAP 完成筛帧、四组手眼求解、独立姿态留出验证及桌面候选平面估计。**已有候选参数，但没有更新正式 station 为 verified，也没有开启机器人或实时投影。**

## 数据与配置

- 标定板：用户提供 8x15，checker 19 mm、marker 14 mm、ArUco 4x4。探测核验 OpenCV `(15,8)`、legacy=True、DICT_4X4_1000；98 个内角点。旧板与旧映射不复用。
- 新 session 的 left=can_follower_l、right=can_follower_r，关节运动范围和画面一致。此次不再交换通道。旧数据修复由另一个工作完成，不修改其未提交文件。
- 主相机 D435 408122072021；左 D405 260322274929；右 D405 260422272011。录制 640x480、30 Hz、rotation=0、H264，未录 depth。
- 内参来源：本轮查询相同序列号设备的 640x480/30Hz 工厂流配置，未启动流。不是录制时嵌入 MCAP 的快照；这是来源限制。相机代码实际请求 rgb8，保存方向已核对。
- 主相机 K: fx=604.5344, fy=604.1679, cx=313.1813, cy=250.9277，畸变系数全零。
- 左腕 fx=392.7629, fy=392.3184, cx=326.0964, cy=232.5528；右腕 fx=394.9420, fy=394.4251, cx=317.5007, cy=235.5693。完整 inverse_brown_conrady 系数见 `device_intrinsics.json`。使用 RealSense SDK 将像素反投影为归一化射线后做 PnP，再用 SDK 投回原图；未直接把 inverse 系数当 OpenCV forward 畸变。

RealSense 模型实现参考：[librealsense rs.cpp](https://github.com/realsenseai/librealsense/blob/master/src/rs.cpp)。当前安装 SDK 的投影/反投影往返也已抽查。

## 筛帧与验证

- 主相机两组前 12 秒递板准备画面排除；腕相机前 4 秒排除。时间线已浏览。之后仍按停稳和检测质量筛选。
- 约 10Hz 扫描；帧前后各 0.25s 的六关节最大跨度不超过 0.020rad，最近实测状态时间差不超过 20ms；至少 12 角点、600px²角点凸包及板区域清晰度检查。
- 连续合格片段中选一张，相近关节姿态去重，整组每第四个姿态留出。没有根据验证误差删除样本、调整关节零偏或拟合相机内参。
- FK 使用 YAM XML 的 joint1..6 与 link6。固定 main 求 `A Y = X B`，X 为 base_T_camera。腕相机求 `A C B = D`，通过对 B 取逆归约到同一求解器，C 为 link6_T_camera。腕相机相对世界的位置仍随关节变化。
- 四组手眼的 train/validation 为左 main 11/3、右 main 15/4、左 wrist 9/3、右 wrist 7/2。

| 组 | 留出姿态数 | 平移 RMSE（mm） | 旋转 RMSE（度） | 原图角点投影 RMSE（px） |
|---|---:|---:|---:|---:|
| 左 main | 3 | 3.33 | 0.56 | 3.86 |
| 右 main | 4 | 4.13 | 0.61 | 3.34 |
| 左 wrist | 3 | 1.92 | 0.29 | 2.45 |
| 右 wrist | 2 | 1.83 | 0.58 | 1.81 |

误差是基于相机板检测与机器人 FK 的一致性，不是外部测量得到的机械臂绝对精度。表中投影误差由训练求出的固定变换预测验证帧，不能与每帧重新 PnP 的约 0.1–0.5 px 残差混淆。

右 main 训练帧 30.622s / 87.364s 的投影 RMSE 分别约 14.69 / 11.04px，保留并标为复查对象，没有静默剔除。可能涉及板变形/夹持变化、平面姿态估计、运动学或时间配对，当前未证明具体根因。右 wrist 仅两个留出姿态，不能过度解释。

## 桌面

原时间筛选留下 11 张；同一摆放位置存在重复，桌面拟合前按相机坐标下板中心 3cm 聚类，得到 9 个位置、7 train / 2 validation，一位置一帧。桌面最终划分以 `solve_v1/table_plane_candidate.json` 和 `solve_v1/table_grouped/` 为准，覆盖初筛页面的临时时间划分；四组手眼的划分不变。

两个留出位置的板面到拟合平面的距离 RMSE 约 2.83 / 2.83mm。用户给出图案面高于桌面约 35mm，按拟合面法向向下平移 0.035m 得到桌面候选。

左基座坐标下，桌面候选方程为：

`[-0.01681085, 0.03623189, 0.99920200] dot p + 0.02766077 = 0`（p 单位米）。

桌面在左基座原点处的 z 截距约 -27.68mm，这不是整个桌面处处相同的高度。35mm 是近似测量，未测误差直接影响投影位置；视频中多处手接触板，因此不声明桌面独立现场验收完成。工作区边界、夹爪接触点仍未标定。

## 产物

根目录：`/home/chenyuan/workspace/real2sim/runs/yam_calibration_new_20260915`。

- `index.html`：总入口，含选帧、验证投影和桌面最终分组。
- `selection_v1/`：所有 65 张原始解码 PNG、角点预览、时间戳/帧号/关节值、排除审计。PNG 没有额外有损压缩；原 MCAP 为 H264。
- `solve_v1/summary.json`：四组候选变换、训练/验证指标与双基座变换。
- `solve_v1/calib_*_new.json`：逐帧 PnP 与手眼预测误差；同名目录内为投影对照，绿点为检测、红点为预测。
- `solve_v1/table_plane_candidate.json`：桌面最终空间分组、验证指标和约 35mm 厚度来源。
- `device_intrinsics.json`、`verification.txt`：内参来源与检查结果。

验证：65 张 PNG 与原 MCAP 解码像素逐张一致，时序组不跨手眼 train/validation，保护时间与阈值通过；合成 eye-in-hand 变换恢复及独立姿态预测通过 1e-6 检查。未启用正式 station。

## 重现

依次运行仓库中的 `scripts/prepare_yam_calibration_new.py`、`scripts/solve_yam_calibration_new.py`、`scripts/fit_yam_table.py`。选帧用 YAM gello_software/.venv 的解释器，求解和桌面用 workbench/.venv。参数详见各脚本 --help；本批次脚本显式包含新板和起始保护范围，不能不经配置套到其他站位。新选帧与求解目录必须不存在，防止覆盖旧结果。

## 下一步

先人工检查原图与预测对照、复测 35mm 厚度；对右 main 两张异常姿态排查板平整性和夹持，再补充独立验证姿态。随后做真实桌面参照点投影检查，确认误差满足任务要求，才更新站位标定状态并生成实时布局包。现在不把候选文件当成已经验收的数采指导配置。

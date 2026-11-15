> 历史记录：当前参数已由用户确认并固定，见 [当前 YAM 标定](YAM_CALIBRATION_CURRENT.md)。旧 runs 优化目录已清理。

# YAM 右臂重新固定板后的诊断（2026-09-15）

用户重新固定板并录制 `calib_right_main_verify`，随后明确确认主相机与右基座没有移动。本轮只读取这条新MCAP，不与旧批次共用板到夹爪变换。

## 数据与筛选

源 `/mnt/sdb/YAM/data/calib_right_main_verify/mcap/success/episode_0001_c40240a2732e4bd6888d97ecfd60e0e7.mcap`。约92秒，主相机/分辨率/旋转和修正后的右臂通道与上一批一致。板按已确认的15x8格、19mm/14mm、legacy配置检测。复用同序列号、同流配置的工厂内参快照，未重新标定内参。

时间线核查开头递板、结尾手接触板，保守仅允许12–84秒。沿用停稳/质量阈值，去除近重复姿态，留下15个姿态，12 train / 3 validation；PNG原图、角点、时间戳及排除原因均保留。

## 两种求解结果

新安装独立估计 `base_T_camera` 和 `link6_T_board`。先用原位姿残差求解，再以同一训练帧的原图角点进行固定内参重投影优化；soft_l1尺度1px，没有拟合关节零偏、没有给每帧自由的板安装变换、没有按验证误差删帧。

| 新批次指标 | 位姿求解 | 角点优化 |
|---|---:|---:|
| 训练投影RMSE px | 3.27 | 1.81 |
| 验证投影RMSE px | 3.54 | 2.43 |
| 验证最大单帧RMSE px | 4.28 | 3.05 |
| 验证平移一致性RMSE mm | 3.56 | 2.43 |
| 验证旋转一致性RMSE deg | 0.71 | 1.04 |

角点对齐改善，但旋转一致性误差增加，不能只报告有利指标。这3个姿态没有用于拟合，但在方法修改后被重复评估，并非额外采集的全新盲测。

此次选中帧没有旧批次11–15px的异常；新旧验证姿态不同，因此不能直接用两批RMSE差异证明重新固定板是唯一改善原因。

## 尚未解决的重复性

- 相机/基座未动，原位姿法新旧估计仍相差13.93mm、1.57度。用同一角点优化方法分别处理两批后仍差14.19mm、1.80度。优化损失不能消除该差异，不能将新结果自动升级为正式标定。
- 新批次仅删去一个训练姿态再求解，相机位置最多改变6.57mm。此为训练集敏感性检查，不是验证集调参。
- 去重时只找到一对足够接近的重复参考片段：17.413s与14.110s。最大关节差0.044rad，隐含板安装差约2.70mm/0.60度；这是含测量和模型误差的一致性诊断，不足以证明或排除物理滑动。
- 当前不能确认主因是板平整性/夹持、标定尺度、运动学或配对误差。新结果是投影审查候选，不更新正式站位或双基座关系。其他三组标定不变。

## 产物与使用

YAM目录：`/home/chenyuan/workspace/real2sim/runs/yam_right_remount_20260915`，打开 `index.html` 比较同一验证帧的两种预测。绿点是检测、红点是预测。

`selection_v1` 保存15张原始PNG和角点；`solve_v1/calib_right_main_verify.json` 为位姿结果；`pixel_refined.json` 为角点优化候选；`old_batch_pixel_refined.json` 仅用于同方法跨批诊断；`diagnostics.json` 为训练删一敏感性和重复姿态诊断。源MCAP没有修改。

65张旧批选帧未使用于新安装拟合。新15张PNG与MCAP解码像素逐张一致、分组不跨集、首尾保护时间通过；角点索引与OpenCV一致且优化预测深度为正。

重现选帧：`scripts/prepare_yam_calibration_new.py` 原参数外增加 `--task-glob calib_right_main_verify --prepare-seconds 12 --end-seconds 84`。默认参数保持原 `_new` 批次行为。调用原 `solve_yam_calibration_new.py` 后，用 `refine_yam_main_pixels.py --source .../calib_right_main_verify.json --intrinsics .../device_intrinsics.json --output .../pixel_refined.json` 进行主相机训练角点优化。

下一步应对固定安装下的已知空间参照做独立投影验收，并核对打印后实测尺度、板平整性及模型/FK约定；不建议继续凭训练残差逐帧删除，也不直接要求无差别重录五组。

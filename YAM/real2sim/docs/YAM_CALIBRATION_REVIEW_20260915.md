> 历史记录：当前参数已由用户确认并固定，见 [当前 YAM 标定](YAM_CALIBRATION_CURRENT.md)。旧 runs 优化目录已清理。

# YAM 2026-09-15 首批标定数据审查

本轮完成五组 MCAP 解码、板检测、候选停稳帧筛选与分组留出；尚未求解或验收相机内外参，没有更新 station 为 verified。

## 数据与产物

原始数据：`/mnt/sdb/YAM/data/calib_*/mcap/success/*.mcap`。
审查目录：`/home/chenyuan/workspace/real2sim/runs/yam_calibration_20260915/selection_v1`，打开 `index.html` 查看原图与角点预览。
每组 `selection.json` 记录源 MCAP、相机、状态通道、时间戳、帧号、关节位置、停稳时间段、质量指标与 train/validation 划分；`audit.json` 记录每个采样帧的排除原因。`train/`、`validation/` 的 PNG 为原始 MCAP 解码像素（原录制已使用 H264），不是 JPEG 二次压缩或放大图。`*_corners.png` 是预览。
同级初查目录有按 0.5 秒采样的 JPEG 与时间线，仅用于观察过程，不是用于求解的原始帧。

用户确认与 Xtrainer 是同一块实物板：15 mm 方格、11 mm marker、4x4 字典。检测核验 OpenCV 11x8 / legacy=True，使用包含该组 marker 的 DICT_4X4_1000。

## 本轮筛选结果

| 任务 | train | validation |
|---|---:|---:|
| calib_left_main | 5 | 1 |
| calib_right_main | 1 | 0 |
| calib_left_wrist | 0 | 0 |
| calib_right_wrist | 1 | 0 |
| calib_table_check | 0 | 0 |

这些是当前保守阈值下的候选结果，不代表整段视频在所有算法下只能利用这些帧。尚不足以作可信的五组标定，特别无法在每组形成足够的独立验证姿态；没有验证误差可报告。

## 排除与独立划分

- 视觉时间线中存在递板、夹持准备；左主相机保守排除前 18 秒，右主相机排除前 22 秒，其余前 2 秒。不是仅按固定秒数筛选，之后继续检查状态与检测质量。
- 约 10 Hz 扫描。运动组在帧前后各 0.25 秒内，六关节最大跨度不超过 0.020 rad；最近状态距图像时间不超过 20 ms。此为停稳筛选，不能证明板刚性或完成时钟标定。
- 角点不少于 12、角点凸包不少于 600 px²，板区域拉普拉斯清晰度不低于 35。原尺寸及三倍插值图检测择优，角点换回原图坐标；插值不增加真实分辨率。
- 合格检测须形成至少三个采样点、跨度至少 0.20 秒的片段，片段间隙不超过 0.65 秒；相近关节姿态去重，每个片段只保存一个代表帧。
- 桌面板另外要求共同角点位移稳定；本批首先因角点数量不足未通过。
- 按完整姿态分组顺序每第四组留出验证，不随机拆相邻帧。当前没有任何拟合；验证集没有用于调参、内参拟合或外参求解。
- 8 张候选原图已人工浏览，未见递板人员接触；仍不构成刚性安装或标定精度验收。

## 关键发现

1. `calib_left_*` 的大幅运动状态来自 `/right/state`；`calib_right_*` 来自 `/left/state`。相机/任务侧名和 native 状态侧名相反。manifest 明确映射；没有改原数采命名或硬件配置。后续 FK、双基座与腕相机求解必须沿此证据核对，不能只看任务字符串选择状态。
2. 大量帧处于连续运动，特别两组腕相机没有足够长的停稳片段。
3. 主相机看到的板较小，桌面组当前检测不足 12 个角点，无法稳定求板姿态。需要扩大板在画面中的像素尺寸或采用更高分辨率；调整主相机安装后须统一重录其相关标定。
4. MCAP session 仍缺 K 与畸变，不能借用 Xtrainer 的参数。需按实际流配置读取并记录内参，或另行采集充分多样的内参数据；读取当前设备参数也要核对是否与录制流一致。

## 下一步

先补内参读取和在线角点/停稳反馈，少量试采验证有效后再补录。每组 20–30 个不同位置和多轴朝向，每次真正停稳 2 秒，另留 5–10 个独立姿态。板和工具不能滑动。主相机与桌面组先检查板实际角点识别，而非仅凭肉眼可见。腕相机组固定板、移动腕相机。桌面厚度与工作区边界仍待记录。

不要为了输出数值放宽门槛然后声称标定成功。若研究运动帧利用，需要额外验证时钟、曝光与动态状态配对，并用独立停稳数据验收。

## 重现

在 YAM 上使用原数采解释器以读取 MCAP，脚本仅从 workbench 的独立 OpenCV 目录加载检测器，不启动设备、不修改依赖：

```bash
/home/chenyuan/workspace/YAM/gello_software/.venv/bin/python \
 /home/chenyuan/workspace/real2sim/scripts/prepare_yam_calibration.py \
 --yam /home/chenyuan/workspace/YAM \
 --workbench /home/chenyuan/workspace/real2sim \
 --data /mnt/sdb/YAM/data \
 --output /home/chenyuan/workspace/real2sim/runs/NEW_REVIEW_DIRECTORY
```

输出目录必须不存在。该脚本是本次五组任务的审查入口，启动保护时间、左右映射与筛选阈值针对当前记录写明在代码中，不是任意任务的自动标定器。

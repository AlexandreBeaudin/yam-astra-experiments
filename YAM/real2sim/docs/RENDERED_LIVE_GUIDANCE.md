# TASK-X-0003：用资产渲染替代实时线框

2026-09-23。当前 task3 的130条指导投影改用 Isaac Sim 5.1.0 的资产渲染：研钵和研杵直接读取审核过的 STL，粘土仍是暂定30mm球体。物体位置、朝向、相机内外参、训练100/验证30以及 split_group_id 均未改变。

## 显示与录制

- 指引为640×480 RGBA，只包含研钵、研杵和粘土的颜色/曲面；桌面、网格、历史机械臂和背景均透明。
- 主视角仍使用已保存的标定相机，不使用管理员斜鸟瞰画面投射。
- 实时页面默认50%透明度，可拖动原滑条调整。真实桌面和手臂仍来自实时相机；指引不会根据当前手臂自动判断遮挡。
- 原按钮开始/结束录制、上一条/下一条、12条现场试采顺序均保留。
- placement_guidance.json 额外记录 projection_kind=isaac_asset_rgba 和渲染参数；guidance_projection.png 保存开始录制时的这张渲染图，guidance_overlay_first_frame.jpg 保存与第一帧的叠加。
- 旧包和旧轨迹不覆盖；发布新版本后，在下次启动并选择 TASK-X-0003 时自动加载。已经运行的 collect 固定原会话版本，若要加载新版需在结束当前轨迹后正常退出并重新启动采集。

## 本次输出

新包：runs/task_x_0003_rendered_train100_val30_20260923。

已通过管理员发布，release_id 为 `987ae4e6d4bf4fbbb9f691b013ce43ba`。在实际 Xtrainer Python 环境运行 `python -m scripts.run_guided_collection --task TASK-X-0003 --check-only`，确认启动器自动选中此发布版本；该检查未启动硬件。发布记录保存在新包的 publication.json。

- mortar_000.png … mortar_129.png：实际透明资产渲染。
- asset_render/report.json：实际运行版本、STL/布局/相机/图像哈希、可见像素及标定投影包络检查。
- wireframe_vs_rendered.png：相同历史背景与相同布局下，旧线框和新资产的50%叠加对比；不是当前task3现场实拍。
- overlay_acceptance.json：130条位置/相机/分组保持不变、透明背景及完整性检查。
- full_scene/：新版本对应的260张管理员完整场景图。

## 渲染方法

统一环境为 Isaac Sim 5.1.0 / Python3.11 / PhysX。本步骤不运行物理，也不启动机械臂或相机。

在原场景灯光下独立渲染物料，隐藏历史机械臂和环境几何，避免旧机械臂姿态截断目标物体。PathTracing 32 spp，在1280×960按2倍内参渲染。用语义分割取得物体掩码，先乘alpha再面积下采样，最后恢复直通alpha，避免透明边缘混入背景形成黑边或白边。发布时还核对渲染报告、PNG及布局哈希，拒绝错位或缺失图像。

## 后续新版本构建

几何分布先由 prepare_mortar_pestle_distribution.py 生成审阅草稿，然后：

```bash
bash scripts/isaac_python.sh scripts/render_task3_live_assets.py \
  --source /path/to/geometry_bundle --output /path/to/NEW_rendered_bundle
bash scripts/isaac_python.sh scripts/render_mortar_pestle_review.py \
  --run /path/to/NEW_rendered_bundle
PYTHONPATH=src .venv/bin/python scripts/prepare_task3_live.py \
  --run /path/to/NEW_rendered_bundle
```

再导入管理员并发布。实时准备脚本不再为新的线框包生成采集页面；旧的已发布版本仍能读取与回溯。当前只适配 task3 的物料配方，没有声称其他任务或 YAM 的投影已一并替换。

## 验证与边界

全部130条资产渲染通过：三件物体可见、相机投影包络一致、RGBA尺寸正确、背景透明、像素哈希匹配；130条原始布局和分组逐项比较一致。

离线真实采集桥：2条试采+3条正式记录、三路不同历史画面、开始时PNG和表示类型固定、前后切换、默认50%、试采与正式覆盖分离、过期画面拒绝、无JS错误。证据为 runs/task3_rendered_live_acceptance_20260923_v2/report.json。

这次改善的是目标显示，不增加抓取/交接/捣压的物理验收结论。颜色和材质是当前资产的近似外观，粘土尺寸仍是已注明的假设。

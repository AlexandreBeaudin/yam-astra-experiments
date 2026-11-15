# TASK-YAM-0015 物料尺寸复核（2026-09-20）

## 结论与当前状态

旧模型把 MAT-00050 抽屉映射为进深250、正面宽314、高254 mm。重新读取飞书原图及外部/内部成对尺寸后，候选修正为**进深314、正面宽250、高254 mm**；原字段仅写“长/宽”，轴向仍需实物确认，不能视为已经测量确认。把手候选为横跨143.1、伸出29.7、上下19.8 mm，后两个方向也待确认。

七件物料不是照片重建的精确扫描件：使用照片辅助参数化建模；颜色、曲率、纹理、间隙仍含估计。修改没有更动接受的相机标定、原始采集数据或机械臂配置。

## 修正范围

- 新增 `configs/reconstruction/yam_task_0015_dimensions.json`，记录模型XYZ、来源文字和未测假设。模型生成拒绝尺寸与该配方不一致的输入；渲染溯源记录配方SHA256。
- 抽屉外壳按候选外部314×250×254、内部312×236×248 mm建模；壳厚按对应内外尺寸反推，不把5.9 mm套用到所有板材。
- 单层抽屉高75.4、托盘壁厚1.7、前沿厚5.9 mm；三层间隙均分是估计；增加托盘和开口缺口。
- 把手整体尺寸单独检查；安装底片大小未知，保留估计，不纳入把手尺寸。
- 纸巾标签曾增加总高度，修正为总高24.6 mm；遥控器按键最高点修正为34.2 mm。
- 勺碗改为闭合有厚度曲面，底部落在z=0；1.6 mm作为竖直厚度近似，并非已测曲面法向厚度。
- 方块高度34.4 mm仍由照片推断，飞书只给了长宽。纸巾与遥控器按平放姿态建模，字段中的“高”对应其产品长轴，不等于摆放后的世界Z轴。

## 测试与限制

1. `scripts/check_yam_material_dimensions.py` 实际读取USD几何包围盒，七件外部尺寸通过；抽屉内部空间、各层高度、前沿厚度、把手独立范围通过；勺碗闭合性、面朝向、正体积和非退化三角面通过。只能证明几何实现符合候选配方，不能反证实物轴向正确。
2. Isaac Sim 5.1.0 离线生成七件物料×三个正交视角，与飞书原图并排。没有启动相机或机械臂，也未进行抓取/关节物理验收。
3. 把旧60布局中的柜体改为候选尺寸后重算：0/60同时通过；56条主相机完整可见性失败，56条抽拉走廊超出临时工作区（两组有重叠）。这不是机器人IK失败的判定；此轮未重新运行IK。
4. 先前RTX标记点最大约0.993 px的误差只验证投影实现，不验证模型形状和尺寸。不能复用旧包的可用性结论。

旧 `runs/task_yam_0015_guidance_v1` 和 `runs/yam15_guidance_rendered_v2` 标记 `RECALLED.json`，新建GuidanceSession拒绝加载。归档episode和历史投影保持原样；已运行进程不会自动中断，需结束旧指导会话。当前没有发布新的现场可用分布。

## 对照材料与下一步

YAM共享项目目录：`/home/chenyuan/workspace/real2sim`。

- `runs/yam15_dimension_review_20260920_v2/index.html`：飞书原图＋正/侧/顶三视图。
- `runs/yam15_dimension_audit_20260920_final/dimension_report.json`：几何测试。
- `runs/task_yam_0015_dimension_audit_20260920/old_layout_recheck.json`：旧布局复查。

先确认柜体正面宽/进深以及把手伸出/上下两个方向；随后更新任务配置、重新生成并校验新分布（完整视野、抽拉空间、物体间隔、IK等），重新渲染并关联新配方哈希，最后用实时画面验收。不能只替换旧包PNG，也不能改相机标定或放宽限制来掩盖几何错误。

复现几何审计（使用新的输出路径）：

```bash
.venv/bin/python scripts/check_yam_material_dimensions.py --output runs/dimension_audit_new
bash scripts/isaac_python.sh scripts/render_yam_dimension_audit.py \
  --assets runs/dimension_audit_new --photos runs/yam15_photo_audit_20260920 \
  --output runs/dimension_review_new
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_guidance_recall.py
```

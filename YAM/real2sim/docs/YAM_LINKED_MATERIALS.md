# YAM 任务关联物料：重建、验证与发布

本机 2026-09-15 实施入口：`scripts/reconstruct_yam_materials.py`。沿用现有飞书 Client、物料规范化、BOS 内容寻址上传和物料字段回写模块；新增任务关联解析、6 件照片检查后的配方、USD/GLB/MJCF 导出及 MuJoCo 验证。

## 本轮范围

从「YAM 数采记录」表名称唯一发现表 ID，通过「物料」字段的真实 `record_ids` 匹配物料主表，不能根据任务名称或关联显示文字猜编号。当前映射为：

| YAM 任务 | 关联物料 | 本次版本 |
|---|---|---|
| TASK-YAM-0001 击球进门 | MAT-00011 足球网；MAT-00012 足球 | v001；v002 |
| TASK-YAM-0002 印章 | MAT-00033 印泥；MAT-00034 印章 | v002；v001 |
| TASK-YAM-0004 香肠入箱 | MAT-00052 火腿肠；MAT-00053 泡沫箱 | v001；v001 |

用户已明确同意跳过空关联的 TASK-YAM-0003、0006、0007。本轮不替用户补物料关联。库存数量仅作登记信息，不能作为任务用量。

## 执行入口

在 `/home/chenyuan/workspace/real2sim` 下执行。每次新一轮同步使用新的 `--run`；快照固定后不能被新数据静默覆盖。

```bash
# 读取真实任务/物料关联、下载主照片并校验附件大小和图片编码
.venv/bin/python scripts/reconstruct_yam_materials.py sync --run runs/yam_materials_20260915

# 已同步的快照：按审核后的显式配方构建、检查、渲染，不访问机械臂
.venv/bin/python scripts/reconstruct_yam_materials.py build --run runs/yam_materials_20260915

# 明确发布步骤：上传单物料版本，逐个回写物料表并读回核对
.venv/bin/python scripts/reconstruct_yam_materials.py publish --run runs/yam_materials_20260915
```

配方入口 `configs/reconstruction/yam_linked_materials.json`；可用 `--recipes` 指定其他经过照片/尺寸核对的配方。未提供配方的新关联物料会明确报错，不自动套用相似物料。`--config` 是物料表业务配置；`--bos-config` 是独立 BOS 业务配置。

当前 `configs/bos.local.json` 的资产目标为：

`bos://rhos-baige-general-beijing-prod/project/handzero-project/real2sim/<bundle_id>/files/`

这个入口发布物料资产，沿用已配置的资产目的地。上一轮遥操 MCAP 的 confidential 桶及 `S2/TeleopData/YAM/` 路径不受影响。`publish` 明确执行物料回写，更新「仿真资产地址」「资产版本」，不依赖通用上传 CLI 的自动回写开关。不会修改任务数据路径、质检状态、物料数量或发送飞书消息。

## 产物与版本

资产位于 `assets/materials/<编号>/<版本>/`，包含：

- `asset.usda`：米制、Z 向上、带刚体和独立碰撞体的 USD。
- `visual.glb`：基础颜色外观网格；足球含近似色块。
- `asset.mjcf.xml`、`visual/*.obj`、`collision/*.obj`：可迁移加载的 MuJoCo 模型及相对路径网格。
- `recipe.json`、`physical.json`、`frames.json`：显式几何配方、物理参数来源及坐标约定。
- `evidence/source.jpg`、`evidence/source.json`：原图、登记尺寸、物料记录 ID 和原图哈希。
- `manifest.json`、`validation.json`：任务关联、版本、输入/配方/生成源码哈希、检查结果和限制。
- `oblique.png`、`front.png`、`top.png`、`validation_scene.xml`：标准姿态的三视角渲染与检查场景。

构建时分配高于本地/登记版本的新版本号，并固定到运行目录的 `versions.json`。只在临时目录完成构建/检查/渲染后发布本地版本目录，已有版本不覆盖。失败可按同一输入重跑；同一已完成批次不重复构建。`batch.json` 固定全部可上传文件的内容寻址 ID，发布前发现文件变化会拒绝上传。

上传前重新核对任务关联、尺寸、照片元数据及现有资产指针，避免用旧快照覆盖并发的新登记。BOS 完成清单最后发布，飞书回写与 BOS 上传各自记录状态。回写失败可重跑 `publish`，复用相同对象。回执在运行目录 `uploads/`，不放入资产源目录。

## 几何与物理边界

这是照片辅助的参数化候选，不是通用图生三维或已认证数字孪生。尚无深度融合、完整照片纹理恢复、真实抓取或 YAM 场景布局验收。所有资产保持 `guidance_ready=false`、`contact_validated=false`。

- 足球网：保留前方开口，框架与视觉网丝分开；网碰撞近似为侧面/背面/顶部薄片，网丝尺寸与网格间距为估计。
- 足球：外观球面三角网格，碰撞使用解析球体，避免网格离散造成不必要的滚动激励；图案为近似。
- 印泥：照片为闭合盒体，本候选保留闭合状态；未实现铰链、开盖或露出的印泥面，不能用于验证蘸印泥任务。
- 印章：底座和有收腰的柄；未恢复底面刻字、倒角或印迹转移。
- 火腿肠：按登记总长和直径建立水平胶囊体；封口细节、包装文字、柔性均未恢复。
- 泡沫箱：五个独立碰撞体组成开口容器，保留内腔；底厚假定等于登记壁厚，省略缺口和泡沫形变。

质量、惯量与摩擦均明确标为假设。原点位于外包围盒 XY 中心与最低支撑面的交点。渲染用标准姿态，静置结果另存，不用人为清零速度保持“稳定”。USD 与 MuJoCo 使用不同接触模型，不宣称两后端物理结果等价。

## 环境与验证

本机 workbench 使用 Python 3.11、MuJoCo 3.8.1。本轮仅在 workbench `.venv` 新增 `usd-core==25.5.1`（通过 `uv pip install --python .venv/bin/python usd-core==25.5.1`）；原采集 venv 未修改。其他既有依赖包括 NumPy、SciPy、trimesh、Pillow。此次未安装或执行 Isaac/PhysX，历史 PhysX 结果不计入本轮。

本轮已执行：

- 6 件几何检查：有限顶点、各部件闭合、法线一致、尺寸边界和凸碰撞体检查通过。
- 18 次 MuJoCo 静置通过：0°、+5°、−5° 初始倾角，240 Hz、2.5 秒，检查支撑面误差及末 0.5 秒漂移；非球体/胶囊体额外检查最终倾角。
- 18 张三视角渲染产生并检查图像有效性，人工查看对照总览。照片视角未标定配准，不报告像素精度。
- 7 项新增测试通过：关联 ID/空关联/重复/错表/路径安全、外形尺寸、内腔与入口未填实、全部 USD/MJCF 搬移后加载、解析球体，以及小球落入箱内并停在箱底的真实碰撞检查。
- 飞书/回写 13 项、BOS 12 项、历史物料 3 项回归通过，共 35 项。

新增测试首次执行发生一次原生进程退出 139，无 Python traceback；启用 faulthandler 的独立重跑以及随后连续 3 次独立执行均通过，未定位到可复现原因。该记录保留，不把首次执行表述为通过。

足球首轮多面体碰撞静置未通过，失败记录在 `runs/yam_materials_20260915/MAT-00012_failed_validation.json`。最终资产改用解析球形碰撞体后通过全部三次静置；失败稿未上传。

运行和对照入口：`runs/yam_materials_20260915/index.html`，汇总图为同目录 `overview.jpg`。`source.json` 固定输入，`batch.json` 固定资产内容，`uploads/*.json` 保存上传及回写回执，`publication.json` 在全部完成后生成。

## 本轮最终发布结果

6 件物料全部发布成功：117 个资产文件，共 5,301,769 字节，另有 6 份 BOS 完成清单。物料主表的 6 条「仿真资产地址」「资产版本」均回写并 GET 读回核对通过。`publication.json` 与 `summary.json` 状态均为 `complete`。未修改空关联任务、物料关联字段、原始数采数据或质检状态。

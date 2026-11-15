# 通用数采分布管理：第一阶段（2026-09-20）

> 以下记录 9 月 20 日只读阶段。9 月 23 日本机 Xtrainer 新增全场景预览、版本化微调和发布，最新用法见 [管理员编辑与发布](MANAGER_EDIT_PUBLISH.md)。录制证据读取的 YAM/Xtrainer 边界仍保持不变。

管理入口位于 real2sim，共享目录格式、网页、SQLite 索引与证据浏览；YAM 通过只读 MCAP 适配器接入。按用户本轮确认，Xtrainer 只保留接口，调用会明确报“尚未实现”，等待真实数据后再开发与验收。

## 当前能做什么

- 浏览布局版本、60条目标图、物料组合次数和设计位置散点；按编号/物料筛选。
- 查看单条目标图、俯视/可旋转三维包络、检查结果和原始布局参数。包络不是精确USD网格，也不表示物理任务通过。缺少显式顶点时不猜几何。
- 查看轨迹实际保存的首帧、录制时目标及叠加图，保留源时间、相机名、分辨率和哈希。
- 停用包可用于历史浏览，页面显示 RECALLED 原因；每次刷新重新检查源停用标记和 manifest 哈希。
- 无目标轨迹明确标记 unlinked；不会按任务名、当前网页选择或相同 layout_id 补造目标。

第一阶段是只读目录与证据查看器，尚无审核提交、批准发布、实际位置标注/统计、自动识别或补采建议。不会把“已关联目标”计为达标，也不把布局散点标为实际分布。设计统计以选定版本为单位，不混合工位坐标。

## 启动

独立 Python 3.11 管理环境（本机已安装）：

```bash
cd /home/chenyuan/workspace/real2sim
# 新机器安装；不改数采/Isaac环境
uv venv --python 3.11 .runtime/manager
uv pip install --python .runtime/manager/bin/python -r requirements-manager.txt -e .

.runtime/manager/bin/python scripts/collection_manager.py serve --port 8780
```

浏览器打开 http://127.0.0.1:8780/ 。管理端口8780与YAM实时预览8765独立，不需要启动遥操或仿真器。默认只监听回环地址，无远程账号服务；SSH转发示例（端口/主机按现场连接替换）：

```bash
ssh -N -L 8780:127.0.0.1:8780 -p 2222 chenyuan@192.168.77.21
```

在查看电脑打开同一 localhost 地址。不要直接反向代理为公网服务。

## 导入

默认工作目录 `runs/collection_manager`，可在子命令前指定 `--workspace /path/to/catalog`。只保存数据库和内容寻址图片，不改原始轨迹、标定或指引包。

```bash
# 历史停用包也可以导入管理目录，但不会变成可用采集包
.runtime/manager/bin/python scripts/collection_manager.py import-bundle \
  runs/yam15_guidance_rendered_v2 --station yam_chenyuan

# 单条YAM MCAP
.runtime/manager/bin/python scripts/collection_manager.py import-episode \
  /path/to/episode.mcap --platform yam

# 一个任务下的所有已完成MCAP；跳过 .in_progress
.runtime/manager/bin/python scripts/collection_manager.py import-episode \
  /home/chenyuan/workspace/YAM/data/TASK-YAM-0015 --platform yam --recursive

.runtime/manager/bin/python scripts/collection_manager.py inspect
```

同一文件重复导入不增加记录。源文件哈希变化会形成新证据版本；相同ID的冲突导入拒绝覆盖。批量导入遇到错误会停止，已完成记录保留，可以修复原因后重跑。没有后台自动扫描，新增数据需显式导入，然后刷新网页。

## 公共格式与平台边界

`management/evidence.py` 的读取器返回 `(document, images)`：

- `schema_version / id / platform_id / station_id / task_id`：站位未知时保留null，不把当前标定套到历史数据。
- `source / source_sha256 / provenance`：原始文件与读取依据。
- `overview_camera / image_size / frame_source_time_ns / timestamp_basis`：保留真实相机名和时钟，不硬编码通用相机尺寸/臂数。
- `association_status / placement_status`：linked+pending_review 或 unlinked+no_target。
- `target`：实际保存的不可变目标，含版本、布局、投影哈希等；缺少时null。
- `observations`：预留实际位置记录，本阶段为空。
- `images`：首帧及可选投影，独立写入内容寻址缓存。

YAM读取MCAP `/session`、`/guidance/initial` 及所选RGB相机的第一条实际消息。H264首包无法独立解码时明确报错，不用后续画面冒充首帧。检查CRC、目标投影SHA256、尺寸、相机序列号/方向及任务绑定；导入前后检查源文件哈希。当前YAM历史指引约定主视角为main，因此已有目标时禁止改配腕相机；没有目标的轨迹可通过 `--camera` 显式选择其他已录相机。

Xtrainer未来适配器必须使用录制开始保存的 placement_guidance.json 与该记录指向的首帧，验证会话、任务、camera_hash和图像哈希。缺失时钟不能把文件名当时间。公共层不导入采集器，也不启动相机/机器人；记录读取格式与平台运动学独立。

只有录制目标中的 `bundle_sha256` 与目录 manifest 哈希、平台和任务都匹配时，才建立精确目录版本关联。旧Xtrainer的 binding_hash 属于不同哈希域，不能冒充 bundle_sha256。没有匹配目录版本的已保存目标仍可独立查看。

## 验证及当前本机数据

- 8项新测试通过：真实像素首帧、H264、目标绑定/哈希拒绝、幂等导入/冲突保护、动态停用、路径边界、Xtrainer保留入口、回环HTTP隔离。
- 本机940条已完成MCAP的topic清单扫描，未发现 `/guidance/initial`；这只是目标关联盘点，不是全数据质量验收。
- 导入TASK-YAM-0015的35条真实轨迹，全部unlinked；导入60条历史渲染布局，明确显示已停用。
- 有目标的读取与关联使用临时合成MCAP测试，没有混入真实目录统计；现场目标快照端到端验收仍需以后真实采集。
- 实际Chrome无头检查覆盖60张布局、筛选、二维/三维切换、35条证据列表、首帧查看和移动端截图，无JavaScript错误。未启用硬件。

测试命令：

```bash
.runtime/manager/bin/python -m unittest discover -s tests -p test_management.py -v
# 浏览器检查依赖独立测试环境中的playwright，不是数采依赖
python scripts/check_collection_manager_browser.py --chrome /usr/bin/google-chrome \
  --output runs/manager_browser_NEW
```

后续第二阶段：在现有只读证据上增加带版本的人工审核、位置标注及实际分布。审核记录独立追加，不能覆盖原始目标或图片；不能仅凭通过按钮生成实际位置热力图。自动核验与补采策略再基于人工标注验收。

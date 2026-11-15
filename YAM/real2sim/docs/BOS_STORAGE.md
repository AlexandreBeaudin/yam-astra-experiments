# BOS 结果存储模块

位置：`src/real2sim/storage/`。与机械臂、仿真器、飞书解耦，接收一个已导出的文件或目录。既可上传单个物料版本，也可上传任务结果、视频和校验报告。当前是显式发布步骤，不会在重建后自动触发上传。

## 本机配置

在 `/mnt/sdb/rhos_collector/real2sim` 执行：

```bash
# 本机已安装；其他机器可安装该可选依赖
bash scripts/no_proxy.sh .venv/bin/pip install -e '.[bos]'
.venv/bin/python scripts/configure_bos_credentials.py
cp configs/bos.example.json configs/bos.local.json
```

交互输入 AK 和 SK，二者均隐藏输入，写入 `~/.config/real2sim/bos.json`，权限 600。该文件独立于飞书凭据。不要将密钥放入项目配置、命令参数、表格或聊天。自动任务也可通过环境变量 `BOS_ACCESS_KEY_ID`、`BOS_SECRET_ACCESS_KEY` 注入，必须成对设置，优先于凭据文件。读取时拒绝符号链接、非当前用户所有或其他用户可读的凭据文件。

编辑 `configs/bos.local.json`：

```json
{
  "endpoint": "https://bj.bcebos.com",
  "bucket": "your-existing-bucket",
  "prefix": "real2sim",
  "multipart_threshold_bytes": 67108864
}
```

Endpoint 需要与实际 Bucket 所在区域匹配；示例北京地址仅为示例。使用已建好的 Bucket，不自动创建、不修改 ACL、不开放匿名访问。AK/SK 所属身份需要目标前缀的对象写入、读取元数据及分片上传/完成/中止权限；403 直接报错，不当作文件不存在。BOS 服务端可配置清理未完成分片的生命周期规则，以清理进程被强制终止后留下的分片。

## 使用

```bash
# 离线：不读取凭据，不发起网络请求
.venv/bin/python scripts/upload_bos.py assets/materials/MAT-00019/v001 \
  --config configs/bos.local.json --dry-run \
  --receipt runs/bos_uploads/cup_plan.json

# 实际上传：检查配置后移除 --dry-run
.venv/bin/python scripts/upload_bos.py assets/materials/MAT-00019/v001 \
  --config configs/bos.local.json \
  --receipt runs/bos_uploads/cup_upload.json

# 重建批次报告
.venv/bin/python scripts/upload_bos.py runs/material_reconstruction_batch \
  --config configs/bos.local.json \
  --receipt runs/bos_uploads/batch_upload.json
```

receipt 必须放在输入目录之外。上传失败退出码为 1，并写入失败状态；SDK 原始异常不输出，避免泄露签名请求。可以重复执行同一命令。

Python 调用（安装包后）：

```python
import json
from real2sim.storage.bos import upload_bundle

config = json.load(open('configs/bos.local.json'))
receipt = upload_bundle('assets/materials/MAT-00019/v001', config)
# 完成后将 receipt['manifest_uri'] 登记到业务系统/物料记录
assert receipt['status'] == 'complete'
```

## 云端结构与完整性

```text
bos://<bucket>/<prefix>/<bundle_id>/
  files/asset.usda
  files/visual.glb
  files/manifest.json      # 如果输入目录本来就有物料 manifest，保留原名
  files/...                # 完整保留相对目录结构、中文路径
  manifest.json            # 本模块最后发布的批次完成清单
```

`bundle_id` 是规范化清单的 SHA-256，清单记录每个文件的相对路径、字节数、SHA-256。文件内容或路径变化产生新批次，相同内容可复用同一批次；本机目录的绝对路径不影响批次 ID，也不会出现在云端完成清单中。绝对路径仅保留在本机 receipt 中。

每个对象带 `sha256` 自定义元数据。上传前 HEAD 检查大小与该元数据，一致则跳过；已有对象不一致时停止，不主动覆盖冲突。每个待上传文件先复制到临时目录并重新校验，再上传稳定副本，临时空间最多约为最大单文件大小。超过阈值使用官方 SDK 的分片接口，异常时尝试中止分片。上传后再次 HEAD 核对。此处的校验是本机哈希与远端元数据/大小检查，不是下载远端全文重新计算哈希。

只有所有文件验证完成后才上传完成清单。消费端应从 `manifest_uri` 读取清单，而非仅凭前缀下出现文件就认定成功。下载后可按清单重算 SHA-256。中途失败可能留下已成功上传的对象；再次执行会跳过它们。支持文件级续传，尚不支持跨进程恢复单个文件的分片进度。不删除已上传对象。

## 范围与限制

- 默认排除隐藏路径、`__pycache__`、`node_modules`、`*.local.json`、`*.pem`、`*.key`、`bos.json`、`feishu.json`；拒绝其他符号链接及非普通文件。这不是通用敏感内容检测器，应选择明确的产物目录。
- 只上传选中目录实际包含的字节，不解析 USD 外部引用、不重写已有 JSON 中的本机路径。例如批次报告引用的 `assets/materials/` 模型需要单独上传；完整可迁移场景打包属于后续资产导出工作。
- 不改变物料的 `guidance_ready`、`contact_validated` 等验证状态，上传成功不表示模型通过物理验证。
- 返回稳定的 `bos://` 引用，不生成公共下载地址或把临时签名 URL 保存到清单。需要外部访问时另行按业务身份鉴权。
- 内容寻址用于幂等发布；HEAD 后 PUT 不是原子条件锁。请勿让其他程序修改本模块专用前缀，同批次的并发上传应使用相同内容。

## 已运行验证（2026-09-15）

`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_bos.py -v`

测试覆盖离线规划、中文路径、重复上传、冲突保护、403、失败不发布清单、多分片与失败中止、密钥权限、环境变量、源文件变化及 HTTPS 配置。共 12 项通过，使用内存 BOS 替身、官方 SDK 响应解析器，并通过本机回环 HTTP 服务执行了真实 SDK 签名上传与 HEAD 校验；不连接真实云端。

另对 `assets/materials/MAT-00019/v001` 和 `runs/material_reconstruction_batch` 运行了真实文件离线规划，见 `runs/bos_uploads/`。随后已配置北京 Endpoint 与用户指定前缀，使用本机凭据完成真实 BOS 上传：纸杯资产 3 个文件（85,495 字节）、重建批次报告 47 个文件（11,951,690 字节），均完成远端元数据/大小校验并发布完成清单。纸杯重复上传验证为新增 0、跳过 3。实际回执见 `runs/bos_uploads/cup_upload.json`、`batch_upload.json`、`cup_repeat_upload.json`。本次文件未达到默认分片阈值，真实云端分片上传尚未验证。

官方接口参考：[BOS Python 上传文件](https://cloud.baidu.com/doc/BOS/s/yl5jfscvi)、[初始化](https://cloud.baidu.com/doc/BOS/s/5jwvyrf21)、[官方 Python SDK](https://github.com/baidubce/bce-sdk-python)。使用的 PyPI 包名为 `bce-python-sdk==0.9.79`，Python 导入名为 `baidubce`。

## 上传完成后自动回写飞书

本机 `configs/bos.local.json` 已启用：

```json
"feishu_writeback": {
  "enabled": true,
  "config": "feishu_materials.local.json"
}
```

`config` 相对 BOS 配置文件所在目录解析，复用现有飞书凭据。继续使用原上传命令即可：BOS 上传与 HEAD 校验完成 → 本机保存成功回执 → 飞书回写 → 重新读取记录验证。`--dry-run` 不访问飞书、不回写。

单物料目录的 `manifest.json` 必须带 `material_id`、`source_record_id`、`version`，并包含 `asset.usda`。系统核对物料清单与已上传清单的 SHA-256，查询在线表并核对唯一物料编号及记录 ID。仅更新现有「仿真资产地址」和「资产版本」文本列：前者写入 `bos://<bucket>/<prefix>/<bundle_id>/files/`，后者例如 `v001`。文件夹内可找到 `asset.usda`、`visual.glb` 和原始物料清单；BOS 批次完成清单位于该 `files/` 文件夹的上一级 `manifest.json`。

回执的 `status: complete` 表示 BOS 成功，`feishu_writeback.status` 单独记录 `pending/complete/failed/skipped`。飞书失败时退出码为 2，保留 BOS 成功回执；重新运行相同上传命令会跳过已有 BOS 文件，再尝试回写。进程意外退出可能留下 pending，同样可以重跑。同值回写会跳过 PUT。只更新指向最新发布版本的地址，不维护表内历史列表；历史批次仍保留在 BOS 和本机回执中。

批次报告等没有唯一物料映射的结果跳过回写，并在回执中记录原因。它们不会被填写到某个物料的「仿真资产地址」。后续若需要任务级报告登记，应提供对应任务表的字段映射。

应用需有[更新记录接口](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/update)权限以及目标多维表格编辑权限。不会创建新表或新列，也不会发送飞书消息。

2026-09-15 实际验证：`MAT-00019` 已成功 PUT 更新上述两列，并通过 GET 读回比对；回执见 `runs/bos_uploads/cup_upload.json`。本次 BOS 文件全部跳过复用，无需重新传输。飞书相关测试共 13 项通过（含 6 项回写测试）。

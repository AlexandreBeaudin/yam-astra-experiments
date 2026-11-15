# 飞书单表物料库接入

入口 `scripts/sync_feishu_materials.py` 使用标准库，通过飞书开放 API 只读获取表头、分页记录。没有向飞书写入或发送消息的代码。2026-09-15 已真实联通知识库中的“物料主表”，完成 50 条记录同步。报告在 `runs/feishu_materials/connection_test.json`，快照在同目录 `latest.json`。当前“尺寸”文本保留到 `key_dimensions`，“原始素材”文本保留到 `source_uri`，主展示照片保留附件元数据；没有下载附件或发布新仿真资产。纸杯实际编号为 `MAT-00001`，无需改成示例中的六位数字编号。

## 一次性配置

1. 在飞书开放平台创建企业自建应用，例如“Real2Sim 物料同步”，按记录和字段读取接口申请相应应用身份读取权限，按租户流程发布/启用。
2. 在对应多维表格中为该应用配置访问权限（关联应用/文档授权入口以当前界面为准）。API 权限与具体文档数据访问权限都需要满足；不需要匿名公开表格。
3. 获取多维表格的 `app_token` 与目标数据表的 `table_id`。常规 Base 链接中的 `/base/...` 与查询参数 `table=...` 可提供这些标识。知识库 `/wiki/...` 链接的节点 token 不能直接当作 `app_token`，需解析到对应 Base。
4. 复制示例配置，将两项标识填入，并按实际表头修改 `fields`。可选字段不使用时可删除映射项；`material_id` 和 `name` 必须对应实际列。

```bash
cd /mnt/sdb/rhos_collector/real2sim
cp configs/feishu_materials.example.json configs/feishu_materials.local.json
```

推荐在本机交互式终端运行以下命令持久化凭证：

```bash
.venv/bin/python scripts/configure_feishu_credentials.py
```

依次输入 App ID 和 App Secret，Secret 输入不回显。凭证存放于当前用户的 `~/.config/real2sim/feishu.json`，目录权限 700、文件权限 600，不在项目目录内。它是受文件权限保护的明文文件，不是加密保险库。重复运行命令可更新凭证；同步程序自动读取，无需每次输入。实际飞书连通需要另外运行同步命令验证。

也支持服务器环境变量。两项环境变量同时设置时优先于本地文件；仅设置一项会报错，避免混用不同应用的凭证。以下交互输入方式不会把 Secret 明文写入命令历史：

```bash
read -r -p 'Feishu App ID: ' FEISHU_APP_ID
read -r -s -p 'Feishu App Secret: ' FEISHU_APP_SECRET
export FEISHU_APP_ID FEISHU_APP_SECRET
```

不要将 Secret 填入飞书表格、项目共享配置或聊天。环境变量配置只在当前终端及其子进程有效；本地凭证文件则可由同一用户运行的后续同步进程自动读取。

## 先检查表头，再同步

```bash
.venv/bin/python scripts/sync_feishu_materials.py --inspect
.venv/bin/python scripts/sync_feishu_materials.py
```

`--inspect` 输出实际列名、字段 ID 与类型。同步输出到 `runs/feishu_materials/latest.json`，并保存以内容 SHA-256 命名的历史快照。只有完整分页、字段转换、重复编号检查全部成功才替换最新快照。零数量允许，未知尺寸留空；尺寸/质量要求有限正数。编号和名称不能为空，不能用行号代替稳定编号。

同步只保留映射字段，不导入无关人员列。附件保存 `file_token` 和文件元信息，不保存临时下载 URL。当前入口不下载附件或模型包、不自动执行链接内容，不需要先申请素材下载权限。

## 与现有 real2sim 的边界

本入口完成的是“飞书 → 系统本地物料元数据”，不是任意上传模型自动可仿真。现有 `assets/registry/paper_cup.v1.json` 和已完成的 stack cups 结果不被修改。飞书状态仅作为登记信息，不能将“已验证”文字直接转成物理验证通过。

获得真实表头和记录后，下一步将具体物料编号显式绑定到本地资产。例如，若纸杯实际编号确为 `MAT-000001`，可把它关联到 `paper_cup/v1`；不能在不知道实际编号时擅自绑定。新模型需要下载、单位/坐标/碰撞检查，再发布为本地资产版本。数据采集任务最终锁定物料编号、资产版本与同步快照哈希。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_feishu.py' -v
```

覆盖分页、富文本、单位转换、重复编号、无效数值、字段缺失和状态隔离。此处为本地模拟接口测试，不代表真实飞书认证、权限或网络验证通过。

官方接口：

- https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal
- https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list
- https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list

## BOS 发布回写

物料同步脚本仍只读取表格。BOS 上传命令现支持在上传完成后更新对应记录的「仿真资产地址」和「资产版本」，由独立模块 `real2sim.storage.feishu_writeback` 实现，具体配置、权限及失败重试见 [BOS_STORAGE.md](BOS_STORAGE.md)。

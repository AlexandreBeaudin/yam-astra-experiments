# TASK-YAM-0008 / 0010 数据发布（2026-09-16）

用户明确要求上传全部 200 条，保留 `success/fail` 分类。

| 任务 | 内容 | 成功 | 失败 | MCAP 字节数 |
|---|---|---:|---:|---:|
| TASK-YAM-0008 | 双臂配合旋开瓶盖 | 91 | 9 | 3,058,469,898 |
| TASK-YAM-0010 | 双臂配合扣开口香糖瓶盖 | 99 | 1 | 1,688,633,826 |

合计 190 条成功、10 条失败，4,747,103,724 字节。成功/失败标签按原目录保留，本轮未重新评判任务成功标准。

## 本地与云端

本地入口 `/home/chenyuan/workspace/YAM/data/` 当前指向 `/mnt/sdb/YAM/TeleopData/`。本轮检查发现数据盘入口在清单准备后发生迁移，重新解析逻辑路径并按原清单逐文件核对大小和 SHA-256 后继续；未改回旧入口，未再次重命名任务目录。

- 本地：`/home/chenyuan/workspace/YAM/data/TASK-YAM-0008/`、`/home/chenyuan/workspace/YAM/data/TASK-YAM-0010/`。
- 云端根：`bos://rhos-baige-confidential-beijing-prod/S2/TeleopData/YAM/`。
- 数据键：`<编号>/mcap/success/<原文件名>.mcap` 或 `<编号>/mcap/fail/<原文件名>.mcap`。
- 完成清单：`<编号>/manifest.json`，逐文件记录路径、大小、SHA-256 和 outcome；selection 为 `success_and_fail`。

MCAP 文件名、内容和原始 session 信息保持不变。逐文件检查了 session 任务名、MCAP summary 及同侧 CAN 配对；这些是正常同侧采集的新数据，未冒称为执行过旧数据修复。

## 发布与核验

当前状态：已完成。200 条 MCAP（4,747,103,724 字节）全部上传并通过本地哈希和远端 HEAD 大小/SHA-256 元数据核验，2 份完成清单发布成功；飞书两条记录的路径已更新并 GET 读回核对通过。回执状态为 `complete`。

采用前次发布流程：预检飞书编号/条数/路径、完整本地清单及哈希、目标前缀 → 上传临时稳定副本 → HEAD 校验大小及 SHA-256 元数据 → 再核对本地哈希与远端对象 → 最后发布完成清单 → 更新飞书并 GET 读回核对。

飞书仅处理两个对应记录的「本地路径」「云上路径」；数据条数保留原先登记的每批 100，失败录制不会改成成功。上传校验不表示对远端全文重新下载计算哈希，也不代替视频质量验收。

审计目录：`/home/chenyuan/workspace/YAM/video/bos_upload_20260916/`（不进入 Git）。`records_before.json` 为回写前记录；`inventory.json` 为原始盘点；`plan.json` 记录当前实际位置及最终上传范围；`receipt.json` 为上传/回写回执；`TASK-YAM-*.manifest.json` 为完成清单本地副本。`publish.py` 为绑定本次计划的专用脚本，默认只读预检，带 `--apply` 执行发布，可复用匹配的已有对象。

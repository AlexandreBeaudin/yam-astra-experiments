# YAM 两批成功数据发布（2026-09-15）

## 范围与映射

飞书「YAM 数采记录」表 `tblKSnT6Qi9szlEl`：

| 编号 | 原任务目录 | 记录 ID | 文件数 | 字节数 |
|---|---|---|---:|---:|
| TASK-YAM-0004 | pick_huotuichang | recvvbzMk1BeaC | 100 | 2,243,354,640 |
| TASK-YAM-0006 | pick_up_the_small_square_and_place_it_in_the_groove | recvvfPuP6WCYD | 100 | 1,437,822,290 |

共 200 条成功 MCAP，3,681,176,930 字节。两批无失败文件或未完成文件。

## 本地与云端目录

本地逻辑目录为 `/home/chenyuan/workspace/YAM/data/<编号>/`，实际目录为 `/mnt/sdb/YAM/data/<编号>/`。只重命名任务目录，保留 `mcap/success/` 层次和每条录制文件名，未重写 MCAP 消息。历史 `session.task` 仍是原任务名，任务编号映射记录在云端完成清单中。

云端目录：

- `bos://rhos-baige-confidential-beijing-prod/S2/TeleopData/YAM/TASK-YAM-0004/`
- `bos://rhos-baige-confidential-beijing-prod/S2/TeleopData/YAM/TASK-YAM-0006/`

云端每个任务目录包含 `mcap/success/*.mcap` 和最后发布的 `manifest.json`。采用用户指定的编号目录，未使用物料上传器的内容哈希目录层次；复用现有 BOS 认证、文件上传和 HEAD 校验实现。相同大小及 SHA-256 元数据的对象跳过，冲突对象报错停止。只有全部数据上传并核验后才回写飞书。

飞书只更新上述两个记录的「本地路径」和「云上路径」，分别指向本地逻辑任务目录和云端任务目录；不修改编号、任务描述、质检状态或物料表。

## 执行与验证

- 发布前逐文件核对 MCAP summary、`/session` 原任务名及 `yam-physical-arm-sides-v1` 修复标记，并计算 SHA-256。
- 两批目录已完成重命名；200 条 MCAP（3,681,176,930 字节）全部上传完成，无跳过文件。两个完成清单均已发布并校验。
- 两条飞书记录的「本地路径」「云上路径」均已更新，并逐项 GET 读回核对通过；本机回执状态为 `complete`。
- 上传使用临时稳定副本，按计划再次计算哈希；每个文件上传后用 HEAD 检查大小和 SHA-256 元数据。完成前再次校验本地文件哈希及云端 HEAD。
- HEAD 校验不等于下载全部远端文件重新计算哈希，也不代表完成视频质量或任务成功标准复核。
- 每批完成清单最后发布；飞书更新后用 GET 读回核对。

本机审计文件位于 `/home/chenyuan/workspace/YAM/video/bos_upload_20260915/`（不进入 Git）：

- `plan.json`：原/新目录、飞书记录及逐文件大小与 SHA-256。
- `records_before.json`、`fields_before.json`：回写前的相关记录和字段定义。
- `receipt.json`：执行状态、逐文件结果、完成清单及飞书读回结果。
- `TASK-YAM-0004.manifest.json`、`TASK-YAM-0006.manifest.json`：对应云端完成清单。
- `publish.py`：本次发布的专用脚本；默认只检查，`--apply` 才执行。此脚本绑定本次计划，不是任意任务通用上传入口。

凭证通过本机加载器读取，未写入上述审计文件或上传清单。左右臂修复原件备份位置保持不变。

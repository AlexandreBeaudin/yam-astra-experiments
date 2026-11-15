# recording_timer 测试数据 BOS 上传记录（2026-09-20）

## 结果

已将本地目录 `/mnt/sdb/recording_timer` 下的测试数据上传至：

```text
bos://rhos-baige-confidential-beijing-prod/S3/EgoData/recording_timer/
```

本批次保留本地相对目录结构，数据文件位于 `device11251/2026-07-13/0003_1783957824/` 下。

- 文件数：49
- 总大小：2,613,980,041 字节（约 2.61 GB）
- 上传结果：49 个全部首次上传，0 个跳过
- 文件类型：MP4 录制文件及 JSON 元数据
- 完成清单：`bos://rhos-baige-confidential-beijing-prod/S3/EgoData/recording_timer/manifest.json`

## 核验

上传前对本地文件生成 SHA-256 和字节数清单；上传后每个对象均通过 BOS HEAD 校验，确认大小及 `sha256` 元数据一致。完成清单在全部数据文件核验后最后发布，避免消费者看到不完整批次。

本地运行产物：`video/bos_upload_recording_timer_20260920/plan.json`、`manifest.json`、`receipt.json`。

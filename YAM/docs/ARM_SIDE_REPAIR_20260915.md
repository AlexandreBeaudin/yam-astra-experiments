# 2026-09-15 左右臂数据修复记录

## 根因与配置

原配置按主从配对命名：left=can_follower_r/FTC2FNQP，right=can_follower_l/FTBSX7UI；相机按画面物理侧别命名。写入器按配置命名，未交叉验证相机安装位置，因此同名 state 和腕部视频对应不同机械臂。

已交换完整配置（CAN、主臂串口、关节与夹爪参数一起），保持原控制配对：

| 物理侧别 | CAN | 主臂串口标识 | 数据 |
|---|---|---|---|
| left | can_follower_l | FTBSX7UI | /left/state；action[:7] |
| right | can_follower_r | FTC2FNQP | /right/state；action[7:14] |

实际修改 ~/.config/yam/arms/yam_left.yaml、yam_right.yaml；仓库忽略的本机配置副本也同步。默认 profile 加载验证通过。未连接/使能机器人。旧守护进程需在支撑好从臂后通过安全菜单或 scripts/safety off 正常关闭，再启动新遥操。

## 历史数据

按 MCAP session.start_time_ns 筛选，北京时间 2026-09-13 00:00 起：

- pick_huotuichang：100 条。
- pick_up_the_small_square_and_place_it_in_the_groove：100 条。
- calib_left_main、calib_right_main、calib_left_wrist、calib_right_wrist、calib_table_check：各 1 条。

共 205 条，原大小 3,932,584,999 字节。交换两侧 state topic、action/requested.position、action/accepted.position 与 requested_position 的两个 7 维分段、session.arm_configs 及 quality 的 state counts/rates。相机消息字节、state 数值字节、消息序号及各时间戳保持精确不变。session 添加 arm_side_repair 标记；不要二次交换。

4,342,972 条消息逐条核对转换规则并检查 CRC；205 份原件和修正件 SHA-256 均与迁移日志一致。重新扫描：0 条待修复、205 条已修复、4 条早于日期范围。224 个 HDF5 内部创建时间均早于截止日期，未修改。未将本次左右修复当作全量视频质量或硬件标定验收。

原路径已替换为修正版；备份在 /mnt/sdb/yam-arm-side-backups/20260915-v1，包含 plan.json、completed.jsonl 和完整原件。配置备份在 ~/.config/yam/backups/arm-side-repair-20260915-v1。原先拟使用的 /mnt/sdb/YAM/backups 无写权限，在修改任何数据前改用上述备份位置。

抽查修正后六关节速度 RMS：calib_left_main 左/右 0.2082/0.0228 rad/s；calib_right_main 左/右 0.0167/0.2510 rad/s；小方块第 1 条左/右 0.0136/0.2812 rad/s，已与视频中操作侧别一致。

## 工具与验证

工具：gello_software/scripts/repair_arm_sides.py。默认只生成清单，应用时核对源文件指纹、保留原件，逐消息验证后原子替换每个文件。遇到未知映射、额外 topic/字段、近期 HDF5 会停止，避免盲目交换。支持中断后重新扫描，并拒绝重复交换。

测试：gello_software/tests/test_repair_arm_sides.py，5 项通过，包括完整文件转换、未知格式拒绝、配对整体交换、备份与过期清单保护、修正配置的新录制跳过。README 与 feishu 已更新，并保留 feishu 原有未提交内容。

以前提取的标定关节观测/NPZ、训练缓存若基于旧数据，需从修正版重新生成。video/side_audit_small_square 的旧配图保留为修复前证据；repair_final_audit.json 是本次最终统计。

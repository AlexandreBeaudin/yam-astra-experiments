# YAM 当前固定标定：yam_chenyuan_20260915_v1

用户实测确认：左主相机现有结果＋右主相机重新固定板后的角点优化结果，与桌面距离测量大致匹配，并明确要求按这套参数推进、固定版本、清理中间过程。

以下相对路径均以 `/home/chenyuan/workspace/real2sim` 为根。

## 唯一当前参数

- 版本文件（Git）：`configs/stations/yam_chenyuan_20260915_v1.json`。
- 来源与验收记录（Git）：同目录 `.provenance.json`。
- 本机使用入口：`configs/yam_station.local.json`，内容与固定版本完全一致。
- 证据/审查入口：`runs/calibration_releases/yam_chenyuan_20260915_v1/index.html`。

标记 `calibration_status=verified` 的依据是本次用户实测确认与此前离线验证，适用范围为场景重建和摆放指导。用户没有提供精确测量数值，因此不把“大致匹配”表述为毫米级外部计量认证。工具TCP、工作区边界、接触动力学仍是其他配置项，不因相机标定接受而自动补齐。

## 固定来源

| 参数 | 采用来源 |
|---|---|
| 左 base 到 main | calib_left_main_new 原位姿求解 |
| 右 base 到 main | calib_right_main_verify 重新固定板后的训练角点优化 |
| 左右腕相机到 link6 | calib_left_wrist_new / calib_right_wrist_new 原求解 |
| 双基座关系 | left_base_T_main × inverse(right_base_T_main) |
| 桌面 | 已有板面拟合，按用户提供约35mm向下修正 |
| 内参 | 匹配序列号、640x480流的设备工厂内参快照 |

世界坐标为 left_base。沿桌面法线投影后的距离：左 base 到 main 390.335mm、右 base 到 main 409.292mm、双 base 798.363mm。

右主相机旧批次、删两帧重拟合及其他试验结果不作为运行配置候选；后续不再根据旧候选与固定版本的差异阻止正常场景重建。

## 腕相机的正确用法

主相机保存 `world_T_camera`。腕相机保存 `robot_mount.arm/link/link_T_camera`，不能填入某个静止姿态下的世界矩阵。

`resolve_camera_transform(station, camera_name, base_T_link)` 使用
`world_T_base @ base_T_link @ link_T_camera`，其中 FK 必须来自声明的 arm/link 和对应时刻。未提供FK会明确报错，不默认零关节姿态。坐标系为相机光学坐标。

保存的腕相机畸变为 RealSense inverse_brown_conrady；消费端必须按相应模型处理，不能直接套用OpenCV forward系数。主相机系数为零。

## 清理规则

保留原始 `/mnt/sdb/YAM/data` MCAP，保留本固定版本的源参数JSON、输入文件SHA256、最终选帧、留出验证图与桌面分组。移除已被替代的三个 runs 标定根目录（含删帧重拟合子目录）和本轮临时下载包；通用求解/诊断脚本保留，历史结论仍在Git文档中。旧报告的 runs 路径不再是有效入口，以本文件为准。

清理清单见证据目录 `cleanup.json`。不删除物料资产、数采数据、迁移备份或用户未提交代码。

## 检查

3项动态相机安装测试、3项核心回归通过。固定配置经加载器验证；使用真实YAM模型FK验证腕相机随关节变化、主相机固定；本机配置与版本文件字节一致。未启动机器人或数采程序。

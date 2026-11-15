# YAM统一仿真环境交接 · 2026-09-15

用户决定整个real2sim项目统一使用 **Isaac Sim 5.1.0 / Python 3.11 / PhysX**，不按机器人平台另选版本。
共享项目：`/home/chenyuan/workspace/real2sim`，提交 `968b08e`，tag `simulation/isaacsim_5_1_verified_20260915`。共享项目根AGENTS.md、版本契约及锁文件已同步。

## 已完成与证据

Xtrainer本机32个离线阶段、54项单元测试通过；3000张三视角图像、5组物理对照和7种物料通过软件回归。54个杯子摆放候选中49个通过，九层覆盖与旧版相同；TASK-X-0001的36个摆放和连续三条录制联调通过。实际RTX投影45个点最大误差0.8964px，机器人12个臂连杆的运动轨迹与旧版一致。

独立进程退出兼容问题已经修复，并用故意抛异常验证错误不会当作成功。原始纸杯模型仍未完成完整释放/叠杯；升级回归通过不等于物理任务成功。录制联调用历史图像与模拟事件，未启动真实设备。

本轮同步了共享代码、版本约束、验收报告和小体积证据；大体积视频和原始输入仍在Xtrainer。本轮没有在YAM安装/运行Isaac，也没有验收YAM完整场景。

## 下一步

1. 安排驱动兼容/升级检查：YAM现为550.163.01，官方5.1要求页列出的Linux驱动为580.65.06。本轮未改驱动、未重启。容器仍依赖宿主驱动，不可通过改仿真版本绕过。
2. 用已有 `/usr/local/bin/uv` 安装独立 `.runtime/isaacsim-5.1`；不要改YAM/gello_software/.venv。工具venv没有pip，不必先往里面装pip。
3. 导入/转换YAM模型，用固定 `yam_chenyuan_20260915_v1` 参数搭建双臂、桌面、主/腕相机场景。**之前先用MuJoCo搭建YAM仿真场景的建议撤销**，MuJoCo仅用于模型FK/IK和标定。
4. 验证三视角投影、机器人姿态和摆放分布，再验收YAM任务接触与真实数采。不能将物体绑定到夹爪代替抓取。
5. 保留一次启动、原按钮开始/结束、手动前后指引、开始时保存投影、事后对照第一帧；不恢复人工标点或两分钟门槛。
6. 物料重建工作及原有未提交修改均保留；YAM现场和完整场景仍需该机后续实现。

驱动准备好后执行：

```bash
cd /home/chenyuan/workspace/real2sim
bash scripts/install_isaac.sh
bash scripts/isaac_python.sh scripts/smoke_isaac.py
```

## 入口

- [版本契约](../../real2sim/configs/simulation_runtime.json)
- [安装与兼容修复](../../real2sim/docs/ISAACSIM_5_1_MIGRATION.md)
- [离线验收报告](../../real2sim/docs/ISAACSIM_5_1_TEST_REPORT.md)
- [验收JSON](../../real2sim/runs/isaacsim_5_1_migration/acceptance.json)
- [代码与证据指纹](../../real2sim/runs/isaacsim_5_1_migration/release.json)

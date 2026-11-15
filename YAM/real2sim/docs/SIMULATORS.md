# 仿真器选型记录

**当前决策（2026-09-15）：Xtrainer 与 YAM 统一 Isaac Sim 5.1.0 / Python 3.11 / PhysX。**
唯一版本契约为 `configs/simulation_runtime.json`，唯一仿真入口为 `scripts/isaac_python.sh`。
MuJoCo 仅保留 YAM 标定/FK/IK 的模型工具用途，不作为第二套正式仿真后端。
[迁移说明](ISAACSIM_5_1_MIGRATION.md) · [测试报告](ISAACSIM_5_1_TEST_REPORT.md)。

以下为历史选型讨论，不能据此重新选择平台专用仿真器或回退版本。

状态更新（2026-09-14）：首版已选用并跑通 Isaac Sim 4.5 RTX，复用官方机器人 USD。未安装或验证官方 Isaac Lab 任务，接触回放后端仍需实验。下文保留初始选型依据。

| 候选 | 适合承担什么 | 本项目需要验证什么 |
| --- | --- | --- |
| MuJoCo | 机器人运动、接触和轨迹回放候选主后端；支持 MJCF 与 URDF 导入 | 夹爪/杯壁碰撞表示、非中心主点投影、离屏渲染、材质与外观上限 |
| Isaac Sim | USD 场景、机器人仿真与渲染候选主后端；有 URDF 导入器 | GPU/驱动兼容、部署成本、相机配置、模型导入与执行器语义 |
| Blender | 参数化道具制作、材质、离线高质量渲染；可承担纯视觉回放候选 | 动画与相机时间/坐标一致性；不把动画输出当机器人接触仿真验证 |

建议：已有官方 X-Trainer 环境和 USD 来源，首版优先复用 Isaac Lab / Isaac Sim，减少机器人搭建成本；若运行条件或版本兼容不满足，再试 MuJoCo。
Blender 可作为资产工具搭配任一主后端，避免一开始实现跨引擎实时同步。
这是工程判断，不是已完成的性能测试。Gazebo/其他引擎保留扩展点，出现 ROS 集成等需求时再评估。

## 最小对比实验

先在官方环境运行 50 个时间样本，包含固定相机和腕部相机；需要比较时用同一份中立场景测试备选：

1. 完整机器人、桌面、两个简单空心杯；核对关节名称、零位、连杆和夹爪。
2. 按标定的完整 K（含非中心主点）渲染，检查光学轴、flip、裁剪和畸变处理。
3. 检查 TCP/已知几何关键点投影；不接受只设置近似 FOV 就称为内参匹配。
4. 记录安装耗时、导入问题、每帧时间、显存、无窗口运行与三视角输出能力。
5. 简单放置/抓取接触测试只用于确认能力，不作为任务数字孪生验收。
6. 依据正确性、目标档位、运维成本选择主后端，固定实际验证的软件版本。

本轮已确认 RTX 4090、驱动 580.178.04 可用；无窗口三视角渲染完成。

## 官方参考

- [MuJoCo 建模与模型格式](https://mujoco.readthedocs.io/en/stable/modeling.html)
- [MuJoCo 渲染](https://mujoco.readthedocs.io/en/stable/programming/visualization.html)
- [Isaac Sim URDF 导入](https://docs.isaacsim.omniverse.nvidia.com/latest/importer_exporter/ext_isaacsim_asset_importer_urdf.html)
- [Isaac Sim 系统要求](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html)
- [Blender 渲染](https://www.blender.org/features/rendering/)

安装前重新核对版本与系统要求；不使用上述候选关系承诺特定性能或模型转换无损。

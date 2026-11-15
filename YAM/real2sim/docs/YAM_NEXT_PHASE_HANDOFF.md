# YAM 下一阶段交接：仿真重建、分布监督、BOS / 飞书

这是当前接手入口，更新于2026-09-15。仿真环境已统一到5.1，见第1节和项目根AGENTS.md。下一位agent应在YAM本机继续开发，不重新迁移、不重启标定优化。三个工作方向为：①搭建YAM仿真场景；②完成数采分布监督；③接通BOS与飞书的上传/读取闭环。

未另行注明的共享模块/configs/runs路径均相对 `/home/chenyuan/workspace/real2sim`。

## 0. 当前确定的基线

- 原项目 `/home/chenyuan/workspace/YAM`，原采集代码 `gello_software/`。分支 `migration/real2sim-guidance`；标定固化入口提交 `e356517`。
- 通用项目 `/home/chenyuan/workspace/real2sim`，分支 `refactor/platform-adapters`；标定固化提交 `e063a82`，tag `calibration/yam_chenyuan_20260915_v1`。
- 固定版本：`configs/stations/yam_chenyuan_20260915_v1.json`。实际入口 `configs/yam_station.local.json` 与其一致。用户已直接测量，确认桌面投影距离大致匹配并要求按该版本推进。不要再以旧优化参数差异阻止开发。
- 世界坐标为左基座；左main采用原候选，右main采用重新固定板后的角点优化，左右腕相机采用各自link6安装变换。桌面含用户近似35mm板厚修正。
- 预测桌面距离：左base到main投影390.335mm、右base到main投影409.292mm、双base798.363mm。
- 相机内参来自匹配设备工厂配置；相机标定适用于已确认的重建/指导用途，不自动代表TCP、工作区边界或接触参数已测定。
- 旧优化runs目录已按用户要求清理。最终证据保留在 `runs/calibration_releases/yam_chenyuan_20260915_v1/`，原始 `/mnt/sdb/YAM/data` MCAP 保留。

先读 YAM/AGENTS.md：用户可见改动必须更新YAM根README.md和feishu.md。原仓库存在用户未提交的README、feishu、handoff修改及ARM_SIDE_REPAIR脚本/测试，保持它们，不把它们混入自己的提交。当前Git origin是本地bundle归档，尚未配置GitHub协作远程；两端并行修改时需显式同步，不能假设git pull能找到另一台电脑的新提交。

## 1. YAM仿真环境重建

已有：MuJoCo模型 `/home/chenyuan/workspace/YAM/i2rt/i2rt/robot_models/arm/yam/yam.xml`，六关节FK/IK适配器 `src/real2sim/platforms/yam.py`，固定站位参数，部分已迁移物料资产。尚无已完成验收的YAM双臂+桌面+三相机场景。

**用户已于2026-09-15确定：Xtrainer与YAM统一使用Isaac Sim 5.1.0 / Python 3.11 / PhysX。** 之前建议以MuJoCo搭建YAM最小仿真场景的方案已撤销。MuJoCo仅保留标定与模型FK/IK用途。YAM需将机器人模型导入/转换到统一Isaac场景；不能认为Xtrainer的USD任务入口已自动支持YAM。统一配置为 `configs/simulation_runtime.json`，入口为 `bash scripts/isaac_python.sh`，依赖使用共享锁文件。详见 [迁移与部署说明](ISAACSIM_5_1_MIGRATION.md) 和 [本机离线验收报告](ISAACSIM_5_1_TEST_REPORT.md)。

YAM当前驱动为550.163.01，低于官方5.1要求页列出的580.65.06。先安排驱动兼容/升级检查，再安装独立 `.runtime/isaacsim-5.1`；不要修改数采venv，不要用容器或MuJoCo绕过驱动问题。本轮交接不代表YAM已经安装/运行5.1或完成场景验收。YAM已有 `/usr/local/bin/uv`，可直接用安装脚本；其工具venv没有pip，不必先往里面装pip。

具体顺序：
1. 构建双臂场景时给两套模型的body/joint/actuator名称加左右命名空间。按固定world_T_left_base和world_T_right_base安装；保留原六关节顺序，读从臂state，不把主臂action当作实测姿态。
2. 主相机用world_T_camera；腕相机挂到各自link6。`platforms/profile.py:resolve_camera_transform(station,name,base_T_link)`已支持运动相机，必须传对应臂/时刻的FK，不可把腕相机固定在世界坐标。
3. 渲染坐标与光学坐标显式转换；校验640x480图像、K、主点、保存方向。D405使用inverse_brown_conrady，不能直接当普通OpenCV正向畸变处理，也不能只凭一个fovy认为完全匹配。
4. 依据桌面平面方程放置桌面；外形尺寸/工作区边界仍需实测或登记。link6是法兰，不是已标定TCP。核对实际夹爪、支架、碰撞体与模型是否一致。
5. 从一条真实MCAP抽多个关节姿态，输出main/left/right的仿真与真实对照。只检查机器人/桌面几何时不要把物体强行绑定到夹爪冒充抓取。
6. 场景参数、资产ID/版本、标定版本、模型来源、单位和哈希写入scene manifest；做到同一输入可复现。

完成条件：场景可离线加载和渲染；三相机安装与FK一致；真实轨迹的机器人姿态可回放；对照及误差有记录。真实接触抓取另行验收，不能用视觉对齐代替。

## 2. 数采分布监督

已有原生接入：YAM `scripts/run_bimanual_teleop.py --guidance-bundle`，原camera preview内的前后指引和overlay，MCAP `/guidance/initial` 在实际开始录制时固定目标，`scripts/export_guidance.py` 可导出首帧对照。之前软件回归通过，尚未用YAM实际任务完成布局生成到连续采集的端到端验收。

必须保留用户已确定的操作：启动一次teleop → 手动选指引/摆放 → 原扳机开始 → 原按钮结束保存 → 手动还原 → 上一条/下一条 → 再录。不恢复人工标点门槛、两分钟有效期或一次性票据，不要求每条重启，不自动替操作者切换指引。目标快照不等于真实摆放已核对。

下一步：
1. 从任务表精确匹配YAM任务ID，解析关联物料record_ids，再核对用量与资产版本。当前飞书配置是共享物料主表；尚未配置一个最终YAM任务。历史TASK-X-0001属于Xtrainer，不可当YAM默认任务。库存数量不等于任务用量。
2. 将YAM运动学与场景接入候选生成器。VLM可提出初始摆放，确定性采样可作基线；视野、边界、接近/抓取姿态IK、遮挡和碰撞由程序校验，VLM不能自行宣布通过。
3. 几何检查只解决其声明范围：单点IK不等于路径无碰撞，物体静置不等于抓取成功。记录种子、分层、拒绝原因与未实现检查。
4. 生成正确platform=yam、main相机配置和固定calibration_version的manifest及RGBA目标投影，使用约50%透明度。不得重命名Xtrainer包来绕过平台校验。
5. 先做离线首帧对照，再现场连续录几条，覆盖换指引、同指引重复、失败保存、废弃；确认目标跟随同一个MCAP生命周期。
6. 实际摆放的首帧核验/分布统计在录制后进行。将“建议分布”和“实际达到的分布”分开统计。

已有历史模块可参考：guidance.py、collection.py、feishu.py、platforms/*、prepare_goal_supervision.py、generate_layouts.py。部分脚本仍固定Xtrainer任务/路径，不直接运行它们来宣称YAM已完成。

## 3. BOS与飞书：配置已迁移

用户本次明确授权通过SSH直接复制本机已有凭证。已将以下文件放到YAM当前用户私有目录：

- `/home/chenyuan/.config/real2sim/feishu.json`：App ID / App Secret。
- `/home/chenyuan/.config/real2sim/bos.json`：BOS AK / SK。

目录700、文件600，属主chenyuan。不要输出内容、粘贴进聊天、写入Git、打包进资产或让用户重新输入。项目业务配置已复制到 `configs/feishu_materials.local.json` 和 `configs/bos.local.json`（600，Git忽略）。它们保存表映射、endpoint/bucket/prefix及回写配置，不复制Xtrainer硬件配置。

凭证加载器：`real2sim.credentials.load_credentials` 和 `real2sim.storage.bos_auth.load_credentials`。成对环境变量优先于文件；后续若读取了意外身份，检查是否存在旧环境变量，但不要打印其值。

### 飞书读取

```bash
cd /home/chenyuan/workspace/real2sim
.venv/bin/python scripts/sync_feishu_materials.py --config configs/feishu_materials.local.json --output runs/feishu_materials
```

本轮已从YAM真实读取物料表，得到52条记录，无缺失可选列。快照 `runs/feishu_materials/latest.json`。读取是元数据同步，不能自动认定附件已下载、资产已重建或登记的状态等于物理验证通过。

任务关联读取沿用标准Client/list_all及任务适配器；先发现具体YAM任务表与字段，不猜table_id。附件下载需要相应权限和独立下载/校验流程。

### BOS上传与飞书回写

现有 `scripts/upload_bos.py` 与 `storage/bos.py` 支持内容寻址清单、幂等文件上传、HEAD校验、分片、上传回执；完成清单最后发布。回执必须在源目录之外。

```bash
cd /home/chenyuan/workspace/real2sim
# 先检查选定资产的上传计划；替换成真实产物目录
.venv/bin/python scripts/upload_bos.py /absolute/path/to/exported_asset --config configs/bos.local.json --dry-run --receipt runs/bos_uploads/plan.json
# 执行实际发布时移除 --dry-run，并使用独立回执路径
```

**复制来的BOS配置已启用上传完成后的飞书回写**：物料清单必须有唯一material_id/source_record_id/version，完成后更新现有资产地址和版本字段，再读回核对；其他任务报告无唯一物料映射时会跳过。上传成功与飞书回写成功是独立状态，回写失败后重跑可复用BOS文件。不要上传整个工作区，避免把凭证、本机配置或无关数据带上云。不会自动发送飞书消息。

本轮只进行凭证迁移与只读联通测试，不通过写生产表或上传测试对象来验证权限。已存在的Xtrainer上传与回写成功记录仅证明之前该身份可用，不能说YAM本轮已做过真实上传/回写。

### BOS读取的下一步

现有模块以上传为主。BOS SDK可读取对象，本轮只读探测结果另见连接报告。完整资产消费器尚需实现：从bos://manifest读取完成清单 → 下载到临时缓存 → 校验每个文件大小/SHA256 → 全部成功后原子发布版本目录；拒绝路径穿越/不安全链接，不把私有地址转换成永久公开URL。下载状态与物理验证状态分开。

建议任务/物料记录保存稳定的bos://清单或版本路径，仿真器只读取经校验的本地缓存；不要让训练和渲染过程依赖易过期的临时下载URL。

## 4. 环境和验收记录

原采集解释器：`YAM/gello_software/.venv/bin/python`，本轮不修改其依赖。workbench `.venv` Python3.11，原有科学计算依赖部分只读复用i2rt venv，OpenCV在.runtime；来源见 runs/migration/environment.json。BOS SDK安装/连通结果见本轮连接报告，不假设原采集环境也安装了SDK。

当前标定冻结相关6项测试已通过；新增数采接口4项及原生回归历史共86项通过，但不是本轮现场数采验收。新增代码运行相关测试，真实验收分别记录，不重复报告历史数字冒充当前执行。

下一位agent第一步：读本文件与当前标定文档，检查两个Git工作区；先生成YAM最小双臂/桌面/三相机场景，再接一个明确任务的布局包。云端读写作为产物发布/消费层接入，不嵌进机械臂控制循环。


## 5. 本次凭证迁移与联通的最终结果

- 两份凭证、两份业务配置通过SSH复制，字节一致已核对；凭证目录700、文件600。未输出凭证值或将其写入Git。
- 飞书读取通过：52条物料，缺失可选列0；快照在 runs/feishu_materials/latest.json。
- BOS SDK安装成功：bce-python-sdk 0.9.79，另装crc32c 2.9.post0、future 1.0.0、pycryptodome 3.23.0、six 1.17.0，仅修改workbench/.venv。
- BOS真实HEAD与GET通过：读取一个已存在纸杯资产的完成清单，380字节、3个文件条目，HEAD大小一致。未向云端上传或改写任何对象。
- 本机MAT-00019/v001的上传dry-run通过：3文件、85,495字节，无网络写入、无飞书回写。
- 机器报告：runs/migration/cloud_access/connection_report.json；上传规划：同目录bos_upload_plan.json。

这些结果支持后续直接开发，不需再次配置账号。完整BOS下载缓存器、YAM任务字段映射、实际YAM产物上传和飞书回写验收仍由下一阶段完成。应用现有权限若不足，应根据具体接口错误处理，不重复索要已迁移的Secret。

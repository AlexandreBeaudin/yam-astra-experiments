# NUC：分布审查、摆放对齐和网页采集

2026-10-20，通过 SSH 读取 Xtrainer 的 `/mnt/sdb/rhos_collector/dobot_xtrainer20250912/dobot_xtrainer` 和相邻 real2sim。共享子模块从 `eeb45ed` 更新到 Xtrainer 已提交版本 `29b11eced78255dac1a8d076eccce48bd20570fb`。没有复制异地标定、私有凭证或采集数据。原部署及用户改动保留。

## 已部署入口

- 管理/审查页：本机浏览器打开 **http://127.0.0.1:8780/**。用户服务 `yam-guidance-manager.service` 在登录后启动。管理器只读入离线布局、已完成 MCAP 和首帧，不连接机器人或相机。
- 实时摆放与采集页：由 `scripts/collect` 启动原 YAM 采集器，使用终端打印的 `http://127.0.0.1:8765/<token>/` 链接。不要省略 token；相机由采集器独占，网页共享帧。
- 透明目标叠加于 main 相机，提供前后切换、透明度调整；保留 main/left/right、三台 D405、rotation=0。
- 网页“获取控制权”后可准备、结束、保存成功/失败、废弃、停止；仍先在终端确认使能，每条录制仍需双主臂扳机“松开→按到底→松开”。停止后的电机处置仍通过原终端安全菜单。
- 初始指引在录制开始时固定到 MCAP `/guidance/initial`，包含布局、split/group、标定和投影快照；事后更换指引不会修改已录数据。网页切换指引是人工操作，不自动改变机器人姿态。

## 没有指引时使用采集网页

```bash
cd ~/workspace/YAM/gello_software
scripts/collect --task commissioning --no-guidance --check-only
scripts/collect --task commissioning --no-guidance
```

`collect --check-only` 只检查配置和命令，不访问硬件。原 `scripts/teleop --check` 另负责相机出流及设备预检。启动前如存在 FAULT 守护进程，应先托稳从臂，通过 `scripts/safety off` 正常退出；不要强杀保持机械臂的进程。

## 接入本机已完成的 real2sim 建模和标定

本次尚未收到本机已验证 bundle 的路径。因此不会伪造标定、把 Xtrainer 投影作为 YAM 投影，也没有生成假任务的正式发布。以下使用实际文件替换大写占位符。

1. 复制 `gello_software/configs/guidance/station.example.json` 到 `~/.config/yam/guidance.json`。
2. 设置 `station_id` 与当前 profile 的 name 相同（本站 `rhos-nuc-1`），`task_ids` 为实际任务编号、`task_id` 为默认任务；`calibration_bundle` 指向本机接受的标定参考指导包；`registry` 使用本机绝对目录。
3. 指导包至少包含 `manifest.json` 和每条布局的 `<id>.png`。manifest 必须声明 `task.platform=yam`、正确 task_id、`calibration_status=verified`；camera 的序列号、image_size、saved_image_rotation_deg 必须对应本机 main（218622271430、640×480、0）。candidate 包的 geometry/IK validation 必须通过；已停用、父版本变化、投影变化、跨工位和跨标定均拒绝。
4. 在本机审查真实包，确认后登记为下次采集版本：

```bash
cd ~/workspace/YAM
real2sim/.runtime/manager/bin/python real2sim/scripts/collection_manager.py \
  --workspace ~/.local/share/yam/collection-manager import-bundle /ABS/YAM_BUNDLE \
  --platform yam --station rhos-nuc-1
cd gello_software
scripts/collect --task TASK-YAM-ID --bundle /ABS/YAM_BUNDLE --check-only
scripts/collect --task TASK-YAM-ID --bundle /ABS/YAM_BUNDLE --publish
scripts/collect --task TASK-YAM-ID --check-only
scripts/collect --task TASK-YAM-ID
```

发布只代表操作员选择布局版本，不代表真实摆放/碰撞/任务已验收。按任务、工位和完整相机参数哈希查找最新有效发布；没有匹配版本直接停止，不悄悄进入无指引采集。显式 `--bundle` 可试采而不发布。每次启动固定版本；新发布只影响下次启动。

本机管理页已导入 commissioning 试采 MCAP，显示为未关联布局（unlinked），因为旧数据录制时没有指导包。新录数据可用同一命令导入：

```bash
cd ~/workspace/YAM
real2sim/.runtime/manager/bin/python real2sim/scripts/collection_manager.py \
  --workspace ~/.local/share/yam/collection-manager import-episode ~/data/YAM \
  --platform yam --recursive
```

## 分布生成范围

已同步 Xtrainer 的独立 train/validation 种子、配额、分箱、近重复拒绝、分布审计和发布完整性校验。共享 YAM 抽屉任务生成器支持按六种物料池/方位分箱及几何、位置代理 IK 筛选，默认 100 train + 30 validation；不是任意新任务的通用自动规划器。

对于已有该类建模与任务配置，可在独立工具环境调用：

```bash
cd ~/workspace/YAM/real2sim
.venv/bin/python scripts/prepare_yam_drawer_guidance.py \
  --source /ABS/material-source.json --config /ABS/task.json \
  --station /ABS/yam-station.json \
  --model ../i2rt/i2rt/robot_models/arm/yam/yam.xml \
  --output /ABS/NEW_BUNDLE
```

这是几何投影和模型 FK/IK 检查，不是接触仿真或全路径碰撞验收。其他任务需对应 task adapter，不能直接套用抽屉物料及规则。真实 Isaac 场景渲染仍使用独立 Isaac Sim 5.1.0/Python 3.11/PhysX 环境；本轮未部署 NUC GPU 仿真运行时，也未声称完成真实场景生成。

Xtrainer 管理器的“生成/全场景渲染/发布”工作流存在平台专属限制，本机 YAM 使用上述生成与发布 CLI、管理页审查和原生采集网页；没有将 Xtrainer 全场景按钮宣称为已适配 YAM。布局修改仍应在 real2sim 工具链中重验再发布。

## 访问与环境

管理器独立环境 `real2sim/.runtime/manager`；几何工具环境 `real2sim/.venv`；采集环境 `gello_software/.venv` 保持原依赖。服务绑定 localhost，不会自动使能机械臂。异机浏览使用 SSH 隧道：

```bash
ssh -N -L 8780:127.0.0.1:8780 -L 8765:127.0.0.1:8765 rhos@NUC_IP
```

然后访问本地 8780 或终端打印的 8765/token 页面。部署验证包含原生网页控制、指引快照、拒绝错平台/任务/标定/工位、发布后篡改、分布审计等离线测试；不替代真实投影对齐与操作者采集验收。

## 本轮离线验证

- YAM 新入口/指引快照/网页控制/依赖测试：14 passed。
- 共享版本发布校验：5 passed；管理目录/证据：8 passed；管理工作流：2 passed；分布策略：5 passed。
- 管理页 HTTP 与带同源请求头的目录 API 已返回成功，包含 1 条未关联的真实 MCAP 证据。
- 独立工具环境已安装 numpy/scipy/OpenCV headless/Pillow/MuJoCo（仅模型 FK/IK），解析后的完整依赖在 `~/.local/state/yam-deploy/guidance-utility-requirements.txt`。未更改采集虚拟环境依赖。
- 此轮没有启动机械臂、相机、Isaac，未重启正在运行的安全守护进程。

浏览器验收通过：真实 commissioning 首帧加载、离线指导前后切换和透明度调整均成功，JavaScript 错误为 0。报告和截图：`~/.local/state/yam-deploy/guidance-browser-check/`。离线 UI 测试使用临时合成投影，未登记到正式任务或发布目录。

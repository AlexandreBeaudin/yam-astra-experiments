# TASK-YAM-0015：6 条斜放试采布局（2026-09-21）

用户确认按正面宽250、进深314、高254 mm生成，并同意抽屉60°–75°斜放。仅供现场试采，未宣称完成抓取或路径验收。旧两个包保留 RECALLED 标记。

## 本机入口

新包：`/home/chenyuan/workspace/real2sim/runs/yam15_trial_20260921_v1`。
管理页：http://127.0.0.1:8780/ ，选择版本前缀 **2769fc7290**（6条，已入目录/待审批），不要选择旧60条停用包。目录标签表示没有发布审批记录，不代表拒绝本轮用户授权的试采。`index.html`、`overview.jpg` 可离线查看。

新包是标定几何线框投影，不是Isaac实体渲染：黄色大框为抽屉外包络；前方细框为200mm拉出预留区域；其余框为本条三件小物体。几何程序不进行仿真或使用其他渲染后端，本轮没有启动Isaac、相机或机械臂。

每条使用三种不同物料各一件，所有物品放进同一个上层抽屉，起始关闭。6种物料各出现3次，6个朝向/左右分层各1条，共6种组合；本小批次不是完整20种组合覆盖。已有相机标定、工作区、14px入镜边距、15mm物体间隔和200mm拉出距离未放宽。把手前伸按29.7mm用于法兰位置代理，方向/夹爪姿态仍需现场核对。

原近正向朝向范围无法同时容纳修正后的柜体与拉出区域并完整入镜；本次只调整抽屉位置采样与朝向，保持原检查门限。

## 试采

```bash
cd /home/chenyuan/workspace/YAM/gello_software
scripts/teleop --recording-format mcap --control-hz 200 --state-hz 200 \
  --task-name TASK-YAM-0015 \
  --guidance-bundle /home/chenyuan/workspace/real2sim/runs/yam15_trial_20260921_v1
```

相机网页：http://127.0.0.1:8765/ 。先核对抽屉和物体外轮廓是否与投影相符，再使用原双扳机按下、松开后录制流程。先试前两条；录完还原并切下一条。保持 task-name 与指引包任务一致，避免证据导入时任务绑定不匹配。数据仍写入 TASK-YAM-0015；以 MCAP 保存的指引包哈希区分本次试采，管理页关联不可由当前选择补造。

SSH查看时在自己的电脑转发8765和8780，然后访问相同localhost地址。

## 检查与复现

6条全部通过工作区、完整视野、平面间距、投影分离、拉出区域检查；每条5个法兰位置代理检查至少一臂在关节限位内可达。30个位置代理不是实际TCP姿态、扫掠碰撞或接触验证。

`integration_report.json` 验证新包逐条快照、网页切换、同一writer连续三条录制（含失败和重复指引）、开始时固定目标和首帧导出。使用临时合成图像MCAP，不混入真实数据目录。现场投影与抓取等待本次试采验证。

在YAM根目录复现，输出目录必须全新：

```bash
/home/chenyuan/workspace/real2sim/.venv/bin/python \
  real2sim/scripts/prepare_yam_drawer_guidance.py \
  --source /home/chenyuan/workspace/real2sim/runs/task_yam_0015_guidance_input_v2/source.json \
  --config real2sim/configs/tasks/task_yam_0015_trial_20260921.json \
  --station real2sim/configs/stations/yam_chenyuan_20260915_v1.json \
  --model i2rt/i2rt/robot_models/arm/yam/yam.xml \
  --output /path/to/NEW_trial_bundle
```

新增配置显式指定 layout_count=6；原生成器默认60条流程及覆盖保持兼容，旧尺寸配置仅用于历史复现。

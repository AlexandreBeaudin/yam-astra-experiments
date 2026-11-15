# TASK-YAM-0015：随机三物品放入上层抽屉

2026-09-19，当前产物是可接入数采的候选指引包；已通过离线集成测试，未启动机械臂或相机，尚未现场验收。不要把 `calibration_status=verified` 解读为任务抓取验证通过。

## 任务来源与规则

飞书 YAM 数采记录的 TASK-YAM-0015，关联物料按 record_id 读取。读取并保存“任务细节”和“注意事项 & 质检要点”：拉开抽屉、依次拿起物品、全部放入同一个抽屉、推回抽屉。用户补充明确：每条三个随机被操作物，统一上层。本版按三种不同物料、每种一件实现，不用库存数量推断任务用量。初始关闭。

固定物料 MAT-00050 抽屉。可选池：MAT-00005 小方块、MAT-00012 足球、MAT-00021 海绵擦、MAT-00037 纸手帕、MAT-00038 玩具遥控器、MAT-00048 勺子。

60组候选覆盖6选3的20种组合，每种组合3次，对应抽屉朝向[-15,-5]、[-5,5]、[5,15]度。种子20260919，打乱顺序与物体位置。每件物料出现30次；左右分布与朝向组合6层各10组。几何拒绝采样之后的空间位置不是均匀分布，不代表所有可行姿态均已覆盖。

## 位置与预览

YAM共享项目 `/home/chenyuan/workspace/real2sim`：

- 配置 `configs/tasks/task_yam_0015.json`
- 飞书快照和照片 `runs/task_yam_0015_guidance_input_v2/`
- 指引包 `runs/task_yam_0015_guidance_v1/`
- 管理者预览 `runs/task_yam_0015_guidance_v1/index.html`：可直接用浏览器打开，不需要启动机器人。
- 总览 `overview.jpg`；逐条透明投影 `yam15_000.png` 等；逐条深色背景预览 `yam15_000_preview.jpg` 等。
- `manifest.json` 保存任务两列原文、随机规则、逐条用料/几何/IK证据、标定和照片/代码哈希。
- `integration_report.json` 记录真实包的原生网页接口及MCAP集成验证；图像输入为合成空白测试帧，不能当作现场对齐证据。

投影是根据尺寸和照片判断摆放方向生成的保守三维包络线框，不是照片重建网格或Isaac渲染。球、勺等也用外包络指导占位。细框表示抽屉拉出预留区域；方框不是接触碰撞模型。仿真统一Isaac Sim 5.1不变，本次没有运行物理仿真或其他仿真后端。MuJoCo仅用于已有YAM模型FK及法兰位置求解。

## 数采使用

确认当前站位仍匹配 `yam_chenyuan_20260915_v1` 后，现场试采使用：

```bash
cd /home/chenyuan/workspace/YAM/gello_software
scripts/teleop --recording-format mcap --control-hz 200 --state-hz 200 \
  --task-name TASK-YAM-0015 \
  --guidance-bundle /home/chenyuan/workspace/real2sim/runs/task_yam_0015_guidance_v1
```

原带token的相机预览页会显示逐条物料编号/名称/数量、上层初始关闭提示和候选未现场核实提示。按照当前清单备3件小物体和1个固定抽屉，按投影摆放，保持原使能/双扳机录制/成功失败保存/废弃流程。录完手动还原，再点击上一条/下一条。无需标点、不重启、不自动换指引，可以重复同一条。

录制开始时固定 `/guidance/initial`，包含当前三件物料和抽屉、任务规则、标定版本、目标投影。录制开始之后切换不改变本条快照。共享GuidanceSession优先采用layout.required_materials，旧包仍回退顶层required_materials；不能把整个候选池误写成每条实际用料。

```bash
.venv/bin/python scripts/export_guidance.py /path/to/episode.mcap --output /new/review_directory
```

导出 `placement_guidance.json`、`guidance_projection.png`、`first_main.png`、`guidance_overlay_first_frame.jpg`。实际摆放仍为pending_first_frame_review，不能将选中指引统计当作物体已摆对。

## 检查范围和现场剩余事项

已检查：初始物体完整进入主相机（含14px边距）、物体间至少保守15mm平面包络间距、投影物体不重叠、抽屉200mm拉出预留区无初始物体、物体与预留区处于配置的暂定工作区。所有物体按冻结的倾斜桌面放置，不是世界z=0。

每条对3件小物体上方与抽屉拉出前后共5个法兰位置代理点分别检查左右臂关节限位内FK/位置IK，至少一臂位置误差小于2mm。这些点使用假设的120mm法兰净空，没有约束真实夹爪姿态，不构成抓取/开抽屉可达性保证，也未要求整段由同一只手完成。

现场需核实：桌面边界、TCP/夹爪姿态与障碍、抽屉实际拉出行程/可用内空间，以及球稳定性与纸抽屉是否需要固定。方块高度34.4mm是估计；其他包络轴向按照片解释；抽屉长250mm被解释为前后深度，宽314mm为左右宽度。机械臂遮挡、路径/双臂碰撞、放置打包、接触动力学、抽屉关节均未验收。

先录少量试采并检查首帧投影，再决定是否扩大采集。旧标定已接受，不重新开启优化；这里核实的是当前安装与任务布局。

## 修改分布和复现

管理者可以在JSON中修改随机种子、已审阅物料尺寸、柜体位置/朝向范围、候选工作区和净空参数；不要只改PNG或手工改已发布manifest。生成器核对任务关联池、登记尺寸和照片，变化后需先更新审阅配置。本版固定完整20组合×3朝向，尚无鼠标拖拽编辑器。

```bash
cd /home/chenyuan/workspace/real2sim
# 重新读取时使用全新目录，不覆盖原始输入；不写飞书
.venv/bin/python scripts/sync_yam_guidance_task.py --task TASK-YAM-0015 --output runs/yam15_input_NEW
.venv/bin/python scripts/prepare_yam_drawer_guidance.py \
  --source runs/yam15_input_NEW/source.json \
  --config configs/tasks/task_yam_0015.json --output runs/yam15_bundle_NEW
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_yam_drawer_guidance.py -v
```

生成器拒绝覆盖已有包。启用新包时显式修改teleop的--guidance-bundle路径。飞书附件下载使用记录返回的带权限上下文tmp_url，原裸file_token接口在当前表返回400；签名临时URL/凭证不写入产物。无关物料空白行不影响按任务关联记录读取，关联的无效记录仍拒绝。

## 本轮验证

- 新增3项测试通过：逐条选料快照独立、倾斜桌面/重叠/越界/拉出区拒绝、逆畸变不得冒充正向系数。
- 共享core 3项通过。
- YAM原生指引/相机预览/MCAP回归29项通过。
- 实际60条包逐条读入、哈希与用料验证；20种组合各3次，每物料30次。
- 原网页指引切换，以及同一个writer连续3条（含失败保存、重复指引、录制后切换）保留各自用料和投影；首帧导出通过。全程临时测试MCAP，无真机启动。

重跑真实包联调：

```bash
cd /home/chenyuan/workspace/YAM/gello_software
.venv/bin/python scripts/check_yam_drawer_integration.py \
  --bundle /home/chenyuan/workspace/real2sim/runs/task_yam_0015_guidance_v1 \
  --report /tmp/yam15_integration_report.json
```

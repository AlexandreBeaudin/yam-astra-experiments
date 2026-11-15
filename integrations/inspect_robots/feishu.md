## Trial 12: V2 prompt and full-range gripper goals (2026-11-14)

The current `move_joints` per-call ceilings are **0.45 rad per arm joint** and
**1.0 normalized gripper delta**. Open (`1.0`) and close (`0.0`) each take one
model/tool call, with the existing local interpolation and all low-level joint,
tracking, feedback and hold guards preserved. Actual grasp closure is not a
measured arrival guarantee. The earlier 0.50 allowance below is historical.

[astra_exploration.txt](astra_exploration.txt) now contains the supplied V2
five-stage policy: actively square the jaws with `right_j5`, inspect joint lag,
stop lowering on the specified contact cues, and release early above the paper.
No scene-specific joint waypoints or initial pose vector were added. The V2
0.015 rad contact cue is a model instruction, not a new automatic contact guard;
joint lag alone is not an independently verified contact measurement.

URDF and MJCF identify model `joint6` (logical `right_j5`) as the flange hinge
with axis approximately local `(0, 0, -1)`. Offline FK confirms this joint rotates
the grasp frame around its approach axis without translating its center; that
axis is vertical when the approach is vertical, rather than at every wrist pose.

The Responses request already uses `reasoning.effort="medium"`; it remains so.
No `max_output_tokens` is sent: the pinned policy supports that option only for
the Messages wire. No latency reduction is claimed from an API-setting change.
Fresh episodes use V2; existing resumed checkpoints retain their saved prompt.

## Current motion caps and Astra prompt (2026-11-14)

`astra_run.py` enforces **0.45 rad per arm joint** and **1.0 normalized
gripper delta** per `move_joints` call in every phase. Clear-space transit uses
coupled axes; the task prompt requests 0.03–0.06 rad terminal alignment steps.
The 0.15 rad / 0.10 gripper limits in older trial notes below are historical.
Optional exact-path attended review remains available, with the same 0.45 cap;
it is no longer required to unlock that cap. No automatic collision checker is
installed, and the cap itself does not certify swept clearance.

The supplied five-stage prompt and kinematic priors are in
[astra_exploration.txt](astra_exploration.txt), appended verbatim through the
agent's `eval_notes` system-prompt extension. The priors are operator-supplied,
not newly calibrated by this change. Each call still uses the existing local
interpolation (at most 0.003 rad / 0.02 gripper per waypoint, 30 Hz), daemon
joint/velocity limits, feedback/freshness guards, fixed left-arm targets and
final SAFE HOLD. Low-level gains and limits are unchanged.

This is a custom YAM adapter using the vendored **robocurve/inspect-robots**
framework (`Task`, `Scene`, `eval`, tools and guards) and `inspect_robots_agent`.
It does **not** import UK AISI `inspect_ai`, `Sample`, `@solver` or `@tool`.
The pinned source is recorded in `upstream-lock.json`.

`planned_motion.py` has nominal MuJoCo IK; YAM/i2rt also has MuJoCo/Mink
kinematics. The live runner installs no pose planner, grounded pose inputs or
trusted swept-path checker, so Cartesian tools remain disabled. Its active
control path is joint targets through the existing safety daemon, logical
right=`can_yam_a`, left=`can_yam_b`.

Offline preflight (no station connection, LLM request or motion):

```sh
YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py
```

It reports `effective_cap_rad=0.45`, `max_gripper_delta=1.0`, prompt path and hash.
`--check-station` additionally reads station services; `--run` starts a live
episode. Historical trial/resume evidence retains its original prompt.

# Paper-X / Inspect Robots 离线集成

## 2026-11-14：模型超时重试与实验续跑

模型请求默认超时从 120 秒增加到 600 秒，每个决策最多 4 次总尝试；
可用 `--model-timeout`、`--model-attempts` 配置。超时、传输错误、429、5xx
由现有客户端退避重试；其他 4xx 立即失败。重试不发送机器人命令、不重复执行
工具；daemon 保持原目标，独立心跳继续运行，最终失败仍进入确认 SAFE HOLD。

新增 `--resume <中断目录>`，默认仅离线检查；加 `--check-station` 只读检查
站点；加 `--run --right-channel-confirmed can_yam_a` 才实际续跑。完整命令见
[README 的恢复说明](README.md#interrupted-experiments-retries-and-resume-2026-11-14)。
每次模型请求前原子写入并 fsync checkpoint，保存上下文、输入图像、API 返回的
推理条目、工具调用与结果、目标和实测关节、左右臂状态、累计预算；发请求前保存
HTTP 次数。返回工具执行前使 checkpoint 失效，避免重放已执行或不确定的动作。
只自动恢复等待模型的中断点，运动过程中中断需另外审核。

恢复需原 daemon 确认 SAFE HOLD、反馈新鲜，左右臂与保存实测边界差不超过
0.008（关节 rad、夹爪归一化单位），右臂目标残差不超过 0.08；不自动归位。
保留旧上下文并补充中断说明和新相机/状态，由模型重新判断下一步；不恢复旧的
attended path 授权。新日志写入独立 resume 子目录，原证据不变。再次中断时
用该子目录继续。调用/HTTP/步数预算跨段累计，每次明确续跑有新的 2400 秒窗口。

完整离线集成测试 67 项通过，包含 6 项恢复测试。
[run-010 恢复评估](offline-review/run-010-resume/assessment.json) 已保存。

run-010 的第 27 个请求约 122.99 秒超时，前 26 个成功，累计运动 1014 步。
旧 wire 包含恢复输入、图像和 API 返回推理；测试验证其精确往返编码及图像哈希。
旧记录 evaluation_completed=true 与 framework error 不符，今后错误会正确标为
未完成。受限编程环境中只读连接 127.0.0.1:6002 超时，尚未验证当前姿态或实际
续跑。需在正常站点终端执行；不要为续跑重启或复位仍保持位置的机器人。



2026-11-12 run-002 修复：run-001 在 60 次预算中只用 3 次：J0 +0.02 rad
探测、保持 1 秒、主动 give_up。无控制器拒绝或故障记录，未尝试抓取。
探测后实测变化约 +0.00954 rad，保持后 +0.01411 rad；最终残差 0.00593 rad。
原 prompt 将低信号视觉变化也引向立即停止，且腕相机最初看不到粉块。
详见 [证据与修改](RUN_002_POSTMORTEM.md)；run-001 的原始记录完全保留。

新的 StationPolicy 仅扩展决策前观测，不修改固定上游源码。目标变化后被动等待
1–3 秒，再刷新图像与实测状态，0.008 rad 为报告容差；超过 0.08 rad 停止。
等待不发命令、不追补残差，期间继续检查新鲜度、deadline、录像和 session。
新增 main_comparison 为 main 的固定显示裁剪（x=96:544, y=192:448），
左边上次决策、右边当前决策，首帧相同；完整双相机保留。报告实测变化和 JPEG
内容是否变化，但内容变化不证明新曝光或有效运动。决策输入单独持久化为
 decision_observation 事件和 derived comparison JPEG，原上游每步观测日志保持。
两相机 replay 中该派生图不冒充第三个物理相机。

prompt 允许在明确可见的自由空间中最多六个不同的小探测（<=0.02 rad），
不再因一次低信号探测自动停止；碰撞、过限、跟踪异常、冻结/不一致图像仍停止。
明确对齐、靠近、夹持、抬起验证、搬运、降低、释放、撤离后验证各阶段。
下一次固定 latch 为 run-002，180 calls / 5400 steps / 2400 秒；运动幅度限制不变。
结束记录区分模型 termination reason 与 evaluation_completed，done 也不算物理成功证明。

本次 28 项集成测试通过，默认离线预检通过；没有新模型请求或硬件实验。
离线测试不证明模型下次会如何决策，也不证明物理抓放成功。目前只能说修复了已知
早停诱因，不能可靠预测下一次完整成功；仍需观察抓取、抬起保持、纸内释放和撤离。

2026-11-10 更新：按导师的新方案，已准备 `astra_run.py` 单次 Astra YAM3
pick-and-place episode，不要求抓取示例或经过验证的相机到机器人标定。
`astra_exploration.txt` 要求 Astra 从小幅可逆、初始 ≤0.02 rad 的单关节探测开始，
根据 main/right 图像、实测关节与执行反馈逐步更新空间关系估计，再靠近、抓取、
小幅抬起验证、搬运并放入白纸 X 区域；关键不确定性无法解除时停止。

实际复用同一固定 upstream commit 的 `LLMAgentPolicy`、Responses client、
control_tools/move_joints、残差反馈、结束工具、eval、guards、日志及图像侧文件。
新增 agent 源码位于 `vendor-agent/`，11 个文件验证 Git blob SHA-1/SHA-256，
未修改上游代码。只新增本站适配层，通过现有 SafetyClient/ZMQClientRobot 和
SupervisedMotionSession 发令，不直接打开 CAN，不启动服务，不隐式归零或停车。
左臂固定初始目标，仅暴露右臂 7 维 joint/gripper；没有新写 agent 循环或 IK。

默认离线预检：
```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py
```

只读现场检查：同命令加 `--check-station`。下一次正式执行（**会调用 Astra 并运动**），
在现场已导出 `RHOS_API_KEY`，确认物理任务臂为 can_yam_a、工作区清空且停止控制可用后：
```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/astra_run.py --run --right-channel-confirmed can_yam_a
```

单个 scene/epoch，最多 180 次模型请求、无 HTTP 重试、5400 control steps，2400 秒截止；
未完成的 HTTP 最长可等 120 秒，但截止后不再发运动。固定
`astra-yam3-run-002/` 为下一次单次 latch，仅现场预检通过后创建，不自动续跑或重试。
旧 Trial 与 latch 不变。RHOS 固定 endpoint/model/actor header；使用上游 transport
注入能力，关闭尚未验证的可选 explicit cache 字段，不假设有可读 reasoning summary。

每次 move 上限 0.05 rad/joint、0.10 gripper，每 waypoint 上限 0.003 rad / 0.02，
最多 30 Hz；保持 daemon joint bounds、250 ms 实测新鲜度、左臂漂移与右臂跟踪检查，
结束/异常时要求确认 SAFE HOLD。这些数值不代表已验证碰撞空间；无标定的桌面/接触模型。
现有 preview 无曝光 timestamp/frame ID，明确向模型及日志标为未知，不将 receive time
当成新曝光或标定证据。接受命令、实测残差与物理成功分开记录；scorer abstain，需查看
释放后全块位于纸内且夹爪撤出的图像。

历史 run-001 准备阶段：22 项集成测试和 5 项既有 replay 测试通过。真实上游 agent 的离线 HTTP fake 闭环
测试已通过：拒绝过大探测、小探测执行后接收新
合成图像和实测反馈并结束；没有真实模型请求/运动。默认预检通过；只读现场检查在
localhost:6002 的 safety status 超时，当前进程没有 RHOS_API_KEY，尚未验证现场服务
及 RHOS tool-call 兼容性，当时尚未启动正式 episode；run-001 后于 2026-11-12 执行并 give_up。缺失的 httpx/websockets 从已有 dream-env
复制到隔离 runtime-deps（含许可证、锁文件，不改变共享环境），Python 3.11 实测可用。
详细参数、源码版本、证据与命令见 [README](README.md)。

下文是历史 mock 工作流；以前要求标定包和已审阅抓取 primitive 的限制不适用于上述
经导师更新授权的探索式 joint-control episode。

2026-11-10 Trial 005：未找到经实验室审阅、通过本站监督控制器执行的右臂抓取
primitive；停在 mock 边界，不增加硬件适配。详见
[源码审查及操作员交接](FIRST_ACTION_AUDIT.md)，含精确候选源码、profile、物理映射
与标定限制、缺失文件/现场证据。运行：

```bash
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/first_action_mock.py --output integrations/inspect_robots/first-action-demo-new
```

输出目录必须不存在；已验证结果为 `first-action-demo/`。使用保存的 Astra 响应、
manifest、validator、decision journal 固定 hash 和图片 hash；对比已验证决定。
四个独立日志事件：提出右臂 pick pink_block、语义验证通过（无运动许可）、mock
拒绝接受且未提交、实测完成不可用。使用合成顺序，不伪造测量，不调用 API、不连接
机器人、不发运动命令、不读取 NOTES 或凭据。原 Trial 004 toy demo 保持独立。

入口、源码版本、RHOS 路由与本站差异详见同目录 [README](README.md)。所有新增文件均放在独立的 `integrations/inspect_robots/`，不修改原 runner、Trial 包、真实调用 latch、YAM 或既有回放。

未找到可访问的 QZ 固定 checkout / mock 集成；仍需 QZ 绝对路径、完整 core commit、mock 路径及其 revision/diff、独立 agent/YAM 插件版本。当前采用可访问 upstream `772019598e42ccc7921a043dee3a2ea2692020ea`，不声称它是 QZ 版本；52 个 vendored 文件的 Git blob hash 已验证。

真实使用框架 `Policy.act(Observation) -> ActionChunk`、`Embodiment.step(Action) -> StepResult`、Task、eval、EvalLog 和相机侧文件接口。复用 Trial 004 已审阅图片（SHA-256 `dd5b3634b293b5fa88cde2b24a2f49a687c531aeee8bdf9fef9fb9be01de6822`）、任务及语义 validator。模拟 pick 后产生 held 状态，下一次模拟决策读取该状态再 place，最后请求新观察。相机始终是旧保存帧；更新的是 mock 状态，不代表真实新采集。物理任务评分 abstain，成功仍未验证。

```bash
cd /home/rhos/workspace/jiaxin/shurui
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python integrations/inspect_robots/demo.py --output integrations/inspect_robots/demo-new
PYTHONDONTWRITEBYTECODE=1 YAM/gello_software/.venv/bin/python -m unittest discover -s integrations/inspect_robots -p 'test_*.py' -v
```

输出目录必须不存在。已运行结果在 `demo-output/`。`framework-log.json` 的 `paper_x_transitions` 保存 input、proposal、validator、mock outcome 和 next input；`replay.py` 读取真实框架日志，复用现有 HTML 播放器模板生成带 MOCK 标记的回放。使用合成步骤时钟，不拼接 stationary60 的真实 MCAP。HTML/图片/链接已检查，未认证浏览器实际渲染；不生成 MP4。

RHOS 仅提供未启用配置说明：`base_url=https://api.rhos.cc/v1`、`model=gpt-6-astra`、`api_key_env=RHOS_API_KEY`、`wire=responses`、`effort=medium`、`max_retries=1`。该 agent 版本不暴露本站需要的 `x-openai-actor-authorization: local-image-extension` 自定义头接口；每请求一次尝试也不等于全流程一次调用或 latch 限制，尚不能直接替换已审阅传输。

YAM 插件静态审查版本 `5a017d1b47b731f00cb7f26e59831dca03a15a71` 未安装或导入。主要差异：插件直接打开双臂 CAN，本站由 6001/6002 控制器和安全会话/租约/故障 latch 管理；本站须校验 400 ms 电机 watchdog；本站使用 yam_pro，而插件未覆盖 arm_type；版本化配置为 left=can_yam_b、right=can_yam_a、30 Hz，插件默认 can0/can1、10 Hz；main/top 相机别名、物理左右与夹爪标定未核实。还缺右臂任务对左臂保持策略、限位和生命周期一致性、相机/桌面/基座/TCP/碰撞几何标定以及真实释放后的新图像。不可从旧图片或语义 ID 推导可执行物理轨迹。

验证：12 个集成测试 + 9 个 Trial 004 离线/链接回归 + 2 个既有 replay 测试，共 23 个通过。试验 API 调用 0，机器人连接 0，机器人命令 0；不读取 NOTES 或凭据文件。
# 策略证据 UI 更新（2026-11-10）

打开 `first-action-ui/replay.html` 查看 Trial 005 的真实已保存 Astra 提案；
`replay-ui/replay.html` 是明确标记的模拟框架回放。两者复用现有回放模板。
界面分别展示推理摘要、动作及 reason、验证与运动许可、命令接受、测量完成、
观测新鲜度及结果证据；响应文本、工具调用和 token 元数据可展开查看。
四个阶段按合成顺序播放，不代表真实耗时，后续证据不会提前显示。

已保存响应的 summary 为空，因此显示 NOT RETURNED。不解码或导出加密推理，
不把动作 reason 或推理 token 数当作推理摘要。未来请求可在支持时设置
`reasoning.summary="auto"`；本次没有修改请求、额度锁、网关或硬件路径，也未
验证 RHOS 摘要支持或实时流。运行 first_action_mock.py 的原命令并指定新输出目录
即可生成含 replay.html、manifest.json、timeline.json 和已保存图片的完整视图。

Inspect Robots 已提供 transcript、推理/审批/错误事件、帧存储、JSON 日志和 HTML
查看器；站点验证、观测新鲜度、接受与测量完成的区分仍需适配。校准、规划和运动
许可不会由查看器自动补齐。新增回归测试覆盖摘要提取、加密内容排除、阶段因果与
真实 UI JavaScript 的拖动和文本显示。Chromium 因环境 socket 限制退出，浏览器布局
尚未验证。没有 API 请求、机器人连接或命令，旧导出保持原样。


## 2026-11-12：硬件实验回放

`astra_run.py --run` 自动录制 main/right preview（每路目标 5 次/秒，含模型等待阶段，
另存 policy observations），JPEG 与 station-events.jsonl 逐条 flush/fsync。运动前
写 command_dispatch，RPC 返回后写同 ID 的 command_acceptance；没有回执表示结果未知。
录制失败在下一次 adapter check 停止继续动作。独立 HTTP client 只读现有 preview，
不打开相机驱动。运动 session 退出并请求 HOLD 后自动生成 run 目录下 `replay.html`。

浏览器直接打开，支持播放/暂停、拖动、速度、双路画面及 sample age、关节/命令事件、
已完成 wire capture 的 Astra 返回内容和工具调用、最终 hold 结果。`replay-frames/`
必须与页面保持相邻；异机观看请复制整个 run 目录。这是保留真实接收时间的采样回放，
不是全帧率同步视频；曝光时间与物理成功仍未验证。

强制停止后可离线重建，不需要 finalized framework log、API 或硬件：

```bash
YAM/gello_software/.venv/bin/python integrations/inspect_robots/hardware_replay.py --run-dir integrations/inspect_robots/astra-yam3-run-001
```

恢复器统计损坏 journal 行和缺失/哈希错误图像，将没有最终结果的 run 标为 partial。
Ctrl+C 尝试正常 hold/清理与导出；hard kill 无法执行清理，不能保证 hold 或恢复尚未
写盘的数据。旧 `replay.py` 仍用于 mock，新实验使用 `hardware_replay.py`。
离线测试覆盖中断恢复、RPC 结果未知、磁盘失败阻止命令、等待时后台录制与损坏证据。

本次验证：26 项集成测试通过；默认离线预检通过且 latch 未消耗；Node DOM stub
验证页面初始化、拖动、双路图像、播放与事件显示。未执行真实硬件实验或 Astra 请求。

## Run 006 离线复盘与提示词修订

提示词中的“最多六种探测”改为现有调用/时间/步数预算内的任务进展或具体不确定性
消除。已确认方向上的行进算任务进展；每次新探索必须回答明确问题。main 中指尖与
方块的相对位置及接近间隙用于判断进展，wrist 用于细化对指。J0 不再缩小间隙时需
评估不同的伸展假设。保留抓取、小幅抬升验证、搬运、放下、释放、撤离及最终视觉验证。
运动上限、guard、watchdog、停止条件和 SAFE HOLD 代码未改。

[Run 006 离线报告](offline-review/run-006-prompt-revision/REVIEW.md) 包含完整
[prompt diff](offline-review/run-006-prompt-revision/prompt.diff)、YAM Pro 现有模型
的基座坐标 FK 候选回放及[保存输入结果](offline-review/run-006-prompt-revision/saved-input-result.json)。
相机到基座关系仍未知，模型回放不证明实际碰撞净空。两次保存输入案例均因 ConnectError
未获得新 Astra 决策，不能记为行为验证通过。14 项 harness 和 4 项既有离线测试通过。
`offline_run006_review.py` 默认仅离线生成；`--infer` 仅请求保存输入推理并保存返回，
没有机器人连接或动作派发路径。本次未启动实机运行。

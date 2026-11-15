# 连续数采与指导投影留档

2026-09-15 更新：按用户要求取消人工标点核对、两分钟有效期及“一次核对只能录一条”的限制。当前流程只保存指导目标；实际摆放是否一致，后续再与数据第一帧核对。旧版人工核对接口已停用。

## 采集员操作

1. 保持原流程：终端一激活 Xtrainer 环境，`bash collect.sh` 选 2 启动节点。
2. 终端二激活 Xtrainer 环境，`bash collect.sh` 选 **5：摆放指导＋三相机采集**。核对现场仍匹配所用相机/桌面标定，进入原数采启动步骤。
3. 启动器打开 http://127.0.0.1:8770 。新页面显示 top 及左右相机；四相机版本额外显示前视。任务物料列为参考，不需要点击物料确认或标点。
4. 等待“指引已就绪”，按半透明投影摆放物体，用原手柄按钮开始录制，再用原按钮停止。
5. **不退出 collect**。手动遥操到大致还原位置，点击“上一条指引”或“下一条指引”（也可下拉选择），等待切换就绪，摆放，再开始下一条。
6. 同一个指引可重复录制多条，选择没有两分钟有效期，也不自动跳转下一条。

录制中切换指引不会改变本条轨迹已经固定的目标，后续录制使用新目标。通常应按用户工作流程在录完、还原后切换。

四相机入口：`python -m scripts.run_guided_collection --four-cameras`。原菜单 3/4 保持旧的数采与 OpenCV 预览；只有指导模式关闭旧预览窗口。

更新代码后，已经运行的旧 Python 进程需要退出并重新启动一次。新版运行期间逐条采集不需要重启。

## 每条轨迹增加的文件

在 `collect_data/<时间戳>/` 中，原 topImg、leftImg、rightImg、observation 保存方式不变，额外写入：

| 文件 | 内容 |
|---|---|
| placement_guidance.json | 任务、采集会话、开始时的布局完整参数、分层、相机内外参与哈希、物料清单、透明度、投影哈希、首帧路径及哈希、录制起止状态 |
| guidance_projection.png | 开始录制时所选布局的原始 RGBA 透明投影，与生成文件逐字节一致 |
| guidance_overlay_first_frame.jpg | 使用保存的透明度，将目标投影叠加在本条数据的第一张 top 原图上，供之后检查 |

对照图来自**数据第一帧**，不是浏览器截图或按钮按下时另外抓取的预览帧。投影 PNG 自带 alpha，页面总透明度另存在 JSON 的 guidance.opacity 中。

`association_status=guidance_saved` 仅表示指导目标已保存；`placement_validation=pending_first_frame_review` 表示实际摆放尚未检查。`quality_control=not_reviewed`。未加载指引、任务/标定/采集会话不一致时仍允许录制，但 JSON 明确记为 `unlinked`，不伪造投影。

页面“指引分布记录”统计已记录目标的轨迹，不能视为物体实际分布或质检通过数据量。后续可以据 JSON 中的相机、布局和首帧开展自动/人工核对；本轮没有实现该离线核对算法。

## 接入方式与边界

- 三／四相机数采入口在录制按钮状态上升沿调用 bridge.begin，固定内存中已经加载的布局与投影字节。首帧保存后写入关联，停止时更新状态。若开始事件未被观察到，首个保存回调作为兜底固定时刻。
- 开始时的快照不受随后指引变化影响；工作线程不能在异步写文件时重新读取当前布局来替代快照。
- bridge 后台线程约 10 Hz 发布原子 JPEG、读取 selected.json 与按 SHA256 命名的不可变投影。producer.json 返回已加载 revision，页面等待确认再显示“就绪”。正在切换期间不要开始录制。
- 仅后台线程进行图像编码与关联文件读写；控制循环不访问 HTTP，不写这些附加文件。原采集保存动作不变，目标投影不会画进数据集原始图片。
- 相机预览刷新率约 10 Hz，受浏览器和机器负载影响，多相机预览没有硬件同步保证。旧画面/生产者超过 2 秒未更新时接口拒绝，相机校准仍需现场确认。
- 新数采进程创建新 session_id；旧会话选择不会自动误绑定。页面在新会话连接后加载目标。不同进程不能同时占用一个 bridge 目录。
- bridge 文件默认在 real2sim/runs/live_xtrainer；服务仅监听回环地址，数采入口端口 8770，离线样例端口 8767。
- 关联异步保存：首帧、每 25 帧检查点及停止时更新。断电或强制杀进程可能留下 recording 状态、缺少末尾元数据，应当按未完整结束处理。保存回调计数不替代文件完整性或质量检查。

## 验证结果

在原 Xtrainer Python 环境中，5 项 bridge 单元测试通过：无有效期重复使用、连续轨迹、开始后切换不改本条、投影字节和透明度对照、旧会话拒绝、生产者互斥。既有按钮和启动测试 5 项继续通过。

无硬件集成测试使用临时目录与历史图片，连续录制三条、验证前后切换、重复同一目标、保存投影及第一帧对照、相机过期拒绝、人工标点 UI 移除、浏览器无 JS 错误。测试记录不进入真实统计。

```bash
# 原数采仓库
python -m scripts.run_guided_collection --check-only
python -m unittest discover -s tests -p 'test_guidance_bridge.py' -v
python -m unittest discover -s tests -p 'test_collection_setup.py' -v

# real2sim 仓库
.venv/bin/python scripts/check_goal_supervision.py
.venv/bin/python scripts/check_collector_integration.py
```

尚未启动真实机械臂或相机做本版现场采集验证。

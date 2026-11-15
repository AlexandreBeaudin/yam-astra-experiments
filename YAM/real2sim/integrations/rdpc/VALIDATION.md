# 验证记录

2026-10-21，rhos-nuc-1，独立 Python 3.11，PyYAML 6.0.3 / Pillow 11.3.0。

命令：`RDPC_SOURCE="$PWD/.runtime/rdpc-83fc502" .runtime/rdpc-test-venv/bin/python -m unittest discover -s integrations/rdpc/tests -v`

结果：10项 PASS。覆盖正常三图及身份回显、重复不执行、明确失败封套、非法数量、输入摘要被篡改、过期不领取、生成期间丢失租约不发布、停止不领取、并发只领取一次、原版 CloudTransport 成功/失败文件传输与标记最后上传（传输两项分别计数）。

CloudTransport 使用上游原代码与内存对象代理，校验实际 PNG/result.json/OK或FAIL 摘要；没有访问真实 BOS。测试初始化 schema 仅在临时 SQLite 中，运行进程由 Worker 创建 schema。测试没有确认 Scheduler 注册/任务派发/中心 ack，也没有证明 RDP 页面业务接回。跨机 HTTPS、同事的 Scheduler/独立数据库/真实BOS/页面验收仍待双方配置后执行。

不含真实算法、真实分布、已确认任务、机器人或相机验收；没有修改共享或正式数据库。

## 三类型扩展

2026-10-21 在 NUC 独立临时目录运行同一 unittest 命令（测试目录为暂存版本）：16项 PASS，另含4组新类型云传输子案例。新增资产成功身份回显/缺失身份/非法图片、补充 human_mock/数量不匹配，以及资产和补充各自的成功/失败 CloudTransport 校验。原10项回归全部通过。云代理仍是内存测试，并非真实 BOS 验收。

扩展前单类型 Worker 已实际注册；三类型部署后的注册证据和任务回执需单独核对，单元测试不替代中心派发验收。

# 本机 GitHub 同步（2026-09-23）

共享项目：`/mnt/sdb/rhos_collector/real2sim`。
远端：`git@github.com:yuz1wan/real2sim-guidance.git`。
同步分支：`sync/github-manager-20260923`；备份：`backup/pre-github-sync-20260923`（223e521）。
已合并默认分支 eeb45ed 和最新试验分支 fd00df9。README 重复段落冲突采用上游内容；合并后的受跟踪源码与 fd00df9 一致。本地未提交的三相机联调修复继续保留，未加入同步提交。原始数据、标定与现用指导包未替换。未推送本地合并提交。

## 本机验证

- 管理器 8 项 unittest 通过，独立 Python 3.11 `.runtime/manager` 安装 requirements-manager.txt，pip check 通过。
- 指引相关测试 5 项通过、1 项因缺本机 YAM 模型跳过；试验排布 2 项、平台核心 3 项、球门布局 4 项通过。
- Xtrainer 启动路径检查通过；历史三相机连续三条录制联调通过，投影固定、前后切换及腕相机图像验证通过，无浏览器错误，未启动硬件。
- 新管理目录导入本机 TASK-X-0001 的 36 条布局；实际轨迹 0 条。浏览器实测布局列表、详情、筛选通过，无 JS 错误。截图：`runs/github_sync_20260923/manager.png`。
- 初次旧工具环境运行测试缺少 src 导入路径，补充 PYTHONPATH=src 后相关测试通过。物料导出测试因缺 mujoco 未执行成功，不计为通过。

## 管理入口

```bash
cd /mnt/sdb/rhos_collector/real2sim
.runtime/manager/bin/python scripts/collection_manager.py serve --port 8780
```

打开 http://127.0.0.1:8780/ 。测试服务已退出，需要上述命令启动。管理目录为 `runs/collection_manager`。当前目录未批准发布任何布局。现有 Xtrainer 指导采集继续使用原 collect.sh 选项 5。

## 当前边界

管理器是只读目录和证据查看器；Xtrainer 轨迹读取仍为未实现接口，不能把布局导入等同于轨迹接入。旧 Xtrainer 包缺显式八顶点包络，目标图片和原始参数可看，但不产生三维包络/位置散点。未进行真机现场验收或完整仿真回放。

上游物料工具包含 MuJoCo 静置/渲染路径，与 AGENTS.md 的统一 Isaac Sim 5.1 生产仿真约定存在差异；本次仅同步代码，未运行或认可该路径作为正式仿真验收，后续需适配。

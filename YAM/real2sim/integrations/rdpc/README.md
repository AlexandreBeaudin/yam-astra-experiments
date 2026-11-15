# NUC real2sim Worker 联调

本适配器验证三种 TEL 类型的派发、SQLite、进度和测试结果回传：`tel.simulation_asset` 生成 MOCK 占位 OBJ；`tel.placement_strategy` 生成 MOCK PNG、production_mode=algorithm_mock；`tel.placement_supplement` 生成 MOCK PNG、production_mode=human_mock。全部 mock=true。不读取已确认分布，不运行仿真、相机或机器人，不自动审核通过。三种类型共享 capacity=1，串行执行。补充任务必须 supplement_count=requested_count，不代表真实人工操作。

## 版本和位置

- real2sim 源码基线：`29b11eced78255dac1a8d076eccce48bd20570fb`，分支 `feat/rdpc-real2sim-mock-worker`。
- 原版 RDP bridge/protocol 锁定：`rhos-ai/rhosdata-platform@83fc50277f813e735d7cc377ac489656d745483d`。
- NUC 上游代码：`.runtime/rdpc-83fc502/rdpc/worker/`，原样导出，不修改 bridge。
- 独立 Python 3.11 环境：`.runtime/rdpc-test-venv/`。不改变仿真环境。
- 正式运行 SQLite/cache：`.runtime/rdpc-real2sim/`，必须私有本机目录；禁止放共享盘、凭证或业务生产数据。

重新准备上游时，从可信的 RDP checkout 执行 `git archive 83fc50277f813e735d7cc377ac489656d745483d rdpc/worker rdpc/WORKER_DATABASE.md rdpc/CLOUD_INFRA.md` 并解压至 RDPC_SOURCE；依赖用独立 Python 3.11 venv 安装 `requirements.txt`。升级上游需双方约定 commit 后重测，不自动跟随 main。

## 同事需要一次给齐的配置

1. Scheduler HTTPS origin（不含路径或末尾 `/`）、构建 commit，确认数据库和 BOS 配置已可用。
2. Worker ID，建议 `r2sg-local`；独立 token 至少32字符，环境变量名 `RDPC_WORKER_TOKEN`。实际值通过私密通道落本机，不放聊天/Git/命令参数。
3. Scheduler 可访问的 NUC HTTPS callback origin，对应反代 `127.0.0.1:8851`；有效受信任证书，不关闭 TLS 校验。双方确认 VPN/隧道/反向代理的网络路由。
4. 白名单允许 `tel.simulation_asset`、`tel.placement_strategy`、`tel.placement_supplement`。每类型最新注册实例会成为主 Worker，必须使用独立联调 Scheduler 或确认当前无真实任务，防止测试算法接走真实任务。
5. 中心当前版本的 BOS 测试 prefix（`bos://桶/根/vN/tel.placement_strategy/`），由中心管理输入发布和结果检查；Worker 不需要 BOS AK/SK、admin token 或数据库密码。
6. 谁负责创建合成任务、上传输入和查看结果。RDPC 的 DUMMY 按钮只下发 dummy，不能用于此 TEL Worker。

Scheduler `workers` 节示例（合并到现有配置，不覆盖其他配置）：

```yaml
workers:
  r2sg-local:
    name: real2sim NUC1 MOCK TEST
    token_env: RDPC_WORKER_TOKEN
    callback_url: https://real2sim-worker.example.invalid
    task_types: [tel.simulation_asset, tel.placement_strategy, tel.placement_supplement]
```

中心与 Worker 分别注入同一个机器 token；admin token 与它不同。调度器启用该类型，推荐 lease 30秒、timeout 300秒。云 prefix 从中心配置派生，不另猜桶和版本。

## NUC 启动和停止

先完成上述网络和授权配置，再执行：

```bash
cd /home/rhos/workspace/YAM/real2sim
umask 077
mkdir -p .runtime/rdpc-real2sim
cp integrations/rdpc/worker.example.yaml .runtime/rdpc-real2sim/worker.local.yaml
# 编辑这个 local.yaml：两端 URL、worker_id/token_env 必须与中心一致。
export RDPC_SOURCE="$PWD/.runtime/rdpc-83fc502"
# 由安全终端/服务管理器注入 RDPC_WORKER_TOKEN；禁止 echo token。
.runtime/rdpc-test-venv/bin/python integrations/rdpc/run.py \
  --config "$PWD/.runtime/rdpc-real2sim/worker.local.yaml"
```

run.py 前台托管 Worker 和子系统，任一退出会停止另一方。Ctrl+C 先停算法再注销 Worker；在途任务会失效，因此正常下线先停止下发、等待中心终态。不要用 `nohup` 后遗忘进程，也不要运行多个实例共享 SQLite。尚未填写示例 URL 时启动会拒绝注册。

## 验收

本机隔离测试（临时 SQLite、模拟数据，不触碰中央库）：

```bash
RDPC_SOURCE="$PWD/.runtime/rdpc-83fc502" .runtime/rdpc-test-venv/bin/python \
  -m unittest discover -s integrations/rdpc/tests -v
```

生成一组输入和中央任务请求模板，prefix 必须换成同事确认的值：

```bash
RDPC_SOURCE="$PWD/.runtime/rdpc-83fc502" .runtime/rdpc-test-venv/bin/python \
  integrations/rdpc/prepare_test.py --directory .runtime/rdpc-real2sim/samples \
  --prefix bos://TEST_BUCKET/TEST_ROOT/v1/tel.placement_strategy/ --count 3
```

命令仅创建本地 `<UUID>/input/{files/,result.json,OK}` 和其旁 `request.json`，不上传/下发。合成 materials 无真实资产依赖，仅供直接 RDPC 协议联调，不能冒充通过 RDP 剧本业务校验的真实任务。同事将 input 的内容发布到 request.input_uri 对应目录，校验后最后上传 OK，再用 admin 身份 POST `/rdpc/v1/tasks`，body 使用 request.json。输出 URL 为同一 UUID 的 outputs 目录。中心当前云配置须允许这些路径。

验收顺序：

1. 中心 Workers 页显示此 ID、健康且支持 placement_strategy；中心能回调 NUC。
2. 提交正常任务，约5秒延迟后进度更新；中央终态 succeeded，output result.json 有3张图，均 MOCK，metadata 回显输入 strategy_group_id/script_task_uuid。
3. SQLite `reported_state=succeeded AND ack_revision=revision`；中心 BOS 有完整 files、result.json、最后发布的 OK，摘要一致。仅 SQLite succeeded 不算中心成功。
4. 另生成 `--fail` 的新 job：合法输入携带 test_force_failure，中央应 failed，error_code=real2sim_mock_input_invalid，输出 FAIL，不能显示成功。
5. 同请求幂等重放不能重复执行。失败重试生成新 job；正常验收后排空、停止测试 Worker。

mock 输出不能当真实分布审核/发布。第一阶段可只在 RDPC 中验证。要验证 RDP 页面接回，需由 RDP 正常测试业务流程创建真实关联的任务，管理员不能用这里随机生成的 script UUID 冒充数据库记录。

已验证与未验证结果见 `VALIDATION.md`。没有修改共享/正式数据库，不需要新增中央 migration。清理仅限本机测试目录；TEL 文件不适用 DUMMY 清理接口，由中心负责人管理测试 prefix 生命周期。

## 三类型联调更新

当前 NUC 实例：Worker ID `r2sg-local`，token_env=`RDPC_WORKER_TOKEN`，Scheduler `https://aarons-macbook-air.tailf177e3.ts.net:8830`，回调 `https://rhos-nuc-1.tailae29ac.ts.net:8851`。Tailscale Serve 将外部8851转到本机127.0.0.1:8851，启动配置位于 `.runtime/rdpc-real2sim/worker.local.yaml`；凭证在 `~/rdpc-token.txt`，不进入仓库。运行日志 `.runtime/rdpc-real2sim/supervisor.log`。

生成输入时加 `--task-type tel.simulation_asset` / `tel.placement_strategy` / `tel.placement_supplement`，prefix 的最后一段必须是对应类型。资产输入带一张合成 PNG 和物料 metadata；分布及补充输入为空 files，补充额外要求 supplement_count 与 requested_count 一致。三种均可加 `--fail` 验证合法封套里的业务失败，不必破坏 SHA。

每种先发一条成功任务，再发一条 --fail 任务，核对中央状态、BOS OK/FAIL 和 SQLite ack。capacity=1，逐条完成后再发；中心忙时不会自动排队。测试资产不能用于正式仿真，human_mock 不能当真人确认分布或用于真实采集。

资产 MOCK 输入校验：描述字段仅要求尺寸说明 `size_text` 为非空字符串；材质 `material_kind`、类别 `category`、尺寸来源 `size_source` 可为空或省略。保留 material_uuid/material_code/material_name、图片、封套身份与摘要校验。缺少尺寸的任务仍失败，历史失败任务需新建 job 重试。

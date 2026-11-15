# Real2Sim 联调操作说明

## RDPC 测试 Worker

NUC 路径 `/home/rhos/workspace/YAM/real2sim`，接入目录 `integrations/rdpc/`。本次启用三种 TEL 类型（详见下方更新），根据输入数量生成带 MOCK 水印的合成 PNG，回传进度和成功/失败；不使用已确认分布，不运行仿真和硬件。

先让 Scheduler 同事提供地址、Worker 身份/独立凭证、安全回调、任务白名单和测试 BOS prefix，再按 [启动和交接说明](integrations/rdpc/README.md) 配置。密钥只注入本机受控环境，不发送聊天或提交 Git。

本机10项测试已通过；双机 Scheduler 和真实 BOS 尚未联调。完整边界见 [验证记录](integrations/rdpc/VALIDATION.md)。测试输出不能审核发布为真实采集分布。

## RDPC 三种任务联调

测试 Worker 现支持 `tel.simulation_asset`（占位OBJ）、`tel.placement_strategy`（MOCK分布图）、`tel.placement_supplement`（human_mock补充图），全部 mock=true。共享并发容量1，不运行真实仿真或硬件。16项本机测试通过；请按 [三类型联调说明](integrations/rdpc/README.md) 分别下发成功/失败任务，检查中心确认与BOS结果。测试补充结果不能当真人确认的分布使用。

资产 MOCK 输入校验：描述字段仅要求尺寸说明 `size_text` 为非空字符串；材质 `material_kind`、类别 `category`、尺寸来源 `size_source` 可为空或省略。保留 material_uuid/material_code/material_name、图片、封套身份与摘要校验。缺少尺寸的任务仍失败，历史失败任务需新建 job 重试。


管理页在无匹配发布版时提示使用本平台入口发布指引，或显式选择无指引模式；不再暗示所有平台都会自动退回普通采集。YAM 指引入口缺少发布版时会拒绝启动。

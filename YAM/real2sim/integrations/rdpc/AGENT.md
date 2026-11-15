# real2sim RDPC 测试适配器

本进程支持 tel.simulation_asset、tel.placement_strategy、tel.placement_supplement，始终返回 mock=true；分布为 algorithm_mock，人工补充为 human_mock，不启动 Isaac Sim、机器人、相机或已确认分布流水线。只有 Worker bridge 访问 Scheduler/BOS；算法仅使用 Worker SQLite 和本地输入输出缓存。

运行入口 run.py：前台托管两个独立进程，算法使用环境变量白名单，不继承机器 token、云密钥或数据库密码。RDPC_SOURCE 指向锁定的上游源码。不要复制 tests/fixtures 作为生产算法。版本与操作见 README.md。

只更新 state/progress/result_uri/error_code，日志 INSERT；禁止修改租约、revision、ack、身份、中央库。Worker 创建 schema，测试独立临时库除外。领取用短事务，结果按 attempt 和租约比较更新；中心确认看 ack_revision/reported_state。云输出由 bridge 校验上传，不能把本机成功当中心成功。

新真实算法接入另做评审，运行必须使用仓库 Isaac Sim 5.1.0 / Python 3.11 / PhysX 规范。不得把人工补充或测试输出当已审核通过的数据。资产输出为明确标记 MOCK 的占位 OBJ，不是物料重建结果。三种类型共享 capacity=1，按本机收到顺序串行处理。

资产 MOCK 输入校验：描述字段仅要求尺寸说明 `size_text` 为非空字符串；材质 `material_kind`、类别 `category`、尺寸来源 `size_source` 可为空或省略。保留 material_uuid/material_code/material_name、图片、封套身份与摘要校验。缺少尺寸的任务仍失败，历史失败任务需新建 job 重试。

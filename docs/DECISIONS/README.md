# 架构决策索引

| ADR | 决定 |
| --- | --- |
| [ADR-0001](ADR-0001-on-demand-low-frequency-refresh.md) | 按需更新加低频巡检，不做每日全量更新 |
| [ADR-0002](ADR-0002-postgresql-job-queue.md) | PostgreSQL 任务表加 Cron，暂不引入 Celery/Redis |
| [ADR-0003](ADR-0003-kimi-capability-boundary.md) | Kimi 会员/Agent/Code 与应用 API、商业数据授权解耦 |
| [ADR-0004](ADR-0004-evidence-snapshot-separation.md) | 原始证据、事实和派生快照分离 |
| [ADR-0005](ADR-0005-public-private-data-isolation.md) | 公开公司事件与基金内部投资数据隔离 |
| [ADR-0006](ADR-0006-no-vector-database-phase1.md) | 第一阶段不使用独立向量数据库 |
| [ADR-0007](ADR-0007-identity-first-automated-publication.md) | 人工只处理身份例外，例行事实自动发布，其他异常保留为未确认线索 |
| [ADR-0008](ADR-0008-official-identity-verification-and-rerouting.md) | 使用官方工商证据核验主体，身份歧义由人工选择并重新路由原记录 |
| [ADR-0009](ADR-0009-dual-channel-access-and-data-scope.md) | 采用个人与机构双通道，平台共享事实与个人/机构私有数据按作用域隔离 |
| [ADR-0010](ADR-0010-licensed-business-identity-verification.md) | 授权商业工商数据可作为独立身份核验依据，但不得冒充政府官方来源或降低事件发布门槛 |
| [ADR-0011](ADR-0011-global-company-identity-index.md) | 公司主档跨个人、机构和基金全局复用，已核验法定名称进入共享身份索引 |

状态均为“已接受”，适用于 `DEMO / VALIDATION`。改变决定需新增 ADR，不回改历史理由。

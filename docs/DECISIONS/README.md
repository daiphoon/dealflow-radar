# 架构决策索引

| ADR | 决定 |
| --- | --- |
| [ADR-0001](ADR-0001-on-demand-low-frequency-refresh.md) | 按需更新加低频巡检，不做每日全量更新 |
| [ADR-0002](ADR-0002-postgresql-job-queue.md) | PostgreSQL 任务表加 Cron，暂不引入 Celery/Redis |
| [ADR-0003](ADR-0003-kimi-capability-boundary.md) | Kimi 会员/Agent/Code 与应用 API、商业数据授权解耦 |
| [ADR-0004](ADR-0004-evidence-snapshot-separation.md) | 原始证据、事实和派生快照分离 |
| [ADR-0005](ADR-0005-public-private-data-isolation.md) | 公开公司事件与基金内部投资数据隔离 |
| [ADR-0006](ADR-0006-no-vector-database-phase1.md) | 第一阶段不使用独立向量数据库 |

状态均为“已接受”，适用于 `DEMO / VALIDATION`。改变决定需新增 ADR，不回改历史理由。

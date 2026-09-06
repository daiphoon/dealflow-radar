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
| [ADR-0010](ADR-0010-licensed-business-identity-verification.md) | 历史决策：授权商业工商数据作为独立身份依据；供应商路线已由 ADR-0018 取代 |
| [ADR-0011](ADR-0011-global-company-identity-index.md) | 公司主档跨个人、机构和基金全局复用，已核验法定名称进入共享身份索引 |
| [ADR-0012](ADR-0012-cloudbase-identity-local-authorization.md) | CloudBase 仅核验身份，用户邀请、角色、基金授权和 RLS 继续由本地系统管理 |
| [ADR-0013](ADR-0013-single-host-invitation-deployment.md) | 历史决策：上海单机与个人备案；单机安全和备份原则保留，短期地域路线由 ADR-0014 取代 |
| [ADR-0014](ADR-0014-hong-kong-invitation-deployment.md) | 邀请测试先部署腾讯云中国香港，验证后再以企业主体评估迁入大陆和备案 |
| [ADR-0015](ADR-0015-on-demand-company-research.md) | 部分保留：后台队列、共享缓存、额度、取消恢复和分级展示；天眼查研究方案由 ADR-0018 取代 |
| [ADR-0016](ADR-0016-licensed-evidence-details.md) | 部分保留：事件、证据详情和原始响应分离；天眼查专用投影路线由 ADR-0018 取代 |
| [ADR-0017](ADR-0017-investor-material-change-layer.md) | 供应商模块只作为数据入口，投资者主层由版本化快照生成可验证的重要变化；模型只解释已有证据支持的变化 |
| [ADR-0018](ADR-0018-provider-neutral-bounded-web-research.md) | 采用供应商中立、预算受限、证据优先的公开网络研究；天眼查进入安全退役，不引入无限自主 Agent |
| [ADR-0019](ADR-0019-manually-reviewed-exchange-identity.md) | 以独立依据记录交易所披露资料人工主体核验，复用私有导入与身份工作台，不冒充政府来源或绕过研究闸门 |

当前有效决策适用于 `DEMO / VALIDATION`。ADR-0014 取代 ADR-0013 的短期地域与备案主体路线；ADR-0018 取代 ADR-0010，并部分取代 ADR-0015、ADR-0016 的供应商专用实现方向，仍保留其通用队列、缓存、取消恢复和证据分层原则。历史理由不回改。

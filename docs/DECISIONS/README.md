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
| [ADR-0012](ADR-0012-cloudbase-identity-local-authorization.md) | CloudBase 仅核验身份，用户邀请、角色、基金授权和 RLS 继续由本地系统管理 |
| [ADR-0013](ADR-0013-single-host-invitation-deployment.md) | 历史决策：上海单机与个人备案；单机安全和备份原则保留，短期地域路线由 ADR-0014 取代 |
| [ADR-0014](ADR-0014-hong-kong-invitation-deployment.md) | 邀请测试先部署腾讯云中国香港，验证后再以企业主体评估迁入大陆和备案 |
| [ADR-0015](ADR-0015-on-demand-company-research.md) | 新公司使用后台身份确认与天眼查六大模块按需研究；结果映射到内部事件分类，并实施共享缓存、双重额度、取消恢复和分级展示 |
| [ADR-0016](ADR-0016-licensed-evidence-details.md) | 授权数据优先由已有缓存生成平台证据详情；供应商链接只作补充，零记录和风险概览不误导为事实结论 |
| [ADR-0017](ADR-0017-investor-material-change-layer.md) | 供应商模块只作为数据入口，投资者主层由版本化快照生成可验证的重要变化；模型只解释已有证据支持的变化 |

状态均为“已接受”，适用于 `DEMO / VALIDATION`。ADR-0014 取代 ADR-0013 的短期地域与备案主体路线；ADR-0015 分两个代码 PR 实施，真实调用保持关闭至最终新公司验收；历史理由不回改。

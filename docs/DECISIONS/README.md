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
| [ADR-0020](ADR-0020-public-identity-research.md) | 用户提供名称和信用代码，系统受限查证；公开交叉核对独立于官方核验，资料不足保留候选而不要求执照扫描件 |
| [ADR-0021](ADR-0021-incremental-event-delivery.md) | 保留现有系统，增量重构事件处理链；先单类事件闭环，再来源/成本、低频监控和真实价值验证，机构能力条件后置 |
| [ADR-0022](ADR-0022-curated-baseline-and-incremental-research.md) | 负责人确认资料经轻量校验形成初始数据，用户按需触发后台增量研究；人工确认、原来源及抓取状态分开，独立检索与导入维护分开验收 |
| [ADR-0023](ADR-0023-research-delivery-and-stale-refresh.md) | 多维初始资料、按需研究与过期访问更新整体交付；固定分母验收和后台启用分开 |
| [ADR-0024](ADR-0024-research-acquisition-and-extraction-evaluation.md) | 先比较专业搜索、正文获取与有证据约束的抽取，再按实测完成事项维护闭环；不全量盲换供应商 |

当前有效决策适用于 `DEMO / VALIDATION`。ADR-0014 取代 ADR-0013 的短期地域与备案主体路线；ADR-0018 取代 ADR-0010，并部分取代 ADR-0015、ADR-0016 的供应商专用实现方向，仍保留其通用队列、缓存、取消恢复和证据分层原则。历史理由不回改。

ADR-0021 补充 ADR-0017/0018/0020，并取代旧看板的待办推进顺序；不改变既有身份、权限、费用上限或发布边界。[实施看板](../10-implementation-plan.md)维护唯一当前状态，[完整计划](../15-incremental-event-delivery-plan.md)维护验收契约；历史 ADR 的实现状态不作为当前任务入口。

ADR-0022 部分取代 ADR-0018/0020 对负责人已确认资料的重复公开查证前置，并补充 ADR-0007 的人工复核初始事实路径；ADR-0021 的交付方式保留，E4 按新切片推进。既有未经人工确认申请的核验、权限、费用上限和自动负面发布规则继续有效；新决策已明确，实现与启用状态以看板为准。

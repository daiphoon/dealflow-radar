# 原始股雷达

面向未上市被投企业股东、基金投资人和投后管理人员的私有投后信息监测平台。产品定位是：后台按需更新与低频巡检，前台即时读取 PostgreSQL 中已发布的数据；每条重要结论都能回到来源、证据和审核记录。

## 当前状态

当前为 `DEMO / VALIDATION` 的第 1 阶段，仅完成产品、数据、架构、安全、成本和实施设计。没有应用代码、依赖安装、真实外部调用或生产部署。

默认安全开关：

```env
APP_MODE=demo
EXTERNAL_CALLS_ENABLED=false
PAID_API_CALLS_ENABLED=false
AUTO_REFRESH_ENABLED=false
```

## 核心原则

- 用户查询只读数据库；数据过期时先返回旧快照，再按预算和冷却规则入队。
- 原始证据、结构化事件、指标观测和派生快照分层保存。
- 无新文档不调用 LLM，无变化不重建报告。
- 公开公司事实可复用，基金投资金额、持股比例和内部估值按租户与基金隔离。
- Demo 使用虚构数据；真实资料、密钥和私有导入文件不进入 Git。

## 文档导航

| 主题 | 文档 |
| --- | --- |
| 产品范围与验收 | [产品需求](docs/00-product-requirements.md) |
| 领域对象与身份解析 | [领域模型](docs/01-domain-model.md) |
| 组件、时序与后台流水线 | [系统架构](docs/02-system-architecture.md) |
| 表、约束、幂等与血缘 | [数据库设计](docs/03-database-design.md) |
| 事件、评分与审核 | [事件分类](docs/04-event-taxonomy.md) |
| Provider、Kimi、DeepSeek 与导入 | [数据源策略](docs/05-data-source-strategy.md) |
| 公式、预算闸门与四种规模情景 | [成本控制](docs/06-cost-control.md) |
| 权限、隐私与合规 | [安全合规](docs/07-security-compliance.md) |
| API 与严格事件 Schema | [API 设计](docs/08-api-design.md) |
| 页面与报告草图 | [界面线框](docs/09-ui-wireframes.md) |
| 第 2—5 阶段 | [实施计划](docs/10-implementation-plan.md) |
| 离线测试与验收 | [测试策略](docs/11-test-strategy.md) |
| 部署、恢复与故障处置 | [运维手册](docs/12-operations-runbook.md) |
| 已确认架构决定 | [ADR 索引](docs/DECISIONS/README.md) |

第 2 阶段开始前需先审核本阶段设计，详见[实施计划](docs/10-implementation-plan.md)。

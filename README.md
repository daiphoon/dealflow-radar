# 原始股雷达

面向未上市被投企业股东、基金投资人和投后管理人员的私有投后信息监测平台。产品定位是：后台按需更新与低频巡检，前台即时读取 PostgreSQL 中已发布的数据；每条重要结论都能回到来源、证据和审核记录。

## 当前状态

当前为 `DEMO / VALIDATION` 第 2 阶段候选版本，已实现并验证最小数据闭环：

- 10 家虚构公司及两个虚构租户/基金；
- Mock 文档幂等导入、主体精确匹配、候选事件、证据和人工审核；
- 审核通过后事务化发布事件并生成当前公司快照；
- 公司列表、公司详情、证据、新鲜度和授权基金投资概览；
- 刷新任务 `dry-run` 与重复任务合并；
- 应用层基金授权过滤及 PostgreSQL RLS 策略迁移；
- 外部搜索、模型、付费 API 和自动刷新默认全部关闭。

本机已完成 PostgreSQL 16 迁移、Schema 漂移检查、非表所有者 `NOBYPASSRLS` 账户的租户/基金隔离，以及 API 和服务端渲染页面的端到端验证。该版本仍不能视为生产可用：SQLite 只用于离线自动测试，测试身份 Header 也不是生产认证系统。

## 本地启动

要求 Python 3.12、[uv](https://docs.astral.sh/uv/) 和 Node.js 20 以上。事实主库推荐 PostgreSQL 16：

```bash
POSTGRES_PASSWORD='请替换为本地密码' docker compose up -d db
export DATABASE_URL='postgresql+psycopg://demo_user:请替换为本地密码@127.0.0.1:5432/equity_radar'
uv sync --all-groups
uv run alembic upgrade head
uv run python -m scripts.seed_demo
uv run uvicorn backend.app.main:app --reload
```

上例为本地快速启动，`demo_user` 是迁移和导入账户。真实数据环境不得让日常应用使用表所有者账户；应用应改用单独的非表所有者、无 `BYPASSRLS` 权限账户。第 2 阶段已用该账户完成越权负向验证，结果见[安全合规](docs/07-security-compliance.md)与[实施记录](docs/IMPLEMENTATION_LOG.md)。

如果本机暂时没有 PostgreSQL，可用 SQLite 完成无真实数据的离线烟测：

```bash
export DATABASE_URL='sqlite:///./data/demo.db'
uv sync --all-groups
uv run alembic upgrade head
uv run python -m scripts.seed_demo
uv run uvicorn backend.app.main:app --reload
```

另开终端启动前端：

```bash
cd frontend
npm ci
npm run dev
```

访问 `http://127.0.0.1:3000`。前端默认使用虚构机构管理员身份；API 调试可访问 `http://127.0.0.1:8000/docs`。初始事件都在人工审核队列中，审核通过前不会显示为已发布事实。

## 验证

```bash
uv run ruff check backend migrations scripts tests
uv run pytest -q
cd frontend
npm audit
npm run typecheck
npm run build
```

所有测试默认离线，且不会调用付费服务。

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

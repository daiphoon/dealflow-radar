# 原始股雷达

面向未上市被投企业股东、基金投资人和投后管理人员的私有投后信息监测平台。产品定位是：后台按需更新与低频巡检，前台即时读取 PostgreSQL 中已发布的数据；每条重要结论都能回到来源、证据和审核记录。

## 当前状态

当前为 `DEMO / VALIDATION`，第 2 阶段最小数据闭环已经验收，并已补充按需缓存、Mock Worker V1 与人工研究导入 V1：

- 10 家虚构公司及两个虚构租户/基金；
- Mock 文档幂等导入、主体精确匹配、候选事件、证据和人工审核；
- 审核通过后事务化发布事件并生成当前公司快照；
- 公司列表、公司详情、证据、新鲜度和授权基金投资概览；
- 刷新任务 `dry-run`、14 天动态新鲜度、24 小时冷却与重复任务合并；
- 公司列表只展示状态，不批量触发任务；公司详情过期时仅在自动刷新开关启用后入队；
- 单次 Mock Worker 可领取一个虚构数据任务，支持租约、过期重领、零费用用量记录和 `stale → fresh` 闭环；
- 本机 JSON 人工研究导入支持文件/批次幂等、公司身份解析、证据血缘、未解析主体审核和零费用记录；
- 本机人工审核工作台可查看候选、证据、独立评价与不确定性，并以必填理由批准或驳回事件；
- 应用层基金授权过滤及 PostgreSQL RLS 策略迁移；
- 外部搜索、模型、付费 API、自动刷新和审核工作台默认全部关闭。

本机已完成 PostgreSQL 16 迁移、Schema 漂移检查、非表所有者 `NOBYPASSRLS` 账户的租户/基金隔离，以及 API 和服务端渲染页面的端到端验证。该版本仍不能视为生产可用：SQLite 只用于离线自动测试，测试身份 Header 也不是生产认证系统。

## 本地启动

要求 Python 3.12、[uv](https://docs.astral.sh/uv/) 和 Node.js 20 以上。事实主库推荐 PostgreSQL 16：

```bash
export POSTGRES_PASSWORD='请设置迁移账户本地密码'
export APP_DATABASE_PASSWORD='请设置另一个应用账户本地密码'
export DATABASE_ADMIN_URL="postgresql+psycopg://demo_user:${POSTGRES_PASSWORD}@127.0.0.1:5432/equity_radar"
export DATABASE_URL="postgresql+psycopg://equity_app:${APP_DATABASE_PASSWORD}@127.0.0.1:5432/equity_radar"
docker compose up -d db
uv sync --all-groups
DATABASE_URL="$DATABASE_ADMIN_URL" uv run alembic upgrade head
DATABASE_URL="$DATABASE_ADMIN_URL" uv run python -m scripts.seed_demo
uv run python -m scripts.bootstrap_local_database
uv run uvicorn backend.app.main:app --reload
```

`demo_user` 只执行迁移和虚构数据导入；API 默认使用 `equity_app`。初始化脚本可重复执行，会创建或更新该应用账户、撤销建库和绕过 RLS 等高权限，并授予当前及未来迁移表的必要权限。两个本地密码不得相同，也不得提交到 Git。

缓存参数由 `REFRESH_POLICY_VERSION`、`RECENT_QUERY_TTL_DAYS` 和 `REFRESH_REQUEST_COOLDOWN_HOURS` 配置。Demo 默认分别为 `demo-v1`、14 天和 24 小时；`AUTO_REFRESH_ENABLED=false` 时仍会准确显示过期状态，但不会因页面访问创建任务。即使开启自动入队，同步请求也不会调用搜索、模型或付费 API。

已有 `mock_refresh` 任务时，可在另一个终端运行一次虚构数据 Worker：

```bash
export WORKER_TENANT_ID="$(uv run python -c 'from backend.app.demo import ALPHA_TENANT_ID; print(ALPHA_TENANT_ID)')"
APP_MODE=demo uv run python -m scripts.run_mock_worker
```

每次命令最多处理该租户的一个任务；无任务时返回 `idle`。命令要求显式设置 `APP_MODE=demo`，且只接受两个固定虚构租户。此 Worker 不加载 Provider、不访问网络，只用于验证队列闭环：有快照时更新检查时间但保留事实基准日，无快照时保持 `unknown`，不得用于真实公司检查。

### 人工研究导入 V1

V1 只接受 `data/private/research_imports/` 下不超过 1 MiB 的 JSON，且每批最多 500 条、`license_status` 必须为 `public`、目标公司须预先存在。导入只创建原始证据、实体提及、`in_review` 候选事件和审核项；身份未解析时只创建提及审核项，不生成事件或快照。入口不访问网络、不调用模型，外部调用和估算费用恒为 0。

可用仓库中的纯虚构示例验证：

```bash
mkdir -p data/private/research_imports
cp data/sample/manual_research_import.json data/private/research_imports/manual-example.json
export RESEARCH_IMPORT_FILE=manual-example.json
export IMPORT_USER_ID="$(uv run python -c 'from backend.app.demo import ALPHA_USER_ID; print(ALPHA_USER_ID)')"
uv run python -m scripts.import_research_json
```

重复执行同一文件返回 `duplicate`，不会新增文档、事件或费用记录。真实导入文件不得提交 Git；V1 不接受内部财务、投委会、投资协议等敏感材料，也没有开放上传 API，因为当前测试身份 Header 不适合真实资料入口。未解析主体可以进入审核队列，但“选择正确公司并重新生成候选”的专用处理流程尚未实现，通用事件审核接口会拒绝直接批准这类记录。

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

### 人工审核工作台 V1（仅本机）

只在本机私有验证时显式开启后端开关，并让前端使用当前租户内具有 `reviewer` 角色的本地用户 ID：

```bash
export REVIEW_WORKBENCH_ENABLED=true
uv run uvicorn backend.app.main:app --reload

cd frontend
export DEMO_USER_ID='replace_with_local_reviewer_uuid'
npm run dev
```

访问 `http://127.0.0.1:3000/reviews`。页面展示候选事实、三类时间、五项独立评价、证据和不确定性；批准或驳回都要求理由及核对确认。批准会发布事件并重建公司快照，驳回会保留决定历史；两者均不调用外部 Provider。实体提及歧义只读展示，不能用通用事件按钮直接批准。

`X-Demo-User-Id` 仍只是本地测试身份，不是登录系统。真实数据工作台不得绑定公网地址或部署到共享环境；正式认证完成前，本开关必须保持关闭。

## 验证

```bash
uv run ruff check backend migrations scripts tests
uv run pytest -q
POSTGRES_RLS_DATABASE_URL="$DATABASE_URL" uv run pytest -q tests/integration/test_postgres_rls.py
cd frontend
npm audit
npm run typecheck
npm run build
```

默认测试不会调用付费服务；真实 PostgreSQL RLS 测试只有显式提供受限账户 URL 时才运行。

GitHub CI 在 Pull Request 和 `main` 推送时使用临时 PostgreSQL 16，一次完成后端静态检查、SQLite 迁移、RLS 测试和前端生产构建。CI 只使用虚构数据与临时凭据，业务外部调用开关保持关闭。

## 核心原则

- 用户查询的业务结果只读取数据库；数据过期时先返回旧快照，再按开关和冷却规则入队。
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

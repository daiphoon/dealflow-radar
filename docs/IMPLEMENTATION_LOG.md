# 实施记录

## 2026-07-13｜第 0 阶段与第 1 阶段

- 日期：2026-07-13
- 任务：创建项目级规则，完成产品、领域、架构、数据库、事件、数据源、成本、安全、API、界面、实施、测试和运维设计；创建 6 份 ADR 与最小目录骨架。
- 关键文件：`AGENTS.md`、`README.md`、`docs/00-product-requirements.md` 至 `docs/12-operations-runbook.md`、`docs/DECISIONS/`、`.env.example`、`.gitignore`、`docker-compose.yml`。
- 实际命令：`find . -maxdepth 3 -print`；`git status --short --branch`；`diff -u <(awk ...附件指定区块...) AGENTS.md`；`python3 -c ...`（链接、围栏、标题、实体、ADR、图表、路径、成本和验收矩阵检查）；`ruby -e ...`（Compose YAML 解析）；`rg ...`（默认开关、私有目录和疑似密钥扫描）。
- 测试结果：`AGENTS.md` 与附件区块一致；29 个必需实体、6 份 ADR、9 张 Mermaid 图和 12 行成本情景检查通过；成本表复算通过；Compose YAML 仅含 `db` 服务且解析通过；疑似真实密钥扫描无命中，`.env.example` 仅含空值/占位值；默认外部、付费、自动刷新和外部测试开关均关闭；未启动服务、未联网、未安装依赖。当前目录不是 Git 仓库，`git status` 按预期返回非仓库；本机无 Mermaid CLI，按禁止安装依赖要求未做渲染测试，仅完成结构检查。
- 未解决阻塞：第 1 阶段无阻塞；第 2 阶段开始前需确认单 Demo 租户运行方式、测试身份 RBAC 边界和首版界面范围。

## 2026-07-14｜第 2 阶段最小数据闭环

- 日期：2026-07-14
- 任务：初始化并推送私有 Git 仓库基线；实现 10 家虚构公司的 Mock 导入、主体匹配、候选事件、证据、人工审核、事件发布、公司快照、基金授权读取、刷新任务合并、公司列表和公司详情；补充真实 PostgreSQL/RLS 验收和可重复的迁移/应用账户分离。
- 关键文件：`backend/app/`、`migrations/`、`tests/`、`frontend/`、`data/sample/mock_research.json`、`scripts/seed_demo.py`、`scripts/bootstrap_local_database.py`、`docker-compose.yml`、`.env.example`、`pyproject.toml`、`uv.lock`、`README.md`、`docs/07-security-compliance.md`、`docs/10-implementation-plan.md`、`docs/11-test-strategy.md`。
- 实际命令：`git init -b main`、`git push -u origin main`、`uv sync --all-groups`、`docker compose up -d db`、`uv run alembic upgrade head`、`uv run alembic check`、`uv run python -m scripts.seed_demo`、`uv run python -m scripts.bootstrap_local_database`（首次创建、重复执行和共用密码拒绝）、非表所有者 PostgreSQL `psql` RLS 查询、显式 PostgreSQL RLS Pytest、`uv run ruff check ...`、`uv run ruff format --check ...`、`uv run pytest -q`、`npm install`、`npm audit`、`npm run typecheck`、`npm run build`、`uv run uvicorn ...`、`npm run dev ...`、本地 `curl` 页面/API 烟测。
- 测试结果：Docker Desktop 29.6.1 与 PostgreSQL 16 容器健康；真实迁移和 Schema 漂移检查通过；受限应用账户首次创建和重复收敛通过，共用迁移用户或密码按预期拒绝，且不能读取 Alembic 迁移状态表或删除业务表记录；无请求上下文时受保护表返回 0 行，Alpha 管理员仅见 10 笔本基金投资和 10 条审核记录，Beta 投资人仅见 1 笔本基金投资且不见审核记录，无基金授权和跨租户用户返回 0 行；真实 API 审核发布后快照、事件、证据和授权投资概览正确，未认证为 `401`、无授权详情为 `404`，公司列表和详情页均为 `200`；Ruff 与格式检查通过；默认 Pytest 16 项通过、1 项 PostgreSQL 测试按预期跳过，显式真实 PostgreSQL 测试 1 项通过；前端依赖审计 0 个已知漏洞，TypeScript 与生产构建通过；所有业务外部调用和估算费用为 0。
- 未解决阻塞：Codex 浏览器连接报 `Cannot redefine property: process`，已完成 HTTP 端到端烟测但未完成视觉截图验收；FastAPI TestClient 有一条上游弃用警告，不影响当前测试结果。

## 2026-07-15｜第 2 阶段视觉验收

- 日期：2026-07-15
- 任务：在真实 PostgreSQL 受限应用账户下完成公司列表和公司详情页的桌面浏览器视觉验收。
- 关键文件：`docs/10-implementation-plan.md`、`docs/11-test-strategy.md`、`docs/IMPLEMENTATION_LOG.md`。
- 实际命令：`uv run uvicorn ...`、`npm run dev ...`；浏览器 DOM、普通视口截图、页面尺寸和控制台日志检查。
- 测试结果：公司列表双列卡片、状态标签和已发布事件正常；详情页投资关系、事件评分、证据和信息缺口正常；页面无横向溢出，控制台无警告或错误；业务外部调用为 0。
- 未解决阻塞：视觉验收无阻塞；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-15｜按需缓存 V1

- 日期：2026-07-15
- 任务：实现配置化 14 天最近查询 TTL、24 小时请求冷却、动态新鲜度、详情过期按开关入队、列表不扇出、活动任务合并和中文状态展示；完整调度器与 Worker 不在本次范围。
- 关键文件：`backend/app/config.py`、`backend/app/main.py`、`backend/app/services.py`、`tests/unit/test_database_config.py`、`tests/integration/test_demo_vertical_slice.py`、`frontend/app/`、`.env.example`、`README.md`、`docs/02-system-architecture.md`、`docs/08-api-design.md`、`docs/10-implementation-plan.md`、`docs/11-test-strategy.md`。
- 实际命令：`uv run pytest -q tests/unit/test_database_config.py tests/integration/test_demo_vertical_slice.py`；`uv run ruff check ...`；`uv run ruff format --check ...`；`uv run pytest -q`；`npm run typecheck`；`npm run build`；`npm audit`；临时 PostgreSQL 数据库迁移、虚构数据导入、受限账户初始化、`tests/integration/test_postgres_rls.py` 和按需缓存 API 烟测；临时 SQLite 视觉夹具、`uv run uvicorn ...`、`npm run dev ...`、浏览器 DOM/截图/页面尺寸/控制台检查；临时环境清理检查。
- 测试结果：默认 Pytest 19 项通过、1 项 PostgreSQL 测试按预期跳过；临时 PostgreSQL 16 受限账户 RLS 测试 1 项通过；真实数据库烟测确认列表返回 10 家公司且不入队，首次无快照详情返回 `unknown` 并创建 1 个任务，第二次返回 `refreshing` 且任务数仍为 1；桌面浏览器确认 `fresh`、`stale`、`refreshing` 的中文标签、颜色、旧数据展示和列表同步状态正确，1280px 视口无横向溢出，控制台无警告或错误，全部请求为 `200`；Ruff、格式、TypeScript 和生产构建通过，依赖审计 0 个已知漏洞；所有业务外部调用和估算费用为 0；临时数据库与角色均已删除。
- 未解决阻塞：完整 `refresh_policies` 表、预算闸门、`next_check_at`、Cron、Worker 和动态升降频仍按后续阶段实施；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-15｜Mock Worker V1

- 日期：2026-07-15
- 任务：实现按租户单次领取一个 `mock_refresh` 的 Demo Worker，补充可配置短租约、心跳、过期租约恢复、模拟无变化处理、零成本用量台账和命令行入口；不接入真实 Provider、常驻进程或 Cron。
- 关键文件：`backend/app/worker.py`、`backend/app/config.py`、`backend/app/models.py`、`migrations/versions/0004_add_refresh_job_heartbeat.py`、`scripts/run_mock_worker.py`、`tests/integration/test_mock_worker.py`、`.env.example`、`README.md`、`docs/02-system-architecture.md`、`docs/10-implementation-plan.md`、`docs/11-test-strategy.md`、`docs/12-operations-runbook.md`。
- 实际命令：`uv run pytest -q tests/unit/test_database_config.py tests/integration/test_migrations.py tests/integration/test_mock_worker.py`；`uv run ruff check backend migrations scripts tests`；`uv run ruff format --check backend migrations scripts tests`；`uv run pytest -q`；临时 SQLite `alembic upgrade head`、`alembic check` 和 `alembic downgrade base`；`npm audit`；`npm run typecheck`；`npm run build`；临时 PostgreSQL 16 数据库迁移、Schema 漂移检查、虚构数据导入、受限账户初始化、RLS 测试、按需入队、`python -m scripts.run_mock_worker` 两次执行和临时环境清理检查。
- 测试结果：针对性测试 11 项通过；默认 Pytest 22 项通过、1 项 PostgreSQL 测试按预期跳过；Ruff、格式、SQLite 迁移往返、TypeScript 和生产构建通过，依赖审计 0 个已知漏洞；临时 PostgreSQL 受限账户 RLS 测试通过，真实命令行闭环确认 `stale → refreshing → fresh`、任务完成并释放租约、心跳存在、首次运行零外部调用和零费用、第二次运行 `idle`；无快照保持 `unknown`，事实 `data_as_of` 不变，租户之间不交叉领取；临时数据库与角色已删除。
- 未解决阻塞：该 Worker 只模拟虚构数据无变化检查；真实 Provider、预算闸门、`refresh_policies` 表、`next_check_at`、Cron、常驻进程、周期心跳和重试仍未实现；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-15｜Mock 隔离加固与 CI

- 日期：2026-07-15
- 任务：要求 Mock Worker 显式启用 Demo 模式并限制为两个固定虚构租户；增加单 Job GitHub CI，自动验证后端、迁移、PostgreSQL RLS 和前端构建。
- 关键文件：`backend/app/demo.py`、`backend/app/worker.py`、`scripts/run_mock_worker.py`、`tests/unit/test_mock_worker_cli.py`、`tests/integration/test_mock_worker.py`、`.github/workflows/ci.yml`、`README.md`、`docs/11-test-strategy.md`、`docs/12-operations-runbook.md`。
- 实际命令：`uv run pytest -q tests/unit/test_mock_worker_cli.py`；`uv run ruff check backend migrations scripts tests`；`uv run ruff format --check backend migrations scripts tests`；`uv run pytest -q`；`npm ci`；`npm run typecheck`；`npm run build`；Ruby YAML 解析；`git diff --check`；临时 PostgreSQL 16 容器迁移、Schema 漂移检查、虚构数据导入、受限账户初始化和完整 Pytest。
- 测试结果：Mock Worker 隔离和闭环测试 12 项通过；默认 Pytest 31 项通过、1 项 PostgreSQL 测试按预期跳过；临时 PostgreSQL 受限账户环境 32 项全部通过；Ruff、格式、CI YAML、TypeScript 和生产构建通过，`npm ci` 审计 0 个已知漏洞；业务外部调用和估算费用为 0，临时容器已删除。GitHub CI 远端结果由 PR 检查记录，不在推送前声明通过。
- 未解决阻塞：FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-15｜人工研究导入 V1

- 日期：2026-07-15
- 任务：实现本机 JSON 人工研究导入、批次与文件幂等、公开来源许可限制、公司身份解析、事件/证据去重、未解析主体审核、租户管理员 RLS 和零费用用量记录；所有候选强制人工审核，不开放上传 API，不调用外部服务。
- 关键文件：`backend/app/providers.py`、`backend/app/services.py`、`backend/app/models.py`、`migrations/versions/0005_add_research_imports.py`、`scripts/import_research_json.py`、`data/sample/manual_research_import.json`、`tests/unit/test_manual_research_provider.py`、`tests/integration/test_manual_research_import.py`、`tests/integration/test_postgres_rls.py`、`.env.example`、`README.md`、`docs/`。
- 实际命令：人工导入针对性 Pytest；`uv run ruff check backend migrations scripts tests`；`uv run ruff format --check backend migrations scripts tests`；`uv run pytest -q`；临时 PostgreSQL 16 空库迁移、Schema 漂移、虚构数据导入、受限账户初始化、RLS 负向测试、实际 CLI 首次/重复导入、关系计数和含数据迁移降级；`npm audit`、`npm run typecheck`、`npm run build`；`git diff --check`。
- 测试结果：默认 Pytest 51 项通过、1 项真实 PostgreSQL 测试按预期跳过；临时 PostgreSQL 受限账户 RLS 测试通过，7 张表启用 RLS，伪造跨租户上下文写入被拒绝；实际 CLI 首次导入 2 条虚构记录，形成 2 份文档、1 个 `in_review` 事件、2 个审核项、0 个快照和 1 条零费用记录，第二次返回 `duplicate`；同事件多来源只追加候选证据，已发布事件的新证据不直接挂接，冲突整批回滚；PostgreSQL 含数据升级/降级、Ruff、格式、TypeScript、生产构建通过，依赖审计 0 个已知漏洞；业务外部调用和费用为 0，临时容器与私有测试文件已清理。
- 未解决阻塞：实体提及审核的公司选择与候选重建流程、真实认证、API 上传、CSV/Excel/Markdown、内部敏感材料和真实 Provider 均未实现；尚未开始真实公司验证；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

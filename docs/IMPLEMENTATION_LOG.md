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

## 2026-07-15｜两家公司私有真实数据试点

- 日期：2026-07-15
- 任务：为 2 家用户授权真实公司建立专用本地验证租户和公开身份主数据，每家仅选 1 条官网事件做最小导入验证；不录入投资金额、持股、估值、内部材料或基金/投资关系，不自动发布。
- 关键文件：本机 `data/private/research_imports/` 下 2 份权限为 `600` 的 Git 忽略 JSON（未提交）、`docs/10-implementation-plan.md`、`docs/IMPLEMENTATION_LOG.md`；本地 PostgreSQL 中的专用 tenant、user、role assignment、company 和导入血缘记录。
- 实际命令：公开官网和政府页面人工核验；`ManualResearchImportProvider.load()` 离线 Schema 校验；应用会话降权为非所有者 `equity_app` 后运行 `python -m scripts.import_research_json` 首次与重复导入；PostgreSQL 血缘/计数查询、RLS 跨租户负向检查和只读 API 烟测。
- 测试结果：2/2 记录均按统一社会信用代码精确命中，形成 2 份原始证据、2 个 `in_review` 候选事件和 2 个 `pending` 审核项，证据关系均完整；重复导入均返回 `duplicate` 且新增计数为 0；公司快照和投资关系仍为 0；导入入口外部调用、Token 与估算费用均为 0，项目未调用付费 Provider；试点租户可见 2 个批次/审核/用量记录，现有 Demo 租户对这些记录的 RLS 可见计数均为 0。
- 未解决阻塞：未经用户确认不得创建真实基金/投资关系，因此试点公司暂不出现在公司列表；审核队列尚无前端页面；当前时间模型无法单独表达“仅日期精度”，已将不确定的时分保持为未知；2 个候选事件均保持待审，等待项目负责人决策。FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-15｜人工审核工作台 V1

- 日期：2026-07-15
- 任务：实现默认关闭的本机人工审核工作台，展示候选公司、事实、不确定性、三类时间、五项独立评价、证据和触发规则；事件可在必填理由与核对确认后批准或驳回，身份提及歧义保持只读；不接入正式认证或外部 Provider。
- 关键文件：`backend/app/config.py`、`backend/app/main.py`、`backend/app/schemas.py`、`backend/app/services.py`、`frontend/app/reviews/`、`frontend/lib/api.ts`、`frontend/app/globals.css`、`.env.example`、`tests/`、`README.md`、`docs/`。
- 实际命令：审核工作台针对性 Pytest；`uv run ruff check backend migrations scripts tests`；`uv run ruff format --check backend migrations scripts tests`；`uv run pytest -q`；`npm audit`；`npm run typecheck`；`npm run build`；`git diff --check`；真实 PostgreSQL 受限应用角色下启动本地 API 与前端，执行只读工作台 API 计数、桌面/手机 DOM、截图、页面尺寸和控制台检查。
- 测试结果：针对性测试 13 项通过；默认 Pytest 52 项通过、1 项真实 PostgreSQL 测试按预期跳过；Ruff、格式、TypeScript、生产构建和差异检查通过，依赖审计 0 个已知漏洞；真实私有试点工作台正确读取 2 条候选及完整证据，两条均继续保持 `pending`，未提交批准或驳回；1280px 与 390px 视口均无横向溢出，浏览器控制台无警告或错误；业务外部调用、Token 和费用均为 0。
- 未解决阻塞：正式认证、身份提及解析、纠正/撤回、筛选分页和公开部署均未实现；真实基金/投资关系仍等待授权资料；日期精度模型仍不能单独表达“仅日期”，2 条真实候选继续等待项目负责人决定。FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-15｜身份优先自动发布与来源校验 V1

- 日期：2026-07-15
- 任务：按 ADR-0007 将新导入改为“身份例外人工处理、低风险可靠事实自动发布、其他记录保留为未确认线索”；增加来源 URL 检查、零网络/零写入 `dry-run`、日期精度、路由审计、未确认线索展示和可空快照基准日；不接入全网采集、LLM 或付费 Provider。
- 关键文件：`AGENTS.md`、`backend/app/config.py`、`backend/app/providers.py`、`backend/app/services.py`、`backend/app/models.py`、`backend/app/schemas.py`、`migrations/versions/0006_add_publication_routing.py`、`scripts/import_research_json.py`、`frontend/app/companies/[id]/page.tsx`、`frontend/app/reviews/page.tsx`、`frontend/lib/api.ts`、`tests/`、`docs/DECISIONS/ADR-0007-identity-first-automated-publication.md`、`README.md`、`.env.example` 和受影响文档。
- 实际命令：`uv run ruff check backend migrations scripts tests`；`uv run ruff format --check backend migrations scripts tests`；`uv run pytest -q`；`npm run typecheck`、`npm run build`、`npm audit`；本机 PostgreSQL `alembic upgrade head` 和 `alembic check`；三个公开 URL 的状态/页面内容检查；两家公司单事务数据纠正、关系查询、事件输出序列化与审核页服务端渲染烟测；`git diff --check`。一次在仓库根目录运行 npm 因无 `package.json` 失败，改在 `frontend/` 后通过；首次数据事务因 PostgreSQL 不支持 `min(uuid)` 自动回滚，改为显式类型转换后完整提交；首次无状态序列化诊断遗漏数据库参数，补齐后通过。
- 测试结果：Ruff 与格式检查通过；Pytest 62 项通过、1 项 PostgreSQL RLS 测试按预期跳过，只有 FastAPI TestClient 上游弃用警告；SQLite 迁移往返及 PostgreSQL DDL/实际迁移和 Schema 漂移检查通过；TypeScript、Next.js 生产构建通过，依赖审计 0 个已知漏洞。`dry-run` 测试确认不连接数据库、不访问网络且输出保守检查上界。两条私有真实试点记录中，一条官网事件返回 HTTP 200、来源日期得到纠正并按 `identity-first-v1` 自动发布；另一条原深链接返回 HTTP 404，事件降级为 `unconfirmed_lead`，公司官网仍作为独立身份入口展示；两家公司官网已补入私有身份主数据，快照不再用系统发现日冒充事实基准日。审核页烟测返回 `200`，失效深链接不再可点击并显示 HTTP 404，日期精度字段不再伪造时分。公开网页检查共 6 次请求，Token 与估算费用均为 0，并已写入 `usage_ledger`。
- 未解决阻塞：专用“选择公司并重新路由”的身份解析流程、官方工商接口核验、自动采集/Cron、真实认证和真实基金/投资关系仍未实现；因此两家试点公司按现有授权规则仍不进入公司列表。Codex 执行环境把外部域名解析到保留测试网段，内置 SSRF 防护会保守标为不可用；未为方便测试而放宽私网防护。正式运行环境需用公开 DNS 做小批次验证。FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-16｜官方工商身份核验与歧义重路由 V1

- 日期：2026-07-16
- 任务：实现官方工商身份结构化导入、统一社会信用代码校验、有效期内身份候选、管理员与审核员联合授权的歧义选择，以及基于原始记录重建事件并重新执行发布路由；不抓取验证码、登录页面或未公开接口，不在页面请求中访问外网。
- 关键文件：`backend/app/providers.py`、`backend/app/services.py`、`backend/app/models.py`、`backend/app/main.py`、`migrations/versions/0007_add_official_identity_verifications.py`、`scripts/import_official_identity_json.py`、`frontend/app/reviews/`、`tests/`、`docs/DECISIONS/ADR-0008-official-identity-verification-and-rerouting.md`、`.env.example`、`README.md` 和受影响文档。
- 实际命令：`git diff --check`；`uv run ruff check backend migrations scripts tests`；`uv run ruff format --check backend migrations scripts tests`；`uv run pytest -q`；`npm audit --audit-level=high`；`npm run typecheck`；`npm run build`；临时 PostgreSQL 16 空库迁移、`alembic check`、虚构数据导入、受限应用角色初始化、RLS 测试及官方身份冲突选择到事件重路由的真实事务闭环；本地虚构 SQLite 夹具下的审核页桌面浏览器交互、页面尺寸和控制台检查。
- 测试结果：默认 Pytest 73 项通过、1 项 PostgreSQL 测试按预期跳过；真实 PostgreSQL 受限角色 RLS 测试 1 项通过，迁移应用与 Schema 漂移检查通过；事务闭环确认官方冲突记录不会静默改写主数据，人工选择后更新法定全称和代码、保留曾用名、完成审核并将来源未核验的事件安全路由为 `unconfirmed_lead`，所有业务外部调用、Token 与估算费用均为 0。Ruff、格式、TypeScript、Next.js 生产构建通过，依赖审计 0 个已知漏洞；浏览器确认候选选择、必填理由、二次确认、提交成功和审核计数更新，1280px 视口无横向溢出，控制台无警告或错误。真实 PostgreSQL 验收发现并修复解析器版本超出字段长度的问题，并增加回归断言；临时数据库容器和浏览器夹具均已清理。
- 未解决阻塞：尚未获得官方工商数据接口或批量资源的授权，因此 V1 采用私有目录中的结构化官方查询结果导入，通过 Provider 边界预留未来授权 API；不会为自动化而绕过验证码、登录或调用未公开接口。正式认证、自动采集/Cron 和真实基金/投资关系仍不在本阶段；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-17｜个人用户安全查询公司 MVP

- 日期：2026-07-17
- 任务：按 ADR-0009 完成平台共享、个人私有、机构私有和系统受限四类数据作用域安全基线；增加无基金用户按统一社会信用代码或工商全称精确搜索共享公司、共享详情与基金私有叠加层；保留现有影子验证基金和审核流程，不实现正式认证、订阅、关注列表或外部采集。
- 关键文件：`migrations/versions/0008_add_data_scope_security_baseline.py`、`migrations/versions/0009_scope_private_alias_document_uniqueness.py`、`backend/app/models.py`、`backend/app/services.py`、`backend/app/main.py`、`backend/app/schemas.py`、`frontend/app/page.tsx`、`frontend/app/companies/[id]/page.tsx`、`frontend/lib/api.ts`、`tests/integration/test_personal_company_query.py`、`tests/integration/test_postgres_rls.py`、`.env.example`、`.github/workflows/ci.yml` 和受影响文档。
- 实际命令：`uv run ruff check backend migrations scripts tests`；`uv run ruff format --check backend migrations scripts tests`；`uv run pytest -q`；SQLite `alembic upgrade head`、`alembic check`、`alembic downgrade base`；隔离 PostgreSQL 16 从 `0007` 升级到 `0009`、Schema 漂移检查、降级至 `0007`、重新升级及 RLS 负向测试；`npm audit --audit-level=high`、`npm run typecheck`、`npm run build`；本地 PostgreSQL `0008`、`0009` 迁移前后自定义格式备份及 `pg_restore -l` 检查；受限应用账户启动 API 和前端，执行个人及机构路径浏览器 DOM、页面和控制台检查；`git diff --check`。
- 测试结果：Pytest 76 项通过，仅有 FastAPI TestClient 上游弃用警告；Ruff、格式、SQLite 和 PostgreSQL 迁移往返、15 张受保护表的 RLS、TypeScript、Next.js 生产构建和依赖审计通过，依赖审计 0 个已知漏洞。无基金用户可以精确搜索并查看共享虚构 Demo 公司，不能看到租户公司、私有别名、文档、实体提及、未确认线索、刷新状态或投资字段；基金用户仍能看到两家真实公司、影子基金投资叠加层、机构私有已确认事件和 2 条审核历史。浏览器控制台无错误；当前两家真实公司未被晋升为共享目录；重复运行 RLS 测试不污染本地数据库；四个安全开关均为关闭，业务外部调用、付费调用、Token 和估算费用均为 0。
- 未解决阻塞：正式认证与订阅权益尚未实现，当前仍使用受控 Demo 用户头；搜索只支持精确工商全称、信用代码和已核实共享别名；个人关注、备注、请求收录、模糊搜索、真实 Provider 和 8—10 家真实公司双路径影子验证留待后续。FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-17｜受控平台共享事实晋升闭环

- 日期：2026-07-17
- 任务：实现平台管理员将个人或机构私有候选人工晋升为独立平台共享事实、拒绝候选及撤回共享事实；保留私有来源与原文，新增共享展示证据、追加式决定审计、跨机构事实去重、RLS 和复用审核工作台的最小界面；四个外部及自动开关继续关闭。
- 关键文件：`migrations/versions/0010_add_controlled_shared_fact_promotion.py`、`backend/app/models.py`、`backend/app/services.py`、`backend/app/main.py`、`backend/app/schemas.py`、`frontend/app/reviews/`、`frontend/app/companies/[id]/page.tsx`、`tests/integration/test_shared_fact_promotion.py`、`tests/integration/test_postgres_rls.py` 和受影响文档。
- 实际命令：`uv run ruff check .`；`uv run ruff format --check .`；`uv run pytest -q`；SQLite 空库迁移升级、漂移检查和降级；临时 PostgreSQL 16 空库升级至 `0010`、降级至 `0009`、受限应用账户 17 表 RLS 负向测试；`npm audit --audit-level=high`、`npm run typecheck`、`npm run build`；隔离恢复的真实影子验证库上执行 3 条晋升、1 条拒绝、1 条撤回和个人/机构/其他机构 API 检查；浏览器完成管理员晋升、个人详情和撤回闭环。
- 测试结果：私有候选批准后生成独立共享事件，原候选、owner、导入和私有原文不变；同源重复批准幂等，两个机构相同事实复用一个共享事件；共享证据不带私有 `raw_document_id`；身份歧义、严重负面、无展示证据和失效链接均被拒绝，未检查链接需管理员确认并显示警告；个人用户只见共享事实，其他机构不见来源底稿，基金投资叠加仍正常；撤回后个人不再看到事实且审计和私有来源保留。真实验证晋升法奥机器人、苏州博腾生物制药和上海沛塬电子三条低风险事件，随后撤回法奥机器人用于回归；武汉合生低重要性“拟支持”记录被人工拒绝。里程碑期间业务外部调用、付费调用、Token、估算费用和自动发布均为 0。
- 未解决阻塞：链接健康仍以已有检查状态或管理员确认处理，尚未实现完整自动健康检查或正文语义自动核验；当前仍使用受控 Demo 身份头，平台管理员角色不代表生产认证；并发晋升依靠数据库唯一约束防重但尚未做压力测试；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-18｜受控官方来源监测与候选文档队列 V1

- 日期：2026-07-18
- 任务：为已核验公司增加管理员显式登记可信官网、政府页、RSS、Sitemap、列表页和单页的受控后台监测；实现独立外部访问开关、请求和下载上限、SSRF/DNS/重定向/robots.txt/MIME 防护、内容哈希与条件请求、机构私有候选队列、人工分类和现有研究导入交接；不自动创建事件、共享事实或发布内容。
- 关键文件：`migrations/versions/0011_add_trusted_source_monitoring.py`、`migrations/versions/0012_add_trusted_source_list_path_prefix.py`、`backend/app/source_fetcher.py`、`backend/app/source_monitoring.py`、`backend/app/main.py`、`scripts/run_source_monitor_worker.py`、`frontend/app/monitoring/`、`tests/unit/test_source_fetcher.py`、`tests/integration/test_trusted_source_monitoring.py`、`tests/integration/test_postgres_rls.py`、`.env.example`、`.github/workflows/ci.yml` 和受影响文档。
- 实际命令：`uv run --frozen ruff check backend migrations scripts tests`；`uv run --frozen ruff format --check backend migrations scripts tests`；`uv run --frozen pytest -q`；SQLite 空库 `alembic upgrade head`、`alembic check` 和 `alembic downgrade base`；隔离 PostgreSQL 16 的迁移往返、应用角色 RLS 负向测试；`npm audit --audit-level=high`、`npm run typecheck`、`npm run build`；受限试运行库中的单来源 dry-run、真实免费 HTTP 小批量检查、重复检查、404、人工分类、个人/机构/其他租户 API 和浏览器冒烟验证；`git diff --check`。
- 测试结果：Ruff 和格式检查通过；Pytest 122 项通过、2 项需显式 PostgreSQL 环境的测试按预期跳过，仅有 FastAPI TestClient 上游弃用警告；SQLite 从空库升级至 `0012`、Schema 漂移检查和完整降级通过；此前隔离 PostgreSQL 迁移、应用角色 20 张受保护表的 RLS 负向测试通过；TypeScript、Next.js 生产构建和依赖审计通过，依赖审计 0 个已知漏洞。真实受控验证登记 5 个来源并执行 15 次运行，审计 90 次免费 HTTP 请求、下载 9,556,309 字节，生成 26 条候选，其中 16 条待处理、1 条值得研究、9 条无关；重复检查未生成重复候选，失效来源仅记录失败，个人和其他租户不能读取候选；共享事件数量未变化，业务模型 Token、付费调用、估算费用和自动发布均为 0。浏览器完成来源配置、dry-run、真实检查入队、路径范围修正、候选分类和无权限页面验证，控制台无错误。
- 未解决阻塞：短期真实运行未观察到页面内容变化，变化版本由确定性测试覆盖；实际来源验证覆盖列表页和单页，RSS 与 Sitemap 仅通过本地夹具验证，尚未验证 Sitemap 索引递归；JavaScript 动态页面可能只能取得有限元数据。当前 Mac 网络代理将部分域名解析到保留测试地址时，SSRF 防护会按设计拒绝访问；受控试运行使用一次性公开解析完成验证，没有放宽代码规则或持久化地址。V1 不包含调度器、正式认证、语义相关性判断、自动事件生成或高并发压力验证。

## 2026-07-19｜天眼查授权工商身份 Provider V1

- 日期：2026-07-19
- 任务：按 ADR-0010 接入天眼查授权工商数据的受控后台身份查询，将“政府官方来源”与“授权工商数据”分开记录和展示；实现私有清单、双开关、固定端点、请求上限、缓存与幂等、必要字段最小化、身份冲突人工消歧及独立核验依据字段；不进入同步查询、自动刷新、事件生成或自动发布。
- 关键文件：`backend/app/tianyancha.py`、`scripts/import_tianyancha_identities.py`、`migrations/versions/0013_add_identity_verification_basis.py`、`backend/app/providers.py`、`backend/app/services.py`、`frontend/app/companies/[id]/page.tsx`、`frontend/app/reviews/page.tsx`、`tests/unit/test_tianyancha_identity_provider.py`、`tests/unit/test_tianyancha_identity_cli.py`、`tests/integration/test_official_identity_resolution.py`、`docs/DECISIONS/ADR-0010-licensed-business-identity-verification.md` 和受影响文档。
- 实际命令：Tianyancha Provider、CLI、身份集成和迁移针对性 Pytest；`uv run ruff check backend migrations scripts tests`；`uv run ruff format --check backend migrations scripts tests`；`uv run pytest -q`；SQLite 空库 `alembic upgrade head`、`alembic check` 和 `alembic downgrade base`；隔离 PostgreSQL 16 从 `0012` 升级至 `0013`、Schema 漂移检查、降级与重新升级、空库及真实数据 RLS 负向检查；`npm run typecheck`、`npm run build`、`npm audit --audit-level=high`；10 家真实公司首次、缓存重复和身份歧义补充批次；个人、基金、其他租户和管理员 API 与浏览器验收；私有备份 `pg_restore -l`、Git 忽略和密钥前缀检查；`git diff --check`。
- 测试结果：针对性测试 33 项通过；默认 Pytest 145 项通过、2 项需显式 PostgreSQL 环境的测试按预期跳过，仅有 FastAPI TestClient 上游弃用警告；Ruff、格式、SQLite 迁移往返、隔离 PostgreSQL 迁移与 RLS、TypeScript、Next.js 生产构建通过，依赖审计 0 个已知漏洞。10 家首次批次发起 20 次免费 API 调用，9 家按信用代码与工商全称精确核验，成都普康唯新因同代码名称变化保守记录冲突；补充消歧查询 1 次外部调用并复用 1 次缓存，重复批次外部调用为 0。数据库只保存必要身份字段和响应哈希，完整响应仅存 Git 忽略的 `700/600` 私有缓存；未新增事件或共享事实。个人和机构路径均读取相同公司 ID，个人与其他租户看不到投资或私有底稿，基金叠加、现有共享事件和审核工作台正常。总计 21 次外部调用、0 次付费调用、0 模型 Token、0 估算费用、0 自动发布；浏览器无横向溢出，未替代人工选择身份冲突。
- 未解决阻塞：成都普康唯新的名称变化仍需项目负责人在身份工作台人工确认；V1 仅支持管理员私有清单和人工命令，不含调度、工商变更订阅或生产认证；供应商请求在数据库事务开始前失败时，审计只存在于私有缓存和命令错误；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-19｜全局公司身份索引与已核验别名共享 V1

- 日期：2026-07-19
- 任务：按 ADR-0011 确认公司为跨个人、机构和基金复用的全局主实体；修复显式身份确认发生工商更名时旧法定名称被错误保存为机构私有别名的问题。只将全局目录公司的已核验法定曾用名写入共享身份索引，租户私有公司和其他私有别名继续隔离；不改变事件、证据、原始文档或基金投资作用域。
- 关键文件：`backend/app/services.py`、`tests/integration/test_official_identity_resolution.py`、`docs/DECISIONS/ADR-0011-global-company-identity-index.md`、ADR 索引及受影响的领域、数据库、安全、实施和测试文档。
- 实际命令：定向身份与双通道 Pytest；`uv run --frozen ruff check backend migrations scripts tests`；`uv run --frozen ruff format --check backend migrations scripts tests`；`uv run --frozen pytest -q`；SQLite 空库 `alembic upgrade head`、`alembic check` 和 `alembic downgrade base`；一次性 PostgreSQL 16 空库升级、Schema 漂移、受限应用账户 RLS；`npm audit --audit-level=high`、`npm run typecheck`、`npm run build`；隔离恢复普康唯新身份确认前备份，执行身份选择、三类用户精确搜索、公司详情和浏览器冒烟；`git diff --check`。
- 测试结果：Ruff、格式、SQLite 迁移往返、PostgreSQL Schema/RLS、TypeScript、Next.js 生产构建和依赖审计通过；默认 Pytest 146 项通过、2 项需显式 PostgreSQL 环境的测试按预期跳过，显式 RLS 测试 2 项通过。普康唯新旧工商全称、新工商全称和信用代码对无基金个人、其他机构和影子基金管理员均返回同一 `company_id`；数据库只有一家公司记录。旧法定名称为无 owner 的 `platform_shared` 别名，原候选事件与原文档仍为来源机构私有；个人和其他机构无投资、私有事件或私有线索，影子基金管理员只看到自己的基金叠加与 1 条私有线索。浏览器控制台无错误。本里程碑业务外部调用、付费调用、模型 Token、估算费用和自动发布均为 0。
- 未解决阻塞：无。历史私有别名不批量晋升；品牌名、内部代号、模糊搜索、法定名称历史时间轴和通用身份纠错界面不在本里程碑。FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-19｜可重复真实内容生产 V1

- 日期：2026-07-19
- 任务：把“值得研究”的机构私有候选直接交接到现有研究导入和候选事件流程，保留候选、来源、导入批次、原始文档、事件及证据血缘；增加 dry-run 优先的一次性到期调度命令、失败退避、调度审计和来源许可更新；不自动生成共享事实，不自动发布，不接入通用调度平台。
- 关键文件：`migrations/versions/0014_add_repeatable_content_operations.py`、`backend/app/source_monitoring.py`、`backend/app/services.py`、`backend/app/models.py`、`backend/app/main.py`、`backend/app/schemas.py`、`scripts/queue_due_source_checks.py`、`scripts/run_source_monitor_worker.py`、`frontend/app/monitoring/`、`frontend/lib/api.ts`、`tests/integration/test_trusted_source_monitoring.py`、`tests/integration/test_postgres_rls.py`、`tests/integration/test_manual_research_import.py`、`.env.example`、`README.md` 和受影响文档。
- 实际命令：候选交接、调度、许可和自动发布负向针对性 Pytest；`uv run --frozen ruff check backend migrations scripts tests`；`uv run --frozen ruff format --check backend migrations scripts tests`；`uv run --frozen pytest -q`；SQLite 空库升级至 `0014`、Schema 漂移检查和完整降级；干净 PostgreSQL 16 空库升级、Schema 漂移、`0014 → 0013 → 0014` 往返、虚构数据导入、受限应用角色初始化和 RLS 套件；`npm audit --audit-level=high`、`npm run typecheck`、`npm run build`；隔离恢复的真实试运行副本上执行一次候选交接、重复交接、到期调度 dry-run、三类用户 API 与浏览器验收；`git diff --check`。
- 测试结果：Ruff 和格式检查通过；默认 Pytest 152 项通过、3 项需显式 PostgreSQL 环境的测试按预期跳过，干净 PostgreSQL RLS 3 项通过；SQLite/PostgreSQL 迁移、Schema 漂移及 PostgreSQL `0014` 往返通过；TypeScript、Next.js 生产构建通过，依赖审计 0 个已知漏洞。真实副本将苏州博腾生物制药的一条官网标题候选谨慎导入为 1 个机构私有 `candidate / unconfirmed_lead`，重复提交复用同一导入、原文和事件；个人与其他租户看不到该候选、原文和投资字段，影子基金用户仍能看到自己的投资叠加及私有线索。调度 dry-run 到期来源为 0，未入队、未写用量；本里程碑业务外部调用、付费调用、模型 Token、估算费用和自动发布均为 0。浏览器确认管理端候选交接、许可表单、基金叠加、个人候选队列拒绝和个人共享详情，1280px 无横向溢出且控制台无错误。自查修复了研究导入内部提交后 PostgreSQL 事务级 RLS 上下文丢失，以及 `public_access` 在旧自动发布开关误开时可能被错误路由的问题；复用已被回归测试写入的临时 PostgreSQL 库会使绝对基线计数断言失败，最终按 CI 流程在全新一次性库验证通过。
- 未解决阻塞：真实试运行只验证了 1 条候选交接和 0 条到期来源的调度 dry-run；实际到期入队、无变化、失败退避和重复调度由确定性测试覆盖，尚未在长期运行中观察。正式 Cron、常驻服务、正式认证和个人留存闭环不在本里程碑；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-20｜CloudBase 邀请制身份认证 M3

- 日期：2026-07-20
- 任务：用 CloudBase 邮箱验证码和服务端会话替代对外环境可伪造的 Demo Header；CloudBase 只核验身份，本地 PostgreSQL、tenant、角色、基金授权和 RLS 继续唯一决定业务权限；不开发天眼查即时报告。
- 关键文件：`backend/app/auth.py`、`backend/app/main.py`、`backend/app/models.py`、`migrations/versions/0015_add_cloudbase_authentication.py`、`frontend/app/login/`、`frontend/app/auth/refresh/route.ts`、`frontend/lib/auth-session.ts`、`tests/unit/test_cloudbase_auth_provider.py`、`tests/integration/test_cloudbase_authentication.py`、`tests/integration/test_postgres_rls.py`、`docs/DECISIONS/ADR-0012-cloudbase-identity-local-authorization.md` 和受影响文档。
- 实际命令：`uv run --frozen ruff check backend migrations scripts tests`；`uv run --frozen ruff format --check backend migrations scripts tests`；隔离 SQLite 空库 `alembic upgrade head`、`alembic check` 和 `alembic downgrade base`；隔离 PostgreSQL 16 空库升级、`0015 → 0014 → 0015` 往返、Schema 漂移、受限应用账户和 RLS 套件；定向及完整 `uv run --frozen pytest -q`；`npm run typecheck`、`npm run build`、`npm audit --audit-level=high`；Mock 四角色和真实 CloudBase 无基金个人的登录、刷新、查询、权限拒绝及退出浏览器冒烟；迁移前后 `pg_dump -Fc`、`pg_restore -l` 和 SHA-256 校验；`git diff --check`。
- 测试结果：默认 Pytest 172 项通过、5 项需显式 PostgreSQL 环境的测试按预期跳过，隔离 PostgreSQL RLS 5 项通过，仅有 FastAPI TestClient 上游弃用警告；Ruff、格式、SQLite/PostgreSQL 迁移、Schema 漂移、TypeScript、Next.js 生产构建和依赖审计通过，0 个已知高风险漏洞。真实 CloudBase 原生邮箱账户成功完成收码、首次 subject 绑定、登录、共享公司精确搜索、共享详情、无基金和后台权限隔离、refresh token 轮换及退出；数据库追加 `identity_linked`、`session_started`、`session_refreshed`、`session_ended` 各 1 条。实测发现原生邮箱账户的 `/user/me.providers` 不是文档示例中的列表，旧校验因此把成功登录误报为验证码失效；修复后首次邮箱绑定只允许验证码登录路径，普通 Bearer 和刷新请求不能首次绑定，Malformed Provider 成功响应改为 `503 authentication_unavailable`。本地库保持 `0015`、12 家公司、3 个基金、13 条投资和 12 条事件；业务外部查询、天眼查、付费 API、模型 Token 和自动发布均为 0，四个业务安全开关保持关闭。
- 未解决阻塞：真实 Provider 目前只验收了无基金个人账户；基金用户、其他 tenant 用户和平台管理员的真实 CloudBase 账户应在 M6 外部邀请测试前补测，但本地授权、Mock Provider 和 PostgreSQL RLS 已覆盖其权限差异，不阻塞 M3 代码 PR。CloudBase 服务端故障和图片验证码只由确定性失败测试覆盖，尚未在真实故障中触发；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-21｜M4 个人关注、收录申请与测试权益基线

- 日期：2026-07-21
- 任务：实现单一个人关注清单、人工收录/更新申请和邀请测试期服务端额度；搜索无结果不自动创建公司，关注不授予基金权限，申请不触发天眼查、模型、刷新任务或自动发布；本交付不包含变化水位和固定报告。
- 关键文件：`migrations/versions/0016_add_personal_retention_foundation.py`、`backend/app/personal_features.py`、`backend/app/models.py`、`backend/app/main.py`、`backend/app/config.py`、`frontend/app/watchlist/`、`frontend/app/personal-actions.ts`、`frontend/app/page.tsx`、`frontend/app/companies/[id]/page.tsx`、`tests/integration/test_personal_retention.py`、`tests/integration/test_postgres_rls.py`、`.env.example`、`README.md` 和受影响文档。
- 实际命令：`uv run --frozen ruff check backend migrations scripts tests`；`uv run --frozen ruff format --check backend migrations scripts tests`；`uv run --frozen pytest -q`；隔离 PostgreSQL 16 空库升级至 `0016`、Schema 漂移检查、`0016 → 0015 → 0016` 往返、虚构数据导入、受限应用账户初始化和 RLS 套件；`npm audit --audit-level=high`、`npm run typecheck`、`npm run build`；临时 SQLite 虚构数据下的无基金个人浏览器查询、关注、重复更新申请、无结果收录、取消关注和额度展示；`git diff --check`。
- 测试结果：Ruff 和格式通过；默认 Pytest 175 项通过、7 项需显式 PostgreSQL 环境的测试按预期跳过，仅有 FastAPI TestClient 上游弃用警告；全新 PostgreSQL 16 迁移、Schema 漂移、往返及 24 张受保护表的 RLS 测试 7 项通过；TypeScript、Next.js 生产构建和依赖审计通过，0 个已知漏洞。浏览器确认查询只返回共享公司、关注/取消关注可逆、相同更新申请复用原记录、收录申请不创建公司，并修复成功提交后回到搜索页造成查询额度二次计数的问题。严格自查另修复了 PostgreSQL 事务级 RLS 上下文在提交后响应重读时丢失，以及只追加用量记录被二次更新而遭 RLS 拒绝的问题；未放宽 RLS。业务外部查询、付费调用、模型 Token、估算费用和自动发布均为 0。
- 未解决阻塞：M4 的“自上次查看后的变化”和确定性 HTML/Markdown 固定报告将在下一独立 PR 实现；商业订阅、支付、多观察清单、个人备注、PDF、通知和 LLM 报告不在本阶段。机构基金叠加由自动回归覆盖，本轮未新增第二套机构浏览器数据验收；FastAPI TestClient 上游弃用警告仍存在，不影响结果。

## 2026-07-21｜M4 个人变化回访与固定报告

- 日期：2026-07-21
- 任务：为每个用户独立建立公司查看基线和逐事件回执，回访时只展示新通过审核的平台共享事实；生成个人私有、只追加的确定性 Markdown 时点报告，执行每月 10 次测试上限；不调用 LLM、外部 Provider 或自动发布。
- 关键文件：`migrations/versions/0017_add_personal_changes_and_reports.py`、`backend/app/personal_features.py`、`backend/app/models.py`、`backend/app/main.py`、`backend/app/services.py`、`frontend/app/companies/[id]/personal-change-panel.tsx`、`frontend/app/reports/`、`tests/integration/test_personal_changes_reports.py`、`tests/integration/test_postgres_rls.py` 和受影响文档。
- 实际命令：`uv run --frozen ruff check backend migrations scripts tests`；`uv run --frozen ruff format --check backend migrations scripts tests`；`uv run --frozen pytest -q`；隔离 SQLite 空库升级至 `0017`、`alembic check`、`0017 → 0016 → 0017`；一次性 PostgreSQL 16 空库执行同样迁移往返、虚构数据导入、`NOBYPASSRLS` 应用角色和 RLS 套件；`npm audit --audit-level=high`、`npm run typecheck`、`npm run build`；临时 SQLite 虚构数据下的个人首次查看、共享事实晋升、二次查看、报告生成、其他用户拒绝和基金叠加浏览器验收；`git diff --check`。
- 测试结果：Ruff 与格式检查通过；默认 Pytest 177 项通过、9 项显式 PostgreSQL 测试按预期跳过；隔离 PostgreSQL 的 27 张受保护表与 9 项 RLS/API 测试通过；SQLite/PostgreSQL 迁移、Schema 漂移和 `0017` 往返通过；TypeScript、Next.js 生产构建通过，依赖审计 0 个已知高风险漏洞。浏览器确认链接预取不会标记已读、新共享事实只提示一次、报告不含投资和私有候选、其他用户读取返回 404、机构用户仍能看到授权基金叠加，页面无横向溢出且控制台无警告或错误。严格自查修复了 Next.js 预取提前写入已读、React 开发模式重复 effect 卡在加载、公司切换后不重新记录、报告公司名未冻结以及新鲜度未按生成时点重算的问题。业务外部调用、付费调用、模型 Token、估算费用和自动发布均为 0。
- 未解决阻塞：无。报告按产品决策保留生成时点快照，后续纠正或撤回不回写旧报告，界面已明示需以最新公司详情为准。PDF、多观察清单、个人备注、通知、商业订阅/支付和 LLM 报告不在 M4；FastAPI TestClient 上游弃用警告仍存在，不影响当前结果。

## 2026-07-21｜M4 本地落地验收

- 日期：2026-07-21
- 任务：在不开发新功能的前提下，将持久化本地 PostgreSQL 从 `0015` 升级到 `0017`，验证个人关注、更新申请、查看回执、固定报告、机构基金叠加和跨用户/跨租户隔离，并保留可恢复的迁移前后备份。
- 关键文件：`docs/10-implementation-plan.md`、`docs/12-operations-runbook.md`、`docs/IMPLEMENTATION_LOG.md`；数据库备份位于 Git 忽略的 `backups/m4-local-acceptance/`，不进入仓库。
- 实际命令：迁移前后 `pg_dump -Fc`、`pg_restore -l`、SHA-256 与隔离库实际恢复；`alembic upgrade 0017`、`alembic current`、`alembic check`；非表所有者 `equity_app` 的新表授权与 RLS 负向查询；本机 FastAPI/Next.js 的无基金个人和机构 API、页面及控制台冒烟；迁移前后关键数据、用量和服务状态核对。
- 测试结果：迁移前备份恢复为 `0015`，迁移后备份恢复为 `0017`；原有 12 家公司、3 个基金、13 条投资、12 条事件/原文/证据均未减少，两家真实公司与本地影子验证基金的两条关联保持不变。无基金个人完成精确查询、关注、重复更新申请复用、查看水位和幂等固定报告；个人详情及报告不含投资或私有线索，其他用户读取报告返回 404；同租户其他用户和其他租户通过 RLS 均看不到该个人记录；机构页面只叠加本基金投资。页面无横向溢出，控制台无警告或错误。本轮业务外部调用增量、付费调用、模型 Token、估算费用、刷新任务和自动发布均为 0，四个业务安全开关保持关闭。
- 未解决阻塞：没有 M4 阻塞。持久化库保留 1 条 Demo 关注、1 条待处理 Demo 更新申请、2 个用户的查看水位/回执和 1 份个人 Demo 报告作为可重复查看的本地验收数据；真实新增共享事实后的“再次回访提示”未为本轮伪造数据，继续由已通过的确定性测试和既有浏览器验收覆盖。M5 启动前仍需项目负责人决定部署环境和备份策略。

## 2026-07-21｜M5A 零云费用基础设施基线

- 日期：2026-07-21
- 任务：在不购买香港服务器或创建任何云资源的前提下，建立可迁移的生产后端/前端镜像、Caddy 单一入口、正式模式配置预检、数据库 readiness、受限数据库账户、备份与隔离恢复工具，并用一次性本机 PostgreSQL 和虚构数据完成生产式验收；M5B 上海真实部署仍为独立闸门。
- 关键文件：`backend/Dockerfile`、`frontend/Dockerfile`、`compose.production.yml`、`deploy/`、`scripts/check_production_config.py`、`backend/app/config.py`、`backend/app/main.py`、`.github/workflows/ci.yml`、`tests/unit/test_production_preflight.py`、`tests/integration/test_demo_vertical_slice.py`、`README.md`、`docs/10-implementation-plan.md`、`docs/11-test-strategy.md` 和 `docs/12-operations-runbook.md`。
- 实际命令：Ruff 与格式检查；完整 Pytest；隔离 PostgreSQL 受限账户 RLS 套件；SQLite `base → 0017 → base`；PostgreSQL `alembic current/check`；前端依赖审计、TypeScript 和生产构建；生产 Compose 解析、后端/前端镜像构建及预检；虚构数据迁移、应用角色初始化、`pg_dump -Fc`、SHA-256、`pg_restore -l` 和隔离恢复；数据库停止/恢复 readiness；正式模式伪造 Demo Header；Caddy 响应头、容器用户/能力、镜像层密钥扫描和本机浏览器冒烟。
- 测试结果：Ruff 与格式通过；默认 Pytest 187 项通过、9 项显式 PostgreSQL 测试按预期跳过，隔离 PostgreSQL RLS 9 项通过，仅有 FastAPI TestClient 上游弃用警告；SQLite 迁移往返、PostgreSQL `0017` 和 Schema 漂移检查通过；前端依赖审计 0 个已知漏洞，TypeScript、Next.js 构建和两类生产镜像构建通过。数据库停止时 `/health=200`、`/ready=503`，恢复后 `/ready=200`；伪造 Demo Header 返回 401。备份 `dealflow-radar-20260721T063315Z.dump` 为 308974 字节，SHA-256 为 `bdde2e2f7050629e78d4f51bee626171ca4d7a2db75105a7dec578b65df0f662`，恢复库与源库均为 `0017`，公司/事件/原文/用户/关注/报告数量一致。浏览器确认生产镜像登录页可读，未登录首页重定向到登录页；八个初始安全开关关闭，临时库用量台账的外部调用、模型 Token 和费用均为 0，未自动发布。首次 CI 已完成全部常规测试和镜像构建，但 Job 的 Demo 环境覆盖了验收 env 文件，生产预检按设计失败；修复仅把两个生产构件步骤隔离到虚构 production 环境，不改变常规测试或业务运行配置。
- 未解决阻塞：M5A 不证明真实域名证书、上海主机防火墙、异机加密备份、备份保留、告警送达、主机重启恢复或四类真实 CloudBase 账户权限；这些必须在 M5B 单独验收，M5B 前不得邀请外部用户。M5A 本机备份仅含虚构数据且未加密，验收后删除；正式环境不得直接数据库 downgrade。Caddy 官方镜像仍以 root 运行，但根文件系统只读、禁止提权、移除全部能力后仅加回绑定 80/443 所需能力；是否改为定制非 root 边缘镜像可在 M5B 根据实际主机方案评估，不阻塞本基线。

## 2026-07-21｜M5B 上海单机部署零费用前置

- 日期：2026-07-21
- 任务：依据已确认的低成本邀请测试架构，增加不发布数据库端口的单机 PostgreSQL Compose 覆盖、强制 `age` 客户端加密的备份/隔离恢复工具和只接受加密文件的 COSCLI 安全交接；记录个人备案只用于非经营性验证，候选域名和真实云资源仍等待项目负责人购买。
- 关键文件：`deploy/compose.single-host.yml`、`deploy/single-host.env.example`、`deploy/backup-tools.Dockerfile`、`deploy/backup.sh`、`deploy/restore-test.sh`、`deploy/upload-backup-cos.sh`、`scripts/check_production_config.py`、`tests/unit/test_deployment_backup_scripts.py`、`tests/unit/test_production_preflight.py`、`.github/workflows/ci.yml`、`docs/DECISIONS/ADR-0013-single-host-invitation-deployment.md` 和受影响文档。
- 实际命令：Ruff 与格式检查；完整 Pytest；SQLite `base → 0017 → base` 与 Schema 漂移；前端依赖审计、TypeScript 和生产构建；生产及单机 Compose 解析和结构断言；备份工具镜像构建；两次一次性 PostgreSQL 16 的 `0017` 迁移、虚构数据导入、真实 `age` 加密备份、独立恢复库恢复、版本/公司数量和数据库端口核对；`git diff --check`。
- 测试结果：完整 Pytest 200 项通过、9 项显式 PostgreSQL 测试按预期跳过，仅有 FastAPI TestClient 上游弃用警告；SQLite 迁移往返与漂移检查通过；前端依赖审计 0 个已知高风险漏洞，TypeScript 和 Next.js 构建通过。加固后的临时 PostgreSQL 源库和恢复库均为 `0017`、均含 10 家虚构公司；最终目录只含 `.dump.age` 及两份校验元数据，没有明文 `.dump`，数据库没有主机端口。自查修复了把“要求异机备份”误写成“异机备份已完成”的状态语义，并增加加密失败清理、符号链接拒绝、COSCLI 配置权限、只读工具容器和移除能力。临时容器、卷、备份和私钥全部删除；现有本地数据库未修改，业务外部调用、付费调用、模型 Token、自动刷新和自动发布均为 0。
- 未解决阻塞：尚未购买或创建域名、上海服务器、COS 桶和凭据，未执行真实 HTTPS、ICP备案、COS 上传/下载、生命周期、异机恢复、告警、主机重启或四类真实 CloudBase 账户验收，因此 M5B 仍未完成且不得进入 M6。候选域名 `dealflowradar.cn` 和 `dealflowradar.com` 只经 WHOIS/RDAP 初查，购买时必须再次确认。GitHub CI 结果以本 PR 的远端检查记录为准，不以本地结果代替。

## 2026-08-11｜M5B 香港邀请测试部署决策

- 日期：2026-08-11
- 任务：新增 ADR-0014，将 M5B 短期路线从上海个人备案调整为腾讯云中国香港邀请测试；保留单机 Compose、PostgreSQL 内网隔离和客户端加密异机备份，明确未来成立公司后再评估迁入大陆并办理企业 ICP 备案。本任务只更新架构和实施计划，不购买或创建云资源。
- 关键文件：`docs/DECISIONS/ADR-0014-hong-kong-invitation-deployment.md`、`docs/10-implementation-plan.md`、`docs/12-operations-runbook.md`、`docs/02-system-architecture.md`、`docs/07-security-compliance.md`、`README.md`、`AGENTS.md` 和部署示例注释。
- 实际命令：Markdown 链接与地域旧文案检查、生产 Compose 配置解析、相关文档差异检查和 `git diff --check`。
- 测试结果：当前单机生产 Compose 与全部 profile 解析通过，地域旧文案仅保留在历史 ADR/实施记录或明确的取代说明中，`git diff --check` 通过；未修改业务代码、数据库、迁移或本地数据，未创建云资源、未产生外部业务调用或费用。GitHub CI 以本 PR 的远端检查为准。
- 未解决阻塞：香港服务器套餐、带宽、期限、价格、COS 地域和实际购买仍需项目负责人确认；真实 HTTPS、端口收口、异机恢复、告警、重启、大陆网络质量和四类 CloudBase 账户仍未验收，M5B 尚未完成。

## 2026-08-11｜前端安全依赖修复

- 日期：2026-08-11
- 任务：修复 GitHub CI 新披露的 Next.js 及传递依赖高风险漏洞；将 Next.js 从 `16.2.10` 精确升级至 `16.3.0`，移除会把 PostCSS 固定在受影响版本的旧覆盖，不改业务代码或其他直接依赖。
- 关键文件：`frontend/package.json`、`frontend/package-lock.json`、`frontend/next-env.d.ts`、`docs/IMPLEMENTATION_LOG.md`。
- 实际命令：`npm install`；`npm ls next react react-dom nanoid postcss sharp --all`；`npm audit --audit-level=high`；Node.js 24 与 Node.js 20 下的 TypeScript 和 Next.js 生产构建；`git diff --check`。本机 Docker Desktop 未运行，因此容器镜像构建交由 GitHub CI 验证。
- 测试结果：Next.js 为 `16.3.0`、PostCSS 为 `8.5.23`、Nano ID 为 `3.3.18`、Sharp 为 `0.35.3`；依赖审计为 0 个已知漏洞，TypeScript 和生产构建通过；未调用业务外部 Provider、付费 API 或模型，未修改数据库。
- 未解决阻塞：本地未执行 Docker 镜像构建；GitHub CI 结果以本 PR 的远端检查记录为准。

## 2026-08-12｜M5B 香港邀请测试最终验收收口

- 日期：2026-08-12
- 任务：在已完成香港部署、四角色回归、加密 COS 恢复和主机重启验收的基础上，补齐免费资源告警、大陆三运营商持续拨测、未登录可读的邀请测试说明，以及健康检查/备份/COS 上传失败的飞书通知接线；不修改业务模型、数据库或迁移，不开启付费 Provider、自动刷新或自动发布。
- 关键文件：`deploy/notify-feishu.sh`、`deploy/ops-alert.env.example`、`deploy/systemd/`、`frontend/app/trial-notice/page.tsx`、`frontend/app/login/page.tsx`、`frontend/app/layout.tsx`、`frontend/app/globals.css`、`frontend/next.config.ts`、`tests/unit/test_ops_alert_script.py`、`README.md`、`docs/07-security-compliance.md`、`docs/10-implementation-plan.md` 和 `docs/12-operations-runbook.md`。
- 实际命令：腾讯云控制台创建轻量应用服务器系统盘告警和 15 天免费 CAT 页面性能任务；CAT 从上海电信、广州移动和北京联通三个 LastMile 节点运行；`uv run ruff check backend migrations scripts tests`、`uv run ruff format --check backend migrations scripts tests`、`uv run pytest -q`、`npm run typecheck`、`npm run build`、`npm audit --audit-level=high`、本地浏览器检查 `/trial-notice` 与登录页入口、远端 Ubuntu `systemd-analyze verify` 和 `git diff --check`。
- 测试结果：系统盘利用率超过 75% 的策略 `policy-4dn96jd7` 已启用，系统预设接收人配置了邮件和短信渠道；本轮没有人为填满磁盘测试实际送达。CAT 任务 `task-2binh3i4` 显示 15 天免费试用且未升级付费版，每 5 分钟执行。首批 4 次大陆观测全部为正常，覆盖三家运营商，整体性能 717—36,983 ms，北京联通存在一次明显慢样本，继续留给 M6 观察。飞书脚本 dry-run、非法 URL 拒绝和 Webhook 不进入 curl 参数测试通过；完整 Pytest 204 项通过、9 项 PostgreSQL 显式测试按预期跳过，仅有既有 FastAPI TestClient 上游弃用警告；TypeScript、Next.js 生产构建和依赖审计通过，0 个已知漏洞。浏览器确认说明页和登录前链接可读；构建包含 `/trial-notice`。远端 `systemd-analyze verify` 能解析通知单元，只报告脚本尚未安装到目标路径的预期警告。Next.js 开发服务器曾自动生成嵌套代理说明文件，已删除并通过 `agentRules: false` 防止再次污染工作区，保持根 `AGENTS.md` 为唯一规则源。
- 未解决阻塞：飞书自定义机器人 Webhook 尚未由项目负责人安全提供，因此通知接线只完成代码、测试和安装手册，尚未在香港服务器实际送达；本 PR 合并、部署并验证飞书测试消息前，M5B 仍保持“进行中”。CAT 免费试用剩余 15 天，到期会停止；不得未经确认升级专家版。系统盘/流量包告警与 CAT 免费拨测不产生业务 Provider、模型 Token或自动发布，当前业务安全开关继续保持关闭。

## 2026-08-14｜M6 首位个人账号界面与报告可读性修复

- 日期：2026-08-14
- 任务：根据首位无基金个人测试账号的实际浏览反馈，隐藏无权限的审核与来源监测入口，移除会误导用户复制虚构公司的搜索示例，将个人报告从原始 Markdown 改为安全结构化排版，并将分类、方向、风险、可信度、数据状态和链接状态转换为中文；常见专业缩写首次出现时补充中文说明。历史报告保持不可变，只在展示层兼容转换；新报告使用中文 V2 模板。
- 关键文件：`backend/app/main.py`、`backend/app/services.py`、`backend/app/personal_features.py`、`backend/app/schemas.py`、`frontend/components/report-content.tsx`、`frontend/app/layout.tsx`、`frontend/app/page.tsx`、`frontend/app/reports/`、`frontend/app/globals.css`、`frontend/lib/api.ts`、相关测试和接口/实施文档。
- 实际命令：针对性认证与个人报告 Pytest；完整 Ruff 与格式检查；完整 Pytest；前端依赖审计、TypeScript 和生产构建；使用一次性 SQLite `base → 0017`、虚构数据和无基金个人/管理员 Demo 身份完成浏览器结构与视觉验收；`git diff --check`。
- 测试结果：完整 Pytest 204 项通过、9 项显式 PostgreSQL 测试按预期跳过，仅有既有 FastAPI TestClient 上游弃用警告；前端依赖审计为 0 个已知漏洞，类型检查和生产构建通过。浏览器确认普通个人不再看到管理入口，审核员入口仍保留；搜索框不再暗示虚构公司可查询；旧报告代码值、时间和链接状态正确转为中文，Markdown 符号不再显示，来源链接可点击，CGT 等常见缩写首次出现时附中文解释。临时数据库和服务在验收后删除或停止，未修改现有本地/香港数据；外部业务调用、付费调用、模型 Token 和自动发布均为 0。
- 未解决阻塞：本轮没有发现权限或数据阻塞；最终 PR 合并并部署后仍需用真实 CloudBase 个人账号复核生产页面。缩写说明只覆盖当前内容中常见且语义确定的术语，产品/项目英文专名仍按来源保留；按项目负责人要求本轮不调整字体。M6 尚未获得独立外部用户反馈，不能据此进入 M7。

## 2026-08-15｜M6 公司搜索建议 V1

- 日期：2026-08-15
- 任务：根据首位个人测试账号使用“博腾生物”无法命中完整工商名称的反馈，新增登录后共享目录搜索建议；输入至少两个字符后按信用代码精确、工商名称完全/开头/包含、已核实共享别名排序返回最多 8 个候选。候选选择仍进入既有精确查询，不自动创建、绑定或合并公司；联想输入不重复扣查询次数，但服务端仍检查剩余额度。
- 关键文件：`backend/app/main.py`、`backend/app/services.py`、`backend/app/personal_features.py`、`backend/app/schemas.py`、`frontend/components/company-search-form.tsx`、`frontend/app/api/company-suggestions/route.ts`、`frontend/app/page.tsx`、`frontend/app/globals.css`、`frontend/lib/api.ts`、`tests/integration/test_personal_company_query.py`、`tests/integration/test_postgres_rls.py`、`README.md`、`docs/08-api-design.md` 和 `docs/10-implementation-plan.md`。
- 实际命令：相关个人查询、认证与留存 Pytest；完整 Ruff 与格式检查；完整 Pytest；一次性 SQLite `base → 0017 → base` 与 Schema 漂移检查；一次性 PostgreSQL 16 迁移、受限应用账户初始化和完整 RLS 套件；前端依赖审计、TypeScript 和生产构建；一次性 SQLite 虚构“博腾生物”双候选数据下完成桌面、390px 窄屏、鼠标、键盘、无结果和控制台浏览器验收；`git diff --check`。
- 测试结果：完整 Pytest 205 项通过、10 项显式 PostgreSQL 测试按预期跳过，仅有既有 FastAPI TestClient 上游弃用警告；独立 PostgreSQL 16 的 10 项 RLS/API 测试全部通过，确认同租户机构私有别名也不会进入个人建议；SQLite 迁移往返与漂移检查通过；TypeScript、Next.js 生产构建和依赖审计通过，0 个已知漏洞。浏览器确认“博腾生物”显示两个带工商全称、注册地区和信用代码的候选，鼠标及方向键均可选择，提交简称后保留候选列表，无结果不自动创建公司；390px 窄屏无横向溢出，控制台无警告或错误。自查修复了结果页初次加载后自动再次请求并弹出候选的问题；联想请求不增加正式查询用量，额度耗尽后服务端返回 429。临时数据和服务均未进入现有数据库，业务外部调用、付费调用、模型 Token、估算费用和自动发布均为 0。
- 未解决阻塞：V1 只做已核验名称和共享别名的包含/前缀提示，不做错别字、拼音或复杂相似度推断，避免身份误匹配。代码合并部署后仍需用真实 CloudBase 个人账号复核“博腾生物”生产数据；共享目录达到明显更大规模前不提前引入 PostgreSQL 三元组索引或独立搜索服务。

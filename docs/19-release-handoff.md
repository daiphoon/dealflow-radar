# 19 全链路改进正式交付与部署准备

本轮仅审查、测试、提交、推送和创建 PR，不合并、不部署、不执行生产迁移或创建生产账号。真实研究、模型调用、自动巡检、MCP 和隧道保持未启用。

## 基线与依赖拆分

- 起点：本地与 GitHub main 为 `a42d13cc98d2e144b737709c266fe2066f6455f5`。原工作区 49 项未提交变更，均已私有封存并记录哈希。未修改任何 0035 及以前迁移。
- PR-A：W01—W05 事项与研究可靠性，基于 main，迁移仍为 0035。
- PR-B：W06—W08，基于 PR-A 的有效证据语义计算回访/报告版本；增加 0036，不重写旧迁移。
- PR-C：W09—W11，基于 A 的候选处置/血缘元数据及 B 的最终模型和迁移状态验证诊断；只添加可选部署 profile，默认关闭。
- 三个 PR 串联，审查各自增量；完整测试针对每层累积树执行。必须 A→B→C，不能跳过前置 PR 合并。合并 A 后将 B 改以 main 为目标，重新核验 diff/CI，再对 C 同样处理；本轮不执行合并。
- 混合单元测试按研究/语义拆为两个文件，测试断言保持。正式回放和临时数据库验证脚本是维护工具；临时发布编排、日志、数据库和扫描回执均只留私有目录，不进入 Git。

## PR-A 文件清单

- `backend/app/matter_contract.py`
- `backend/app/matter_retention.py`
- `backend/app/research_extraction.py`
- `backend/app/research_matter_storage.py`
- `backend/app/research_matters.py`
- `backend/app/research_plan.py`
- `backend/app/research_subject.py`
- `backend/app/web_research_service.py`
- `backend/app/matter_fragments.py`
- `backend/app/research_evaluation.py`
- `scripts/research_benchmark.py`
- `scripts/research_replay.py`
- `scripts/verify_integrated_local.py`
- `data/sample/integrated_replay.json`
- `tests/integration/test_integrated_evidence.py`
- `tests/unit/test_integrated_contract.py`
- `tests/unit/test_integrated_research.py`

另更新本交付记录和 `docs/IMPLEMENTATION_LOG.md`。E4.10 日期规则、证据血缘/字段校验、既有显示与 Node 24 配置保留；全量回归继续包含其原测试。

## PR-B 文件清单

基于 [PR-A #100](https://github.com/daiphoon/dealflow-radar/pull/100)；前置提交 `6e43871`。

- `backend/app/main.py`
- `backend/app/models.py`
- `backend/app/personal_features.py`
- `backend/app/schemas.py`
- `backend/app/watchlist_monitoring.py`
- `backend/app/research_cost_preview.py`
- `backend/app/semantic_content.py`
- `frontend/app/companies/[id]/page.tsx`
- `frontend/app/companies/[id]/personal-change-panel.tsx`
- `frontend/app/personal-actions.ts`
- `frontend/components/record-browser.tsx`
- `frontend/lib/api.ts`
- `frontend/tests/current-company-results.test.cjs`
- `frontend/tests/record-browser.test.cjs`
- `migrations/versions/0036_visible_semantic_versions.py`
- `tests/integration/test_integrated_returns.py`
- `tests/integration/test_curated_import.py`
- `tests/integration/test_migrations.py`
- `tests/integration/test_personal_changes_reports.py`
- `tests/integration/test_web_budget_delivery.py`
- `tests/unit/test_integrated_semantics.py`

另更新本记录和实施记录。

## PR-C 文件清单

基于 [PR-B #101](https://github.com/daiphoon/dealflow-radar/pull/101)（`a0c65b2`）；它基于 PR-A #100（`6e43871`）。只增加诊断基础设施，不启用生产服务。

- `backend/app/diagnostic_mcp.py`
- `backend/app/diagnostics.py`
- `deploy/compose.diagnostic.yml`
- `docs/10-implementation-plan.md`
- `docs/18-integrated-delivery.md`
- `docs/DECISIONS/ADR-0027-integrated-diagnostics-and-semantic-delivery.md`
- `docs/IMPLEMENTATION_LOG.md`
- `pyproject.toml`
- `scripts/bootstrap_diagnostics.py`
- `scripts/diagnostic_export.py`
- `tests/integration/test_diagnostic_service.py`
- `uv.lock`
- `.github/workflows/ci.yml`

另更新本正式交付记录。三个 PR 合计 52 个唯一文件；A 19 个、B 23 个、C 14 个（共享交付记录在各批递进更新，不把重复计为唯一文件）。

## GitHub 门禁

2026-09-24，仓库为私有，当前账号有 admin 权限；main protected=false。rulesets 和 branch protection GET 均返回 403，明确要求升级套餐或改公开。按负责人边界不购买、不改可见性、不绕过；当前采用 PR、`Verify` 全部成功和人工确认的门禁。GitHub 尚不能强制阻止直接写 main，须明确保留该限制。

## 验证

PR-A 定向测试 17 通过；完整正式后端 1111 通过、19 条件跳过（595.32 秒），前端 28 通过，类型/构建通过。迁移、Ruff/格式、生产 Compose、API/前端/备份镜像构建、npm audit、Secret/私有路径扫描和 diff 检查全部通过。19 项为相应数据库条件跳过，PostgreSQL 权限参数实际执行。远端 CI 以本 PR 页面当前 Verify 为准，不用本地结果替代。每批分别执行 Ruff、格式、SQLite 往返和 PostgreSQL 迁移/漂移、定向与完整 pytest、前端测试/类型/构建、生产 Compose 配置、Docker 生产镜像构建、Gitleaks 和私有路径检查。日志不提交。

PR-B 定向 14 通过；完整后端 1119 通过、19 条件跳过（607.77 秒），前端 30 通过，typecheck/build 通过；Ruff/格式、SQLite 往返、PostgreSQL 迁移/漂移、生产 Compose、API/前端/备份 Docker 构建、npm audit、Secret/私有路径扫描和 diff 检查通过。迁移只新增 0036，远端 CI 以当前 Verify 为准。

PR-C 定向 6 通过、6 SQLite 条件跳过；真实 PostgreSQL 权限、SDK HTTP 和模拟转发已执行。完整后端 1125 通过、25 条件跳过（608.39 秒）；其中 PostgreSQL 诊断参数实际执行。前端 30 通过及类型/构建通过，诊断 Compose 与新增 CI 隔离断言通过；Gitleaks 对最终 337 个发布文件扫描零发现，私有路径扫描零发现。

## 受控部署清单（待另行批准）

1. 三个 PR 按依赖审查并合并，实际 Verify 通过后冻结最终提交与镜像摘要；镜像按目标服务器架构构建，不直接部署 Mac 验证镜像。保留当前线上镜像和配置；现场只读核验线上基线，不把历史部署记录当最新状态。
2. 另批批准远程操作，经既有 Tailscale 运维。确认研究/模型/巡检/自动发布开关关闭、无活动任务、预算为零，保持诊断 profile 关闭。
3. 迁移前加密备份并取得双哈希回执，完成隔离恢复。记录旧回执 first_seen_at、报告历史、证据/观测与关键表摘要。
4. 在维护窗口停用会写入回执/报告的应用入口；通过 owner 运行在线 `alembic upgrade head` 到 0036，再按既有 bootstrap 流程确认应用角色新表最小权限、RLS 和 `alembic check`。不得通过离线 SQL 跳过已有回执语义基线。
5. 启动固定版本应用，检查 health/ready/login、匿名及伪造身份拒绝、公司页/回访/报告幂等和配额、来源撤权及旧数据不变；前后数据库连接与资源占用对照。验收后补加密备份和回执。
6. 不运行 diagnostic bootstrap、不创建生产 MCP 账号、不启用 profile/隧道/真实 Provider 或巡检。MCP 启用需要独立的数据、权限、网络和故障隔离验收。

回滚：先停止新应用的写入及全部新 Worker，切回已记录的旧镜像和配置；保留 0036、回执、报告请求映射和全部新观测，不执行有历史数据的 downgrade。应用回退在隔离恢复库验证兼容后执行。若需整库恢复，必须另行批准并核算备份后新增数据，不能以回滚名义丢失历史。诊断未启用时无需变动现有网站/Tailscale；将来启用失败只停 diagnostic 容器并撤销专用凭据。

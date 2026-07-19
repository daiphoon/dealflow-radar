# 12 运维手册（设计）

当前已有可本地运行的 Demo；本手册定义当前能力和后续部署的最小操作边界。

## 1. 部署前检查

1. 使用云服务器或合作者服务器；不使用家庭 Ubuntu 服务器，不依赖 Tailscale。
2. 从 `.env.example` 创建部署 Secret，确认外部调用、付费调用、自动刷新和审核工作台等安全开关仍为关闭，禁止提交 `.env`；自动发布策略虽默认启用，但 URL 未检查时会降级为未确认线索。
3. 检查镜像版本、PostgreSQL 持久卷、TLS、允许来源、备份目标和磁盘余量。
4. 先执行迁移备份与 `alembic upgrade` 演练，再部署 API/Worker/Cron。
5. 用两个租户/基金的虚构数据运行权限冒烟测试。
6. 外部 Provider 逐个完成许可、价格、预算和 `dry-run` 审批后才启用。

Docker Compose 文件只依赖标准容器、环境变量和卷，可迁移到不同主机；反向代理与证书由部署环境提供，不绑定云厂商 SDK。

## 2. 日常健康指标

监控 API 错误/延迟、数据库连接和存储余量、任务队列年龄、租约过期、Provider 失败率、无变化任务占比、缓存命中率、LLM Schema 成功率、身份例外量、未确认高风险线索量、预算消耗和备份结果。同步查询外部调用数应恒为 0。

## 3. 安全的更新操作

更新前先运行 `dry-run`，核对公司、原因、Provider、预计搜索/Token/商业数据调用和费用。只在变更单明确授权的范围内开启 `EXTERNAL_CALLS_ENABLED`；付费能力还需 `PAID_API_CALLS_ENABLED` 与正预算。任务完成后立即核对 `usage_ledger` 和有效产出，不长期保留调试日志正文。

### Mock Worker V1（仅 Demo）

先确认数据库中已有 `mock_refresh` 任务，再按虚构租户运行一次：

```bash
export WORKER_TENANT_ID="$(uv run python -c 'from backend.app.demo import ALPHA_TENANT_ID; print(ALPHA_TENANT_ID)')"
APP_MODE=demo uv run python -m scripts.run_mock_worker
```

命令每次最多领取一个任务；无任务返回 `idle`。必须显式设置 `APP_MODE=demo`，并使用代码中固定的两个虚构租户之一；缺失模式、非 Demo 模式和其他租户均在连接数据库前拒绝。该入口不会加载 Provider 或访问网络：有当前快照时模拟无变化检查并保留 `data_as_of`，无快照时保持 `unknown`。运行后核对任务已完成、租约已释放、心跳存在，且 `usage_ledger.external_calls = 0`、`estimated_cost = 0`。它不能代替真实公司信息检查，也不是常驻 Worker 或 Cron。

### 人工研究导入 V1（本机、公开来源）

先确认数据库已迁移到 head、目标公司主数据已存在、操作者是 active 机构管理员。将 JSON 放在 Git 忽略目录后执行：

```bash
mkdir -p data/private/research_imports
cp data/sample/manual_research_import.json data/private/research_imports/manual-example.json
export RESEARCH_IMPORT_FILE=manual-example.json
export IMPORT_USER_ID="$(uv run python -c 'from backend.app.demo import ALPHA_USER_ID; print(ALPHA_USER_ID)')"
RESEARCH_IMPORT_DRY_RUN=true uv run python -m scripts.import_research_json
uv run python -m scripts.import_research_json
```

先核对 `dry-run` 输出的目标公司、策略版本、URL 检查上界、验证尝试上界、零 Token/费用和零数据库写入；因为不读取公司主数据，该数量是身份解析前的保守上界，且该步骤不连接数据库、不访问网络。正式输出只包含批次 ID、状态和路由计数。完成后核对：未解析记录只形成实体提及审核项且不生成事件；安全记录为 `auto_published` 并进入快照；其他记录为 `unconfirmed_lead` 且不创建逐条事件审核项。默认 `EXTERNAL_CALLS_ENABLED=false` 时 URL 未检查，因此所有已解析记录安全降级为未确认，`usage_ledger` 的外部调用、Token 和费用均为 0。只在受控小批次显式开启 URL 检查，并核对已解析记录的 URL 检查数不超过 `SOURCE_URL_MAX_CHECKS_PER_IMPORT`；该检查仍不调用模型或付费 API。重复同一文件应返回 `duplicate`。批次号复用但内容改变、来源代码元数据冲突或外部记录内容冲突时整批失败并回滚，不要绕过去重键手工改库。

### 政府官方工商身份导入

```bash
mkdir -p data/private/identity_imports
cp data/sample/official_identity_import.json data/private/identity_imports/official-example.json
export IDENTITY_IMPORT_FILE=official-example.json
export IMPORT_USER_ID='replace_with_local_admin_reviewer_uuid'
IDENTITY_IMPORT_DRY_RUN=true uv run python -m scripts.import_official_identity_json
uv run python -m scripts.import_official_identity_json
```

`dry-run` 必须显示零数据库写入、零外部调用、零 Token 和零费用。正式执行前核对证据域名为 HTTPS 政府/GSXT、信用代码及校验位、工商全称、注册地、登记状态和核验时间。`conflict` 不得通过手工 SQL 改为 verified；必须在专用工作台选择并留理由。重复文件应返回 `duplicate`。

V1 只接收不超过 1 MiB、最多 500 条且许可为 `public` 的 JSON。不得放入内部财务、投资协议、投委会材料、API Key、Cookie 或商业数据库受限内容；原始文件由操作者在私有目录管理，不进入 Git，也不会被系统复制到存储。当前没有网页/API 上传入口。

### 天眼查授权工商身份查询 V1（本机受控）

查询清单必须放在 `data/private/identity_imports/`，每批最多 10 家，每家公司必须同时给出工商全称和通过校验位验证的统一社会信用代码。可复制 `data/sample/tianyancha_identity_manifest.json` 后只在私有目录替换查询项。先执行不读取 Token、不连接数据库、不访问网络的 dry-run：

```bash
export TIANYANCHA_IDENTITY_MANIFEST='licensed-identity-request.json'
TIANYANCHA_IDENTITY_DRY_RUN=true uv run python -m scripts.import_tianyancha_identities
```

核对公司数和预计请求数后，只在一次性本机窗口中运行。Token 使用环境或 Secret 注入，不写入项目 `.env`、命令参数、日志或 Git；如果已由官方 CLI 安全保存，可在不打印内容的情况下读入当前 shell：

```bash
export TIANYANCHA_AUTHORIZATION="$(uv run python -c 'import json,pathlib; print(json.loads((pathlib.Path.home()/".tyc/config.json").read_text())["headers"]["Authorization"])')"
export IMPORT_USER_ID='replace_with_local_institution_admin_uuid'
APP_MODE=demo \
EXTERNAL_CALLS_ENABLED=true \
TIANYANCHA_IDENTITY_CALLS_ENABLED=true \
PAID_API_CALLS_ENABLED=false \
AUTO_REFRESH_ENABLED=false \
AUTO_PUBLISH_ENABLED=false \
TRUSTED_SOURCE_CALLS_ENABLED=false \
uv run python -m scripts.import_tianyancha_identities
unset TIANYANCHA_AUTHORIZATION
```

适配器只调用固定 Core 端点的名称候选和工商登记两个工具；不跟随重定向，每次请求受超时、响应大小、低频间隔、总请求数和一次重试限制。完整响应只写入 `data/private/provider_cache/tianyancha/` 的权限受限缓存，数据库不保存联系方式。运行后核对 `official_identity_verifications.verification_basis=licensed_business_data`、`usage_ledger` 的调用/缓存/零 Token/零费用，以及 `conflict` 未改写公司主档。相同清单在缓存期内重复运行应为零外部调用并返回 `duplicate`。随后立即恢复两个外部调用开关为 false。

### 身份例外与历史审核工作台 V1（仅本机受控环境）

确认 API 只绑定 `127.0.0.1` 并设置 `REVIEW_WORKBENCH_ENABLED=true`；前端 `DEMO_USER_ID` 必须是当前租户内同时具有 `reviewer` 和 `institution_admin` 角色的本地用户，才能修改身份主数据。访问 `/reviews` 后，新导入通常只出现身份歧义；只能从 30 天内、与该提及相关且明确标注政府官方或授权商业依据的候选中选择。提交后核对审核状态、公司信用代码/全称、实体提及、事件证据和发布路由；原 URL 未检查时应为 `unconfirmed_lead` 且不进入快照。工作台读取和身份决定都不调用外部 Provider。

完成私有验收后关闭前后端并取消该开关。当前 Header 身份可被伪造，禁止将工作台暴露到公网、局域网共享地址或多人环境；生产部署必须先实现正式认证和会话保护。

### 受控可信来源监测 V1（仅本机受控环境）

先以平台管理员打开 `/monitoring` 登记已核验公司、来源名称、类型、根域名、HTTPS 起始 URL、访问依据、许可、频率和保留策略。列表页应先用 dry-run，并在实样中确认内容链接路径；如导航链接过多，设置明确的列表内容路径前缀。dry-run 入队后以四个业务安全开关均关闭的 Worker 处理，核对请求、字节、Token 和费用全部为 0。

真实免费 HTTP 检查仅在一次性受控窗口中临时设置 `EXTERNAL_CALLS_ENABLED=true` 和 `TRUSTED_SOURCE_CALLS_ENABLED=true`，同时保持 `PAID_API_CALLS_ENABLED=false`、`AUTO_REFRESH_ENABLED=false`、`AUTO_PUBLISH_ENABLED=false`。每次 Worker 只领取一个任务；运行后核对 `source_check_runs`、`usage_ledger`、robots、失败状态、候选公司归属和去重结果，再立即恢复双开关为 false。404、robots 拒绝、DNS/对端异常、MIME、超时或大小超限不得手工改成成功；候选只可人工标记并交给现有研究导入，不得直接写事件或共享层。

## 4. 常见事件处置

| 事件 | 立即动作 | 恢复条件 |
| --- | --- | --- |
| 预算超限 | 停止新外部调用，任务标记 `budget_deferred`，保留查询 | 管理员调整预算或进入新周期；不得自动换贵源 |
| Provider 故障/限流 | 记录错误与 `retry_after`，有限重试；必要时使用已批准低成本替代 | 健康检查恢复，积压在预算内消化 |
| Worker 崩溃 | 不手工重复创建任务；等待租约过期后重领 | 检查点、幂等键和用量记录一致 |
| 严重负面误报 | 若误入已确认层，立即从快照撤下并标记撤回；保留证据、策略原因与审计 | 完成纠错或驳回，回归测试证明同类记录只进入未确认线索 |
| Secret 疑似泄漏 | 关闭 Provider、吊销并轮换、检索日志和提交历史 | 新 Secret 通过最小权限验证，完成事件复盘 |
| 跨基金越权 | 立即禁用相关账户/服务身份，保全审计，关闭受影响入口 | 修复 RLS/授权并通过负向回归，通知义务已评估 |
| 许可到期/撤稿 | 停止抓取和展示受限正文，标记来源与相关事件 | 获得新授权或完成撤回/替代证据审核 |

## 5. 备份与恢复

每日加密 PostgreSQL 备份，文件存储按许可和变化量备份，备份与主机隔离并设保留期。每月至少演练一次恢复到隔离环境：恢复数据库与文件 → 校验迁移版本 → 校验哈希和证据外键 → 重置过期租约 → 验证 RLS → 用虚构用户冒烟测试。Demo 规划目标为 RPO 24 小时、RTO 8 小时；正式 SLA 需另定。

## 6. 删除与退出

删除先冻结访问并生成范围清单，区分用户账户、租户私密数据、共享公开事实和依法/依约需保留的审计。异步清除后验证数据库、存储、缓存和备份生命周期；出具不含私密内容的审计结果。停止服务时先关闭外部调用和 Cron，再排空/终止任务、备份、撤销 Secret，最后关闭 API。

## 7. 变更控制

更新频率、事件分类、评分、权限、Provider、成本上限、重大负面规则、财务口径或 Kimi 边界发生变化时，先更新 ADR、迁移/配置和测试，再部署。历史迁移不回改。实施命令、结果和阻塞只写入简洁的[实施记录](IMPLEMENTATION_LOG.md)。

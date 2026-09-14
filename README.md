# 原始股雷达

面向未上市被投企业股东、基金投资人和投后管理人员的私有投后信息监测平台。产品定位是：后台按需更新与低频巡检，前台即时读取 PostgreSQL 中已发布的数据；每条重要结论都能回到来源、证据和审核记录。

## 当前状态

当前为 `DEMO / VALIDATION`，第 2 阶段最小数据闭环已经验收，并已补充个人安全查询、按需缓存、Mock Worker V1、人工研究导入 V1 与受控可信来源监测 V1：

> **当前推进方向**：[ADR-0021](docs/DECISIONS/ADR-0021-incremental-event-delivery.md) 保留“增量交付＋局部重构”；[ADR-0022](docs/DECISIONS/ADR-0022-curated-baseline-and-incremental-research.md) 采用“负责人复核初始资料＋按需增量研究”，经轻量校验即可按人工依据准入。E4.4 于 2026-09-14 的单次线上维护验证结束，2 次搜索、8 次 HTTP 未形成新增可展示内容，原有 5 条初始资料保持。9 月 15 日按批准完成按组提前回退与服务页后置的本地修复，已用封存队列及 Mock 验证，待 PR 审查和部署。当前默认仍是 4 次搜索、8 次网页 HTTP；12 次网页请求仅为下一单公司验证建议，尚未启用。本轮工程没有真实研究调用，原盲测 0/1 和 EV14 未通过保留。[完整计划](docs/15-incremental-event-delivery-plan.md)固定范围与验收，[实施看板](docs/10-implementation-plan.md)维护当前状态和下一唯一切片。

- 10 家虚构公司及两个虚构租户/基金；
- Mock 文档幂等导入、主体精确匹配、候选事件、证据和历史审核闭环；
- 身份优先的自动发布路由：安全事实自动发布，异常事实保留为未确认线索，只有身份歧义进入人工队列；
- 登录测试用户可按信用代码、工商全称或已核实别名精确查询平台共享公司，也可用至少两个字获取已核验共享目录中的名称候选，无需基金关系；
- 公司详情在服务端分离平台共享事件/证据与基金投资、机构私有事件和未确认线索；
- 本人历史查询按实际搜索和正文检查展示类别来源覆盖、缺口和缓存基准时间；未检出记录不代表不存在该类信息，取得正文不等于事实已核实；
- 刷新任务 `dry-run`、14 天动态新鲜度、24 小时冷却与重复任务合并；
- 公司列表只展示状态，不批量触发任务；公司详情过期时仅在自动刷新开关启用后入队；
- 单次 Mock Worker 可领取一个虚构数据任务，支持租约、过期重领、零费用用量记录和 `stale → fresh` 闭环；
- 本机 JSON 人工研究导入支持文件/批次幂等、公司身份解析、证据血缘、来源 URL 检查预算和发布策略审计；
- E4.1 本地 Excel 导入支持负责人确认的主体/别名准入、初始资料、追加更正和零调用预览；[操作及边界](docs/12-operations-runbook.md#负责人确认表格导入-e41本地)。
- 本机人工审核工作台主要展示身份歧义；既有事件审核记录仍可追溯；
- 政府官方与历史授权商业工商身份使用不同核验依据；旧商业数据适配器已退出活动代码，历史核验依据只作审计，不构成可继续展示或调用的依据；
- 平台管理员可登记已核验公司的官网、政府页、RSS、Sitemap 或列表页，后台低频检查后只生成机构私有候选文档，不自动创建事件或共享事实；
- 标记为“值得研究”的候选可在管理页直接录入谨慎结构化事实，复用现有研究导入、身份解析、证据和去重流程，原候选与私有底稿保持血缘；
- 到期来源可由默认 dry-run 的一次性调度命令小批量入队，连续失败指数退避，复用现有 Worker 重试和租约恢复；
- 应用层作用域过滤及 PostgreSQL RLS 双重保护，私有别名、文档、提及、事件、证据和快照均有明确 owner；
- CloudBase 邀请制邮箱认证已形成代码路径：正式模式不再信任 Demo Header，CloudBase 只核验身份，本地角色、基金授权与 RLS 继续决定业务权限；
- 个人用户可将共享公司加入单一默认关注清单、提交人工收录/更新申请并查看处理状态；测试查询、关注和申请上限由服务端执行；
- 已核验且允许进入共享目录的公司，可由平台管理员将个人申请接入受限公开网络研究队列；Worker 按实际配置记录类别覆盖（新普通增量任务定向查询融资与产品，其余类别不声称已检查），优先复用 14 天共享搜索缓存，百度为主源、博查只按条件回退，搜索摘要只作发现线索，必须安全抓取原网页后才能形成带证据的分级候选；
- 研究队列、阶段进度、取消、租约和断线恢复均持久化在 PostgreSQL；多人查询同一公司复用同一任务，取消不会删除已取得的合格共享缓存，任何候选都不会自动发布为已核实事实；
- 每个账户独立记录已看过的共享事件，回访时只展示新通过审核的共享事实；可生成个人私有、只读的确定性 Markdown 报告，不调用模型或外部数据源；
- M5A 提供可迁移的生产后端/前端镜像、Caddy 单一入口、生产配置预检、数据库就绪检查和 PostgreSQL 备份恢复工具，可在本机零云费用验收；
- M5B 已在腾讯云中国香港单机落地：公网 Web 只开放 `80/443` 且访问统一进入 HTTPS，PostgreSQL、API、前端和 SSH 均不直接暴露；客户端加密备份已上传私有 COS 并完成下载、校验和隔离恢复；
- 外部搜索、模型、付费 API、自动刷新、自动发布和审核工作台默认关闭。

历史验收已记录 PostgreSQL 16、受限数据库角色、香港 HTTPS、备份恢复和 CloudBase 账户回归；当前生产状态仍以实际运维核验为准。M6B 内容价值尚未通过，其原申请和问题保留。已有可信来源监测与受限公开研究是两条独立后台流程，不能据此宣称完整持续监控已实现；代码审查发现的事件归并、差分接线和费用口径缺口见完整计划。

部署版本、真实单公司对照、原始资料导入及下一唯一切片统一见[实施看板的有效检查点](docs/10-implementation-plan.md#2-有效执行检查点)。工程测试通过不等于内容价值、25 家覆盖或独立用户验收通过。

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

### M5A 零云费用生产式验收

仓库根目录的 `compose.production.yml`、`backend/Dockerfile`、`frontend/Dockerfile` 和 `deploy/` 提供不绑定云厂商的生产构件。本机验收使用独立的一次性 PostgreSQL、虚构账号和虚构公司，只绑定 `127.0.0.1`，不会购买或创建云资源：

```bash
docker compose \
  --env-file deploy/acceptance.env.example \
  -f compose.production.yml \
  -f deploy/compose.acceptance.yml \
  config -q
```

完整的构建、迁移、备份、隔离恢复、浏览器验收和安全停止命令见[运维手册](docs/12-operations-runbook.md#m5a-零云费用生产式验收)。本配置仍保持外部调用、付费调用、自动刷新和自动发布关闭；不要把虚构验收密码或未加密本机备份用于真实环境。

### M5B 香港单机邀请测试环境

邀请测试环境已经采用腾讯云中国香港服务器承载应用和 PostgreSQL，数据库仅加入内部容器网络；备份先用 `age` 公钥加密，COS 上传脚本拒绝明文 `.dump`。以下命令用于重建时检查配置结构，不会购买资源、连接 COS 或启动服务：

```bash
docker compose \
  --env-file deploy/single-host.env.example \
  -f compose.production.yml \
  -f deploy/compose.single-host.yml \
  config -q
```

示例文件包含故意保留的占位值，不能直接用于生产。重建步骤、密钥、COSCLI、恢复和告警接线见[运维手册](docs/12-operations-runbook.md#m5b-香港环境部署顺序已落地保留作重建手册)，部署取舍见 [ADR-0014](docs/DECISIONS/ADR-0014-hong-kong-invitation-deployment.md)。邀请入口为 `https://app.dealflowradar.cn`；中国香港服务器不以大陆 ICP 备案为前置，但正式迁入大陆仍须企业备案。

### CloudBase 邀请制认证 V1

本地开发和 CI 默认 `AUTH_PROVIDER=demo`，继续使用固定虚构用户。只有在 CloudBase 控制台已开启邮箱登录、创建受邀账户，且本地 `users` 有唯一同邮箱 active 用户后，才切换前后端的服务端配置：

```bash
export AUTH_PROVIDER=cloudbase
export CLOUDBASE_ENV_ID='replace_with_cloudbase_env_id'
export CLOUDBASE_CLIENT_ID='replace_with_client_id_or_leave_empty'
export PHONE_LOGIN_ENABLED=false
```

访问 `http://localhost:3000/login`，验证码登录成功后 access/refresh token 只保存于 Next.js 的 `HttpOnly` Cookie。本地认证验收期间不要混用 `localhost` 和 `127.0.0.1`，浏览器会把两者视为不同的 Cookie 站点。CloudBase group 不会映射成业务角色；API 仍从 PostgreSQL 加载 tenant、角色与基金授权，并设置既有 RLS 上下文。`AUTH_PROVIDER=cloudbase` 时 `X-Demo-User-Id` 完全无效。

手机号验证码是默认关闭的邀请测试扩展，不是公开注册。启用前必须先在同一个既有 CloudBase 账户上绑定中国大陆手机号，并确认邮箱和手机号登录返回同一稳定 `subject`；手机号登录禁止按邮箱首次绑定本地用户，因此不会生成第二个业务账户。首版不在业务数据库、Cookie 或 URL 保存完整手机号，邮箱登录继续作为恢复方式。应用默认实行 60 秒冷却、单号码每天 5 次、全环境每天 50 次的内存保护，CloudBase 自身的持久限额作为第二层保护；应用重启会清空本地计数，生产扩容到多实例前必须重新设计共享限流。启用和回退步骤见[运维手册](docs/12-operations-runbook.md#手机号验证码登录-v1默认关闭)。

CloudBase 身份请求不等于公司信息外部查询，不会调用任何公司信息 Provider，也不会开启 `EXTERNAL_CALLS_ENABLED`、`PAID_API_CALLS_ENABLED`、`AUTO_REFRESH_ENABLED` 或 `AUTO_PUBLISH_ENABLED`。完整配置、四角色验收、故障回退和控制台操作见[运维手册](docs/12-operations-runbook.md)；边界见 [ADR-0012](docs/DECISIONS/ADR-0012-cloudbase-identity-local-authorization.md)。

### 个人关注与测试权益

所有已登录的受邀测试用户暂时获得相同测试额度：每个上海自然月查询 100 次、关注 20 家、固定报告 10 次；新公司研究默认每人 10 家/天、30 家/月，同一目标 24 小时内复用原请求。用户可在个人页面申请有期限的临时额度，由平台管理员决定。可通过 `PERSONAL_DAILY_REQUEST_LIMIT`、`PERSONAL_MONTHLY_REQUEST_LIMIT` 等环境变量调整；它们只是邀请测试策略，不是商业套餐或付费订阅。

M6A-3 的旧供应商专用 Worker 已退出活动代码。同步公司搜索和详情始终只读 PostgreSQL；共享目录没有结果时，用户仍须提交准确工商全称或统一社会信用代码，不会根据简称自动建公司。平台管理员只有在准确主体已对应到“已核验且允许共享”的全局公司后，才能批准进入 R3 后台队列。申请、阶段进度和取消结果持久化在 PostgreSQL，用户断线或重新登录后仍可恢复；外部调用只可能发生在默认不启动的独立 Worker，网页请求不会等待 Provider。

访问 `/watchlist` 查看当前账户的关注、研究阶段和申请。关注只允许已核验平台共享公司，不授予公司或基金数据访问权；搜索无结果时提交准确工商全称或信用代码，只会同步写入 PostgreSQL，不会在网页请求中调用搜索、模型或发布事实。R3 Worker 需要独立的四重调用开关和 Provider 密钥才可运行，默认全部关闭；个人关注和用量仅本人可见，平台管理员只处理用户明确提交的申请。

打开共享公司详情时，页面在真正挂载后记录当前用户已看到的共享事件 ID；第一次只建立基线，以后只提示新增的 `published + platform_shared` 事件。预取、关注、基金关系、私有候选或未确认线索都不会改变该基线。访问 `/reports` 可查看本人生成的固定 Markdown 报告；报告为生成时点的不可变快照，后续纠正或撤回不回写旧报告，必须以公司最新详情为准。

缓存参数由 `REFRESH_POLICY_VERSION`、`RECENT_QUERY_TTL_DAYS` 和 `REFRESH_REQUEST_COOLDOWN_HOURS` 配置。Demo 默认分别为 `demo-v1`、14 天和 24 小时；`AUTO_REFRESH_ENABLED=false` 时仍会准确显示过期状态，但不会因页面访问创建任务。即使开启自动入队，同步请求也不会调用搜索、模型或付费 API。

已有 `mock_refresh` 任务时，可在另一个终端运行一次虚构数据 Worker：

```bash
export WORKER_TENANT_ID="$(uv run python -c 'from backend.app.demo import ALPHA_TENANT_ID; print(ALPHA_TENANT_ID)')"
APP_MODE=demo uv run python -m scripts.run_mock_worker
```

每次命令最多处理该租户的一个任务；无任务时返回 `idle`。命令要求显式设置 `APP_MODE=demo`，且只接受两个固定虚构租户。此 Worker 不加载 Provider、不访问网络，只用于验证队列闭环：有快照时更新检查时间但保留事实基准日，无快照时保持 `unknown`，不得用于真实公司检查。

### 人工研究导入与自动路由

入口只接受 `data/private/research_imports/` 下不超过 1 MiB 的 JSON，且每批最多 500 条、`license_status` 必须为 `public`、目标公司须预先存在。身份未解析时只创建提及审核项，不生成事件或快照；身份已验证时由 `PUBLICATION_POLICY_VERSION` 对来源、URL、可信度和风险进行确定性路由。满足条件的事件自动发布并事务化更新快照，其他记录保存为 `unconfirmed_lead`，不会创建逐条事件审核任务。

默认 `EXTERNAL_CALLS_ENABLED=false`，URL 状态为未检查，因此记录会安全降级为未确认线索。只在受控的小批次公开来源验证中显式开启外部调用；系统每批最多检查 `SOURCE_URL_MAX_CHECKS_PER_IMPORT` 个 URL，默认 20 个，超出部分保持未确认。URL 检查不调用模型、不消耗 Token、不使用付费 API，验证尝试数仍写入 `usage_ledger`。

可用仓库中的纯虚构示例验证：

```bash
mkdir -p data/private/research_imports
cp data/sample/manual_research_import.json data/private/research_imports/manual-example.json
export RESEARCH_IMPORT_FILE=manual-example.json
export IMPORT_USER_ID="$(uv run python -c 'from backend.app.demo import ALPHA_USER_ID; print(ALPHA_USER_ID)')"
RESEARCH_IMPORT_DRY_RUN=true uv run python -m scripts.import_research_json
uv run python -m scripts.import_research_json
```

`dry-run` 只校验文件并展示目标公司、发布策略、URL 检查上界、验证尝试上界和预计费用，不连接数据库、不访问网络；因为不读取公司主数据，该数量是身份解析前的保守上界。确认后再执行正式命令。重复执行同一文件返回 `duplicate`，不会新增文档、事件或费用记录。真实导入文件不得提交 Git；当前入口不接受内部财务、投委会、投资协议等敏感材料，也没有开放上传 API，因为文件隔离、恶意内容检查和许可审计尚未单独验收。

### 单类中标公告闭环（E1）

已有 JSON 人工研究导入可显式选择 `TENDER_EVENTS_ENABLED=true` 和记录 `event_subtype=tender_notice`。完整输入仍遵循人工导入契约，`evidence_excerpt` 提供单公告、带明确字段标签的原文；信用代码和工商全称均需匹配已核验主体，来源、文档和批次须允许使用。`facts` 字段仍满足既有输入 Schema，但中标事实由原文规则抽取重新生成，不信任人工填入的金额。`dry-run` 另显示开关和拟走中标路径的记录数，不代表抽取/主体/权限已通过。

接线复用现有私有导入及可信来源的人工交接，开关默认 `false`；只生成候选及逐字段证据。平台管理员在原审核页选择具体版本后才能共享，更正前原事实继续显示。公司重要变化区展示已核实版本、原文引用和修订历史，异步解读按事实版本缓存，固定报告保留生成时版本。转载不重复生成事件或解读，必要证据撤回后隐藏依赖内容，旧报告不改写。

迁移 `0028/0029` 已随 PR #77 经批准部署到香港环境，新写入开关保持关闭；保留已有历史，有观测/审核记录时禁止丢弃历史的降级。此入口尚未接到通用公开网络研究 Worker，未验证真实来源覆盖，不支持任意网页或多标段表格。完整边界与下一切片见[实施看板](docs/10-implementation-plan.md)。

### 关注公司低频检查（E3）

已实现默认关闭的三类低频检查：关注公司首次排队，之后默认每 7 天检查经营、融资、合同/中标；同公司复用任务和缓存，失败退避、连续无新资料降频。`.venv/bin/python -m scripts.queue_watchlist_checks` 默认只读预览；真实入队、Worker 开关和关停流程见 [运维手册](docs/12-operations-runbook.md)。关注页分别显示尝试、成功和下次计划时间，失败不覆盖旧结果；未安装实际定时任务，真实费用与内容价值留待 E4 验证。

### 公开研究费用控制（E2.2）

业务研究和主体查证已接入费用状态、调用前预占、结算及中断保护；未知价格保持未知，新金额上限默认零，真实调用仍需原开关和具体预算授权。平台管理员可使用默认只读的费用核对命令，详见[成本说明](docs/06-cost-control.md)。迁移 `0030` 已随 PR #77 经批准部署，真实调用保持关闭；模型解读、机构来源和基础设施不在此次原子预算范围内。阶段状态与停止点仍以[实施看板](docs/10-implementation-plan.md)为准。

### 工商身份核验导入

政府来源继续通过 `OfficialIdentityProvider` 边界导入已核对的结构化 JSON，不抓取验证码、登录页或未公开接口。文件只能来自 Git 忽略的 `data/private/identity_imports/`，政府证据地址必须使用 `*.gsxt.gov.cn` 或 `*.gov.cn` HTTPS 域名，信用代码会校验字符集和校验位。

```bash
mkdir -p data/private/identity_imports
cp data/sample/official_identity_import.json data/private/identity_imports/official-example.json
export IDENTITY_IMPORT_FILE=official-example.json
export IMPORT_USER_ID="$(uv run python -c 'from backend.app.demo import ALPHA_USER_ID; print(ALPHA_USER_ID)')"
IDENTITY_IMPORT_DRY_RUN=true uv run python -m scripts.import_official_identity_json
uv run python -m scripts.import_official_identity_json
```

程序会优先按统一社会信用代码匹配，再使用工商全称和注册地；冲突不会静默改主数据。核验记录默认 30 天有效，由 `IDENTITY_VERIFICATION_TTL_DAYS` 配置。

历史授权工商身份 V1 使用 `licensed_business_data` 与政府来源分开审计。ADR-0018 与 R1 已移除其活动适配器、CLI、开关和部署入口；旧记录不能改称政府或公开网络证据。向前迁移会把没有独立官方核验支撑的相关公司身份恢复为待核验，撤回没有独立来源支撑的展示事实，并停止展示旧供应商证据引用；仍有独立证据的事件可以保留。必要的原始记录、用量和审计血缘继续保存。生产 Secret 与私有缓存须在 R1 合并部署、数据库备份及验证完成后删除，不提前清理。

### 受控可信来源监测 V1

访问本机 `/monitoring`，平台管理员可为已核验公司明确登记 HTTPS 来源。列表页建议填写内容路径前缀，例如 `/news/detail`，避免把导航或产品目录误收为新闻；来源配置、运行和候选均为当前 tenant 的运营私有数据。`dry-run` 只入队并记录零调用计划，真实检查必须同时显式开启 `EXTERNAL_CALLS_ENABLED=true` 与 `TRUSTED_SOURCE_CALLS_ENABLED=true`，并保持付费、自动刷新和自动发布开关关闭：

```bash
export WORKER_TENANT_ID='replace_with_local_tenant_uuid'
export SOURCE_MONITOR_WORKER_USER_ID='replace_with_local_platform_admin_uuid'
APP_MODE=demo uv run python -m scripts.run_source_monitor_worker
```

每次 Worker 只处理一个任务。默认两个外部访问开关均为 `false`；真实运行只允许已登记域名，遵守 robots.txt，并限制重定向、请求数、响应大小、MIME、超时和域名访问间隔。系统只保留许可策略允许的元数据或最小摘录，不登录、不提交表单、不保存 Cookie、不调用模型或付费 API。候选需要管理员标记；结构化交接只创建机构私有底稿和候选事件，平台共享仍须另一次人工晋升。

到期调度器是一次性 Cron 入口，默认只做 dry-run：

```bash
export WORKER_TENANT_ID='replace_with_local_tenant_uuid'
export SOURCE_MONITOR_WORKER_USER_ID='replace_with_local_platform_admin_uuid'
APP_MODE=demo SOURCE_MONITOR_SCHEDULER_DRY_RUN=true \
uv run python -m scripts.queue_due_source_checks
```

真实入队还需显式设置 `AUTO_REFRESH_ENABLED=true`、`SOURCE_MONITOR_SCHEDULER_ENABLED=true`、`EXTERNAL_CALLS_ENABLED=true` 和 `TRUSTED_SOURCE_CALLS_ENABLED=true`，并保持 `PAID_API_CALLS_ENABLED=false` 与 `AUTO_PUBLISH_ENABLED=false`。调度命令本身不联网，只将到期来源小批量入队；后续仍由 Worker 执行受控检查。

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

访问 `http://localhost:3000`。前端默认使用虚构机构管理员身份；设置 `DEMO_USER_ID` 为无基金测试用户可验证个人查询路径。API 调试可访问 `http://127.0.0.1:8000/docs`。共享搜索和详情只读数据库，不自动创建公司，也不触发外部 Provider；有基金权限时才叠加投资关系和机构私有内容。

### 身份例外与历史审核工作台 V1（仅本机）

只在本机私有验证时显式开启后端开关，并让前端使用当前租户内具有 `reviewer` 角色的本地用户 ID：

```bash
export REVIEW_WORKBENCH_ENABLED=true
uv run uvicorn backend.app.main:app --reload

cd frontend
export DEMO_USER_ID='replace_with_local_reviewer_uuid'
npm run dev
```

访问 `http://localhost:3000/reviews`。新导入默认只有身份歧义进入该队列；既有事件审核记录仍展示证据和决定历史。当 30 天内的关联官方工商候选已入库时，同时具有 `reviewer` 和 `institution_admin` 角色的本地用户可选择主体并填写理由。系统会在一个事务内更新身份、保留曾用名、重建事件/证据并按当前发布策略重路由。该决定不会在页面请求中访问外部网站；原来源链接尚未检查时，记录会安全转为 `unconfirmed_lead`。

`AUTH_PROVIDER=demo` 下的 `X-Demo-User-Id` 仍只是本地测试身份。只有切换 CloudBase 模式并完成真实受邀账户和权限回归后，才可在后续生产部署里程碑评估对外开放；审核工作台开关不能替代认证。

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
- 自动发布不表示人工背书；未确认线索不进入已确认事实、风险结论或公司快照。
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
| 当前状态与下一唯一任务 | [实施看板](docs/10-implementation-plan.md) |
| 增量交付完整范围、依赖与验收 | [事件处理链改造计划](docs/15-incremental-event-delivery-plan.md) |
| 离线测试与验收 | [测试策略](docs/11-test-strategy.md) |
| 部署、恢复与故障处置 | [运维手册](docs/12-operations-runbook.md) |
| 已确认架构决定 | [ADR 索引](docs/DECISIONS/README.md) |

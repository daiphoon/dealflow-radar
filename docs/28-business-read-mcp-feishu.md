# 业务只读 MCP 与飞书身份适配

2026-10-03 状态：#112 已合并，制品和历史范围选择保留；负责人已将生产激活和功能扩展暂缓。当前主线为 [网站核心使用链路与受控发布](29-website-core-release.md)。下面的实现合同保留，不代表飞书/ChatGPT 已接通或真实数据已开放。

本切片依赖 [CI测试设施](27-ci-validation-budget.md)，正式业务基线为 main `d6edae70bb4201584d69ef0c4cfa1ba6bcd50e82`。它是独立候选进程；没有改网站、融资/归并/Provider规则、历史 migration 或诊断五工具。真实配置、合并、制品发布和生产激活另批。

## 实际读取合同

| 工具 | 读取与拒绝边界 |
| --- | --- |
| `find_company` | 仅获准共享公司；法定名/UCC/verified且当前可见别名；固定查询，默认20、最多20条 |
| `get_company_matters` | 正式事项投影及合法短证据；confirmed/candidate、历史、反证、待核及实际研究覆盖；来源授权不足明确标识省略，不说“没有事项” |
| `list_company_reports` | 仅本人获准公司/报告ID；当前许可、基准日、生成日、版本、stale/restricted；不生成 |
| `get_saved_report` | 复用正式存档正文许可；restricted正文为null；8192字符分块、原hash/交付hash/总长/offset/cursor；每块重验授权和内容 |

`business_read.py` 复用 `request_session`、`_company_event_outputs`、`report_evidence_permission`、`report_lead_permission` 和 `get_personal_company_report`。仅当前事务内批量预载引用许可所需行，不缓存权限结论。无refresh、receipt、report创建、研究、watchlist、发布、模型/搜索或链接抓取。递归处理低信任字符串/URL，不执行返回内容中的指令；链接不开放时不返回链接。

当前研究按网站相同的本人请求关联、状态及排序读取，复用 `research_result`；不能让较新但未关联的任务取代网站状态。固定窗口复用正式 `reference_fields` 核对，缺失/冲突保持null，不按读取当天生成窗口。正式刷新请求及Worker会话仅用于虚构回归准备，MCP自身不创建请求或任务；queued/partial/failed与历史未知窗口通过non-owner只读比对。

事项字段使用当前 `EventOut` 的真实名称（`summary`、`curated_versions` 等）；保留人工版本中的 `not_assessed`、日期精度/口径、事实support与反证状态。回归逐字段比对正式网站投影，不仅比对事项ID；没有另建评分或改变事实/归并语义。

业务源首版仅适配既有 PostgreSQL 业务库。逻辑 source ID、公司ID、本人report ID均由私有 grant 明确列出；其他服务器文件、数据库、租户和内部投资数据未覆盖。原诊断角色不能用于正文读取。

## 显式数据库列合同（PR #112 定点收口）

`business_read_contract.py::READ_COLUMNS` 是版本化、手写的 20 表列合同；不遍历 ORM 模型。专用 Session 使用 `load_only(..., raiseload=True)`，JSON 用受限表达式加载；未声明字段不会隐式 lazy load。网站默认仍加载原正式投影，MCP 仅关闭未使用的分析摘要读取，不改许可判断。

| 表 | 显式列 | 查询依据 |
| --- | --- | --- |
| `companies` | `id`, `tenant_id`, `credit_code`, `legal_name`, `registered_region`, `identity_status`, `visibility_scope` | 四工具公司身份、共享范围、正式 RLS / identity_status |
| `company_aliases` | `id`, `company_id`, `alias`, `verification_status`, `visibility_scope`, `owner_user_id`, `owner_tenant_id` | find_company 的已验证别名与当前用户可见性 |
| `sources` | `id`, `code`, `name`, `source_quality`, `license_status` | 正式证据来源名称/质量/当前许可；不需要 base_url/config |
| `raw_documents` | `id`, `source_id`, `title`, `canonical_url`, `published_at`, `published_on`, `observed_at`, `license_status`, `visibility_scope`, `owner_user_id`, `owner_tenant_id`, `content_hash` | permission_inputs / report_evidence_permission：出处与许可；JSON 另走受限视图 |
| `events` | `id`, `company_id`, `owner_user_id`, `owner_tenant_id`, `visibility_scope`, `event_type`, `event_subtype`, `status`, `direction`, `materiality_score`, `risk_severity`, `confidence_score`, `source_quality`, `title`, `summary`, `facts`, `uncertainties`, `occurred_at`, `published_at`, `published_on`, `observed_at`, `fingerprint_version`, `publication_route`, `publication_policy_version`, `publication_reasons` | 正式 _company_event_outputs：事项事实、阶段、反证、发布状态及权限过滤 |
| `event_evidence` | `id`, `event_id`, `raw_document_id`, `source_event_evidence_id`, `owner_user_id`, `owner_tenant_id`, `visibility_scope`, `evidence_excerpt`, `span_hash`, `support_type`, `display_source_name`, `display_source_quality`, `display_title`, `display_canonical_url`, `display_published_at`, `display_published_on`, `display_observed_at`, `display_url_health_status`, `display_url_http_status`, `display_url_checked_at`, `display_final_url`, `display_license_status`, `display_allowed`, `display_detail_payload` | 正式证据投影与 evidence_signature；保留获准短摘录及不可变 display 快照 |
| `event_facts` | `id`, `event_id`, `fact_key`, `name`, `value`, `unit`, `position`, `occurrence_count` | 正式事实名称/值/单位、稳定 fact_key 与排序 |
| `event_fact_supports` | `id`, `event_id`, `event_fact_id`, `event_evidence_id`, `support_status`, `support_reasons`, `policy_version`, `assessed_at` | 正式支持/反证状态；evidence_signature 另走受限视图 |
| `event_observations` | `id`, `event_id`, `schema_version`, `fact_version`, `observation_kind`, `occurred_on`, `date_precision`, `created_at` | 人工版本、不可变 fact_version、日期口径；candidate JSON 另走受限视图 |
| `personal_company_reports` | `id`, `owner_user_id`, `company_id`, `company_legal_name`, `report_version`, `title`, `as_of`, `content_hash`, `source_event_ids`, `markdown`, `created_at` | 本人存档标题/正文/hash/版本、引用许可及分页；不需 idempotency_key |
| `users` | `id`, `tenant_id`, `status` | active_user 与正式 user/tenant 权限；无邮箱/个人资料 |
| `tenants` | `id`, `status` | 当前租户 active 状态 |
| `roles` | `id`, `code` | 0036 RLS 的角色代码 |
| `user_role_assignments` | `user_id`, `role_id`, `valid_until` | 0036 RLS 当前用户角色及到期判定 |
| `investments` | `company_id`, `fund_id`, `tenant_id` | 0036 RLS 公司/基金/租户关联键；无金额、估值或持仓比例 |
| `fund_access_grants` | `fund_id`, `user_id`, `valid_until` | 0036 RLS 基金授权及到期判定 |
| `entity_mentions` | `id`, `raw_document_id`, `candidate_company_id`, `resolution_status`, `mention_text`, `visibility_scope`, `owner_user_id`, `owner_tenant_id` | 正式原文身份许可及正文 mention 边界；id 是 ORM 主键，mention_text 是既有许可输入 |
| `company_research_jobs` | `id`, `company_id`, `created_by_user_id`, `coverage`, `created_at`, `status`, `policy_version` | 正式 research_result / stored_completion / reference_fields；不取 query、processing_trace、错误正文 |
| `personal_company_requests` | `id`, `company_id`, `owner_user_id`, `research_job_id`, `status`, `created_at` | 与网站相同的本人请求关联/最新任务排序 |
| `event_sharing_decisions` | `id`, `source_observation_id`, `source_event_id`, `action` | 正式 curated 版本 visibility 与原观测分享/撤回判断 |

`RawDocument.payload`、`EventFactSupport.deterministic_checks`、`EventObservation.candidate_payload` 不再整列授权给 reader。三个 `security_barrier` 视图分别只提供 excerpt/source_windows/四个来源检查字段/curated_record 布尔；evidence_signature；正式人工/招投标版本需要的 event_fields/metadata/reviewed_at/sources/evidence_ids/candidate/field_links。不提供原文整包、created_by 或处理 trace；缺键与显式 null 区别保留。完整 InvestorChangeAnalysis 权限删除。

视图拥有者是另一新建 NOLOGIN、NOSUPERUSER、NOBYPASSRLS、NOINHERIT 角色，不拥有任何业务表、没有角色成员资格，只获得三个 JSON 源列与 `PROJECTION_RLS_COLUMNS` 明列的 RLS 依赖列。reader 只能 SELECT 三个视图，不能 SET ROLE 成为投影拥有者。视图使用该低权限拥有者执行原表 RLS，事务的正式 user/tenant 上下文不变；非 owner PG 验证另一用户读取私人原文视图返回零行。没有高权 SECURITY DEFINER 函数或事实副本。视图并未使用 security_invoker=true（那会要求 reader 重获底层整包列）；依据 [PostgreSQL 16 CREATE VIEW 的 RLS 语义](https://www.postgresql.org/docs/16/sql-createview.html)。

两个现有完整 JSON 例外明确保留：`event_evidence.display_detail_payload` 参与正式 evidence_signature 的整体完整性校验，裁剪会导致合法证据支持被错误撤销；`company_research_jobs.coverage` 被正式 research_result/stored_completion/reference_fields 读取，包含完成状态与窗口。它们不是任意模型新增字段；MCP 不原样输出，仅返回正式允许的投影。前者仍可能包含保存的展示详情，后者可能含诊断细节；生产批准须接受此内部读边界。进一步键级拆分会改变现有签名/完成度合同，本轮不修改。报告 markdown 是获准正文而非可删除的冗余。其他明确事实 JSON（facts/uncertainties）保留事实含义。

安装仅创建新 reader、上述新 NOLOGIN 角色、专用 schema 和三个视图，不运行 Alembic、不改普通应用角色或既有0036业务结构。reader 无写权限、BYPASSRLS、继承扩权和表owner，事务 READ ONLY；业务连接与并发各2。未来安装仅对新角色执行，不能把旧宽权限角色当成已收窄。完整旧→新列差异和实际被拒 SELECT 证据在本轮私有包。

## 唯一认证路线

ChatGPT网页私有应用 → 专用HTTPS Streamable HTTP → 本站OAuth/DCR/PKCE → 中国飞书登录 → 预审批稳定身份映射 → 本MCP opaque token → 当前业务权限。

复用锁定的 `mcp==2.2.0` 原生OAuth/client鉴别/PKCE/Streamable HTTP；没有新增OAuth依赖或IAM管理平台。只选择public DCR（`none`），不同时做CIMD和预配置客户端。登记未指定scope时仅声明三项read能力，不签发token或数据授权；实际scope仍须匹配预审批grant并经本人同意。元数据仅宣告实际实现的能力；精确redirect、resource/audience和S256，授权码一次使用。mcp 2.2.0的resource校验、public撤销表单及frozen异常兼容由薄边界补齐，未降低协议检查。

本站路由为 `/.well-known/oauth-protected-resource`、`/.well-known/oauth-authorization-server`、`/authorize`、`/token`、`/register`、`/revoke`、`/mcp`。飞书桥为 `/feishu/start`、`/feishu/callback`、`/feishu/consent`。canonical resource/issuer均为同一专用HTTPS主机根；401含resource发现信息。真实ChatGPT redirect必须从当次管理页复制，不能照示例猜值。

中国飞书固定 `accounts.feishu.cn/.../authen/v1/authorize`、`open.feishu.cn/.../authen/v2/oauth/token` 和 `.../authen/v1/user_info`；不跟随重定向，上游响应最多64KiB。最小基础身份scope `contact:user.base:readonly`，真实App启用前按当前飞书控制台确认；不用IM/文档/日历/广域通讯录。只使用 `(app_id, tenant_key, open_id)` 命名空间映射预批准内部user/tenant/公司/报告/source/scopes/expiry；不按email自动建用户或授予管理员。两层state分离，安全cookie绑定浏览器，负责人显式同意；飞书token仅短时内存使用，不交ChatGPT、存储或记日志。

现有 `deploy/notify-feishu.sh` 使用自定义机器人webhook。这不证明已有用户OAuth；建议同租户独立最小“MCP登录”App，不改变告警机器人。真实App选择、回调和权限修改尚未执行。

## 状态、配额与私有配置

| 项目 | 首版明确默认值 |
| --- | --- |
| 业务scope | `dealflow.company.read` / `dealflow.matter.read` / `dealflow.report.read`；无write/profile scope |
| 授权码/登录事务 | 120秒、一次消费 / 600秒、独立state |
| access / refresh | 900秒 / 7天轮换；旧refresh重放撤销整条链 |
| 重新飞书认证 | 最多24小时；不假定飞书上游实时在职状态已由短token证明 |
| client / grant | client持久365天；grant独立显式expiry且可撤销；重启不重新打开已撤销grant |
| 业务限额 | 可配置60次/分钟、1000次/日，滚动24小时64MiB；非永久寿命帽 |
| 公共OAuth限流 | 每哈希IP与端点10次/分钟、100次/日；上游身份按client同样限流 |
| 请求/响应 | 16KiB / 128KiB；分页20、报告8192字符且明确分块 |
| 查询/连接 | statement 2500ms、lock 500ms、pool等候1秒；HTTP工具5秒，超时后实际查询结束才释放槽位 |

控制存储选一种：独立权限600的SQLite文件/私有控制卷。仅写client/授权事务/token摘要/撤销/脱敏审计/滚动配额，不写业务库；过期授权记录清理、审计31日保留。每次 `/mcp` 请求先生成32位 request ID，响应 `X-Request-ID` 与审计一致；不进入事实正文/hash。记录白名单工具、grant ID 的 SHA-256 安全标识及 grant 版本；对象只在正式授权和查询完成后记录获准 company/report ID。前置401、SDK参数错误、scope/对象拒绝、共享总额度/并发拒绝和查询超时均可定位。没有任意参数、原始查询词、正文、cookie、token、code或RPC调用方id。

`mcp_http` 表示HTTP结果，`business_read` 同ID记录最终工具结果；内部 query_outcome 与终态分开。外层超时/末次撤权/断连后，晚完成线程只能补查询指标，不能改成 returned。客户端实际是否收到内容始终 unknown。最后正文发送前先提交审计；写入失败返回503，不交敏感正文。四工具共享原60/1000/64MiB滚动预算，tool字段不拆额度。

新增列通过独立控制SQLite的可重入升级添加，旧行和滚动用量保留；建表/加列/旧principal转换处于同一事务，中断后重启不得重置额度。审计31天、详细行最多50,000；MCP入口全局600次/分钟、20,000次/日，超限拒绝并只按小时累加噪声（保留31天），不按随机token/IP无限建行。容量满时fail closed，不删除窗口内配额来继续服务。飞书必要身份调用独立计数。

`scripts.run_business_mcp` 默认关闭，必须显式 `MCP_BUSINESS_ENABLED=true`。配置来自600私有文件，拒绝symlink/宽权限/过大文件；数据库使用独立 `MCP_BUSINESS_DATABASE_URL`。私有JSON需resource、精确 `client_redirect_uris`、`feishu_app_id`、secret/cursor文件路径、`identity_grants`（tenant_key/open_id及Grant对象）、可选日/分钟额度。Grant包含id、内部user_id/tenant_id、company_ids/report_ids/source_ids、scopes及UTC epoch expires_at。真实值不进入Git、应用.env或聊天。

## 候选部署与激活

`compose.business-mcp.yml` 是**未激活**的optional profile，固定digest变量、UID10001、只读根fs、无caps、384MiB/0.5CPU/64PID，仅本机8050。独立内部DB网络＋用于固定飞书端点的认证出站网络；控制卷独立可写，凭据只读。候选 Caddy 示例只提供专用虚构主机；激活须在正常Caddy追加获准vhost，不替换网站或告警。仅信任当次核验的代理IP，不能用`*`。

集中批准必须绑定：两个PR准确head/Verify及合并后main/tree，linux/amd64不可变制品；生产实际版本/schema兼容；获准域名/DNS/TLS/代理增量及内网；独立飞书App最小scope与两段精确回调；唯一负责人映射、首批2–3家公司/已有报告/source IDs/字段/expiry；新只读角色和控制卷的安装/权限/备份；上述TTL、配额与低负载验收；本人登录/MFA/同意和ChatGPT真实工具调用＋审计。容器目录须由UID10001读取/写入，文件600目录700；不把私有.env嵌入镜像。

紧急关闭只停业务MCP/专用vhost并撤销grant/client/token链，保留脱敏审计；不停止网站、不回退schema、不启Worker。生产Next安全补丁另列实际版本核验与独立发布授权，不因本切片CI成功就宣称已部署。Tunnel退出；没有OpenAI模型API调用或充值。

## 验证与事实状态

快速协议负例不建业务库。少量真实non-owner PG故事从正式curated导入及网站保存报告开始，验证四工具非空、当前网站一致、跨用户/RLS、撤权/撤源/分页、并发、超时、重启与全表内容摘要不变。Mock飞书和HTTP协议通过不等于真实网页连接；实际head/CI、延迟/SQL/字节、小样本限制、完整失败记录见本轮统一私有交付包。

当前目标交付：`MCP_PATCH_READY_FOR_REVIEW`；准确head完整Verify以本轮统一包为准。ChatGPT网页和真实生产业务读取均 `WAITING_PRODUCTION_ACTIVATION`。真实App/账户/网页资格尚待本人按当时管理页验收，不创建Apps、不开放真实数据。

协议依据：[OpenAI MCP认证](https://developers.openai.com/plugins/build/auth)、[飞书官方CLI认证路径](https://github.com/larksuite/cli/blob/main/internal/auth/paths.go)、[飞书官方CLI最小基础身份scope](https://pkg.go.dev/github.com/larksuite/cli/shortcuts/contact)。动态账户资格和App权限以激活时现场为准。

# 业务只读 MCP 与飞书身份适配

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

`bootstrap_business_mcp` 只创建**新**专用角色；固定21张表的必要列，不 `GRANT ALL public`，不改普通应用角色。用户仅id/tenant/status；投资仅RLS依赖键，不含金额。正式ORM许可需要的原文行仅在受RLS约束的服务内使用，不外发RawDocument整包。启动拒绝超级用户、BYPASSRLS、建库/建角色、继承角色、表owner和列写权限。每事务先READ ONLY再绑正式身份，真实提交/回滚与连接回收保持；数据库连接和业务并发各2。

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

控制存储选一种：独立权限600的SQLite文件/私有控制卷。仅写client/授权事务/token摘要/撤销/脱敏审计/滚动配额，不写业务库；过期授权记录清理、审计31日保留。审计含操作/结果/耗时/SQL数/返回行数/字节，不含正文、cookie、token、code或查询参数。飞书必要身份调用独立计数。

`scripts.run_business_mcp` 默认关闭，必须显式 `MCP_BUSINESS_ENABLED=true`。配置来自600私有文件，拒绝symlink/宽权限/过大文件；数据库使用独立 `MCP_BUSINESS_DATABASE_URL`。私有JSON需resource、精确 `client_redirect_uris`、`feishu_app_id`、secret/cursor文件路径、`identity_grants`（tenant_key/open_id及Grant对象）、可选日/分钟额度。Grant包含id、内部user_id/tenant_id、company_ids/report_ids/source_ids、scopes及UTC epoch expires_at。真实值不进入Git、应用.env或聊天。

## 候选部署与激活

`compose.business-mcp.yml` 是**未激活**的optional profile，固定digest变量、UID10001、只读根fs、无caps、384MiB/0.5CPU/64PID，仅本机8050。独立内部DB网络＋用于固定飞书端点的认证出站网络；控制卷独立可写，凭据只读。候选 Caddy 示例只提供专用虚构主机；激活须在正常Caddy追加获准vhost，不替换网站或告警。仅信任当次核验的代理IP，不能用`*`。

集中批准必须绑定：两个PR准确head/Verify及合并后main/tree，linux/amd64不可变制品；生产实际版本/schema兼容；获准域名/DNS/TLS/代理增量及内网；独立飞书App最小scope与两段精确回调；唯一负责人映射、首批2–3家公司/已有报告/source IDs/字段/expiry；新只读角色和控制卷的安装/权限/备份；上述TTL、配额与低负载验收；本人登录/MFA/同意和ChatGPT真实工具调用＋审计。容器目录须由UID10001读取/写入，文件600目录700；不把私有.env嵌入镜像。

紧急关闭只停业务MCP/专用vhost并撤销grant/client/token链，保留脱敏审计；不停止网站、不回退schema、不启Worker。生产Next安全补丁另列实际版本核验与独立发布授权，不因本切片CI成功就宣称已部署。Tunnel退出；没有OpenAI模型API调用或充值。

## 验证与事实状态

快速协议负例不建业务库。少量真实non-owner PG故事从正式curated导入及网站保存报告开始，验证四工具非空、当前网站一致、跨用户/RLS、撤权/撤源/分页、并发、超时、重启与全表内容摘要不变。Mock飞书和HTTP协议通过不等于真实网页连接；实际head/CI、延迟/SQL/字节、小样本限制、完整失败记录见本轮统一私有交付包。

当前目标交付：`MCP_BUSINESS_READ_IMPLEMENTED` + OAuth/飞书Mock通过；ChatGPT网页和真实生产业务读取均 `WAITING_PRODUCTION_ACTIVATION`。真实App/账户/网页资格尚待本人按当时管理页验收，不创建Apps、不开放真实数据。

协议依据：[OpenAI MCP认证](https://developers.openai.com/plugins/build/auth)、[飞书官方CLI认证路径](https://github.com/larksuite/cli/blob/main/internal/auth/paths.go)、[飞书官方CLI最小基础身份scope](https://pkg.go.dev/github.com/larksuite/cli/shortcuts/contact)。动态账户资格和App权限以激活时现场为准。

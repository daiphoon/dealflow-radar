# 26 研究运行契约、固定参考日期与 M3-A 接入预检

本轮从 main `408ec6cf8abbf608cb59e4828480268f314dc465` / tree `fcde523e7e1b3a1850c0faf713d025db2822b8fe` 建立独立候选，不再向已合并 #109 追加。只修运行契约和日期链路，M3 只做盘点；schema 仍为0036，无依赖、融资语义、归并、robots、Provider 或评分规则改动。

## 运行入口与事务身份

`scripts/research_validation_runner.py` 是离线及未来另行授权真实运行的同一入口。由调用者注入 Provider 边界，内部始终调用正式 prepare 和 Worker；Mock 不复制业务循环。CLI 仅接受单独批准的 metered 契约，当前任务不运行该模式。

运行前绑定 checkout HEAD/tree、已加载模块路径及字节哈希、身份输入、Settings/预算/开关、固定日期、数据库目标、单公司请求和 attempt；完整契约 canonical SHA 必须匹配。普通应用角色必须非 owner、无 SUPERUSER/BYPASSRLS/CREATEDB/CREATEROLE，并连接带隔离标记、schema0036 的实验库。身份/UCC/审核别名、唯一队列、来源模式和外层上限不一致时在构建 Provider 前拒绝。隔离标记及迁移版本 SELECT 权限只在虚构库初始化，不代表批准生产角色变更。

`request_session(build_session_factory(...), user_id, tenant_id)` 把身份固定在一个 session 上，在每次 PostgreSQL 事务开始时重新设置事务局部身份；提交和回滚后 SELECT/refresh 不依赖旧 ORM 缓存。另一用户必须使用新 session，关闭后连接不遗留租户上下文。仅 `expire_on_commit=False` 不能让 SET LOCAL 跨事务有效。[SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)、[PostgreSQL](https://www.postgresql.org/docs/current/sql-set.html)。

保存稳定 job/attempt ID；首错及脱敏原始栈、独立重读错误、Provider 关闭错误分别记录。receipt 区分 native job/stage/error、research completion、driver exit、attempt finished 和 evidence sealed。外层停止不强改 partial；已有 attempt 禁止重启，不自动重复已派发/结算调用。未知费用继续服从原账本。

只读页面配置由 `readonly_settings` 统一关闭外调、Worker、自动更新、巡检和发布，并保留事项/增量功能。它约束读取进程配置，不替代生产 Safe Degrade 的服务端写入拒绝。A 给定来源和 B 身份搜索必须分别使用新库、缓存、输入和 attempt。

未来运行要求冻结 checkout + 对应 Python runtime，运行时能读取 Git 绑定和同根模块；不能仅传一个未经核验的 APP_COMMIT。验证镜像只作可追溯构建证据，本轮未发布 registry 制品。

## 固定业务日期

`business_dates.py` 对带时区时间戳先转 Asia/Shanghai 再取 date；date-only 保留日期。job coverage、query plan、抽取上下文、正文时间检查和新报告均使用同一口径。

`2026-09-29T00:00:00+08:00` 与 `2026-09-28T16:00:00Z` 都对应业务日期2026-09-29，窗口为2025-09-29至2026-09-29（首末业务日期包含）。对应 UTC 时间筛选区间是 `[2025-09-28T16:00:00Z, 2026-09-29T16:00:00Z)`；未改变365天合同或 benchmark 分母。

正式证据权限加载后重新构造 subject 时继续保留 job 的冻结日期；不修改融资抽取/验证规则。执行/观察/生成时间仍为真实时间。新冻结输入缺日期、无时区或窗口冲突在外调前拒绝；旧记录缺失/非法则新报告明确说明按生成日列示，读取不改写 coverage。

新模板为 `personal-company-v5`，原 v4 lead 权限继续适用；旧报告正文/hash/幂等键保持原样，不被读取或重试自动重建。新模板版本阻止错误复用旧窗口，但不强行解锁历史失效内容。

## 验证与结果边界

先用真实请求、非 owner PostgreSQL 和正式 Worker 复现跨提交身份丢失及 UTC 日期偏移，再修改。定向回归覆盖 commit/rollback/refresh/SELECT/连接复用/错租户、规则及 Mock 模型完整链、首错与次错、外层停止、重启拒绝、日期边界、旧报告与跨用户拒绝；实际 Next.js 页面显示正确窗口、正文 reload 一致，相关11张业务表完整内容摘要不变。

本地定向97通过/13条件跳过，前端38通过、类型/构建及依赖审计通过；SQLite/PostgreSQL migration check、Ruff/format、生产/Safe Degrade/diagnostic Compose config 和 API/frontend amd64 本地构建通过。完整正式测试、准确 PR/head/tree、远端 Verify 及条件跳过说明以统一私有交付包为准，不以定向测试代替完整 CI。

旧 #109 ENGINEERING PASS、B INCONCLUSIVE/partial、A NOT_RUN、USER_READOUT 固定窗口 FAIL 保留。当前 Mock 工程通过不修订旧真实结果，也不构成召回或业务 PASS。真实项目搜索/抓取/模型调用、生产操作和 MCP/隧道启动均0。

## M3-A 五工具现状

| 工具 | 真实数据来源及可见信息 | 缺失与限制 |
| --- | --- | --- |
| find_company | diagnostic.companies 的法定名、身份状态、ID | 不按 UCC/别名搜索；范围受授权公司限制 |
| get_company_diagnostic | diagnostic.runs，来自 CompanyResearchJob；native status/current_stage/last_error_code、调用及 token | 没有 reference_at/business date；保存字段缺失不能推断成功 |
| get_run_trace | 同一 job 的 disposition、normalized search 元数据、usage ledger | 部分节点用 job.created_at，不代表真实阶段时刻；stage_timestamps=not_recorded |
| get_event_lineage | observation/evidence/support 的历史元数据 | current_support=null、not_revalidated，只表示保存时评估，不是当前有效事实 |
| get_diagnostic_summary | 授权公司有限时间窗的 runs、分页汇总 | 不是全库总量；未记录信息保持 unknown |

现有 MCP 为静态 Bearer + 公司 allowlist/有效期，诊断角色/视图只读、分页快照边界、累计次数/字节、并发/时限和独立审计账本均保留。readOnlyHint 不能替代数据库只读权限。不得新增 SQL/Shell/文件/任意 URL 工具。

指定个人 ChatGPT 客户端实际可到创建 MCP 应用表单，未提交。表单提供 OAuth/无需认证选项，当前服务没有 OAuth；官方说明 ChatGPT 不直接提交自定义 API key，静态 Bearer 兼容性仍 BLOCKED。Platform 隧道权限及组织关联因登录会话不可访问而 UNVERIFIED。[官方认证说明](https://developers.openai.com/plugins/build/auth)、[开发者模式](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)。

Secure MCP Tunnel 的候选架构是香港同一受控边界内的私有 MCP + 出站客户端，Mac 只运维，不需常开；ChatGPT 云端不会因 Mac 能使用 Tailscale 就进入 Tailnet。平台 Read/Manage/Use 权限、ChatGPT 资格、组织关联和业务认证分别验证；Tunnel 不自动解决静态 Bearer 传递。[官方隧道文档](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)。

下一 M3-B 需独立批准虚构库连接、认证兼容适配及临时隧道，先证明实际初始化/list五工具/非空任务读取，再测越权、到期撤销、累计额度/字节、断线/重启及独立停用。不能关闭 Bearer、开放匿名公网或换 API 产品规避指定客户端。M3-C 真实元数据另批公司、字段、时限、累计调用和字节；默认排除正文、持仓、用户、环境、路径和模型原始输出。M3 不依赖 M2 召回达标。

## 下一批准项

新 PR 不自动合并。未来仅使用已见单公司的新 B/A attempt，重新绑定合并 main、配置、来源许可、香港隔离环境、额度/价格和停止条件；旧 attempt 不复活，先 B seal 后 A，是否允许 B 技术失败后独立 A 在新授权前明确。

生产安全补丁另行核准现场只读版本/依赖/入口核验、选择完整候选或旧生产线独立安全补丁、冻结 amd64 制品、备份及 Safe Degrade 回退、健康/登录/报告验收与受控切换。当前源码 Next.js16.3.8 及构建通过不代表生产已更新；本轮不读取或改变生产。研究、Provider、模型、Watchlist、MCP和隧道仍不属于安全补丁授权。

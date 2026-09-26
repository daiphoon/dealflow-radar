# 21 发布前收口与分阶段执行材料

本地工程验证日期：2026-09-25；远端送审进度更新：2026-09-26。基线为远端 `main@3a9680c88530e310629fa240a803c5490460bb2c`，PR #100—#102 已合并，不撤销、不重新拆分。本轮本地验证只使用虚构数据，随后按独立授权推送并创建 PR。生产服务器、真实资料、研究 Provider、模型、MCP 远程入口和 GitHub 设置均未操作。

## 工作区与证据口径

原工作区仍在 `codex/integrated-delivery-review@10e926969e28f071676053aeb6dee93b65363758`；原未跟踪文件 `docs/20-pr-ci-deployment-readiness-report.md` 保留。此次独立 worktree 为 `/private/tmp/dealflow-closeout-20260925`，分支 `codex/release-closeout-20260925`。所有改动以实际远端 main 为父提交。

收口候选已推送到 [PR #103](https://github.com/daiphoon/dealflow-radar/pull/103)，首次远端 head 为 `1f6d86621684ba179cb4170643eff488b5af3ff9`；该提交的 [Verify](https://github.com/daiphoon/dealflow-radar/actions/runs/36151791282/job/108126590567) 已通过。后续文档同步不改变业务代码，合并前仍须核对 PR 当前 head 的最新 Verify。代码按网站收口、诊断收口、评分三个逻辑批次提交，历史报告兼容和实际断线测试另有本地跟进提交；不是重新拆分已合并的 PR。

代码/测试候选：`519052ee7d089ef926c7483a49f2b4b7b864b778`。本地提交依次为 `b9c26c1`（R01/R02）、`eacb54b`（R03/R04）、`9d89aea`（R05）、`2f24944`（保留合法历史报告兼容）、`8384ffc`（真实断线、私有事项与状态目录权限验证）、`519052e`（九类非空业务数据完整字段摘要）。后续仅补交付文档，不改变镜像代码。网站独立送审可选 `b9c26c1`＋`2f24944`；诊断测试复用迁移测试的内容摘要工具，依赖网站测试批次。

完整日志位于此 worktree 的 `data/private/release-closeout-20260925/`，已被 Git 忽略。以下计数分别标明定向与完整测试，不相加冒充完整套件。

## R01—R06 统一交付表

| 编号 | 结论 | 变更文件 | 实际测试与日志 | 剩余限制 | 网站部署 / MCP 启用 / 真实研究验收 |
| --- | --- | --- | --- | --- | --- |
| R01 | 已修复金额小数尾零、重复支持集合及投资方内部“和/与”被拆分 | `backend/app/semantic_content.py`；`tests/unit/test_release_semantics.py`、`tests/integration/test_release_returns.py` | 真函数失败回归 5 失败/11 通过→相关定向 25 通过；等价金额不重复未读、不清零巡检无变化计数；日期、金额、币种、限定词、支持状态变化仍改变版本。`R01-red.log`、`R01-green.log`、`release-targeted.log` | 只规范比较值，不改原文。来源清单变化仍可产生新报告，这是既有合法引用语义。已在旧代码下执行过 0036 的环境必须先核对基线；不静默回填、不统一重新未读 | 0035 环境的本地工程门槛通过；若生产已为 0036，则兼容核验仍阻断网站部署。与 MCP 网络、真实召回验收分开 |
| R02 | PostgreSQL 带旧数据升级与固定旧镜像兼容演练通过；历史报告权限负向测试发现并修复漏洞 | `backend/app/personal_features.py`；`test_release_migration.py`、`test_release_report_permissions.py`；旧夹具版本字符串缩短以符合 PG 列宽 | 213 行虚构旧数据，经真实资料/事项入口建立观测，0035→0036 后旧列逐表摘要一致；非 owner/RLS、跨用户报告映射拒绝、跨键复用仅扣一次、历史报告许可/不可见/缺失证据、多来源少一条、拒绝有历史降级；`R02-runtime-final.log`、`R02-resources.json`、`R02-report-final-targeted.log` | 不修改任何迁移、不新增 0037。旧应用可读公司/报告/旧回执，但不具备新语义回执写入能力；旧历史报告读取也不具备本轮完整撤权保护。真实生产备份恢复和实际线上旧镜像仍未验证 | 本地网站迁移门槛通过；生产现场和备份恢复须另授权。回滚时禁止旧报告读写、回访写入和 Worker，保留 0036 |
| R03 | 诊断不再把保存的支持判断当作当前有效支持；最小列权限通过 | `scripts/bootstrap_diagnostics.py`、`scripts/bootstrap_local_database.py`；`test_release_diagnostics.py` | 真实 PostgreSQL 回归先复现过度承诺/多余表权限；视图所有者只获列白名单，reader 无基础表、角色成员、schema CREATE 和特权函数 EXECUTE。追加处理版本后不改不可变观测；`R03-red.log`、`R03-green.log`、`R04-protocol.log` | `current_support=NULL`、`validity=not_revalidated`，`state` 明确为保存的评估。初始化应用账号收回 PUBLIC 的 watchlist 特权函数执行权，显式授予应用角色；生产若存在多个应用角色应逐一核验/初始化。诊断初始化对额外可执行特权函数或可写 schema 拒绝 | 保持 MCP 关闭时不单独阻断网站；MCP 真实数据前仍需生产权限核验 |
| R04 | 已修复拒绝审计、线程/查询截止时间、额度文件丢失重置和真实容器 tmpfs 配置；接入路径仍阻塞 | `backend/app/diagnostics.py`、`diagnostic_mcp.py`、`deploy/compose.diagnostic.yml`；`test_release_mcp.py`、`test_release_container.py`、`test_release_diagnostic_audit.py` | 五工具真实 SDK/HTTP、游标绑定/过期/篡改、未知对象、九类非空业务表全字段摘要、查询超时、实际 HTTP 断线及协作取消后连接/槽位/字节预占收尾；真实 UID 10001、只读根/凭据、持久状态、资源/日志限制和内部网。`R04-audit-red.log`、`R04-audit-green.log`、`R04-protocol-final.log`、`R04-full-row-digests.log`、`R04-container-red.log`、`R04-container-budget-red.log`、`R04-container-final.log` | 本机 Docker 实际 `NetworkSettings.Ports[8090/tcp]=[]`，虽声明 127.0.0.1 发布端口却未生效；只完成容器内部请求，不能声称宿主机/远程接通。未降低出站隔离。本机真实 HTTP 断线已验证；远程隧道认证头与目标架构下的接入/断线路径仍须独立验收；在途调用只承诺截止时间/DB 超时收尾，不承诺撤权即时中断 | **阻断 MCP 启用**；普通网站不加载诊断 overlay，可独立推进；不替代真实研究质量验收 |
| R05 | 已修复部分评分冒充完整验收、非有限成本/耗时及冻结分母核验 | `backend/app/research_evaluation.py`、`scripts/research_replay.py`；`test_release_scorecard.py`、`test_integrated_research.py` | 缺裁决返回 partial/not_recorded，正式指标为空；子集指标显式命名；冻结 case ID/expected/allowed_date_precision 校验、缺正文仍保留分母、召回上下界、NaN/Infinity/负值拒绝、未知成本和零合格保留不可计算。`R05-red.log`、`R05-green.log`、`release-targeted.log` | 没有冻结 benchmark 的全量裁决也标 unfrozen，不作为正式验收；manifest 哈希仅证明输入固定，不证明人工答案正确。本轮未做真实研究 | 不阻断网站或 MCP 网络；下一轮真实研究还需新留出集、人工裁决、价格/预算和当次调用授权 |
| R06 | 本地发布材料已整理；PR #103 已创建，首次远端 Verify 通过；审查与生产执行后置 | 本文件、有效看板、实施记录 | 本地完整后端 1175 通过/31 条件跳过；前端 30 通过及类型/构建；远端首次 Verify 后端 1174 通过/32 条件跳过、前端 30 通过及类型/构建；main protection 只读导出；迁移/格式/Compose/固定镜像/秘密扫描通过，见下文与 `full-backend-fresh-final.log` | PR 当前 head 的最新 Verify 须在合并前核对；生产实际 SHA、架构、schema、旧镜像和容量未知；本地 arm64 镜像不能直接当成目标服务器镜像 | 网站部署须先完成 PR 审查/合并及独立生产门禁；MCP、真实研究分别授权 |

## 固定运行产物与演练范围

以下为**本地**不可变镜像 ID（未推送 registry，均为 `linux/arm64`）：

| 用途 | 镜像 ID | 来源 |
| --- | --- | --- |
| 候选 API / MCP / Alembic | `sha256:c26a20c2900026fc87d7304f9e621d04b1ffffc8165f1513b9e42e4712883c3e` | 本轮最终运行时代码，131 个源文件 SHA-256 与候选逐一相同；应用/MCP 分进程使用 |
| 冻结旧应用 | `sha256:b086d6e013a58ebbc776778c13cc688a601f95289c9949f6a578e1beec77d7af` | `git archive a42d13cc98d2e144b737709c266fe2066f6455f5` 构建，schema 0035 代码 |
| 前端 | `sha256:7dc255e24a2683556282b94a630b0d2f83876d69e7b147249ce662a22852a4ec` | 未改动前端源码；Node 24.21.0 容器构建 |
| 备份工具 | `sha256:c208fc48a494a120541b3bb78eea25017a894a5d09561c2339415ef8a872c35d` | 既有 Dockerfile；只构建，没有执行真实备份 |

迁移头始终为 `0036`，父迁移 `0035`；与基线相比 `migrations/` 零 diff。迁移仍依赖当前 ORM/投影/版本函数，未来重构必须保留重放兼容。

R04 的补充验证 `R04-protocol-final.log` 为 3 通过/3 SQLite 条件跳过，真实 HTTP 客户端断线后在 4 秒内清理数据库工作；不把这一局域网络测试上界当成生产网络故障或暂停进程下的即时取消保证。`R04-full-row-digests.log` 为 4 通过/3 条件跳过（包含一项迁移回归），确认 Event、Evidence、Observation、FactSupport、ResearchJob、UsageLedger、回执、报告和请求映射九类表均非空，诊断前后所有字段摘要一致。只有迁移前后比对才排除约定新增列；诊断比对不排除语义版本和报告指纹。

同机诊断测试期间，网站容器内部各 5 次 `/health` 和 `/ready` 均为 HTTP 200；实际 HTTP 用时分别为 0.422—14.708ms 和 0.720—8.051ms（不含 Docker CLI 启动），仅为本地轻量探测，不代表持续负载或生产延迟。详见 `R04-site-latency.json`。

固定镜像迁移调用总耗时（含容器启动）1.094 秒，213 行旧数据；宿主直接 Alembic 小样本耗时约 0.04 秒。20ms 周期采样 202 次，未观察到锁等待，最多 5 个该演练库连接。测试后 PG 容器约 461.6MiB、CPU 46.68%；同期有完整回归，数字不是独占资源峰值、更不是生产容量或零停机证明。

## 发布前核验

- 最终后端完整结果：**1175 通过、31 条件跳过，683.24 秒**；全新 PostgreSQL 16 虚构库，固定候选/旧应用/前端镜像参与演练。`full-backend-fresh-final.log`、`full-run-manifest.json` 记录此唯一最终完整结果。前端 `npm test` 30 通过，`npm run typecheck` / `npm run build` 通过；本机 Node 24.12.0，容器 Node 24.21.0，未升级依赖。
- 失败轮保留：旧报告兼容回归曾有 4 项失败，最小修复后相关定向 40 通过/1 条件跳过。另一轮复用测试库产生 4 项 RLS 夹具失败（记录数量残留、旧回执唯一键冲突），没有改断言或产品权限；改为全新数据库按 CI 顺序迁移/seed/bootstrap，完整回归中的这 4 项已通过。中断轮不计入最终完整结果，诊断见 `rls-dirty-database-diagnosis.log`。
- Ruff / format、SQLite 空库往返、PostgreSQL `alembic check`、正式 Compose 配置及 API/前端/备份镜像构建日志保存于私有证据目录。
- main protection 只读结果：PR 必须；`Verify` 必须且 strict=true；enforce_admins=true；force push=false；deletion=false；额外批准数 0。未修改仓库可见性、套餐或保护设置。
- Gitleaks 8.30.1 扫描候选树 347 文件零发现、禁止路径零个；本地可达历史扫描亦为零发现。不是全平台、不可达历史或外部 forks 的无泄漏保证。固定交付提交的复扫 SHA 与结果记录在私有 `final-delivery-manifest.json`。`npm@11.9.0 audit` 返回所有等级漏洞数均为 0，未改依赖。

## 最终 diff 清单

23 个文件；无前端源码、依赖锁文件、历史 migration、真实数据、备份或临时脚本变更。测试代码属于正式回归入口。

```text
backend/app/diagnostic_mcp.py
backend/app/diagnostics.py
backend/app/personal_features.py
backend/app/research_evaluation.py
backend/app/semantic_content.py
deploy/compose.diagnostic.yml
docs/10-implementation-plan.md
docs/21-release-closeout.md
docs/IMPLEMENTATION_LOG.md
scripts/bootstrap_diagnostics.py
scripts/bootstrap_local_database.py
scripts/research_replay.py
tests/integration/test_personal_changes_reports.py
tests/integration/test_release_container.py
tests/integration/test_release_diagnostics.py
tests/integration/test_release_mcp.py
tests/integration/test_release_migration.py
tests/integration/test_release_report_permissions.py
tests/integration/test_release_returns.py
tests/unit/test_integrated_research.py
tests/unit/test_release_diagnostic_audit.py
tests/unit/test_release_scorecard.py
tests/unit/test_release_semantics.py
```

## 分别提交的授权清单

1. **推送/创建 PR：已执行**。仅本分支已扫描的候选提交及必要报告推送到 `daiphoon/dealflow-radar` 的 PR #103；远端 Verify 已通过。不复用旧 PR #100—#102 的 CI 结果；后续提交仍以 PR 当前 head 的最新检查为准。
2. **合并授权**：明确当次 PR、准确 head SHA、当前 main、最新 Verify；按当次审查结果合并。推送授权不包含合并，合并不包含部署。
3. **普通网站生产授权**：通过既有 Mac/Tailscale 管理路径只读核验服务器 SHA/schema/架构/资源/任务/开关/旧镜像；冻结适配服务器架构的镜像 digest 和配置；制作加密备份、双哈希、隔离恢复并核对关键旧数据；维护窗口暂停相关写入口与 Worker；owner 迁移、应用角色初始化、RLS 与 schema check；切换固定镜像，核验 health/ready、登录、匿名拒绝、公司页、回访并发、报告复用/额度与来源撤权；观察后备份和回执。**不加载 `compose.diagnostic.yml`，不安装生产诊断账号/视图。** 若实际 schema 已为 0036，先停止并审查旧语义基线兼容方案，不直接覆盖回执。
4. **MCP 独立授权**：先解决并重测宿主机回环接入路径与出站隔离；再申请远端虚构数据、指定客户端/认证/隧道验证。真实数据外发还需单独列明 public_metadata、公司 ID、有效期、累计调用/字节上限及撤销方案。不假定网页账户已具备接入资格，不使用匿名公网/Funnel 后备。额度文件首次初始化需明确创建 `{}`、归属 UID/GID 10001、0600，后续缺失不得自动重置；配置与状态目录分离。
5. **真实研究独立授权**：冻结新留出集及分层预期事项/日期精度/人工裁决；旧四家仅作回归。现场核价/配额，沿用每公司 4 搜索、8 网页、3 模型、16000 输入/2000 输出、180 秒等硬上限；授权公司、总预算、停止条件和归档位置明确后才可调用。没有授权不启动关注巡检、模型或真实 Provider。

## 回滚步骤

1. 先关闭新写入口、刷新/研究/巡检 Worker，保留失败和审计记录；本轮演练未连接生产。
2. 仅切回已在隔离库验证过且与生产目标架构匹配的旧应用固定镜像。现场旧镜像若不同于本次演练基线，先补兼容验证。
3. **保留 0036，不执行 downgrade，不删报告映射、回执、新观测。** 旧应用回访 POST 不能确认新的 semantic_version；回滚期间禁用该入口以及报告写入和历史报告正文读取。只允许已现场验证的登录/公司只读页面，避免旧报告路径失去本轮权限保护。
4. 验证只读健康、权限与业务旧行；重新部署修复版时恢复新代码的回执/报告能力，不批量重写历史。
5. 整库恢复会丢失备份之后的写入，必须另获明确授权；本轮没有执行真实备份恢复。

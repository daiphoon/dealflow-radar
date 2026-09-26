# M1 Safe Degrade & Production Release Readiness Report

更新：2026-09-26。本文是 M1 唯一发布就绪报告；虚构数据的逐表完整摘要保留在 Git 忽略的 `data/private/m1-safe-degrade-20260926/digests-amd64.json`。**本报告和 PR 的工程验收不代表已经部署。**

## 合并基线与交付状态

| 项目 | 结果 |
| --- | --- |
| [PR #103](https://github.com/daiphoon/dealflow-radar/pull/103) | 严格核对 head `a52b06e09acf148537fe1e1cf117e7cf30e150b7`、base `3a9680c88530e310629fa240a803c5490460bb2c`、required Verify 成功且 `CLEAN / MERGEABLE` 后，按保护规则使用普通 merge commit 合并 |
| main merge SHA / tree SHA | `e1fbfcd6f6c039a6834d426840c3bf38abeced05` / `7bb70e6dc238f9a5a8a122458a9833309dceaeca`；merge 的两个父提交分别为批准的 base 与 head，文件树与 head 相同 |
| 合并后检查 | [main 的 Verify](https://github.com/daiphoon/dealflow-radar/actions/runs/36209264154) 成功；代码合并与生产部署分开 |
| M1-SafeDegrade | 需要代码及部署配置修改，作为单独 PR 审查；主题只含安全只读降级和恢复闭环 |

## 安全控制与可用范围

安全门独立于新旧应用版本：当前安全控制镜像执行数据库角色权限收缩，现有 Caddy 通过单独的 Compose overlay 加载只读 allowlist。安全控制镜像与应用镜像分别固定；切换到冻结旧应用时仍使用本轮安全控制文件和镜像，不依赖旧应用理解新环境变量。普通 `tools` profile 不会启动恢复权限工具；降级 overlay 将普通 migrate、bootstrap-role 及两个 Worker 的命令强制设为 `/bin/false`，避免误用常规工具重新开放写权限。退出降级须先停止旧 API/前端，再通过单独的 `safe-degrade-restore-role` 服务显式恢复原应用角色权限、切回新镜像和正常 Caddy 配置；Worker 单独恢复。

| 降级期间 | 入口或数据库结果 |
| --- | --- |
| `GET/HEAD /health` | 200，正文及响应头明确标记 `maintenance_read_only` |
| `GET/HEAD /ready` | 仅向旧 API 的数据库就绪探针转发，保留只读维护响应头；失败仍为失败 |
| `GET/HEAD /companies/{UUID}` | 仅允许已验证的公司详情深链；实际旧前端在虚构 `0036` 数据和只读应用角色下渲染成功。需要已有有效会话；页面中旧操作按钮即使存在，也无法提交 |
| `GET/HEAD /_next/static/*`、`/favicon.ico` | 仅必要静态资源 |
| 登录、首页查询、报告列表/正文、证据详情、Watchlist、回访/报告/刷新/研究接口及其余路径 | 返回 503 `maintenance_read_only`；网页登录与令牌刷新暂不放行，避免表单看似可用却无法完成。带旧 `result=` 成功反馈的深链也返回 503 |
| 所有 POST/PATCH/DELETE/PUT，包括 Next Server Actions | 在 Caddy 进入旧前端前统一返回 503；不按 GET 方法一概放行 |
| 旧 API 内部意外写入 | 现有应用角色的表 DML、序列写权限和特权函数执行权被撤销；不改 schema `0036`，额外特权函数或角色继承会使收缩事务失败，拒绝进入可服务状态 |

生产已有会话由 CloudBase 身份服务逐次核验；本地使用虚构 Demo 身份验证了同一 API/RLS 读取路径，**没有在生产真实身份服务上执行登录或会话测试**。降级期间不开放新登录，既有会话过期后显示维护状态。这里的“Provider 零调用”指研究搜索、模型和商业数据 Provider；既有会话的身份核验仍可能访问 CloudBase，下一次生产批准须明确接受这项只读认证依赖或选择完全静态维护页。

## 跨版本演练与数据不变量

隔离 PostgreSQL 16 在 schema `0036` 下使用虚构公司、用户、事项、证据、观测、报告、回访及历史任务。测试先以新版生成回访与报告，再撤销应用角色写权限；冻结旧 API 和旧前端的 `linux/amd64` 重建镜像实际读取公司页，Caddy 对报告、关注、刷新、认证写入和并发 Server Action 请求返回维护状态；之后显式恢复权限，新版回访和相同报告复用成功。Caddy 另完成正常入口 → 安全入口 → 正常入口的实际容器切换。只读阶段未启动 Worker、巡检或研究容器，所有研究开关关闭，虚构用量账本中的外部调用数保持 0。

以 PostgreSQL `to_jsonb` 的**完整行内容**排序后计算 SHA-256，而非只比较行数。42 张表在进入安全模式前与旧版页面、恶意/误操作及并发请求之后的逐表 `(行数, SHA-256)` 完全相同；下列重点表各有一条非空虚构记录，具体完整摘要见私有回执：

| 表 | 降级前行数 | 降级后摘要 | 恢复后约束 |
| --- | ---: | --- | --- |
| `personal_event_view_receipts` | 1 | 相同 | `first_seen_at` 与语义版本未改写 |
| `personal_company_reports` / `personal_report_requests` | 1 / 1 | 相同 | 原报告未重建，同一请求复用原报告 |
| `refresh_jobs` / `company_research_jobs` / `usage_ledger` | 1 / 1 / 1 | 相同 | 历史记录未变，无新增任务或外部调用 |
| `personal_watchlist_items` / `company_watch_schedules` | 1 / 1 | 相同 | 无关注或巡检写入 |
| `event_observations` / `event_evidence` / `event_facts` / `event_fact_supports` | 各 1 | 相同 | 不可变观测及事实支持未改写 |

显式恢复正常能力后，42 张表中只有 `personal_company_view_states` 因合法回访更新；所有其余表（包括 events 及 receipts 的 semantic_version）仍与基线一致。不同租户的关注记录在只读角色下继续被 RLS 隔离。额外可执行的 `SECURITY DEFINER` 函数、其他用户 schema 的写权限会使安全转换失败并回滚，已由真实 PostgreSQL 负向测试验证。

本地最终演练对 42 张表的 `(行数, SHA-256)` 映射按键排序、使用无空白 JSON 再做 SHA-256：

| 阶段 | 完整映射 SHA-256 | 变化表 |
| --- | --- | --- |
| 降级前新版 | `345bf8c3290d696af4283f6b1823672b3813a983799d27bcc14e227560488600` | 基线 |
| 旧版只读与攻击尝试后 | `345bf8c3290d696af4283f6b1823672b3813a983799d27bcc14e227560488600` | 无 |
| 显式恢复新版回访/报告复用后 | `29532b7caf96d03eae3957329f4b3f8a4096c707a51a17d507056bd3b703727b` | 仅 `personal_company_view_states` |

回执属于虚构隔离数据；每次随机夹具执行的哈希会不同，验收比较同一次演练的前后值。CI 还将完整逐表回执保存为 `m1-safe-degrade-synthetic-digests` 附件，不上传真实数据、数据库、密码或本地私有目录。

## 实际验证与复现

| 验证 | 本地结果与范围 |
| --- | --- |
| 行为失败回归 | 首次因缺少独立安全门/角色收缩失败；后续额外 schema 写权限及通用 tools 隐式恢复权限分别失败，最小修正后通过 |
| 完整后端 | `1175 passed, 33 skipped`，648.51 秒；虚构 PostgreSQL 与非 owner 应用角色/RLS。全量完成后新增的上述两个负向断言及容器恢复由最终定向回归覆盖；远端 CI 会执行最终文件全量 |
| 最终 M1 跨版本定向 | `2 passed`，8.53 秒；真实 Caddy、旧 amd64 API/前端、新 amd64 API 恢复、42 表内容摘要与 30 并发拒绝。无 Worker 启动，无研究/模型调用；用量账本保持外部调用 0 |
| 前端 | `30 passed`，typecheck/build 通过；本 PR 不修改前端源码 |
| 迁移 | SQLite upgrade/check/downgrade 通过；PostgreSQL upgrade/check、RLS 和 `0036` 虚构数据演练通过。无 migration diff，历史 `0035` / `0036` 不变 |
| 风格/部署 | Ruff/check + format（213 文件）通过；普通生产和安全 overlay Compose config 通过；新旧 amd64 镜像构建通过；API/前端无宿主发布端口，公网只能经 Caddy |
| Secret / private 路径 | 公开文件树 Gitleaks 零发现；本 PR 不含私有目录、日志、数据库、备份或临时脚本，`git diff --check` 通过 |

复现入口：先建立本机一次性 PostgreSQL 并配置 `DATABASE_ADMIN_URL`（不接受远程主机）；普通全量 `pytest -q` 会执行 PostgreSQL 角色/RLS 用例，未显式启用的 Docker 分支按条件跳过。配置 `M1_DOCKER_GATE_TESTS=1`、`M1_OLD_RUNTIME_IMAGE`、`M1_OLD_FRONTEND_IMAGE`、`M1_NEW_RUNTIME_IMAGE` 为已构建的固定 `sha256:` 镜像 ID，再运行 `pytest -q tests/integration/test_m1_safe_degrade.py`；`M1_EVIDENCE_PATH` 可指定 Git 忽略的本地回执路径。镜像固定分支是额外跨版本验收，不为追平全量跳过计数重跑套件。CI 使用同一路径，在冻结旧提交源码构建完成后执行独立跨版本步骤。

## 平台镜像与剩余限制

现场只读预检确认服务器是 `linux/amd64`、当前旧应用对应 `ff174998893a1a868b95e469817c1f1199221e89`、数据库是 `0035`，诊断 MCP 未启用。现场旧 API/前端镜像 ID 分别为 `sha256:e2289b6fa394c6eadc6be1be902dd2cd247287a6e9fe2de5d4babbcfe9c3c12a` 和 `sha256:16777e7fb9dd16416b41a5a963e869eb7fcfaff7163424bf17cff72b9ec9d43a`。本地从同一旧提交源码重建的 `linux/amd64` API/前端镜像 ID 分别为 `sha256:4d03b8c60e55145ca336e93eac9e19c7d548df95f1945fa7949ae8d37157510b` 和 `sha256:a2307595320e23a8a9e33aa8f575e2f2ee76c1258ca139f7577de0ba154e7ff8`；**源码版本相同不等于镜像字节相同**。本地 arm64 历史镜像不充当目标平台制品。

M1 PR 的最终 SHA、`linux/amd64` 新 API/前端镜像 digest 和安全控制镜像需在 PR 通过 CI、合并后绑定为同一清单；本轮不下载或运行生产现场旧镜像。真实生产备份、隔离恢复、`0035→0036`、权限初始化、镜像切换和健康/用户验收均尚未执行。

本地 M1 候选的 amd64 新 API（亦可作为独立安全控制镜像）ID 为 `sha256:9b20cd62a1e731ff6f57414815b0f6b478919080e8fc0bc1d383de348c2660f3`，新前端为 `sha256:845d5bad1834c543c1afea88a88ea0ab3680e83f8a8ca515d6c444bf7901032c`。这些是本地 Docker 镜像 ID，尚未发布到 registry，不冒充可拉取的生产 digest；最终 main 还不存在于本 PR 阶段。制品清单必须分别绑定代码 SHA、文件树、平台、可拉取 digest、安全配置哈希与现场旧镜像 ID，任何一项变化重新核验相应证据。

| 尚存闸门 | 是否阻断当前工程 PR / 生产发布 |
| --- | --- |
| 独立 M1 PR 最终 head 的 Verify 与审查 | 必须通过后才能申请该 PR 的精确合并；本轮不自动合并 |
| 最终 main 与可拉取 amd64 固定制品 | 合并后绑定，阻断生产切换；不使用本地 arm64 旧制品 |
| 生产实际旧镜像字节与真实会话 | 同源码 amd64 重建已有证据；现场实际镜像、CloudBase 会话核验属于获批窗口验收。未通过则静态维护，不开放旧动态公司页 |
| 真实备份、隔离恢复、迁移与发布操作 | 尚未授权/执行，阻断生产部署；不影响本地虚构工程结论 |
| MCP、隧道、真实研究和关注自动巡检 | 本轮不启用，不作为主网站安全降级 PR 的新增优化项 |

## 下一次集中生产发布批准所需动作

1. 明确目标主机、最终合并 SHA、`linux/amd64` API/前端/安全控制镜像 digest、现场冻结旧镜像 ID、维护时间窗和停止条件；对比当前 schema、服务角色、任务、开关和容量无漂移。
2. 先做加密备份、异机副本与双哈希、隔离 PostgreSQL 恢复；冻结旧镜像和安全 Caddy 配置。停公网入口，再停 API、前端、所有 Worker/Cron 与写入口，确认在途任务清空。
3. 所有应用和 Worker 保持停止。若现场仍是 `0035`，经授权先用最终新版 migrate / bootstrap-role 升至 `0036` 并初始化合法应用角色；若已为 `0036` 或有漂移，先审查语义基线，不直接重跑回填。随后才用固定安全控制镜像运行 `safe-degrade-control` 收缩权限，再以 `python -m scripts.safe_degrade_database verify` 核验只读并建立完整摘要；此后禁止普通 bootstrap/migrate，始终保留 `0036`。
4. 优先在从备份恢复的隔离库，用**现场实际旧 amd64 镜像**配合安全 overlay 演练公司只读页、拒绝列表、RLS、42 张表内容摘要及零研究调用。生产启动旧服务前，必须先停入口与所有写服务、成功收缩并核验角色、核对开关与无在途任务；任一步失败维持拒绝服务，不开放普通入口。恢复工具不与收缩工具同时运行。
5. 用最终新版固定镜像切换、核验 health/ready、登录、匿名拒绝、公司页、回访并发、报告复用/额度、证据撤权和资源；失败则按“停入口与 Worker → 权限收缩 → 安全 overlay＋旧镜像”降级。恢复时先停旧 API/前端，切回修复新版，再显式恢复应用角色写权限和正常入口；Worker 只在单独核验后显式启动。最后核对原有行摘要、备份与回执。

本阶段只完成本地虚构数据工程演练与 PR 审查，不授权上述任何生产动作，也不启用 MCP、隧道、真实研究 Provider、模型或关注自动巡检。M1 PR 验收通过后，应提交这份**集中生产发布授权清单**，不继续扩大业务功能范围。

# 实施记录

## 2026-09-15：E4.4 按组提前回退与网页请求额度评估

- 任务/关键文件：按批准修改 `web_research_service.py`，短查询增量任务采用 `same-query-readable-fallback-v2`；已失败组提前回退，备用文章与已经排队的同 URL 优先读取，服务等固定页面在候选截断前后均后置，处理过的队列不改写。新增 `test_group_readability_fallback.py` 并允许既有 fixture 注入 Mock Fetcher；同步 README、看板、增量计划、来源/成本/测试说明，无迁移、依赖或前端修改。
- 实际命令：`git switch -c codex/e44-group-fallback`；定向 Pytest 先复现旧序列失败，再用 Mock HTTP 和本机临时 PostgreSQL 非 owner 角色执行新/旧回退与短查询回归，最后执行既有 Worker/增量/预算/关注/抓取回归；私有 `run_tests.py final/related-final` 保存最终日志并清理临时库容器和凭据。`replay_recorded_queue.py` 对比部署版与新函数，真实封存输入零网络/零生产写入；Ruff、格式、本地链接/公开差异隐私/原材料 SHA-256 和 `git diff --check` 验证。
- 结果：最终定向 72 通过、相关 199 通过/9 个既有条件分支跳过，共 271 通过/9 跳过；新增 16 个 SQLite/PostgreSQL 用例全部通过，保留既有 Starlette 弃用提示。8 次 HTTP 的 Mock 场景先读取替代融资正文，12 次场景再读取服务页；重复排队 URL 只读一次，结算后中断不重复搜索，初始事实与权限保持。封存真实队列在已用 6 次时可提前选择融资回退，但未执行新的真实搜索/读取，不宣称内容召回已通过。
- 边界/停止：当前默认配置和生产上限保持 4 搜索/8 HTTP/2,000,000 字节/180 秒/模型与金额上限 0；12 HTTP 是下一单公司运行建议，尚未启用。原 Excel、固定基准及旧封存结果不变，本轮产品外部调用、生产访问、合并和部署为 0；工程 PR 基于未合并 PR #87 交付后停止，下一合并部署和受控复测待确认，EV14/M6B 未通过保持。私有回执在 `data/private/e4-validation/20260915-e44-group-fallback/`。

## 2026-09-14：E4.4 部署后的单次线上维护验证

- 任务/关键文件：按已批准排程于 21:45 自动续作，正常冷却后通过原生流程新建一个申请；在已部署 `2074d81` 执行原公司两条短查询。同步 README、有效看板和本记录；私有回执位于 `data/private/e4-validation/20260914-e44-deployed/`，未修改产品代码或执行第二次研究。
- 实际命令：`e4_preflight.py`、`run_precheck.py`、`run_phase.py preview/create/business-run`、`capture_case.py before-retest/sealed-discovery/after-retest`、`verify_close.py`；Chrome 配额/公司详情核对、非 owner 只读公司详情检查；`diagnose_fallback.py` 无网络回放，14 组历史记录比较；SSH `post-backup.sh`、SCP 下载密文及两份 SHA-256、本机 Docker age 解密流哈希校验；自动续作工具暂停并读回配置，私有文档/输入哈希验证。
- 结果：本次 Worker 11.624 秒，百度 2 搜索、博查 0、HTTP 8 次/257,333 字节，模型 0、免费配额内现金费用 0；新增 11 条账本，原 139 条及原目标记录保持。融资候选全部失败，取得的访谈/服务页仅作内部文档；新增事件、观测和可见线索均为 0，正文支持输出 `0/1`。原申请人可读 5 条初始资料而不可读导入原件，Chrome 结束状态和日期口径正确；配额 48→46/966→966 对账、关闸、无活动任务及自动续作暂停均通过。复测后加密备份上传 COS、SSH 副本双哈希通过；本次未重复隔离恢复或 COS 直接下载。
- 未解决事项/停止：离线复现证明全局队列使融资回退延迟到服务页耗尽最后 2 次 HTTP；内存后置该页即可在原上限内选择融资回退，未执行真实备用调用。下一仅建议按组提前回退与非事件页面后置，待批准后交付工程 PR；不把诊断或旧替代正文计作本次交付，EV14/M6B 未通过，原盲测不改写，不扩样或进入 E5。

## 2026-09-14：PR #85 / #86 合并部署与单次线上验证排程

- 任务/关键文件：按批准顺序合并 PR #85，PR #86 调整到 `main` 后以 `aa03fc2` 完整 CI 验收，合并部署 `2074d81`；同步 README、有效看板和本记录。原公司正常冷却于当天 21:44:05 结束，依据负责人明确选择安排 21:45 当前任务自动续作一次，私有交接固定原申请人、两条短查询、免费预算和终态停止条件；无产品代码改动。
- 实际命令：GitHub 连接器精确提交合并、`git rebase --onto`、`git diff --exit-code`、`git archive`；SSH/SCP 上传已检查的控制脚本，Compose build/preflight/backup/migrate/bootstrap-role/up；Mac age 双哈希、隔离 PostgreSQL 恢复、`alembic upgrade head/check`、非 owner 只读验收；`capture_case.py before-deployment/after-deployment`、Chrome 现有公司页面核对；Codex 当前任务自动续作工具创建单次任务并读回配置。
- 结果：最终 CI 后端 809 通过/16 跳过，前端 24 通过，迁移、部署配置与生产镜像均通过；12:06（北京时间）新版本健康上线，数据库保持 `0033`，13 项运行开关关闭、5 项金额上限零。初次切换因正常认证审计追加而自动中止，原审计摘要验证无改写；补做最新备份和恢复后切换成功，全表摘要、权限和健康检查通过。原目标 14 组记录不变，页面 5 条初始资料及日期口径保持；私有 `validate_documents.py` 检查 53 个本地链接、5 份原输入/封存哈希、公开差异隐私和 `git diff --check` 全通过。
- 停止/未执行：产品新搜索、网页抓取和模型调用为 0；新申请尚未创建，已安排的 21:45 单次线上验证未执行，原 `0/1`、轮次缺口及 EV14/M6B 未通过保留。排程不会扩充样本或开启常驻研究，异常先查原任务与账本，不自动派发第二次；部署和排程回执留在 Git 忽略的私有目录，旧版本保留用于应用回退。

## 2026-09-14：E4.4 短业务查询与替代正文交付

- 任务/关键文件：依批准实施两组短查询及冻结版本、博查最近一年参数；从失败后的可读正文定位并修复页头日期、双语品牌和重复融资提及识别，财经频道加入既有排序。关键代码为 `research_subject.py`、`research_coverage.py`、`web_research_service.py`、`web_search.py`、`source_fetcher.py`、`financing_events.py`、`financing_storage.py`；关注巡检保持旧查询。同步计划、看板、来源和测试说明，无迁移或新依赖。
- 实际命令：相关 Pytest（增量规则、搜索 Provider、安全抓取、覆盖、增量落库、短查询、读取回退、预算路径与关注巡检）；`ruff check`、`ruff format --check`、`git diff --check`；私有 `prepare_isolated.py`、`remote_canary.py`、`diagnostic_fetch.py`、`secondary_fetch.py`、`test_recorded_body_delivery.py`、`production_snapshot_after.py`，Chrome 配额核对。原始输入/输出、后续诊断、回放分别封存于 `data/private/e4-validation/20260914-e44/`，未执行附件命令。
- 验证结果：最终相关回归 253 通过/3 条件跳过，真实正文经 SQLite/PostgreSQL 非 owner RLS 落库和读取回放 2 通过；Ruff 和格式通过。5 条初始资料及快照不变，2 条新线索可读、重试不重复、普通用户不能读受限底稿。隔离 Worker 为 3 搜索/8 HTTP/200,556 字节/129.986 秒，原始正文支持输出 0/1；随后复用已发现 URL 取得 2 篇融资正文，另耗 4 HTTP/120,834 字节/3.137 秒。合计 3 搜索/12 HTTP，模型 0、免费额度内现金费用 0。配额百度 50→48、博查 967→966 对账一致；线上账本摘要和原 Excel/基准/历史输出哈希不变，13 开关关闭、5 金额上限为零、无临时联网容器和凭据文件。
- 未解决事项/停止：新正文没有明确轮次或实际发生日，不能自动关联为人工 B 轮事项，保留待核实线索；同源转载不算独立确认。后续正文与回放不回写原盲测 0/1；修复未部署，线上新申请与 EV14/M6B 仍待验收。交付 PR 审查后停止，不扩样、不启动巡检或 E5。

## 2026-09-13：PR #83 / #84 合并部署与 E4.3 新复测

- 任务/关键文件：按批准合并 PR #83，PR #84 调整到 main 后以 `024c38a` 完整 CI 验收，合并并部署 `de9cb60`，数据库保持 `0033`；通过原生申请更新对原公司执行一次同组回退复测。同步 README、有效看板、增量计划和本记录；本轮无新增产品代码。
- 实际命令：`gh pr merge 83 --squash --match-head-commit …`、`gh pr merge 84 --squash --match-head-commit …`、`git rebase --onto origin/main …`、`git diff --exit-code 024c38a origin/main`；私有 `backup.sh`、`restore_local.py`、`prepare.sh`、`deploy.sh`；`run_phase.py preview/create/business-run`、`capture_case.py sealed-discovery`、`verify_close.py`、`post-backup.sh`。备份、查询、对照和控制脚本仅存放在忽略目录，未执行附件命令。
- 验证结果：PR #84 后端 783 通过/16 跳过，前端 24 通过，类型/构建、依赖审计及生产镜像通过；合并树一致。加密备份/本机隔离恢复/全表摘要、非 owner RLS 及上线健康检查通过。新研究耗时 17.114 秒、搜索缓存 2 次、博查新增 1 次、HTTP 5 次、34,134 字节、模型 Token 0、免费额度内现金费用 0。旧 133 条账本哈希不变，累计 139 条/124 次外部调用；原样本、原任务和 5 条可见初始资料保持。文档差异、66 个本地链接、公开差异隐私及三份输入哈希检查通过。
- 结论/停止：正文可读 1 份为官网简介，与基准融资无关且无可靠来源发布日期；融资组备用结果 10 条均不合格，新增事件 0，新的基准正文支持输出仍 `0/1`，EV14/M6B 未通过。Chrome 展示与事实状态一致；13 项运行开关、两份环境文件各 19 项开关关闭、5 项金额上限为零、无活动任务/Worker。下一仅提出短业务查询对照，未执行新搜索、模型或其他公司导入。私有回执见 `data/private/deployment/pr83-pr84-20260913/`、`data/private/e4-validation/20260913-e43-retest/`。

## 2026-09-13：E4.3 正文全失败后的同组备用搜索修复

- 任务/关键文件：`web_research_service.py` 保留原两组查询，记录查询及跨组 URL 归属，在正文全失败后追加同组备用源；复用缓存、预占与结算，保留已处理候选及证据。`source_fetcher.py` 共用 robots 规则判定；增量按需任务跨步骤共享 180 秒时限并在派发前检查。新增 `test_readability_fallback.py`，同步计划、看板、来源/成本/测试和架构说明。
- 实际命令：`git switch -c codex/e43-readable-fallback`；仅本机回环端口启动临时 PostgreSQL 16，沿用每例独立库和非 owner 角色的 fixture；`.venv/bin/pytest -q tests/integration/test_readability_fallback.py --tb=short --show-capture=no`，再合并来源抓取、预算、Worker、增量、来源覆盖和关注研究共 10 份测试文件做最终回归；`.venv/bin/ruff check backend migrations scripts tests`、对应 `ruff format --check`、`git diff --check`、本地 Markdown 链接/隐私及三份原输入 SHA-256 检查。使用 `gh pr view 83` 核对当前文档分支和 CI；工程分支以该未合并文档 PR 为基线独立提交。
- 结果：最终相关回归 **228 通过/9 个既有条件分支跳过**，其中本切片 **48 项全部通过**，包括非 owner PostgreSQL、成功/失败结算后中断、未知支出暂停、缓存及取消、搜索/抓取/字节/候选/时限边界、原查询复用和初始事实保留。首次测试发现虚构正文过短及取消 fixture 共用事务，分别改为满足读取门槛的虚构正文和独立取消会话，未放宽产品断言或权限。Ruff 148 文件格式、差异及文档检查通过；保留既有 Starlette 弃用警告。测试日志及检查回执在忽略目录 `data/private/e4-validation/20260913-e43-fallback-fix/`。
- 限制/停止：未新增依赖、迁移或前端改动；本轮未重跑前端/浏览器，完整 CI 以工程 PR 的实际提交为准。产品真实搜索/抓取/模型调用和生产访问、合并、部署均为 0，原 Excel/固定基准/封存输出哈希未变，原 0/1 及 EV14/M6B 未通过保持不变。备用源真实可读性仍未验证；交付未合并 PR 后停止，后续合并部署与原公司新一轮复测按明确批准执行。

## 2026-09-13：PR #81 / #82 合并部署与 E4.3 单公司对照

- 任务/关键文件：按批准顺序合并 PR #81、重排并复验 PR #82 后合并；香港部署 `fb95ec4`，迁移 `0031 → 0033`。原单公司先身份导入及业务发现，封存后比对固定基准，再导入 5 条初始资料并离线回放。同步有效看板、增量计划、ADR-0022 状态及运维说明。
- 实际命令：GitHub 连接器精确提交合并；`git rebase --onto`、验收树 `git diff --exit-code`、`git archive`；SSH/SCP 传输完整脚本后执行 Compose build/preflight/backup/migrate/bootstrap-role/up；Mac age 双哈希及隔离 PostgreSQL 恢复、`alembic upgrade head/check`、非 owner 只读验收。私有 `run_phase.py` 执行原生导入预览/应用及一次临时 Worker；`capture_case.py` 封存，`replay_maintenance.py` 作无 HTTP 的 SQLite 领域回放，`verify_close.py` 检查收尾；Chrome 线上页面与供应商免费配额核对。
- 结果：PR #82 最终 CI 后端 735 通过/16 跳过、前端 24 通过，部署迁移及原数据/权限/健康通过。独立线索 1/1，正文支持输出 0/1；5 候选中 4 个 robots 拒绝、1 个超时。2 次百度搜索、4 次 HTTP、8,997 字节、模型 Token 0、配额内现金 0。初始资料 5 条可见，原日期和来源标签正确；隔离回放和导入重试保留原事实/快照。
- 收尾/未通过项：旧 123 条账本摘要及原身份失败不变，累计 133 条/118 次历史外部调用；临时 Worker 退出、无活动任务、全部常驻开关关闭。部署前后均有加密 COS 备份及本机副本，私钥留 Mac；本轮未重复 COS 直接下载演练。固定查询边界拒绝的事件式来源回查为零调用，未验证该路径；无正文导致真实新增/更正未验证，EV14/M6B 未通过。不追加搜索、不扩样、不启动后置工程；建议下一仅修复正文全失败后的同组备用检索。回执在两份私有目录，真实资料不提交 Git。


## 2026-09-13：E4.1 审查提交与 E4.2 本地业务增量

- 任务/关键文件：E4.1 提交 `f3e6a31` 并更新 PR #81，CI 通过，未合并。E4.2 工程提交 `16891e0` 至独立 PR #82，补充共享别名检索/正文定位、融资字段及事项观测、按需准入与页面对照；`0033` 限定平台管理员追加观测，默认关闭新开关。同步看板、计划、ADR 状态及运维/数据库说明。
- 实际命令：`git fetch/push`、`gh pr view/edit/checks`、`gh api` 与 GitHub 连接器创建 PR；隔离 PostgreSQL 16 迁移/种子/非 owner 初始化及 `pytest -q --tb=short`；新规则、集成、Worker CLI 和启用/关闭分支预算/取消定向 Pytest；Ruff、`npm test`、`npm run typecheck`、`npm run build`；本机 API/前端与虚构资料页面验证。
- 结果：完整回归 726 通过/16 跳过；前端 24 通过，类型和构建通过。页面验证发现中文“两亿元”未识别，补规则及近似金额测试；新路径的预览、并发、撤证及预算/取消收尾复验 50 通过/1 跳过。原记录和快照未被转载/更正覆盖；私有原件与普通用户权限隔离。只保留既有 Starlette 弃用警告。
- 限制：仅 Mock/虚构资料回放，未调用真实产品搜索/抓取/模型或访问生产；原 Excel、固定基准和历史回执保持不变。E4.2 独立交付审查，不合并/部署或自动启动 E4.3；真实引擎效果和动态页面仍未验证。日志和回执留在忽略目录 `data/private/e4-validation/20260913-e42/`。

## 2026-09-13：E4.1 审查提交准备

- 任务/关键文件：按批准将 E4.1 和已确认 ADR-0022 文档纳入现有 PR #81；更新有效看板，后续 E4.2 独立分支实施。
- 实际命令：`git fetch origin`、`gh pr view 81`、定点差异审查、私有交付检查脚本、Ruff 和 `git diff --check`；本轮提交后由 PR CI 验证，旧 CI 不代表新提交通过。
- 验证依据：沿用 E4.1 本地验收回执；本轮重新检查修改范围、文档链接、私有输入哈希和敏感信息。提交结果与新 CI 状态由 PR 留存。未合并、部署、访问生产或执行产品外部调用。

## 2026-09-13：E4.1 负责人确认资料准入与初始化导入（本地）

- 任务/关键文件：新增 `curated_workbook.py`、`curated_import.py`、`curated_publication.py` 与本地 CLI，复用公司/别名、研究导入、原件/提及、事件/证据、观测、人工决定、快照及账本；`0032` 增加负责人依据、选择键及可空联网检查时间。同步页面/模板报告、状态轮询、数据库说明、运维手册和有效看板；未覆盖原文档改动或执行附件指令。
- 实际命令：`uv add 'openpyxl>=3.1.5,<4' 'defusedxml>=0.7.1,<1'`；Ruff；隔离 PostgreSQL 16 上 `alembic upgrade head/check`、虚构种子及非 owner 角色初始化；带两个本地数据库变量的 `pytest -q --tb=short`；导入定向 `pytest -q tests/unit/test_curated_workbook.py tests/integration/test_curated_import.py --tb=short`；`npm test`、`npm run typecheck`、`npm run build`。私有真实选样脚本验证 CLI 默认预览、正式导入、重复执行和两种用途分库；浏览器检查本地公司页并生成一份固定模板报告；收尾复验申请状态 2 通过，16 份文档的 128 个相对链接/13 个锚点及 7 份原输入哈希通过，临时页面、API、前端及 PostgreSQL 容器已关闭。
- 结果：完整后端回归 696 通过/15 跳过；收尾定向 33 通过/2 跳过（SQLite 不适用的 PostgreSQL 权限/并发分支）；前端 23 通过，类型和构建通过。既有 Starlette 弃用警告保留。迁移往返/无漂移、带资料拒绝降级、预览零写入、原子回滚、并发幂等、追加更正、待核/取消/冲突隔离、普通用户/跨租户权限及原消耗保存通过。
- 真实本地资料：沿用原选样，独立 `identity_only` 库 0 条业务事件；`initial_data` 库 5 条历史融资初始资料、2 条保留月份，来源未重读、未自动评分。页面不再把已发布原件当待核线索，固定报告可读。原 Excel、原事件基准与既有私有输入不变；回执、日志、预览及原件均在 Git 忽略目录 `data/private/e4-validation/20260913-e41/` / `data/private/research_imports/`。
- 停止/限制：本地代码交付，未提交/合并/部署本轮代码，未连接生产或改开关；产品搜索、网页读取和模型调用/Token 均为 0，资料依赖已获授权的人工确认。独立发现召回、未来真实增量与 EV14/M6B 尚未通过；不启动 E4.2/E4.3/E5。支持当前表结构与单公司明确落库，不建设通用上传或批量联网。

## 2026-09-13：负责人确认初始资料与 E4 增量研究方向固化

- 任务/关键文件：按负责人明确要求接受已复核表格作为初始资料，采用轻量准入及按需增量更新。新增 ADR-0022，同步 AGENTS、ADR 索引及旧决策适用说明、产品/架构/来源文档、README、唯一看板和增量计划 1.1；保留本轮开始前的四份未提交文档改动及原诊断事实。
- 实际操作/命令：`git status --short`、定点 `rg/sed` 审查当前计划、人工导入、公司/别名、身份研究、静态抓取及融资候选；bundled Python/openpyxl 只读核对表格列结构和已选样本；查阅 OpenAI/Google 官方搜索与深度研究说明；`python3 data/private/e4-validation/20260913-curated-plan/validate_documents.py`。文档验证脚本及原输入哈希回执保存在该私有目录，不提交真实资料。
- 结果：明确 `curator_confirmed` 为拟实现的独立准入依据，政府旁证不再是负责人确认资料的前置；人工确认普通事实可初始化，原待核/日期/来源口径保留。下一唯一切片改为 E4.1 本地准入与初始化，随后 E4.2 业务发现/增量处理、E4.3 独立检索与维护分别验收；旧两 URL 补证入口不再优先实施。
- 验证与停止：14 份文档的 118 个相对链接、9 个 Markdown 锚点、围栏、`git diff --check`、预期修改范围及增量隐私模式检查通过；7 份私有输入（含原 Excel、冻结基准和诊断文件）哈希不变，HEAD 不变。本轮仅文档、表格只读和代码审查，未执行应用测试或新业务路径；产品搜索/抓取/模型调用、生产读写、真实导入及部署均为 0。EV14/M6B 保持未通过，未进入代码实施或 E5。

## 2026-09-13：E4 同一申请零调用诊断与最小补证方案

- 任务/关键文件：按负责人“同意建议”核查封存身份进度、真实最小融资摘录、现有核验/匹配/事件代码及补证入口。更新 `README.md`、实施看板、增量计划；私有诊断脚本、回执和报告在 `data/private/e4-validation/20260913-diagnosis/`，无应用代码或迁移修改。
- 实际命令：`.venv/bin/python data/private/e4-validation/20260913-diagnosis/diagnose_offline.py`；禁网 Python 包装运行 `pytest.main(['-q', '--tb=short', 'tests/integration/test_public_identity_research.py', 'tests/unit/test_web_research_outcomes.py'])`。只读原记录，真实摘录仅函数判定，落库对照为明确虚构公司和内存 SQLite，不连接生产。
- 结果：32 项诊断断言及 58 项相关测试通过，保留既有 Starlette 警告。原先的 3 份返回文本实际包括验证码页、防护页和缺代码的招聘介绍；没有代码冲突。固定计划受全局主源失败标记与缺配对优先影响，最后查询去重，政府旁证未得到备用检索。真实融资摘录的融资措辞可识别，品牌主体匹配不通过；虚构全称对照仅形成通用未确认线索，同文档幂等、跨 URL/文案更正未归并事项，融资字段未结构化。
- 验证边界：0 次网络尝试、产品搜索/网页/模型调用与生产读写；原 Excel、冻结基准、身份进度和输入表哈希未变。32 项通过表示现有行为及缺口被复现，不代表缺陷已修复；58 项为既有测试，不替代真实完整正文、生产权限或价值验收。
- 未解决阻塞/停止边界：给定身份 URL 未被程序核验、品牌与法人关系未接通、完整原文未保存，EV14/M6B 继续未通过。建议下一唯一切片为同一申请的可审计候选补证与准确待补证状态；查询计划调整、品牌关系和融资事项处理分别留待后续范围决定。本轮只交付诊断与方案，不恢复申请、合并部署或追加外部调用。

## 2026-09-13：E4 单公司基准冻结与首次身份准入验证

- 任务/关键文件：只读检查负责人提供的 6 个 sheet、25 家公司/144 条事项记录，按授权选 1 家未上市公司、核验原始报道，冻结前 365 天内 1 条融资披露基准。更新 `README.md`、实施看板、增量计划与本记录；Excel、公司身份、标注/最小证据、私有脚本和回执仅存 Git 忽略的 `data/private/e4-validation/20260913-baseline/`，原 Excel 不变。
- 实际操作/命令：bundled Python/openpyxl 只读抽取及 SHA-256；浏览器核对来源和原搜索账户免费额度；Tailscale 会话重新验证后，生产只读事务/受限角色检查和 Compose `--dry-run`；正常登录账户经现有页面提交一份新申请，私有单申请入口调用既有 `run_web_research_worker_once`。四项调用开关、免费证据对应零单价和日/月总次数 10/35 仅覆盖临时容器，身份/业务预算保持 6/24/4 MB 与 4/8/2 MB/180 秒；无业务代码、迁移或部署修改。
- 结果：11:31（北京时间）身份阶段以 `in_review / identity_evidence_missing` 停止，百度 3 次/博查 2 次搜索、11 次 HTTP、302,227 字节、0 缓存/模型 Token。3 份可读正文缺配对字段，另有 robots 拒绝、政府附件超时、HTTP 失败各 1；固定计划去重后 5 次派发，候选耗尽，没有扩充次数。未创建共享公司或业务任务/正文/事件，业务发现及给定证据处理未执行；冻结分母 1 不改写，召回为 `N/A（准入阻塞）`，EV14/M6B 未通过。
- 费用/关闸验证：免费额度博查 970 → 968、百度 50 → 47，未启用后付费，新增 11 条账本均 `settled / confirmed_free`；累计 123 条账本/112 次历史外部调用，旧 112 条完整摘要及两个原样本记录一致。临时 Worker 退出，12 项运行开关、两份部署配置各 18 项研究/业务开关关闭；长期未知单价/零金额上限不变，3 个服务容器健康、健康定时器正常。本人页面明确“保留进度待补证”；顶部“正在核验”仍有歧义，未修改界面。人工时间和基础设施费用未单独计量。
- 文档验证：`git diff --check origin/main`、4 份文档的 57 个相对链接/围栏/修改范围与增量隐私模式检查通过，私有证据被 Git 忽略，原 Excel 及冻结基准哈希未变。未改应用代码，未重复本地应用测试；PR #80 的合并后 main CI 已确认通过，新文档 PR 的 CI 另列。
- 未解决阻塞/停止边界：事件基准已有，平台仍未取得满足 ADR-0020 的身份正文组合。建议下一步对同一身份记录做零调用诊断和最小补证方案，不自动重启申请、追加搜索、换样本或放宽准入；不发邀请、发布事实、开启模型/巡检或进入 E5。

## 2026-09-13：合并 PR #80 并准备 E4 事件基准

- 任务/关键文件：按明确批准合并 [PR #80](https://github.com/daiphoon/dealflow-radar/pull/80)，更新唯一看板、完整计划中的 E4 基准准备规则和本记录；沿用 M6B 手册与现有模板。合并回执及历史样本索引位于 Git 忽略的 `data/private/e4-validation/20260913-baseline/`。
- 实际命令：合并前查询 PR 文件、批准提交与 CI；`gh pr merge 80 --squash --match-head-commit 93c823f35ed7ccb273384432255074c6cbf0177d`，随后查询 `MERGED`、`git fetch`、`git switch main`、`git merge --ff-only origin/main`；核对合并提交 `8114cc9` 与批准提交的文件树一致，在 `codex/e4-event-baseline` 准备后续文档。无部署或生产操作。
- 验证：三份文档的相对链接、E4 标题锚点、代码围栏、差异空白、修改范围及私有样本/凭据模式检查通过；历史证据索引记录原文件哈希并确认被 Git 忽略。本地仅做文档验证，无业务代码修改；合并后 main CI 已自动启动，其结果另行核对。
- 结果/阻塞：合并前 CI 通过；基准规则限定最小补充 1 家未上市公司/1—3 条事前已知事件，分别记录搜索发现、证据处理及用户理解，明确历史检出间隔不能冒充巡检延迟。原未上市来源不足样本与上市技术例外保留；正例公司、事件和可用证据尚未提供，基准未冻结，EV14 未通过。当前新增产品搜索、网页研究、模型 Token 和现金预算为 0，未扩样执行、发邀请、发布材料或修改代码/生产配置。

## 2026-09-12：E4 原未上市公司一次补查与来源不足收尾

- 任务/关键文件：按明确批准执行同一公司的补充更新申请，保留此前 PR #79 部署记录，更新 `README.md`、唯一实施看板及本记录。账户、样本、授权/免费额度、受限运行脚本和验收结果仅保存在 Git 忽略的 `data/private/e4-validation/20260912-retest/`；无业务代码、迁移或部署配置修改。
- 实际操作/命令：Chrome 原搜索账户控制台核对额度，当前登录账户经现有公司页提交更新申请；生产只读 SQL 核对主体、三份缓存、队列和已用次数。Compose 一次性容器先以只读 `--dry-run` 预览，再用私有单任务入口复用 `run_web_research_worker_once`，限定新申请、公司指纹及唯一可派发查询；日/月次数上限收紧为 1/25，网页/字节上限 6/1,996,814，四项研究开关和零单价仅覆盖本次容器。完成后只读回查原记录、账本、缓存、运行配置及容器状态，并在本人申请页核对结果。
- 结果：22:12 正常结束，**1 次博查搜索、3 份缓存命中、0 次网页请求、0 下载字节、0 模型 Token**。10 条新结果中 7 条主体不符、2 条超出时间范围、1 条受限，0 合格正文/事件；免费资源包 971 → 970，新增 1 条 `settled / confirmed_free` 零费用账本。旧 111 条账本摘要及两个原样本记录不变；平台累计 112 条账本/96 次历史外部调用，原未上市公司业务累计 4 次搜索/2 次网页请求/3,186 字节。三份文档的链接/围栏、变更范围、私有样本/凭据模式与 `git diff --check` 通过；未改业务代码，未重复运行应用测试。
- 关闸/边界：新申请/任务 `completed`，无活动研究任务或 Worker；12 项运行时业务/功能开关和两份部署配置的 18 项研究/业务开关关闭，长期单价及完整研究策略不变，五层金额上限为 0，服务健康。页面明确无可展示证据不代表公司无变化。结论为“当前来源不足”，停止追加搜索和工程扩张；无独立真值，召回/延迟不可计算，无有效事件时不计算每事件成本，独立用户验证未执行，EV14 未通过。下一步先交付文档，再确认事件基准和可用证据；未扩样、发邀请、开启模型/真实巡检或进入 E5。

## 2026-09-12：PR #79 经批准合并部署与补查口径澄清

- 任务/关键文件：按负责人明确批准合并并部署 [PR #79](https://github.com/daiphoon/dealflow-radar/pull/79)，更新 `README.md`、唯一看板和本记录。香港运行提交 `6387d3d41497e998c4b52049ab8f18e9e77761b2`，数据库保持 `0031`；仅两项镜像标签变化。部署与恢复凭据存于 Git 忽略的 `data/private/deployment/pr79-20260912/`。
- 实际命令：`gh pr merge 79 --squash --match-head-commit 0a1bd32c360b1f4942d2260f3624e5114d27df8c`、`git fetch/switch/merge --ff-only`、`git archive`；生产 Compose `build api frontend`、`run --rm -T --no-deps preflight`、`run --rm -T backup`、`deploy/upload-backup-cos.sh`；Mac 一次性 PostgreSQL 16 恢复、`alembic upgrade head/check` 和非 owner 只读检查；生产 `migrate alembic check`、`up -d --no-deps api frontend proxy`、HTTP 和只读表摘要/账本/队列核对。无生产迁移或实际 Worker 运行。
- 验证：PR 提交的完整 CI 为后端 **665 通过/13 跳过**、前端 **20 通过**，合并提交与该提交文件树一致；[main CI](https://github.com/daiphoon/dealflow-radar/actions/runs/34686044112) 通过，后端 665 通过/13 跳过、前端 20 通过。新备份加密上传 COS，同一文件经 SSH 取回后双哈希及隔离恢复通过，41 张业务表完整摘要一致、无漂移，临时库及凭据清理。线上分类 3 项检查、租户/基金/本人关注隔离、健康/就绪 200、匿名及伪造身份 401、公网登录 200/匿名关注页 307 均通过。
- 故障与恢复：首次切换发现部署前配置校验文件为空，按脚本自动回退。原因是辅助脚本尚在传输时即用于预检；完成传输、重采基线并增加 JSON 完整性闸门后重发通过。回退后和最终部署均核对原数据无变化，保留失败回执；未以放宽校验解决问题。
- 边界/待办：12 项运行时业务/功能开关及 Worker 专用开关均关闭，18 项部署开关总核对通过，五层金额上限为 0；41 表与 111 条账本/95 次历史外部调用保持不变，本轮新增产品搜索/网页研究/模型 Token 为 0。原版本保留，健康定时器正常。现有 COS 存储/传输费未单独核算；本次恢复来自同一上传文件的 SSH 副本，未重复 COS 下载验收。一次补查仅为原公司补缺失搜索结果，无需新公司，等待明确执行确认；“无已知事件”的独立基准缺口仍保留，召回/发现延迟不可计算，EV14 未通过。

## 2026-09-10：E4 免费额度对账与单次搜索上限预检

- 任务/关键文件：用户完成原博查账户登录后，核对两家原搜索账户的调用统计、免费额度和消费记录；更新 README、唯一看板及本记录。账单、账户摘录、对账前后记录和隔离预检存于 `data/private/e4-validation/20260910/`，不入 Git。
- 实际命令：只读 PostgreSQL 事务读取 24 条历史搜索记录；先只读预览，再以现有受限 Worker 数据库身份和 `web_research_budget.reconcile` 执行同一事务的 24 条对账，随后独立只读回查。运行私有 `verify-one-search-cap.py`，禁止网络连接并在隔离 SQLite 中验证现有日/月次数闸门。
- 结果：博查生产 9 次按日与官方导出账单一致，全部免费资源包抵扣；百度控制台 11 次成功 API 调用与账本一致，4 次历史失败仍保留，账户免费额度及无付费/消费记录支持现金费用为 0。24 条费用均为 `actual=0` 且有回执、操作者、原状态和原因；账本保持 111 条/95 次历史外部调用，12 个原任务及 13 个原申请状态不变。隔离预检首个模拟搜索允许，第二次分别被日/月上限拒绝。
- 边界/阻塞：仅写入既有费用记录的核对字段，未改价格、金额预算、业务数据或任务状态；本轮没有新增产品搜索、网页研究或模型调用。分类修正 PR #79 尚未合并部署；一次免费额度补查方案待确认，EV14 未通过。

## 2026-09-10：合并运维文档并开始 E4 原缓存对照

- 任务/关键文件：按明确确认合并 [PR #78](https://github.com/daiphoon/dealflow-radar/pull/78) 为 `38c7667003e95f46590b863df0ee356d7794be63`，应用仍运行 `6d8f47f` / `0031`。在 E4 范围内修正 `web_research_service.py` 对看准企业资料页的分类，扩展 `test_bounded_web_research.py`，更新 README、唯一看板和本记录；原输入及对照证据保存于 Git 忽略的 `data/private/e4-validation/20260910/`。
- 实际命令：`gh pr merge 78 --squash --match-head-commit 2525bf8927990b5a0e6c21af9d5c1cef6cf9f49f`、`git fetch/switch/pull --ff-only`；分离 `0a2344b` 历史工作树，使用同一冻结缓存、禁止 socket 联网，在隔离 SQLite 库执行旧版/当前版/修正版对照。先运行资料页条件回退测试复现失败，再运行四项定向测试及 `pytest tests/integration/test_bounded_web_research.py tests/unit/test_web_research_outcomes.py tests/unit/test_web_research_budget.py -q --tb=short`，对两个修改的 Python 文件执行 Ruff 和格式检查。
- 结果：改造前后对五份搜索缓存与一份正文的行为一致；确认原未上市样本的一张资料页被误当作有效结果而阻止条件回退。窄修正后只有该组有效候选由 1 变 0，正确触发原回退规则；上市样本仍为 1 条未确认线索/1 条证据，重复与发布为 0、缺年份未知、原文不变。定向 **4 通过**，相关回归 **93 通过**，Ruff/格式通过；保留既有 Starlette 弃用警告。
- 费用/阻塞：已读取百度官方目录报价及财务页面，账户用量归属和逐项扣费仍待核实；博查原账户待登录，随后 Mac 锁定使浏览器核对暂停。公开报价不回填为历史实际费用，生产单价/金额预算未更改。原任务和累计消耗保留，本轮产品搜索/网页研究/模型调用与生产写入均为 **0**；修正尚未合并部署，补充复测尚未执行，EV14 与独立用户内容价值未通过。

## 2026-09-10：COS 恢复验收收尾与 E4 只读前置核对

- 任务/关键文件：按已批准顺序完成 COS 下载原因核对及真实恢复，保留前轮部署记录并同步 `README.md`、唯一实施看板、运维手册与本记录；文档提交独立 PR 审查，不重新部署应用。具体样本、原申请/任务 ID、缓存、账本和恢复收据保存在 Git 忽略的私有验收目录。
- 实际操作/命令：Chrome 登录后的 CAM/COS 控制台核对当前策略并下载同一恢复点的 `.dump.age` 及两个 `.sha256`；一次受限签名 GET 诊断返回 403。Mac 既有工具镜像执行 `deploy/restore-test.sh`，一次性 PostgreSQL 16 执行 `alembic check`、39 表摘要比较与非 owner 只读验证；生产 `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY` 核对原申请、缓存、账本及运行配置，Compose 一次性 `scripts.run_web_research_worker --dry-run` 显式关闭外部开关并设置数据库默认只读。
- 恢复结果：CAM 当前 `DealflowRadarCosBackupUploadOnly` 仅允许服务器账号向指定前缀上传，下载 403 符合权限边界；管理终端实际从 COS 下载部署后 `20260910T094325Z` 恢复点，700,285 字节加密文件及解密后校验通过。隔离恢复为 `0031`，39 张原表摘要一致、无迁移漂移，租户/基金/本人关注隔离通过；未修改云权限或生产数据，私钥仍在 Mac，临时数据库、容器及连接文件清理完成。
- E4 结果：两份原申请/任务均已结束，dry-run 为 0 待处理申请/0 活动研究任务。已上市技术例外保留 2 份搜索缓存、1 份正文及 1 条未确认线索；原未上市样本保留 3 份搜索缓存，但 0 份有效业务正文、0 条事件，历史身份与业务合计 11 次搜索/29 次网页请求。生产百度/博查单价和保守上界未配置、五层金额上限为 0；平台 24 次历史搜索仍为未知费用，不能当免费或计算可用金额。账本保留 111 条、历史外部调用 95、模型输入/输出 Token 772/310；本轮产品搜索、网页研究及模型调用均为 **0**。
- 收尾验证：生产 39 表摘要与部署后检查点逐项一致，API/前端/数据库健康且无研究进程，健康检查定时器正常；四份 Markdown 的 51 个相对文件链接、代码围栏、历史看板封存正文、增量隐私模式检查及 `git diff --check` 通过。仅改文档，未重复运行本地应用全套测试；远端 CI 以文档 PR 的实际结果为准。
- 边界/待办：COS 实际取回恢复限制已关闭；现有存储/下载费用未单独核算，无新服务或持续 Worker/Cron。E4 下一事项为原缓存零调用对照、历史费用核对和一次受控复测方案；终态任务不直接重开，历史特殊次数授权不扩展，金额、原申请关联方式及 EV14 指标固定前不开真实调用。独立用户内容价值仍未通过。

## 2026-09-10：E0—E3 经批准合并与受控部署

- 任务/关键文件：按负责人明确批准合并 [PR #77](https://github.com/daiphoon/dealflow-radar/pull/77)，备份及恢复演练后部署香港环境；更新 `README.md`、`docs/10-implementation-plan.md` 和本记录。合并/运行提交 `6d8f47fe4ecf0cae99cde244f8e0c1d116a73dbc`，应用未追加代码修改，数据库由 `0027` 升级到 `0031`，旧 release/镜像和原数据卷保留。
- 实际命令：`gh pr merge 77 --squash --match-head-commit 47e935c74e0bbda5c0c3a0cae1fe190426a01815`、`git fetch/switch/pull --ff-only`；生产 Compose 构建、关闭配置预检、`run --rm backup`、`deploy/upload-backup-cos.sh`；同一加密备份经 SSH 取回，本机既有备份工具镜像执行 `deploy/restore-test.sh`，隔离 PostgreSQL 16 执行 `alembic upgrade head/check` 和非 owner 角色只读验证；生产 `run --rm -T --no-deps migrate`、`migrate alembic check`、`bootstrap-role`、`up -d --no-deps api frontend proxy`。切换后通过 HTTP、CUA 浏览器、只读数据摘要和配置核对验收，再创建、上传并取回部署后加密备份。
- 结果：[PR CI](https://github.com/daiphoon/dealflow-radar/actions/runs/34455209077) 与 [main CI](https://github.com/daiphoon/dealflow-radar/actions/runs/34459975405) 通过；后端 **662 通过、13 个 SQLite 不适用分支跳过**，对应 PostgreSQL 权限/并发分支通过，前端 **20 通过**。依赖审计、迁移漂移、格式、类型/构建和生产构件检查通过，仅保留既有 Starlette 警告。切换前恢复及升级演练保留 39 张原表旧字段摘要；生产切换和非 owner 租户/基金/个人隔离验证通过，最终业务表及旧审计不变，仅浏览器正常续期追加 1 条认证审计。API 健康/就绪 200，匿名及伪造 Demo Header 查询 401；公网登录 200、匿名关注页 307；既有登录会话的关注页可读，显示“低频检查未启用”，无控制台错误或横向溢出。
- 备份与边界：切换前后 age 加密备份均上传既有 COS，并在 Mac 留存校验副本；私钥未上传，临时恢复库、容器和连接文件用后清理。服务器健康检查定时器恢复，12 项业务/功能开关保持关闭，无研究/解读 Worker 或新增 Cron；新观测/调度表为空，原 U01、缓存、累计消耗不变，本轮产品外部搜索/模型调用为 **0**。无新供应商或付费服务，备份使用既有存储；实际云存储/传输费用未单独核算。E4 未开始。
- 未解决事项：服务器使用既有 COS 凭据直接下载备份返回 **403**；本轮已验证的异机恢复使用同一已上传文件的 SSH 副本，不能宣称 COS 直接下载恢复通过。保留限制供后续单独核对下载权限与复验，未自动扩权。真实来源召回、价格及内容价值继续由 E4 验收；本轮部署记录同步本地文档与已合并 PR，不额外启动研究阶段。

## 2026-09-10：E0—E3 集成提交与发布配置审查

- 任务/关键文件：保留此前 E0—E3 全部已验证改动，整理集成 PR；补齐 `compose.production.yml` 及两份生产环境示例中的中标开关、关注策略与金额预算传递，首次部署预检增加两项关闭检查；新增 Compose 解析到 `Settings` 的实际配置回归，更新唯一看板的发布顺序和合并前检查点。
- 实际命令：`git fetch origin main` 核对基线 `0a2344b`；新建 `codex/e0-e3-incremental-delivery`。运行 `.venv/bin/pytest tests/unit/test_production_compose_environment.py tests/unit/test_production_preflight.py -q --tb=short`、`ruff check backend migrations scripts tests`、`ruff format --check backend migrations scripts tests`。
- 结果：发布配置/预检 **25 项通过**，Ruff 和 133 个 Python 文件格式检查通过；三份部署示例默认开关关闭、报价未知且金额上限为零，显式配置可以到达 API/Worker/工具容器。首轮测试因生产示例的身份占位值不能实例化而失败，替换为虚构测试环境 ID 后通过；未降低配置校验。此前完整后端 656 通过/13 个 SQLite 不适用分支跳过、前端 20 通过及浏览器验收保留为本地证据，完整远端 CI 以本分支 PR 最新提交为准。
- 边界：仅解析 Compose，未启动生产服务或执行真实调用。新迁移 `0028`—`0031` 尚未在生产应用；有新历史时保留账本并关闭开关，不使用破坏性 downgrade。PR 合并按已同意的流程单列确认，部署前完成备份/恢复与状态核对；E4 仍待开始。

## 2026-09-10：关注公司三类低频检查（E3）

- 任务/关键文件：`watchlist_monitoring.py`、迁移 `0031`、默认只读 `queue_watchlist_checks.py`；接入现有研究 Worker、抓取器每请求关停检查、关注页有限 DTO/组件和配置。同步完整计划、唯一看板、架构/数据库/API/成本/测试/运维及 README；保留 E0—E2.2 改动。
- 实际命令：本机 `docker run --pull=never --rm` 启动一次性 PostgreSQL 16；隔离库执行 `alembic upgrade head/check`、虚构 seed 与 `bootstrap_application_role`，非 owner/非 BypassRLS 角色验证。执行 EV13/迁移针对性 Pytest、最终全新库 `.venv/bin/pytest -q --tb=short --maxfail=2`，`ruff check backend scripts tests`、新增文件格式检查、`git diff --check`。前端 `npm test`、`npm run typecheck/build`；本地 API/Next 服务与 `agent-browser` 验证桌面、390px、详情/首页导航和取消关注。
- 结果：最终后端 **656 通过、13 跳过**，跳过均为 SQLite 不适用分支，对应 PostgreSQL 权限/并发测试通过；前端 **20 通过**，类型/构建、浏览器、迁移漂移与代码检查通过。15 份受影响 Markdown 链接/围栏、封存看板正文一致性、历史迁移未改写和增量凭据模式检查通过。修复失败计数的时区幂等问题、调度与收尾的过期状态竞争；旧迁移测试改用当时的表结构，未改历史迁移。仅有既有 Starlette 弃用警告。
- 边界/阻塞：真实搜索、模型、付费调用为 **0**；仅隔离库应用迁移。默认专用开关关闭，未安装真实 Cron，未执行旧 U01、附件命令、提交/推送/合并/部署。测试浏览器、服务、容器和临时连接配置用后清理；开发服务改动的类型引用已恢复。E3 无未解决工程阻塞，停在本地交付；下一阶段 E4 尚未开始，真实费用、来源召回和低频命中率仍未验证。


## 2026-09-10：公开研究费用预占、结算与恢复（E2.2）

- 任务/关键文件：新增 `web_research_budget.py`、迁移 `0030` 和默认只读的 `reconcile_web_research_usage.py`；接入 `web_research_service.py`、`identity_research.py`，配置金额状态、CNY 上限、平台/公司归属和审计。同步成本/架构/数据库/测试、完整计划、唯一看板与 README；保留 E0—E2.1 改动。
- 实际命令：`docker run --pull=never --rm` 启动本机一次性 PostgreSQL 16，无现有卷；Alembic `upgrade head/check`、虚构 seed、`bootstrap_application_role` 准备非 owner/非 BypassRLS 测试角色。显式设置隔离库 `DATABASE_ADMIN_URL/DATABASE_URL/POSTGRES_RLS_DATABASE_URL` 和关闭开关后执行 `.venv/bin/pytest -q --tb=short --show-capture=no`；针对性 `test_web_research_budget.py`、`test_web_budget_delivery.py`、`test_web_usage_reconciliation_cli.py`、公开研究/主体/覆盖回归；后补 `-k 'identity_and_business or deferred_fallback'`。执行 `ruff check .`、`ruff format --check .`、`git diff --check`、文档链接/历史正文与增量敏感信息检查；前端 `npm test`、`npm run typecheck`。
- 结果：全量后端 620 通过、11 个 SQLite 不适用分支跳过，对应 PostgreSQL 分支全部通过；随后补充两库恢复/归属 6 通过。新预占/费用/CLI/覆盖针对性 24 通过、6 个 SQLite 并发分支跳过；公开研究/主体回归 68 通过。前端 18 通过，类型、Ruff/格式、迁移漂移和文档检查通过。首次相关测试发现旧 Mock 未声明免费、账本新增字段和新迁移先行阻止费用历史降级三处断言需更新，修正后通过；未放宽费用、权限或保留历史要求。仅有既有 Starlette 上游弃用警告。
- 边界/阻塞：真实搜索/模型/付费调用为 0，未运行附件命令、原申请或生产操作，未提交/推送/合并/部署。所有金额均为明确虚构测试值；默认价格未知、金额上限零。迁移仅在一次性测试库执行，测试容器及临时连接配置用后清理。E2.2 无工程阻塞，停在本地交付；E3 待独立开始，模型/机构来源/基础设施未纳入本次原子预算，不宣称真实费用或产品价值已验证。

## 2026-09-10：类别来源路由与实际检查结果（E2.1）

- 任务/关键文件：新增 `research_coverage.py`、覆盖 DTO 和前端 `research-coverage.tsx`；沿用 `web_research_service.py` 的原搜索分组、缓存及正文读取，在现有任务 JSON 保存版本化路由、分类、时间与失败原因。`research_outcome.py` 向本人历史查询提供有限覆盖摘要；同步完整计划、唯一看板、架构/数据库/来源/API/测试说明和 README。
- 实际命令：`.venv/bin/pytest tests/unit/test_research_coverage.py tests/unit/test_web_research_outcomes.py tests/integration/test_bounded_web_research.py tests/integration/test_research_coverage_delivery.py -q --tb=short --show-capture=no` 及针对性子集；显式指定本机隔离库的 `DATABASE_ADMIN_URL/DATABASE_URL/POSTGRES_RLS_DATABASE_URL` 执行 `.venv/bin/pytest -q --tb=short --show-capture=no`。通过 `docker run --pull=never --rm`、Alembic `upgrade/check`、虚构 seed 和 `bootstrap_application_role` 准备 PostgreSQL 16 非 owner/非 BypassRLS 环境；无新迁移。执行 `ruff check .`、`ruff format --check .`、`git diff --check`；前端 `npm test`、`npm run typecheck`、`npm run build`，本机 `agent-browser` 检查详情→申请历史→首页、缓存时间展开与 390px 窄屏。
- 结果：新增后端 24 个用例纳入全量 **598 通过、5 跳过**，跳过项为原有 SQLite 不适用的 PostgreSQL 专用分支；非 owner Worker→API 两库验证通过。前端 **18 通过**，类型/构建、Ruff/121 文件格式通过。浏览器无框架错误和横向溢出，五类结果与缓存原时间可辨；正文取得不增加已核实事实数。同任务缓存回放不增加搜索/网页调用，其他用户不可见，不返回底稿标识或改写任务历史。
- 验证中修正：首次正文记录与缓存回放的时间字段出现微秒差异，统一使用原来源检查时间；失败 Mock 改用既有 `failures` 合约，前端断言对齐既有“含历史”文案；测试文件重名导致收集冲突，集成测试改为独立文件名；非 owner 测试补与真实请求一致的事务权限上下文。未改授权规则或放宽断言，最终仅保留原 Starlette TestClient 弃用警告。
- 边界/阻塞：真实来源、模型和付费调用为 0；未提交、推送、部署或操作旧申请。14 份受影响 Markdown 的链接/围栏、封存历史一致性、历史迁移未改写及新增凭据模式检查通过；浏览器、本轮开发服务及一次性 PostgreSQL 已关闭，开发服务自动改动的类型引用已恢复。新路由复用原调用与预算，历史未知保持未知，机构私有来源不混入共享覆盖；无未解决工程阻塞。停止于 E2.1，下一唯一切片 E2.2 尚未开始；真实来源召回、实际费用及内容价值尚未验证。

## 2026-09-10：中标事件页面、逐版本核实、异步解读和固定报告（E1.3）

- 任务/关键文件：现有私有研究导入显式选择 `tender_notice`，默认关闭 `TENDER_EVENTS_ENABLED`；新增 `tender_presentation.py`、`tender_publication.py`，复用原共享审核、`investor_analysis.py`、`personal_features.py` 和公司/审核页面。兼容迁移 `0029` 为观测审核增加复合外键、保留旧来源唯一约束并补已核实中标的解读 RLS；原私有观测和已应用迁移不改写。同步完整计划、实施看板及架构/数据库/API/测试/README。
- 实际命令：`.venv/bin/pytest -q --tb=short`（显式指定本机新建测试库的 `DATABASE_ADMIN_URL/DATABASE_URL/POSTGRES_RLS_DATABASE_URL`）；针对性执行 `test_tender_delivery.py`、`test_tender_storage.py`、`test_tender_events.py`、迁移/解读/个人详情/导入 CLI 回归。`docker run --pull=never --rm` 创建回环地址的一次性 PostgreSQL 16；`alembic upgrade head/check`、虚构 seed 和 `bootstrap_application_role` 验证非 owner/非 BypassRLS；`ruff check`、`ruff format --check`、`git diff --check`；前端 `npm test`、`npm run typecheck`、`npm run build`。缓存的 `agent-browser` 连接本机 API/Next 开发服务，检查公司详情→审核更正提交→详情版本/解读切换→首页，并检查 390px 窄屏、截图及页面错误。
- 结果：全新隔离库后端 **574 通过、5 跳过**，五项为 SQLite 不适用的 PostgreSQL 专用分支，对应 PostgreSQL 分支通过；最终缓存按当前输入选择、撤证日期隐藏及导入 dry-run 补充回归 **33 通过、1 跳过**。前端 **15 通过**，类型/生产构建、Ruff 和 118 个 Python 文件格式检查通过。浏览器可提交具体更正版本，核实后金额从 12,345,000 变为 12,000,000 元，保留原版本，先隐藏旧解读，Mock Worker 完成后显示新解读；转载不追加调用或提醒，撤回不改旧报告。页面无框架错误或横向溢出。14 份受影响 Markdown 链接/围栏、旧看板封存正文、历史迁移未改写及新增内容凭据/临时文件检查通过。
- 验证中修正：旧来源一次决定约束和解读 RLS 不接受新路径，补兼容迁移；SQLite 时间归一化、提交后读取导入结果、前端变化卡历史缺失、有基金授权用户的旧私有证据叠加均按失败回归修正。复用旧全量测试库时出现已提交 fixture 残留，改用全新隔离库通过，未放宽断言或修改权限。原文金额引用断言按含千分位的原始文本修正；文档检查按 E0 已改名的历史标题定位。仅保留既有 Starlette TestClient 上游弃用警告。
- 边界/阻塞：真实搜索、模型和付费调用为 0；未提交、推送、创建 PR、部署或触及生产/旧申请。两条新迁移只在临时库执行，新写入开关默认关闭，必要证据不可用时隐藏依赖内容，历史有数据时拒绝破坏性降级。浏览器、开发服务和一次性 PostgreSQL 用后关闭。E1.3 无阻塞，E1 单类工程闭环本地交付；停止于此，下一唯一切片 E2.1，真实来源覆盖、费用和内容价值尚未验证。

## 2026-09-09：中标候选归并、观测版本与逐字段证据落库（E1.2）

- 任务/关键文件：新增 `tender_storage.py`、`EventObservation`、兼容迁移 `0028` 及 `test_tender_storage.py`；复用原事件/字段/证据表，按数据库原权限和许可重新抽取校验，在作用域内归并并追加修订。`services.py` 仅增加中标当前投影过滤；同步架构、数据库、测试说明和唯一实施看板，保留前轮所有改动。
- 实际命令：安全开关均关闭，使用 `docker run --pull=never --rm` 启动本机一次性 PostgreSQL 16（仅回环地址、不挂现有卷）；通过 Alembic `upgrade/check`、`scripts.seed_demo` 与 `bootstrap_application_role` 准备隔离库。执行 `uv run --frozen --offline pytest tests/integration/test_tender_storage.py -q` 及针对性子集；配置该临时库的 `DATABASE_ADMIN_URL/DATABASE_URL/POSTGRES_RLS_DATABASE_URL` 后执行 `uv run --frozen --offline pytest -q`；随后新增冻结样本验收并执行 `pytest tests/integration/test_tender_storage.py -k frozen_candidate_corpus -q`。执行全库 `ruff check`、`ruff format --check`、Markdown 本地链接/历史正文检查和 `git diff --check`。
- 结果：后端全量 551 通过，补充两库冻结样本 2 通过；4 个 PostgreSQL 专用用例的 SQLite 分支跳过，对应 PostgreSQL 分支通过。新文件共 50 个参数化用例，46 通过、4 按上述原因跳过。16 份候选归为 8 个事项/16 份来源观测，重放无新增；非 owner RLS、基金授权过期、并发去重、事务回滚、逐字段定位、迁移往返/漂移及拒绝有数据降级通过。首轮复现旧查询混入候选更正金额，补当前投影过滤后两库通过；Ruff 长行与测试辅助类收集警告已修复，最终仅保留既有 Starlette 弃用警告。
- 边界/阻塞：入口默认关闭，未接活动 Worker；候选及冲突不自动发布、不生成快照/解读，不改历史迁移或真实数据。无真实来源、模型、付费调用、提交、合并或部署；未运行前端/浏览器或远端 CI。测试库/临时容器用后清理。E1.2 无阻塞，停在本地交付；唯一下一切片为 E1.3，真实来源覆盖及内容价值尚未验证。

## 2026-09-09：单类中标公告候选契约与离线抽取（E1.1）

- 任务/关键文件：新增 `backend/app/tender_events.py`、`data/sample/tender_notice_cases.json`、`tests/unit/test_tender_events.py`，更新看板/README。按现有材料选择中标公告；只完成离线候选和字段证据，活动 Worker、数据库、页面与旧历史保持原状。
- 实际命令：在 `APP_MODE=demo EXTERNAL_CALLS_ENABLED=false PAID_API_CALLS_ENABLED=false AUTO_REFRESH_ENABLED=false` 下运行 `uv run --frozen --offline pytest tests/unit/test_tender_events.py tests/unit/test_event_schema.py tests/unit/test_change_detection.py -q`；另用同一离线环境的 `python` 对冻结样本调用旧质量函数与新抽取函数。执行 `uv run --frozen --offline ruff check backend/app/tender_events.py tests/unit/test_tender_events.py`、对应 `ruff format --check` 及 `git diff --check`。
- 结果：新增 45 项、相关回归合计 55 项通过；21 份虚构样本产生 16 份候选/3 份身份未解析/2 份不支持，14 份可标识候选归为 6 个事项，160 个字段证据区间精确匹配。旧质量函数接受 21 份且网页指纹不同，仅为纯函数基线，不代表完整业务入口的身份/发布结果。金额精度和 UTC 日期边界已回归；一次 101 字符行的 Ruff/格式失败经定向格式化修正，最终检查通过。保留既有 Starlette 弃用警告。
- 边界/阻塞：无外部搜索、模型、付费调用、数据库写入、迁移、部署或提交；上轮规划文档改动保留。E1.1 无阻塞；真实来源、完整事件落库/权限和产品价值尚未验证。候选标识不含权限授权，E1.2 必须从原数据库记录重新核验；当前支持格式、字段映射和唯一下一切片见看板。

## 2026-09-09：增量交付与局部重构计划固化（E0）

- 任务：依据已接受的仓库审查建议，固定保留范围、E1—E4 主线、条件后置的 E5、EV01—EV15 验收、迁移回退与逐轮停止规则；本轮只交付文档。
- 关键文件：新增 [ADR-0021](DECISIONS/ADR-0021-incremental-event-delivery.md) 和 [完整计划](15-incremental-event-delivery-plan.md)；更新 [实施看板](10-implementation-plan.md)、`AGENTS.md`、README/ADR 索引及架构、来源、成本、测试、M6B 文档。旧看板正文完整保留并标为历史，原未通过验收未改记完成。
- 实际验证：`git status --short`、`git diff --check`；`python3` 内联检查变更范围、Markdown 本地链接/标题/围栏、历史正文与 HEAD 一致、唯一下一切片、15 项验收编号及 16 个代码符号。检查通过；初次脚本误用 `backend/tests`，按实际 `tests` 路径修正后复验通过。人工核对阶段依赖与授权边界，修正架构文档中差分接入的阶段归属。
- 交付边界：只改 Markdown；未运行应用测试、外部来源、模型、旧申请、数据库迁移或部署；产品搜索/模型/付费调用为 0。未提交或合并；E1.1 尚未启动。
- 未解决事项：E0 无阻塞；首类材料可用性、真实来源覆盖和费用基线仍待对应阶段验证，不据文档检查宣称工程或内容价值通过。

## 2026-09-09：政府来源 TLS 密钥协商兼容

- 真实诊断：已从公开正文取得全称和信用代码配对，政府来源连接却在 TLS 握手报 `BAD_ECPOINT`，不能表述为“没有公司资料”或 robots 明确禁止。原服务器证书校验保持开启，使用标准 P-256 协商后 TLS 1.2 / AES-GCM 握手成功；单独限制 TLS 1.2 并不能解决问题。
- 修改：仅对确认为该 OpenSSL 错误的目标主机追加一次有请求计数的 P-256 兼容尝试；不关闭证书/主机名、DNS/对端、robots、域名、大小检查，最低 TLS 1.2 且保留环境中更高下限，不允许弱密码。不处理其他证书错误；不跨主机继承兼容选择；预算不足不重试。
- 验证：抓取器和专用兼容测试 37 项通过，包括重试计数、预算、证书错误、失败不循环、robots 拒绝及主机边界；Ruff 通过。完整 CI 与合并后真实读取另行验收，不将握手成功等同于网页正文已读或公司已核对。
- 范围：仅抓取器、测试和本记录；无迁移、权限或自动发布变更，保留 `0027`。不新增样本，仍续用原申请。
- CI 首轮发现 Linux 默认 SSLContext 最低版本为 MINIMUM_SUPPORTED，而 Mac 本地默认明确 TLS 1.2；保留安全断言，显式提升兼容上下文下限，不能依赖不同 Python 发行版默认值。首轮其余 459 项通过；修正后重跑，不把失败 CI 当作可部署。

## 2026-09-09：真实检索异码结果在读取前隔离

- 任务：PR #74 合并部署后，真实信用代码检索返回其他公司的信用代码页面；旧路径未把它们核验成目标公司，但仍有无效读取成本。新增全称/代码发现相关性过滤，标题仅为其他完整代码的旧候选零调用跳过；摘要仍不能成为身份依据。不修改预算、迁移、权限或准入条件。
- 关键文件：`identity_research.py`、身份回归测试、阶段看板。
- 验证：`uv run --frozen pytest tests/integration/test_public_identity_research.py -q` 21 项通过，覆盖不相干结果不入队、回退、旧异码队列零读取及既有完整流程；Ruff/格式/差异检查通过。完整 CI 随 PR 验证。
- 当前真实进度：原申请仍在身份阶段，合计 3 次百度搜索、11 次网页 HTTP，0 博查/模型 Token/新公司/事件；包含此前 PR #73 的 1 次搜索/8 次 HTTP。保留状态与消耗，修复部署后继续受控单步，不重置或重复申请。

## 2026-09-09：主体检索、正文定位与独立研究预算

- 任务：修正只依赖复合查询、正文开头截断、单页 2 次请求及身份耗尽业务预算的问题。固定互补查询、按缺口有限回退、同供应商相同查询不重复；身份独立 6 次搜索/24 次 HTTP/4 MB，后续研究原额度保留，平台日/月总额共同计数。旧申请保留实际消耗与版本血缘；没有读取额度不再搜索。完整正文只在内存定位，持久化最小摘录/哈希；不更改公司准入、RLS、发布条件或历史迁移。
- 关键文件：`identity_research.py`、`source_fetcher.py`、`web_research_service.py`、配置/部署示例、ADR-0020、阶段看板及身份/抓取测试。
- 实际命令与结果：`uv run --frozen pytest -q` 首轮 427 通过/23 条 PostgreSQL 条件跳过；随后追加 PDF 与旧请求续做回归，定向身份测试 18 项通过、抓取测试含 PDF 定位通过；独立 PostgreSQL 16 上 `alembic upgrade head`、`scripts.seed_demo`、`scripts.bootstrap_local_database` 后，非 owner `test_postgres_rls.py` 21 项通过；Ruff/格式/差异检查通过。测试没有真实外部调用，不以模拟成功代表公司实际核验成功。
- 自查：修复旧测试指纹大小写错误，确保缺证/冲突分支真实被执行；身份字段可能位于页脚，使用专用正文定位但不改变一般新闻清洗；PDF 继续在隔离子进程内受限解析。无需新迁移；回滚应用而保留 `0027` 与审计记录。下一步完整 CI、备份部署、只续做已授权原申请，实际结果保存在忽略的私有验收目录，不预称价值验收通过。

## 2026-09-09：空研究队列后的主体查证权限上下文恢复

- 任务：修复 PR #71 部署后十方运动 pending 申请被 Worker 错误判断为 idle。`_lease_job` 即使没有研究任务也会提交事务，清除 PostgreSQL 事务级身份；将既有上下文恢复移动到分支之前，使主体查证与原公司研究都在原授权内执行。不改 RLS 策略、不添加迁移或扩大权限。
- 关键文件：`web_research_service.py`、`test_postgres_rls.py`、阶段看板。生产保持 `0027`；当前修复不访问生产或调用真实 Provider。
- 验证：在独立 PostgreSQL 16、非 superuser/非 BYPASSRLS 应用角色、没有外层测试事务的完整 Worker 入口先复现修复前 `idle != partial`；修复后身份/原研究衔接定向 18 项通过，完整 PostgreSQL RLS 与身份查证两套测试合计 34 项通过。覆盖两种 Session 提交过期模式、申请状态持久化、恰好一次 Mock 百度调用/零博查、零模型、未创建公司，以及申请人/其他用户无法读平台查证底稿和身份用量。Ruff、格式及差异检查通过，完整 CI 随 PR 交付复验；保留既有 Starlette 弃用警告。
- 限制与回滚：Mock 证明任务可到达搜索步骤，不证明真实来源覆盖或最终核验成功。无需数据库降级；应用回退会恢复该衔接缺陷。经审查合并、备份部署后只续用原申请，研究开关默认关闭，不增加样本或预算。

## 2026-09-09：系统后台主体查证与研究衔接

- 任务：按 ADR-0020，由系统基于用户提供的全称/信用代码查找并交叉核对公开资料，不要求营业执照扫描件；通过后接原研究队列，冲突或缺证保守停止。
- 关键文件：`identity_research.py`、`0027_public_identity_research.py`、原 Worker/申请服务、公司身份标签、迁移/请求/RLS 测试及 ADR-0020/阶段看板。新底稿与身份用量只允许平台管理员读取，身份查证和后续研究共用预算，不修改历史迁移或真实数据。
- 实际命令：Ruff/格式与 `git diff --check`；完整 Pytest（隔离 PostgreSQL 16）；SQLite 升降级/Schema 检查；一次性 PostgreSQL `0026 → 0027 → 0026 → 0027`；前端 `npm test`、`npm run typecheck`、`npm run build`。
- 测试结果：完整回归 444 项通过；追加账本隔离和详情检查后，身份/RLS 定向 14 项通过；前端 12 项测试、类型检查、生产构建通过。修正旧迁移测试对 PostgreSQL 整事务回滚版本的固定断言；复用旧测试库造成的残留数量失败通过全新隔离测试库复核。仅保留既有 Starlette TestClient 弃用警告。
- 未验证边界：尚未部署香港、未运行真实公司搜索或真实账号视觉验收；实际公开来源可达性/覆盖率仍待合并后的原样本验证。当前抓取器只保留有限正文摘录，目标身份不在摘录内时可能无法交叉核对，不能把缺证当作主体不存在。业务搜索/网页外部调用、模型 Token、付费调用、自动发布均为 0；测试容器不包含真实数据。

## 2026-09-09：前端依赖安全补丁

- 任务：解除 PR #71 的既有依赖审计阻塞；仅升级 Next.js `16.3.0 → 16.3.4`、sharp `0.35.3 → 0.35.4`、baseline-browser-mapping `2.10.43 → 2.11.21` 及 Next/sharp 必需配套依赖。不升级 React/TypeScript、不降低 CI 审计阈值、不修改业务代码或数据库。
- 关键文件：`frontend/package.json`、`frontend/package-lock.json`、实施计划及本记录。
- 实际验证：`npm ci`、`npm ls next sharp baseline-browser-mapping`、`npm audit --audit-level=moderate`；12 项前端测试、TypeScript 检查及生产构建通过，审计为 0 个已知漏洞。standalone 本地启动后 CloudBase 模式登录页返回 200（未发送验证码）；未授权远程图片 URL 返回 400，sharp 正常 PNG 处理和异常输入拒绝通过。完整 Linux CI 随独立 PR 验证，未用 Mac 构建替代 Linux 原生依赖验证，也未把接口检查称为真实账号视觉验收；临时服务已停止。
- 安全依据：[Next 图片优化公告](https://github.com/advisories/GHSA-2xp9-vwfh-vxw4)、[sharp 修复公告](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c)、[浏览器映射依赖公告](https://github.com/advisories/GHSA-w5vr-8v7q-w6rv)。Windows 专属风险不适用于仓库定义的 Linux 部署，但不能因页面未使用图片组件就断言优化接口不可达。本次通过已修复版本与审计验证，不执行漏洞利用，也不证明生产曾遭攻击或不存在其他风险。
- 交付边界：独立 PR 待审查，不合并、不部署，不修改 PR #71；研究搜索、网页采集、产品模型 Token、自动发布及生产写入均为 0。依赖下载、GitHub 与开发工具访问不计入业务调用。

- 后续状态：PR #72 的 Verify 全部通过（430 项后端、12 项前端测试及 Linux 生产镜像），经批准 Squash 合并为 `e0104e4`。同步 main 到 PR #71 时仅两份文档的同位置新增发生冲突，保留双方记录并更新当前闸门；业务文件没有冲突。不部署、不运行迁移或真实查询。

## 2026-09-08：同任务 robots 规则复用与读取预算效率

- 任务：在受限公开网络研究的单个任务生命周期内，为同一站点复用已经取得的 `robots.txt` 规则；每个候选 URL 仍按自身路径重新判断是否允许访问。临时规则随任务进度保存，使单步 Worker 退出后仍可继续复用；任务完成或取消时删除，不跨任务或公司复用。
- 关键文件：`source_fetcher.py`、`web_research_service.py`、受限研究测试和实施计划。缓存有效期固定为 15 分钟；单站规则最多保留 64,000 字符、单任务合计最多 128,000 字符及 32 个站点，超限时保守地重新检查而不扩大数据库内容。无迁移、依赖、Provider、预算、权限、发布策略、Worker 启动方式或前端变更。
- 验证口径：两个同源页面只请求一次规则文件；规则允许的路径仍读取、禁止的路径仍在发出页面请求前失败；过期规则会重新取得，任务结束不保留规则。定向抓取/Worker/研究测试 83 项通过；完整离线后端 410 项通过、20 项按未配置 PostgreSQL 条件跳过，只有既有 Starlette 测试客户端弃用提示。GitHub CI 在 PostgreSQL/RLS 环境中 430 项后端测试通过，前端检查、生产配置和容器镜像构建通过，依赖审计为 0 个已知漏洞。测试只使用 Mock/已有缓存，外部搜索、网页读取、模型 Token、付费调用和自动发布为 0。
- 交付边界：代码 PR 保持未合并，等待审查；本轮不查询思格或新公司。合并部署后先用既有缓存做零调用回放，再由项目负责人单独授权一家新的未上市公司执行真实验收。

## 2026-09-08：申请页当前结果与覆盖说明

- 任务：补齐 PR #68 遗漏的个人研究申请入口。当前共享变化/基础资料/线索与任务历史分开；历史默认折叠，不再将八类 `no_data` 铺成失败列表，也不把信息质量默认完成说成已取得业务资料。仍保留覆盖不足提醒及原始任务时间。
- 关键文件：`personal_features.py`、`services.py`、`schemas.py`、`research_outcome.py`、申请页及 `request-research-result.tsx`、前后端回归测试、实施计划。当前计数复用公司详情相同共享筛选条件，按申请涉及的公司批量读取；不逐家公司额外获取完整详情，不传输私有内容。覆盖说明仅派生已有记录，不写历史 coverage；robots 旧错误码表述为规则检查未通过，不直接断言网站禁止。
- 实际验证：一次性 PostgreSQL 16 容器，非 owner 应用角色，迁移至 `0026`、`alembic check`、完整 `uv run --frozen pytest -q` 427 项通过；新增/撤回/重复读取/私有公司/私有线索排除、与详情计数一致、跨账户请求隔离及历史不变均覆盖。Ruff 和格式检查通过；`npm test` 12 项、类型检查和生产构建通过。CI 由独立 PR Verify 复验。
- 浏览器：一次性虚构 SQLite 库 + 本地后端/生产构建，CUA 检查申请页当前 1 条线索、历史默认折叠、展开后的原时间/搜索/正文/规则/预算/质量说明、公司详情跳转及重新加载计数一致。无浏览器控制台错误，未调用更新或报告；未使用真实账户或真实公司数据，不替代香港部署验收。技能推荐的 agent-browser 不在本机，使用已有浏览器能力完成同范围检查。
- 自查：修正测试夹具和旧文案断言，保留所有权限/历史不变断言；无新依赖、迁移、权限规则、Worker 或预算变更。保留既有 Starlette 弃用提示，不顺带升级。研究外部调用、产品模型 Token 和自动发布为 0；Git/CI/Codex 开发成本不计入该口径。
- 交付边界：未合并 PR，不部署或修改香港数据。下一独立项为同任务 robots 规则复用与读取预算效率；现阶段不更换公司增加真实查询，不据页面修复关闭 U01 内容价值闸门。

## 2026-09-07：当前结果统一展示

- 任务：公司页面保留重要变化优先，新增取自当前已授权列表的共享内容概览；区分变化、基础资料和未确认线索，私有数据另列。申请人的旧任务结果在信息缺口下方折叠保留，不改写历史或刷新证据时间。
- 关键文件：公司详情 `page.tsx`、`personal-change-panel.tsx`、`frontend/tests/current-company-results.test.cjs`、前端 test 命令、CI 及实施计划。客户端只新增传递基础资料 ID，不重复传输完整证据。
- 验证：`npm test` 的 6 个页面渲染场景覆盖历史无成果/新增线索共存、当前列表新增/重复读取/撤回、旧水位响应不带回已移除内容、私有叠加不计入概览、无授权响应无私有内容/历史、加载和失败回退。使用现有 TypeScript 与 React 渲染器，无新增依赖；CI 增加执行测试。另运行相关后端测试、类型检查和生产构建，完整 CI 以 PR Verify 结果为准。
- 限制：页面测试模拟已授权 API 响应与个人水位，不代替数据库 RLS 测试、真实浏览器交互或生产账号验收；现有后端权限测试仍执行。无数据库/权限/预算/发布规则变更，无业务外部调用或模型 Token；本轮 PR 不合并、不部署。

## 2026-09-07：证据检查时间精度极小修复

- 任务：修复 PR #66 生产缓存 dry-run 暴露的时间截断；证据检查时间独立解析 ISO 时间戳，保留时分秒、微秒和时区。缺失、无效或无时区值保持未知；不改变搜索日期解析，不新增迁移或外部调用。
- 关键文件：`backend/app/web_research_service.py`、`tests/unit/test_web_research_outcomes.py`、`tests/integration/test_bounded_web_research.py`。
- 验证：新增 UTC、非零时区、Z、微秒及异常输入测试；既有缓存集成测试增加入库时间、重复处理、原文不变和接口序列化断言。SQLite 不存时区元数据，数据库断言按方言区分但不放宽时分秒/微秒要求；生产 PostgreSQL 回放仍要求完整时间相等。执行 Ruff、格式检查及上述两套 Pytest，完整 CI 随 PR 核验。
- 交付边界：按本次用户授权，CI 成功后合并、重新备份部署，仅重放原缓存；部署/回放实测记录保存 Git 忽略的私有验收目录。不扩大查询、模型、发布和产品验收范围。

## 2026-09-07：U01 已有正文识别与无成果说明窄修复

- 任务：修复完成上市的措辞漏识别；区分正文事件日期和转载日期；在原申请及公司详情提供申请人私有的结束时间/结果缺口，不把“任务完成”或“未取得证据”说成公司无变化。
- 关键文件：`web_research_service.py`、`research_outcome.py`、`personal_features.py`、`services.py`、`schemas.py`，公司详情/个人变化/申请页，单元/集成/PostgreSQL RLS 测试，看板及 M6B 手册。
- 兼容与边界：无迁移，无生产写入，无新 Provider/预算/模型/自动发布；现有路由/RLS 版本保留，提取修订 `explicit-change-v2` 单独留痕。事件日期仅取支持句开头直接指向本公司的完整年月日；缺年份、多日期、记者转述日期不推断。缓存重处理沿用原链接检查时间，不伪造今日重新检查。
- 实际验证：Ruff、`npm run typecheck` / `npm run build` 通过；全新一次性 PostgreSQL 16（非 owner 应用角色）完整 `pytest -q` 407 项通过，新增保守日期场景后再次运行相关套件；CI 以该 PR 的 Verify 为准。SQLite/PostgreSQL 升级到 `0026`，PostgreSQL `alembic check` 无漂移；既有 RLS 和新增申请人/同机构其他人/其他机构隔离通过。
- 真实缓存：只读取 U01 已保存的一份研究正文到 Git 忽略目录；本地隔离 SQLite 中禁止 socket 联网后重处理，生成 1 条待核实线索、重复事件 0、已发布事件 0、原文作用域和 payload 未变，外部业务调用/模型 Token 0。该验证是已有正文处理，不是再次执行全网研究或整任务重跑。
- 页面验收：本地生产构建 + 隔离数据库 + 无基金 Demo 身份，Chrome 检查公司详情、线索展开、重新加载及申请页；结束时间、robots/预算/质量缺口可读，日期未知明确提示来源日期不代表近期发生，未混入已核实重要变化或投资字段。保留既有版式，不更换前后端数据访问模式；该结果不冒称生产真实账户验收。
- 自查修正：补充计划/否定措辞和多日期转述拒绝；去重重复兜底原因文案；测试准备中误用无参 Settings 已修正。重复使用一次性 PostgreSQL 测试库产生既有测试遗留冲突，改为全新隔离库完整重跑，不改生产数据或放宽断言。保留现有 Starlette 弃用提示，不升级依赖。
- 后续限制：robots 阻断和来源覆盖不足未在本轮解决；不扩大访问/预算。等待 PR 审查合并后才备份部署，并单独验证生产缓存和真实账号；M6B/U01 内容价值尚未关闭。

## 2026-09-06：交易所披露资料受控人工身份核验（代码交付）

- 任务：解除 U01 例外技术样本缺少完整政府身份资料的入口阻塞；按 ADR-0019 新增独立 `exchange_disclosure` 依据，复用 JSON 导入和身份工作台，不冒称政府来源，不启动真实研究。
- 关键文件：`backend/app/providers.py`、`services.py`、`models.py`，`0026` 新迁移，原身份导入 CLI，公司详情/审核来源标签，虚构 JSON 示例，Provider/导入/重路由/迁移/RLS 测试，ADR-0019、看板和操作手册。
- 实际命令：`uv run --frozen pytest -q`（一次性本机 PostgreSQL 16，显式关闭所有外部业务调用并设置测试专用 `DATABASE_ADMIN_URL` / `POSTGRES_RLS_DATABASE_URL`）；`alembic upgrade head` / `alembic check`；Ruff 检查与格式检查；`npm run typecheck` / `npm run build`；`git diff --check` 和文档相对链接检查。
- 测试结果：最终完整后端套件 385 项通过；包含 SQLite/PostgreSQL `0025 → 0026 → 0025 → 0026`、原数据逐行保留，以及任一身份表有新依据数据时拒绝降级；真实 PostgreSQL 非 owner 角色验证跨用户/机构不能读取或修改私有核验文档；原政府路径、失效平台角色、重复导入、字段冲突、工作台选择、原请求排队与无匹配不建公司/任务均通过。前端类型和生产构建、Ruff、文档链接检查通过。
- 自查修正：交易所代码一致但注册地冲突时保守保留冲突，不直接覆盖主档；工作台选用该依据同样要求有效平台管理员。测试中修正了“未匹配公司”误用既有 Demo 信用代码的准备错误；重复运行遗留的 PostgreSQL 测试记录用全新一次性数据库重新验证，不修改真实数据或放宽断言。保留现有 Starlette 测试客户端弃用警告，不顺带升级依赖。
- 交付边界：仅代码 PR，合并和生产部署另行批准；本轮未操作生产数据库/开关、未执行真实公司导入，新增外部业务调用、模型 Token 和自动发布均为 0。自动测试不证明真实 PDF 已经人工审核，部署后的真实账号视觉验收尚未完成。后续按看板完成备份/`0026`/部署后，方可继续原请求受控验证；例外样本不计未上市独立用户价值验收成功。

## 2026-09-04：M6B 受控运行开关口径同步

- 任务：在 U01—U03 开始前，把独立用户测试手册从 R3.4/R3.5 待完成的旧状态同步到生产 `0025` 和 R3.5 已关闭，并明确网站 API 总开关与一次性研究 Worker 专用开关不是同一组。本检查点只修改文档，不修改代码、数据库或生产配置，也不启动真实用户测试。
- 关键文件：`docs/14-m6b-independent-user-validation-playbook.md`、`docs/IMPLEMENTATION_LOG.md`。
- 实际检查：`git diff --check`、文档相对链接检查、R3.5 旧状态和“V1 不调用模型”残留检查、四个总开关与四个研究 Worker 专用开关文本断言、敏感信息模式检查。
- 结果：M6B 期间网站 API 的外部调用、可能计费、自动刷新和自动发布总开关继续关闭；已核验的单个新公司任务只由管理员在一次性窗口临时开启研究 Worker 专用开关，新增合格证据的模型解读另行授权。U01—U03 可以恢复，但本检查点不实际开始测试。
- 未解决阻塞：无文档阻塞；真实外部研究和模型调用仍须逐任务授权、运行后关闸并核对用量。

## 2026-09-04：R3.5 生产关闭与 M6B 恢复检查点

- 任务：在 PR #62 绿灯并合并后，依次完成香港生产加密备份、`0024 → 0025` 升级、卧安机器人隔离缓存零调用回放和无基金真实账号页面验收；全部通过后正式关闭 R3.5。本检查点只更新进度文档，不继续开发 Agent 或查询新公司。
- 合并与部署：PR #62 以 Squash Merge 合并为 `3b3b383`；CI Verify 全部通过。迁移前备份 `dealflow-radar-20260904T124028Z.dump.age` 为 590,814 字节，备份目录可读、加密校验通过并上传私有 COS，服务器未遗留明文。生产数据库升级到 `0025` 后，公司 24 家、事件 73 条、事件证据 82 条、原文档 78 份，数量均无异常减少；应用角色继续无超级用户和 `BYPASSRLS` 权限。
- 零调用回放：从升级后的生产库流式创建临时隔离副本，以明确拒绝联网的搜索和网页读取替身运行卧安机器人缓存回放；命中 4 次搜索缓存和 1 次文档缓存。新增搜索调用、网页读取、下载字节、文档、事件、模型 Token 和自动发布均为 0；R3.5 的一次缺口补查被拒绝联网并完整记录为失败审计。分析、分析用量和目标公司已发布事件计数前后相同；临时数据库、环境文件和脚本均已删除。
- 页面验收：真实无基金账号可用“卧安机器人”简称取得唯一已核验候选，并打开准确公司详情；页面正确说明当前无达到展示门槛的重要变化，没有把 robots 阻断或缺少正文的缓存噪声包装成结论。页面无服务端错误、浏览器控制台错误，也未显示投资金额、持股比例、内部估值、机构备注或私有线索。
- 安全与成本：生产 `EXTERNAL_CALLS_ENABLED`、`PAID_API_CALLS_ENABLED`、`AUTO_REFRESH_ENABLED`、`AUTO_PUBLISH_ENABLED`、公开网络研究 Worker 及投资解读开关继续关闭；本次生产验收新增外部业务调用、模型 Token、费用和自动发布均为 0。CI 依赖审计采用 npm 主扫描、仅在明确网络或服务故障时回退固定版本 OSV，任何真实漏洞或未分类失败继续失败关闭。
- 后续闸门：R3.5 正式关闭；恢复 M6B U01—U03 独立用户复测，优先验证用户是否理解、认可并愿意持续使用重要变化内容。未取得新的真实用户证据前，不开始 R3.6、新 Provider、递归 Agent、自动发布或长报告。

## 2026-09-04：R3.5 受限研究循环 V2（代码交付阶段记录）

- 任务：在现有 PostgreSQL 研究任务、缓存、预算、取消与断线恢复上，只为明确的高价值证据缺口增加最多一次非递归补查；只有新增、主体已核验且满足正文质量闸门的候选证据，才可进入版本化 JSON Schema 模型解读。模型结果作为独立的“仍待核实”派生层，不修改候选事件状态，不自动发布，不生成用户未请求的长报告。
- 关键文件：`backend/app/web_research_service.py`、`backend/app/investor_analysis.py`、`backend/app/deepseek.py`、`backend/app/investor_analysis_schema.py`、`schemas/research_candidate_analysis.schema.json`、`migrations/versions/0025_expand_analysis_rls_for_research_candidates.py`、公司详情 API/页面、Worker、`.github/workflows/ci.yml`、环境示例以及受影响的 R3.5 测试与设计文档。
- 实际命令：Ruff 检查与格式检查；定向与完整 Pytest；一次性 PostgreSQL 16 中的 `0025` 升级、RLS 负向测试、`0025 → 0024 → 0025` 往返和 Alembic 漂移检查；SQLite `base → 0025 → base`；前端类型检查与生产构建；生产 Compose 配置解析、API/前端镜像构建和生产预检；`git diff --check`、敏感信息模式检查和 Codex Security 差异审查。
- 测试结果：完整离线后端套件 329 项通过、16 项按显式外部或 PostgreSQL 条件跳过；PostgreSQL RLS 定向套件 16 项通过；SQLite/PostgreSQL 迁移往返和漂移、Ruff、前端类型、生产构建、Compose 配置、生产镜像和预检均通过。安全差异审查覆盖全部 13 个变更单元，无可报告漏洞；TAC 连接器未登录，因此受保护报告状态尚无法验证，不影响代码审查结论。本机 `npm audit` 前两次因 npm registry 连接中断未取得结果；服务恢复后以 npm 11.9.0 重新执行，结果为 0 个已知漏洞。CI 自带的 npm 10.8.2 仍调用正在退役的 `audits/quick` 接口并失败，因此将 CI 审计客户端最小固定为 npm 11.9.0；生产镜像构建慢时的作业上限调至 25 分钟。npm 批量漏洞接口在本机和 GitHub Runner 同时超时后，CI 改为仅对明确的网络或 5xx 故障运行独立 OSV 备用扫描；npm 真实漏洞、未分类失败、OSV 漏洞或 OSV 服务故障均仍使 CI 失败。本机已验证正常、漏洞、网络故障和未分类失败四条分支；OSV 2.5.0 对当前 `package-lock.json` 扫描 60 个包且无已知问题，Actionlint 1.7.12 通过。这些修正不删除审计、不改依赖或降低安全闸门。
- 自查修正：发现 `0021` 的旧 RLS 只允许已发布确定性变化分析，已新增 `0025` 将候选研究 Schema 与严格事件路由成对放行，不改历史迁移或数据；补查和原始来源回溯在外部调用前先持久化尝试状态，崩溃恢复不重复调用；模型入口又增加已核验主体提及和提示注入防护。
- 当时未解决阻塞：代码 PR 尚未合并或部署，香港生产数据库应继续保持 `0024`。合并后必须先备份、升级至 `0025`，再对卧安机器人现有缓存执行零新搜索/零模型调用回放和真实页面验收；该闸门通过前不恢复 U01—U03，不查询新公司。该阶段性阻塞已由上方生产关闭记录解除。

## 2026-09-04：R3.4 生产关闭与 R3.5 启动检查点

- 任务：在 PR #60 合并后核对生产备份、数据库升级、数据保护、卧安机器人缓存回放和真实页面验收，正式关闭 R3.4，并把当前唯一工程里程碑切换为 R3.5“受限研究循环 V2”。本检查点只修改文档，不修改业务代码、数据库、生产配置或数据。
- 验收结果：PR #60 以 Squash Merge 合并为 `ded390e` 并部署；生产数据库从 `0023` 升级到 `0024`。迁移前 PostgreSQL 自定义格式备份已通过 `pg_restore -l` 可读性检查，加密对象 `dealflow-radar-20260904T055029Z.dump.age` 已上传私有 COS，且未遗留明文。升级前后公司 24 家、事件 73 条、事件证据 82 条、原文档 78 份，均无异常减少；历史关系保守回填为 152 条原子事实和 169 条待复核支持关系。
- 零调用与页面验收：卧安机器人回放命中 4 次搜索缓存和 1 次文档缓存；新增搜索调用、网页下载、下载字节、模型 Token、新事件和自动发布均为 0。无基金真实账号可搜索并打开卧安机器人且看不到投资私有字段；Demo 共享事件的“事实与证据支持情况”可展开，显示待逐条复核和当前可见证据，未出现跨租户泄漏或页面错误。
- 运行说明：私有回放脚本曾在任务已完成后因提交事务并 `expire_all()` 导致 PostgreSQL 事务级 RLS 上下文丢失而误报失败；仅修正服务器 Git 忽略的私有验收脚本，在重新读取前恢复请求上下文。由于任务已完成且处于一小时冷却期，没有重复执行或产生新调用，不涉及业务代码。
- 后续闸门：R3.5 使用 1 个独立代码 PR，复用现有 PostgreSQL 状态机和调用上限；固定范围后最多补查一次关键证据缺口，只有新增合格证据才允许模型按版本化 JSON Schema 生成内部候选解读。不得新增 Provider、扩张调用上限、递归扩题、第二套运行时、自动发布或用户未请求的长报告；代码 PR 保持未合并，待项目负责人审查。

## 2026-09-04：R3.4 证据—事实支持账本

- 任务：在现有 PostgreSQL 事件、证据和原文档血缘上新增稳定原子事实以及逐证据支持关系，确定性记录“已支持、部分支持、冲突、待复核、无支持”；历史回填一律保守为待复核，公司详情只返回当前用户可见证据的中文支持状态。本阶段不新增搜索、模型 Agent、自动发布或第二套状态存储。
- 关键文件：`backend/app/fact_support.py`、`backend/app/models.py`、`backend/app/services.py`、`migrations/versions/0024_add_evidence_fact_support_ledger.py`、`scripts/reassess_event_fact_support.py`、公司详情页/API 类型、迁移/RLS/共享晋升/事实账本测试和受影响设计文档。
- 实际命令：Ruff 检查与格式检查；事实账本和私有晋升定向 Pytest；一次性 PostgreSQL 16 从空库升级到 `0024`、漂移检查、Demo 初始化、无 `BYPASSRLS` 应用账户和完整 Pytest；SQLite `base → 0024 → base`；有数据 PostgreSQL `0024 → 0023 → 0024`；前端类型检查和生产构建；生产 Compose 解析、API/前端镜像构建和预检；`git diff --check`、敏感信息模式检查和 Codex Security 差异审查。
- 测试结果：定向 13 项通过；全新 PostgreSQL/RLS 环境 337 项通过，仅有既有 Starlette/httpx 上游弃用警告；SQLite 迁移往返和漂移通过；有数据 PostgreSQL 升降级前后事件和证据均为 11 条，重新升级生成 11 条事实和 11 条待复核支持关系；前端类型、生产构建、生产镜像和预检通过。安全自查发现“同主体同值但未证明事实关系”可能误标为已支持，已收紧为 `pending_review` 并增加回归测试。真实搜索、目标网页、付费 API、业务模型 Token、费用和自动发布均为 0。
- 未解决阻塞：代码 PR 尚未合并或部署，香港生产数据库仍应保持 `0023`。本机 `npm audit` 三次因 npm registry `socket hang up` 未取得结果，必须以 PR CI 的依赖审计作为合并闸门。公司页的真实浏览器视觉验收、生产备份、升级到 `0024` 和卧安机器人缓存零调用回放必须等合并后另行完成；在此之前不开始 R3.5。

## 2026-09-04：R3.3 生产关闭与 R3.4 启动检查点

- 任务：在 PR #58 合并后完成香港生产加密备份、部署和卧安机器人现有缓存零调用回放，根据既定关闭条件正式关闭 R3.3，并将当前唯一工程里程碑切换为 R3.4“证据—事实支持账本”。本检查点只更新进度与验收口径，不修改业务代码、数据库或生产数据。
- 验收结果：生产提交为 `9b40438`，数据库保持 `0023`，API、前端和 PostgreSQL 健康。部署前备份已解密并通过 `pg_restore -l`，同时上传私有 COS。受控回放命中 4 次搜索缓存和 1 次文档缓存；新增搜索调用、公开网页下载、下载字节、模型 Token、新事件和自动发布均为 0。robots 阻断来源只记录为内部证据缺口和需授权人工导入，搜索摘要未生成用户可见事实。
- 后续闸门：R3.4 使用 1 个独立代码 PR，只在现有 PostgreSQL 血缘上增加原子事实、证据定位和逐条支持状态，优先确定性核验主体、数字、日期、重复和引用完整性。不新增搜索、LLM/Agent、自动发布、长报告或 JSONL 状态库；R3.4 通过前不开始 R3.5。

## 2026-09-04：R3.3 合规证据通路与原始来源回溯

- 任务：不绕过 robots，在现有受限网络研究 Worker 中分开记录搜索发现元数据、目标页面访问状态和已获取证据；合并有效主备搜索缓存且不新增调用；robots 阻断的高价值线索最多进行一次受预算、缓存、取消和恢复控制的原始来源回溯；仅对政府、监管/交易所和已核验官网 PDF 启用受限文本提取。本阶段不引入 LLM、自主 Agent、新 Provider、数据库迁移、API 或前端改动。
- 关键文件：`backend/app/web_research_service.py`、`backend/app/source_fetcher.py`、`backend/app/pdf_extractor.py`、`backend/app/config.py`、`tests/integration/test_bounded_web_research.py`、`tests/unit/test_source_fetcher.py`、`tests/unit/test_pdf_extractor.py`、`pyproject.toml`、`uv.lock`、环境示例和受影响的数据源/测试文档。
- 实际命令：Ruff 检查和格式检查；PDF、缓存融合、robots 回溯和证据闸门定向 Pytest；完整 SQLite 与一次性 PostgreSQL 16/`NOBYPASSRLS` 测试；SQLite `base → 0023 → base`和 PostgreSQL 迁移/漂移检查；Python 已安装依赖漏洞审计；前端依赖审计、类型检查和生产构建；生产 Compose 解析、后端/前端镜像构建、预检和镜像内 PDF 依赖验证；`git diff --check`和敏感信息模式扫描。
- 测试结果：定向组合 33 项通过；完整 SQLite 套件 310 项通过、15 项按既有 PostgreSQL 条件跳过；一次性 PostgreSQL/RLS 套件 325 项通过；SQLite/PostgreSQL 迁移和漂移、Ruff、Python 依赖审计（未发现已知漏洞）、前端审计（0 个已知高危漏洞）、类型、生产构建、Compose、预检和生产镜像通过。新回归验证主备缓存合并零新调用、受阻摘要不入库、原始来源回溯只执行一次、原候选队列已满时回溯结果仍有专用位、官方 PDF 受限入库以及非权威 PDF 失败关闭。全部自动测试使用 Mock/虚构数据，真实搜索、目标网站、模型 Token、付费调用和自动发布均为 0。
- 未解决阻塞：代码 PR 尚未合并或部署，R3.3 不得仅因自动测试而关闭。合并后必须先备份生产库并部署，再使用卧安机器人现有缓存做零新搜索回放；确认百度/博查新增调用、模型 Token 和自动发布均为 0，且取得合格证据或明确证据缺口后才能关闭 R3.3。扫描型 PDF 的 OCR、非标准 MIME 文件和 R3.4 证据—事实账本不在本 PR 范围。

## 2026-09-04：R3.2 关闭与 R3.3—R3.5 证据研究路线检查点

- 任务：根据 PR #56 和卧安机器人受控缓存回放结果正式关闭 R3.2。主体标点归一化和上市阶段精确变化召回已经完成；剩余问题是搜索 API 发现的部分高价值 URL 位于微信、百家号等按 robots 禁止普通自动客户端读取正文的页面，或正式证据位于尚未解析的官方 PDF。搜索 API 返回 URL 不等于目标网站授权自动读取，系统不绕过 robots，也不把摘要改成事实。
- 关键文件：`README.md`、`docs/02-system-architecture.md`、`docs/10-implementation-plan.md`、`docs/14-m6b-independent-user-validation-playbook.md`、`docs/IMPLEMENTATION_LOG.md`。本检查点不修改业务代码、数据库、迁移、API、前端、生产配置或数据。
- 决策结果：按 R3.3“合规证据通路与原始来源回溯”→ R3.4“证据—事实支持账本”→ R3.5“受限研究循环 V2”推进。R3.3 复用现有缓存、Provider 和 PostgreSQL 状态机，增加来源访问分类、最多一次原始来源回溯和受限官方 PDF 读取；不新增搜索供应商、LLM、自主多 Agent、通用爬虫或第二套状态存储。
- 后续闸门：先合并本文档检查点，再以一个独立代码 PR 只实施 R3.3；先用 Mock/fixture 和卧安机器人现有缓存做零搜索回放。R3.3 通过前不查询新公司、不恢复 U01—U03，也不提前开始 R3.4 或 R3.5。

## 2026-09-04：R3.2 主体匹配与高价值变化召回

- 任务：不改变搜索查询、Provider、调用上限或既有缓存身份指纹，新增仅用于主体比对的 Unicode NFKC 归一化，使工商全称中全角/半角括号可等价匹配；补充递表、递交上市申请/招股书、通过聆讯、境外上市或全流通备案、启动招股、公开发售和正式上市等精确状态变化，静态“未上市/拟上市”资料仍不通过。内部任务新增主体不匹配、URL 阻断、资料页、时间不合格和通过的聚合计数，不记录查询正文或供应商错误正文。变化后的内容质量策略版本为 `bounded-web-quality-v3`。
- 关键文件：`backend/app/web_research_service.py`、`tests/integration/test_bounded_web_research.py`、`docs/IMPLEMENTATION_LOG.md`。无数据库模型、迁移、API、前端、权限或部署配置变更。
- 实际命令：Ruff 检查与格式检查；受限研究定向 Pytest；完整离线 Pytest；一次性 PostgreSQL 16 从空库升级到 `0023`、迁移漂移检查、初始化非表所有者 `NOBYPASSRLS` 账户并运行完整 RLS 套件；SQLite `base → 0023 → base`；前端依赖审计、类型检查和生产构建；`git diff --check`。
- 测试结果：定向 34 项通过；完整离线套件 301 项通过、15 项按既有外部条件跳过；PostgreSQL/RLS 套件 316 项通过；SQLite/PostgreSQL 迁移与漂移、Ruff、前端审计（0 个已知高危漏洞）、类型和生产构建通过。新回归验证旧缓存指纹不变、全角/半角正例、注册地不同的近似主体负例、静态上市资料负例、精确上市阶段词、聚合过滤原因，以及现有缓存命中时百度/博查 Provider 调用为 0。全部自动测试使用 Mock/虚构数据，真实搜索、网页、模型 Token、费用和自动发布均为 0。
- 未解决阻塞：代码 PR 合并部署前不得对生产运行。部署前先加密备份；然后只使用卧安机器人已有搜索缓存回放，百度和博查新增搜索调用必须均为 0，最多安全抓取 3 个现有候选的免费公开原页，模型 Token 和自动发布必须为 0。

## 2026-09-04：R3.2 主体匹配与高价值召回检查点

- 任务：完成 PR #53 生产闭环后的窄范围归因和里程碑收敛。生产百度主源已可用，搜索缓存已包含卧安机器人递表、通过聆讯、境外上市备案、招股及上市后变化等高价值结果；零候选的主要原因是全角/半角括号造成法定全称误拒绝，以及上市阶段精确变化词不完整。将当前唯一工程里程碑定为 R3.2，不重做数据源或整体架构审计。
- 关键文件：`README.md`、`docs/02-system-architecture.md`、`docs/10-implementation-plan.md`、`docs/14-m6b-independent-user-validation-playbook.md`、`docs/IMPLEMENTATION_LOG.md`。本检查点不修改业务代码、数据库、迁移、API、前端或生产配置。
- 实际结果：PR #53 已以生产提交 `4754f68` 部署，数据库仍为 `0023`，部署前加密备份已上传私有 COS。卧安机器人复测完成 2 次百度新搜索、1 次博查新搜索、1 次博查缓存命中和 2 次免费公开网页检查，共下载 133,052 字节；生成 1 份内部文档、0 个事件，模型 Token、估算费用和自动发布均为 0。八个受控运行开关已恢复关闭，服务健康。
- 后续闸门：先合并本文档检查点，再以一个代码 PR 单独修复主体匹配和上市阶段变化召回；不改缓存指纹、不新增 Provider、模型、PDF 解析或新公司。合并部署后使用卧安机器人现有缓存回放，百度和博查新增搜索调用必须均为 0；回放通过前 M6B 保持 `修复后复测`。

## 2026-09-03：R3.1 生产复测来源质量窄范围修复

- 任务：根据卧安机器人首次真实研究结果，在不增加搜索组、Provider 或模型调用的前提下，阻止爱企查等商业资料档案页及搜索结果已明确过旧/未来的页面进入网页抓取队列；将证券监管和交易所正式披露域名置于已核验官网之前、政府网站之后，并把已核验 HTTPS 官网作为零搜索调用候选加入同一有限队列；第二检索组聚焦公告、变更、上市备案及明确产品、产能和风险变化。百度生产 Key 由项目负责人通过不回显、双次确认和原子替换流程单独更新，不进入代码、日志或 Git。
- 关键文件：`backend/app/web_research_service.py`、`tests/integration/test_bounded_web_research.py`、`README.md`、`docs/05-data-source-strategy.md`、`docs/10-implementation-plan.md`、`docs/11-test-strategy.md`、`docs/12-operations-runbook.md`。无模型、数据库模型、迁移、API 或前端改动。
- 实际命令：Ruff 检查和格式检查；Provider/Worker 定向测试；完整 SQLite 与一次性 PostgreSQL 16/RLS 测试；SQLite `base → 0023`、`alembic check` 和 `0023 → base`；前端依赖审计、TypeScript 和生产构建；生产 Compose/预检和后端、前端镜像构建；`git diff --check` 与敏感信息扫描。
- 测试结果：Provider/Worker 定向组合 39 项通过；完整离线套件 290 项通过、15 项按既有条件跳过；一次性 PostgreSQL/RLS 套件 305 项通过；SQLite/PostgreSQL 迁移与漂移、Ruff、前端审计（0 个已知高危漏洞）、类型检查、生产构建、Compose、预检和镜像构建通过。新增回归明确验证生产实测中的 `aiqicha.com/company_detail_*` 和过旧结果不会进入抓取队列，Provider 诊断仍保留；监管披露排序和官网零搜索调用种子均有自动测试。全部自动测试使用 Mock/虚构数据，真实百度、博查、目标网页、业务模型 Token、费用和自动发布均为 0。
- 未解决阻塞：新百度 Key 已确认安全写入且与备份不同，但尚未实际调用验证；代码 PR 合并部署后，只允许复用卧安机器人做一次受控研究，不查询第二家公司。V1 仍不解析交易所 PDF；若同公司复测证明 PDF 是主要漏报来源，再单独立项，不在本修复中扩张范围。

## 2026-09-03：R3.1 PR 2 可信来源排序、近期变化识别和百度主源诊断

- 任务：合并 PR #51 后完成 R3.1 第二个独立代码阶段。对主体匹配的搜索结果按政府、已核验公司官网、经复核媒体原文、其他可定位页面和资料聚合页排序；明确过旧或未来日期降级，最终只有原页发布时间处于可配置近期窗且正文通过变化闸门才生成候选；事件实际发生时间未独立核验时保持未知。百度错误只记录安全类别和 HTTP 状态，不保存密钥、查询正文或上游报错正文；只在主源失败或缺少合格主体结果时回退博查。
- 关键文件：`backend/app/config.py`、`backend/app/web_search.py`、`backend/app/web_research_service.py`、`tests/unit/test_database_config.py`、`tests/unit/test_web_search_providers.py`、`tests/integration/test_bounded_web_research.py`、环境变量示例及 R3.1 数据源、成本、测试、运维和里程碑文档。无数据库模型或迁移变更。
- 实际命令：Ruff 检查与格式检查；Provider、配置和受限研究定向 Pytest；完整离线 Pytest；SQLite 从空库升级到 `0023`、Alembic 漂移检查和降级到空库；一次性 PostgreSQL 16 从空库升级、初始化受限应用角色并运行完整 RLS 套件；前端依赖审计、类型检查和生产构建；生产 Compose 解析、镜像构建和预检；`git diff --check`、敏感信息搜索和 Codex Security 差异审查。
- 测试结果：Provider、配置和 R3 定向组合 64 项通过，其中最新集成测试 21 项；完整离线套件 288 项通过、15 项按既有条件跳过；隔离 PostgreSQL/RLS 套件 303 项通过。SQLite 迁移往返、Alembic 漂移、Ruff、前端审计（0 个已知高危漏洞）、类型、生产构建、Compose、镜像和预检通过；仅有既有 Starlette/httpx 上游弃用警告。安全差异审查覆盖 3/3 个变更源文件及直接安全依赖，无可报告漏洞；TAC 状态因连接器未登录无法验证，仅影响受保护报告展示。全部测试使用 Mock/虚构数据，真实搜索、网页、付费 API、业务模型 Token、费用和自动发布均为 0。
- 未解决阻塞：PR 2 尚未合并或部署；本轮没有界面变更，因此浏览器价值验收须等待合并部署后，先对北京智齿博创既有缓存做零外部调用回放，再由项目负责人单独授权一家新公司真实验收；通过前不恢复 M6B 扩样。

## 2026-09-03：R3.1 PR 1 候选隔离、正文提取和变化质量闸门

- 任务：修复受限公开网络研究把导航、推荐卡片或其他主体关键词误当成目标公司变化的问题。新页面先提取 `main/article` 或清理后正文；只有正文同一支撑片段同时包含准确公司主体和显式重要变化，且页面有可靠发布日期时，才生成用户可见待核实事件；否则只保留 `system_restricted` 原始文档、主体提及和质量决策审计。
- 关键文件：`backend/app/source_fetcher.py`、`backend/app/web_research_service.py`、`tests/unit/test_source_fetcher.py`、`tests/integration/test_bounded_web_research.py`、`docs/10-implementation-plan.md`、`docs/11-test-strategy.md`。无数据库模型或迁移变更。
- 实际命令：Ruff 检查与格式检查；正文提取与受限研究定向 Pytest；完整离线 Pytest；SQLite 从空库升级到 `0023`、漂移检查和降级到空库；隔离 PostgreSQL 16 从空库升级、初始化受限应用账户并执行完整 RLS 套件；前端依赖审计、类型检查和生产构建；`git diff --check`。
- 测试结果：定向 38 项通过；完整离线套件 269 项通过、15 项按既有条件跳过；隔离 PostgreSQL 套件 284 项通过；SQLite 迁移往返、Alembic 漂移、前端审计（0 个已知高风险漏洞）、类型和生产构建通过。全部测试使用 Mock/虚构数据，真实搜索、网页、付费 API、业务模型 Token、费用和自动发布均为 0。
- 未解决阻塞：PR 1 尚未合并或部署；PR 2 的可信来源排序、近期性识别和百度主源诊断未开始。在两个 PR 均通过且完成缓存零调用回放前，不查询新公司、不恢复 M6B 扩样。

## 2026-09-03：R3 生产实样验收与 R3.1 质量闸门检查点

- 任务：合并并部署 R3 的最后一项单步暂停修复，使用北京智齿博创既有申请完成一次受控真实研究；确认任务、进度、恢复、主备回退和安全抓取工程链路可运行，同时识别正文噪声和变化误判。经项目负责人批准，将现有噪声候选审计驳回并保留原始证据，把下一唯一工程里程碑收敛为 R3.1；本检查点只更新文档，不开始 R3.1 代码或查询新公司。
- 关键文件：`README.md`、`docs/10-implementation-plan.md`、`docs/14-m6b-independent-user-validation-playbook.md`、`docs/IMPLEMENTATION_LOG.md`；生产数据操作仅改变一条候选的审核状态，不修改代码、迁移、原文或证据。
- 实际命令：核对 PR #49 HEAD、CI 和可合并状态并执行 Squash Merge；创建、校验并上传客户端加密数据库备份；以固定提交构建和部署香港 release，执行生产预检、Alembic、角色收敛和健康检查；在显式受控窗口中单步继续既有研究任务；只读核对调用账本、文档、事件、证据和可见性；通过事务写入拒绝审核记录并复核安全开关；执行文档差异、链接和敏感信息检查。
- 测试结果：PR #49 合并后的生产提交为 `2ac6006`，数据库为 `0023`，服务健康。真实任务完成 2 次百度搜索尝试、2 次博查回退和 7 次公开网页检查，共下载 40,141 字节；生成 1 份原始文档、1 条候选事件和 0 条已发布事实，模型 Token、估算费用和自动发布均为 0。百度两次返回 HTTP 错误；新芽资料页虽通过页面级主体匹配，但正文混入导航和其他项目融资内容。该候选已审计驳回，原文、证据、任务和调用账本均保留，个人可见待核实线索恢复为 0；八个运行安全开关关闭，公网登录页正常。
- 未解决阻塞：R3 工程闭环已经完成，但内容质量尚未达到继续邀请测试的门槛。R3.1 必须先实现平台内部候选隔离、正文提取和主体/时间/变化质量闸门，再实现可信来源排序、近期变化识别和百度错误诊断；缓存零调用回放通过前不得查询新公司或恢复 U01—U03。

## 2026-09-03：R3 单步暂停时钟与结束路径修复

- 任务：修复人工单步受控运行把步骤之间的暂停时间计入 180 秒执行上限、导致任务未发出下一查询就提前结束的问题；每次租约单独记录实际步骤起点，并在超时结束事务后重新绑定既有 Worker 权限上下文，不改变查询预算、缓存、证据或发布规则。
- 关键文件：`backend/app/web_research_service.py`、`tests/integration/test_bounded_web_research.py`、`tests/integration/test_postgres_rls.py`。
- 实际命令：Ruff 与格式检查；单步暂停针对性测试；全新 PostgreSQL 16 从空库升级到 `0023`、初始化受限应用角色并执行 Worker RLS 恢复回归；完整后端 Pytest；`git diff --check`。
- 测试结果：单步暂停回归通过；真实 PostgreSQL RLS 测试确认一小时前开始但当前重新取得租约的任务仍执行下一检索组，不会误终止；完整后端测试 264 项通过、15 项按既有条件跳过，只有既有 Starlette/httpx 上游弃用警告。测试不调用真实搜索、网页或模型。
- 未解决阻塞：北京智齿博创任务已在外部调用前安全停止，需在修复部署后将这一个任务从错误的超时结束状态恢复到尚未执行的第二检索组；第一组缓存和调用账本继续保留。

## 2026-09-03：R3 百度检索长度兼容修复

- 任务：修复真实单步验收发现的百度中文检索长度超限；已核验公司研究改为“工商全称加精简模块关键词”，不再在发现查询中重复携带信用代码，并在百度适配器执行官方 72 加权字符上限保护。抓取后的公司名称、信用代码或官网域名主体校验保持不变。
- 关键文件：`backend/app/web_research_service.py`、`backend/app/web_search.py`、对应 Provider 与研究流程测试。
- 实际命令：Ruff 检查与格式检查；百度和博查 Provider、受限研究流程及 Worker CLI 针对性 Pytest；完整后端 Pytest；`git diff --check`。
- 测试结果：针对性测试 26 项通过，完整后端测试 263 项通过、15 项按既有条件跳过，只有既有 Starlette/httpx 上游弃用警告。全部测试使用 Mock，不调用百度、博查、目标网站或大模型，不产生费用或自动发布。
- 未解决阻塞：生产任务已安全暂停在第二检索组，首次百度 HTTP 错误和随后博查回退均已入账；修复合并部署前不继续消耗外部查询额度。

## 2026-09-03：R3 PostgreSQL Worker 权限上下文修复

- 任务：修复受限公开网络研究 Worker 在 PostgreSQL RLS 环境中提交任务准备事务后丢失请求上下文、把已排队任务误报为 `idle` 的问题；仅在事务提交边界重新绑定既有 Worker 用户和租户，不改变搜索、回退、预算、证据或发布规则。
- 关键文件：`backend/app/web_research_service.py`、`tests/integration/test_postgres_rls.py`。
- 实际命令：Ruff 与格式检查；R3 全部针对性 Pytest；全新 PostgreSQL 16 从空库升级到 `0023`、初始化受限应用角色并执行新增 Worker RLS 回归测试；`git diff --check`。
- 测试结果：R3 针对性测试 9 项通过；新增 PostgreSQL RLS 回归确认 Worker 在任务准备、租约和 Provider 结果提交后仍能继续下一阶段，不再误报 `idle`。测试和修复没有调用百度、博查、目标网站或大模型，没有产生费用和自动发布。
- 未解决阻塞：修复合并部署前，生产中的北京智齿博创任务保持安全排队且四个 Worker 开关保持关闭；部署后再从该任务继续单步受控真实查询。

## 2026-09-02：R3 受限公开网络研究 Agent V1

- 任务：在 ADR-0018 和 R2 准入结论下实现后台公开网络研究闭环；百度作为主搜索源，只有主源失败或未返回主体匹配结果时才回退博查。复用 PostgreSQL 任务、可信来源抓取、证据和个人申请，不在同步页面联网，不生成报告或调用模型，不自动发布。
- 关键文件：`backend/app/web_search.py`、`backend/app/web_research_service.py`、`scripts/run_web_research_worker.py`、`migrations/versions/0023_add_bounded_web_research_cache.py`、`backend/app/personal_features.py`、`frontend/app/reviews/`、`frontend/app/watchlist/page.tsx`、生产 Compose、环境变量示例及 R3 相关测试和文档。
- 实际命令：R3 Provider、Worker、任务、缓存、取消和权限针对性 Pytest；完整 Ruff、格式和 Pytest；SQLite 迁移往返；全新 PostgreSQL 16 的 `0023 → 0022 → 0023`、Schema 漂移和 RLS 负向测试；前端依赖审计、TypeScript 和 Next.js 生产构建；生产 Compose profile 解析；隔离虚构数据库下的平台管理员准入、九类进度、取消和刷新恢复浏览器冒烟；Codex Security 工作区差异扫描；密钥和退役供应商活动入口扫描；`git diff --check`。
- 测试结果：R3 针对性测试 46 项通过；完整 PostgreSQL 套件 276 项通过，只有既有 Starlette/httpx 上游弃用警告；Ruff、格式、SQLite/PostgreSQL 迁移、Alembic 漂移、31 张 RLS 表、前端依赖审计（0 个已知漏洞）、类型检查、生产构建和 Compose 解析通过。浏览器确认管理员只能把唯一匹配的已核验共享公司接入队列，个人页显示九类进度、队列位置和取消状态，刷新后状态仍在，控制台无错误。安全扫描覆盖 17/17 个变更源文件，未发现可报告漏洞；真实百度、博查、目标网站、付费 API、业务模型 Token、费用和自动发布均为 0。
- 未解决阻塞：代码无阻塞，最终 PR 保持未合并。合并后需先备份香港数据库、升级到 `0023` 并保持 Worker 四重开关关闭；随后只用一家具名新公司进行一次受控真实 Provider 兼容性与内容价值验收。V1 不做补充研究轮次、模型抽取、已核实事实自动晋升或完整报告；单 Worker 发生进程级中断时，正在进行的单个外部请求可能无法做到绝对一次语义，扩容或正式运营前再评估原子预算预留。

## 2026-09-02：R1 旧商业数据供应商生产安全退役

- 任务：合并 PR #44，在香港邀请测试环境完成旧商业数据供应商活动集成的生产退役；保持通用个人申请、取消、额度和断线恢复，保留必要历史审计，不启动 R2/R3。
- 关键文件：`migrations/versions/0022_retire_legacy_business_data_provider.py`、`backend/app/personal_features.py`、`backend/app/providers.py`、`backend/app/services.py`、`compose.production.yml`、`deploy/compose.single-host.yml`、`docs/12-operations-runbook.md`。
- 实际命令：复核并 Squash Merge PR #44；同步 `main`；通过 Tailscale SSH 只读核对生产 `0021`、数据数量、安全开关和磁盘；创建两份客户端加密 PostgreSQL 自定义格式备份、校验并上传私有 COS；以固定提交构建镜像、运行生产预检、升级到 `0022`、收敛应用角色并原子切换 release；执行 API、浏览器、普通用户跨租户和基金授权 RLS 正反向回归；最后删除旧 Secret、13 个缓存文件、旧环境变量和 3 个无引用私有一次性文件。
- 测试结果：生产提交为 `e577672`，`/health`、`/ready`、公网登录页、systemd 健康检查和备份定时器正常；公司、基金、投资、文档、事件、证据、报告、核验、任务和用量总数未减少。12 家仅由旧来源核验的公司恢复待核验，21 条仅由旧来源支撑的事件撤回，旧证据展示数和旧专用 RLS 均为 0；普通用户跨用户/跨租户结果为 0，基金授权用户可见自己的 10 条投资关系，无基金用户为 0。活动代码、容器环境、生产 Secret、缓存和私有运营目录均无旧供应商入口；外部商业数据调用、模型 Token、自动刷新和自动发布为 0。
- 未解决阻塞：R1 无阻塞并已关闭。下一闸门是 R2 搜索源准入与实样验证；未选出合格主源和备用源前，不编码 R3 研究 Agent，也不扩大邀请测试。

## 2026-09-01：M6B 首轮反馈与 P1 修复检查点

- 任务：记录 U00 创始人回归通过及首名外部测试者的新公司研究阻塞；将 M6B 状态收敛为 `修复后复测`，固定 P1 先于后置模型架构工作的顺序，并保存已确认的三个 Pi 借鉴边界。本轮不修改业务代码、数据库、生产配置或既有申请。
- 关键文件：`docs/10-implementation-plan.md`、`docs/14-m6b-independent-user-validation-playbook.md`、`docs/IMPLEMENTATION_LOG.md`；逐次原始记录只进入 Git 忽略的 `data/private/m6b-validation/`。
- 实际命令：核对当前分支、提交和工作区；定点检查 M6B 手册、里程碑看板、生产运行手册、个人申请状态映射及生产数据库申请状态；通过 Tailscale SSH 只读核对生产容器开关和 Worker 状态；执行文档差异与链接自查。
- 测试结果：U00 回归达到目的但不计入独立样本；外部申请已保存准确工商主体，生产数据库状态为旧版 `pending`，没有公司、研究任务、身份候选或外部调用。生产 API 的按需研究、外部调用、天眼查身份/研究、付费、自动刷新和自动发布开关均为 false，运行 Worker 为 0。缺陷定级为 P1，没有发现主体串错、私有泄露、错误事实发布或未经授权调用。
- 未解决阻塞：在 M6B-P1 完成并用原外部申请复测前，不扩大邀请测试。完整 Pi ADR 和模型运行时解耦继续后置，不抢占真实用户核心流程修复。

## 2026-08-31：M6B 独立邀请用户验证启动基线

- 任务：把 M6B 从里程碑标题落实为可执行的独立用户运营验证；固定先 3 名再扩大到 5—10 名的分段方式、无引导核心任务、价值指标、P0/P1/P2 停止规则、M7 前置闸门和个人信息保存边界。本轮不修改业务代码、数据库、生产配置或既有数据。
- 关键文件：`docs/14-m6b-independent-user-validation-playbook.md`、`docs/templates/m6b-test-session.md`、`docs/10-implementation-plan.md`、`docs/IMPLEMENTATION_LOG.md`。
- 实际命令：核对 `main` 与 `origin/main`、当前提交和工作区；定点阅读 M6A 手册及里程碑计划；检查私有记录目录已被 `.gitignore` 忽略；执行文档链接、关键闸门文本和 `git diff --check` 自查。
- 测试结果：M6B 手册明确了 3 名独立个人的构成、10 项单人任务、实际用时与帮助次数记录、内容理解复述、成本审计、四种阶段结论，以及至少 5 名独立用户、核心任务独立完成率和再次使用意愿等 M7 讨论前置条件；真实联系方式和逐次记录只进入 Git 忽略的 `data/private/m6b-validation/`。没有运行生产服务，没有外部查询、付费调用、模型 Token、数据库写入或自动发布。
- 未解决阻塞：尚缺首批 3 名独立测试者的受邀登录标识和真实查询对象，因此本轮只完成可执行基线，尚未产生独立用户验证结论；联系方式不得写入仓库，需由项目负责人通过私有方式逐一提供并参与验证码授权。

## 2026-08-31：Demo 变化证据详情契约修复

- 任务：修复虚构重要变化证据虽然显示“查看平台证据详情”、但后端因不识别 Demo 载荷格式而返回 404 的问题；只允许明确支持的授权结构化证据和虚构 Demo 变化格式显示详情入口，未知或格式错误的载荷保持不可读取；不修改数据库、权限边界或真实授权证据格式。
- 关键文件：`backend/app/services.py`、`tests/integration/test_personal_company_query.py`、`docs/IMPLEMENTATION_LOG.md`。
- 实际命令：证据详情、私有证据隔离、授权研究和变化检测针对性 Pytest；完整 Ruff、格式和 Pytest；前端依赖审计、TypeScript 和 Next.js 生产构建；`git diff --check`。
- 测试结果：针对性测试 4 项通过；完整 Pytest 291 项通过、16 项按预期跳过，只有既有 Starlette/httpx 上游弃用警告；Ruff、格式、TypeScript、生产构建和依赖审计通过，已知高危依赖为 0。自动测试确认 Demo 详情输出“变化前/变化后”，真实授权证据保持可用，未知载荷不显示错误入口，私有证据继续返回 404；真实天眼查、付费 API、模型 Token、费用和自动发布均为 0。
- 未解决阻塞：代码无阻塞；合并并部署香港环境后仍需用现有真实账号复测 Demo 证据详情、真实授权证据回归和 390px 窄屏。

## 2026-08-31：投资者优先公司详情页

- 任务：将公司详情页调整为“工商身份 → 近期重要变化 → 待核实线索 → 机构私有叠加 → 基础资料”的投资者阅读顺序；首次查看只突出近 90 天重要变化，复访优先显示自上次查看以来的新变化，首屏最多展开 3 条；先展示通俗的股东影响，再按需展开变化细节、不确定性和证据。不新增数据库迁移，不改变既有公私数据权限。
- 关键文件：`backend/app/personal_features.py`、`backend/app/schemas.py`、`frontend/app/companies/[id]/page.tsx`、`frontend/app/companies/[id]/personal-change-panel.tsx`、`frontend/app/globals.css`、`frontend/lib/api.ts`、`tests/integration/test_personal_changes_reports.py`、`docs/DECISIONS/ADR-0017-investor-material-change-layer.md`、`docs/08-api-design.md`、`docs/09-ui-wireframes.md`、`docs/11-test-strategy.md`。
- 实际命令：个人变化针对性 Pytest；完整 Ruff、格式和 Pytest；前端依赖审计、TypeScript 和 Next.js 生产构建；隔离 SQLite 虚构数据下的首次查看、复访、4 条变化折叠、基础资料分组、私有字段负向和 390px 窄屏浏览器验收；`git diff --check`。
- 测试结果：针对性测试 3 项通过；完整 Pytest 290 项通过、16 项按预期跳过，只有既有 Starlette/httpx 上游弃用警告；Ruff、格式、TypeScript、生产构建和依赖审计通过，已知高危依赖为 0。浏览器确认首次显示近 90 天、复访明确提示无新变化、首屏只展开 3 条且其余折叠、证据和前后值可展开、基础资料默认收起、个人页面不显示机构私有字段；390px 无横向溢出，控制台无警告或错误。临时数据库和服务已清理，真实天眼查、付费 API、真实模型 Token、费用和自动发布均为 0。
- 未解决阻塞：无代码阻塞。页面价值仍取决于已有结构化变化和证据质量；本任务不扩大采集范围、不调整 Agent 评分标准、不部署生产。

## 2026-08-30：PR #37 生产部署与 M6A 页面价值收口

- 日期：2026-08-30
- 任务：将 PR #37 的合并提交 `2f4a3aa` 部署到香港邀请测试环境，修正生产天眼查缓存目录权限并证明一次性 Worker 可以持久复用缓存；在不重复查询苏州涌现、不再次调用 DeepSeek 的前提下，完成真实公司资料基线和虚构重要变化 Agent 的生产页面技术与视觉验收，并把下一闸门收敛为 M6B 独立用户验证。
- 关键文件：`compose.production.yml`、`deploy/compose.single-host.yml`、`docs/10-implementation-plan.md`、`docs/12-operations-runbook.md`、`docs/IMPLEMENTATION_LOG.md`。
- 实际命令：核对 `main`、PR #37 和远端 CI；通过 Tailscale SSH 记录数据库与用量基线；创建客户端加密 PostgreSQL 自定义格式备份、校验 SHA-256 并上传香港私有 COS；以 Git 归档和固定提交标签构建 API、前端及备份镜像；运行生产配置预检和幂等 Alembic 升级后原子切换 release；执行 API 存活/就绪、systemd 健康检查、数据库计数、运行时开关和 API 无缓存挂载检查；执行按需研究 Worker dry-run；使用本地 Mock 响应在两个相继退出的生产 Worker 容器中完成缓存写入与重放，再删除该测试缓存并校验原缓存摘要；使用真实受邀个人账户浏览苏州涌现公司页、股东和知识产权证据页，以及 Demo 重要变化和 Agent 解读卡片。
- 测试结果：升级前备份 `dealflow-radar-20260829T225211Z.dump.age` 为 489389 字节，SHA-256 为 `6b72d64b96d964edc3f6197eb13add456c54dd1a19eeb855c86c61164451e90e`，本地校验和 COS 上传成功。生产 API/前端镜像均为 `2f4a3aa`，数据库仍为 `0021`；公司、事件、证据、原始文档、研究任务和分析记录计数与部署前一致。缓存目录为 `0700 / 10001:10001`，13 个既有缓存文件内容摘要部署前后均为 `b9c43ef5b3c9bc80c37ca44e666e2f68c5ebb499fc2380954d30461b16b77180`；Mock 首次写入为 1 次模拟调用，第二个容器重放为 0 调用/1 次缓存命中，测试文件随后精确删除。Worker dry-run 显示运行限额 100/天、1000/月，保留 10% 后有效限额为 90/天、900/月；所有业务外部、付费、自动刷新、自动发布、天眼查和 Agent 开关均为 false，常驻 API 无缓存挂载，运行 Worker 数为 0。本次部署增量业务外部调用、模型 Token、费用和自动发布均为 0。生产浏览器确认苏州涌现显示 5 类资料，股东详情包含 9 条股东、工商登记持股比例和出资字段；知识产权页如实显示 40 条分类数量但 0 条逐项明细；Demo 页面清楚分开 20%→25% 的事实变化、模型解读、不确定性和后续观察，页面无控制台错误或私有投资字段。
- 未解决阻塞：苏州涌现当前只有第一份结构化基线，因此“重要变化”为 0 是符合规则的结果，不能伪造前后变化；知识产权供应商响应没有逐条记录，后续只有真实用户证明该缺口影响价值时才评估明细接口。M6A 工程与创始人技术验收已收口，但尚未由独立测试者证明持续使用价值；下一闸门是 M6B，当前不扩充普通功能、批量公司或自动监控范围。

## 2026-08-29：按需研究 Worker 持久化缓存部署修复

- 任务：为生产 Compose 增加默认关闭的专用按需研究 Worker，在香港单机环境将天眼查私有 Provider 缓存绑定到固定主机目录，避免一次性容器退出后丢失缓存并重复消耗 API 次数；常驻 API 不获得该缓存访问权。
- 关键文件：`compose.production.yml`、`deploy/compose.single-host.yml`、环境变量示例、`.github/workflows/ci.yml`、`tests/unit/test_on_demand_worker_deployment.py`和本运维手册。
- 实际命令：针对部署与缓存契约 Pytest；完整 Ruff、格式和 Pytest；SQLite `base → 0021 → base` 与 Schema 漂移；一次性 PostgreSQL 16 迁移、应用角色和 RLS 套件；前端依赖审计、TypeScript 和生产构建；生产镜像与 Compose 解析；两个相继退出的新容器共享同一私有目录完成缓存写入/重放；profile、开关隔离、缺失目录失败和文件权限检查；Codex Security 工作区差异扫描；`git diff --check`。
- 测试结果：部署/Provider/Worker 针对性测试 13 项通过；完整 Pytest 289 项通过、16 项按预期跳过；独立 PostgreSQL RLS/API 套件 16 项通过；SQLite 和 PostgreSQL 迁移、Ruff、格式、前端类型检查、生产构建、生产镜像及 Compose 均通过，前端依赖审计为 0 个已知漏洞。首个容器写入时 `external_calls=1`、`cache_hits=0`，退出后第二个全新容器重放为 `external_calls=0`、`cache_hits=1`，缓存文件权限为 `0600`；默认 profile 不包含该 Worker，Worker 临时开关不影响 API，缓存目录缺失时安全失败。Codex Security 扫描未发现可报告问题；真实天眼查、付费 API、大模型和自动发布调用均为 0。
- 未解决阻塞：代码无阻塞；合并后部署前需由运维以 `10001:10001`、`0700` 创建私有缓存目录并配置 `PROVIDER_CACHE_DIRECTORY`，再按受控窗口临时开启专用 Worker 开关。本 PR 不部署或修改生产数据。缓存保留期限、静态加密和文件型 Docker Secret 可作为后续独立加固，不阻塞本修复；TAC 状态因 Codex Security Access 连接器未登录而无法验证。

## 2026-08-28：M6A-4 证据体验与授权来源记录语义修正

- 任务：改善平台证据详情页宽度、覆盖提示和中文错误页；把天眼查风险/人员数量概览从“待核实线索”拆为可展示但不作风险结论的“授权来源记录·影响待判断”；停止把 `ftShareholding` 误标为持股比例，并保守隐藏旧快照中被误标的日期；会话过期统一使用经生产预检的公网域名跳转。
- 关键文件：`backend/app/tianyancha.py`、`backend/app/on_demand_research.py`、`backend/app/services.py`、`frontend/app/evidence/[id]/`、`frontend/app/companies/[id]/page.tsx`、`frontend/lib/public-origin.ts`、`scripts/check_production_config.py` 及相关配置、测试和文档。
- 实际命令：Ruff 逻辑和格式检查；定点及完整 Pytest；SQLite 迁移往返与 Schema 漂移；全新 PostgreSQL 16 迁移、受限应用账户和 RLS 回归；前端依赖审计、TypeScript、Next.js 生产构建和生产容器构建；Compose 解析与初始安全开关预检；Codex Security 工作区差异扫描。
- 测试结果：定点测试 `50 passed`；完整离线 Pytest `258 passed, 14 skipped`；PostgreSQL RLS `11 passed, 3 skipped`；SQLite `base → 0020 → base` 与 Alembic 漂移检查通过；前端无已知高危依赖漏洞，类型检查、生产构建、生产容器和失败关闭预检通过；安全差异扫描未发现可报告漏洞。真实天眼查、付费 API、业务模型 Token、费用和自动发布均为 0。
- 未解决阻塞：无代码阻塞。本 PR 不猜测天眼查未验证字段，因此知识产权逐条明细和真实股东持股比例留给下一个受控响应契约 PR；浏览器视觉验收待部署后由真实账号完成。

## 2026-08-27：M6A-4 授权数据明细与平台证据详情

- 任务：从已有授权数据缓存投影股东、招投标、工商变更等明细，增加平台证据详情 API/页面，并纠正零记录、人员风险和天眼查首页的误导语义。
- 关键文件：`migrations/versions/0020_add_evidence_display_details.py`、`backend/app/tianyancha.py`、`backend/app/on_demand_research.py`、`backend/app/services.py`、`frontend/app/evidence/[id]/page.tsx` 和 ADR-0016。
- 实际命令：Ruff；授权数据/按需研究/权限/迁移针对性 Pytest；完整 Pytest；SQLite/PostgreSQL 迁移和 RLS；TypeScript 和 Next.js 生产构建。
- 测试结果：Ruff 通过；Pytest `256 passed, 14 skipped`；独立 PostgreSQL 16 RLS `11 passed, 3 skipped`；SQLite `base → 0020 → 0019 → 0020 → base` 和 Alembic 漂移检查通过；前端无高危依赖漏洞、类型检查和生产构建通过；本地浏览器从公司详情进入平台证据详情，结构化字段、隐私提示和供应商首页不可定位提示均正常，无页面错误或控制台告警。
- 未解决阻塞：代码 PR 未合并前不部署或重放生产缓存；风险和人员明细如需下钻，必须另行通过缓存和预算闸门。

## 2026-08-26：M6A-3 新公司按需研究 PR 2

- 任务：在 `0018` 现有队列和作用域模型上完成六大模块按需研究 V1；每次 Worker 只处理一个模块，缓存先于预算，按来源记录与内容哈希去重；低风险结构化资料进入“已核实事实”，风险和人员资料进入“待核实线索”，原始供应商响应保持系统受限；不生成报告、不调用模型、不恢复自动发布。本 PR 未新增数据库迁移，也未查询既有 10 家真实样本。
- 关键文件：`backend/app/tianyancha.py`、`backend/app/on_demand_research.py`、`backend/app/services.py`、`backend/app/personal_features.py`、`scripts/run_on_demand_research_worker.py`、`frontend/app/companies/[id]/page.tsx`、`frontend/app/watchlist/page.tsx`、生产环境开关示例及相关测试和运维文档。
- 实际命令：Ruff 逻辑和格式检查；完整 Pytest；SQLite `base → 0018 → base` 与 Schema 漂移；全新 PostgreSQL 16 的迁移、虚构数据、受限应用账户和完整 RLS 套件；前端依赖审计、TypeScript 和 Next.js 生产构建；生产 Compose 解析与预检；本地虚构数据浏览器桌面和窄屏冒烟；Codex Security 工作区差异扫描；`git diff --check`。
- 测试结果：Ruff 和格式通过；默认离线 Pytest 253 项通过、14 项 PostgreSQL 测试按预期跳过，仅有既有 FastAPI TestClient 上游弃用警告；SQLite/PostgreSQL 迁移和 Schema 漂移通过，PostgreSQL RLS 14 项通过；前端 0 个已知高危漏洞、类型检查和生产构建通过；生产 Compose 与初始安全开关预检通过。Mock 闭环验证六模块顺序、缓存重放零外部调用、同内容跨时间不重复、部分失败恢复、取消后保留当前模块结果、跨租户只复用共享事实而不泄露投资或原始文档、个人页面分层展示和窄屏无横向溢出。安全差异扫描覆盖 13/13 个变更源文件，未发现可报告漏洞。本轮真实天眼查、付费 API、业务模型 Token、报告生成、费用和自动发布均为 0。
- 未解决阻塞：无代码阻塞。真实天眼查六个工具的响应契约和内容价值尚未验证；PR 合并部署并由项目负责人开通 VIP 后，只用一家此前未入库的新公司做受控真实验收。首版继续强制单 Worker，未增加跨进程原子预算预留前不得水平扩容。

## 2026-08-26：M6A-3 新公司按需研究 PR 1

- 任务：实现默认关闭的新公司按需研究基础：用户提交准确工商全称或信用代码，后台核验身份、等待用户确认，再创建或复用全局公司研究任务；完成 10/日、30/月个人额度、临时提额、供应商 900/日、9000/月自动闸门、队列、租约、取消、断线恢复和完整缓存优先。本 PR 不执行六大研究模块，不查询现有 10 家样本，不生成报告或事实。
- 关键文件：`migrations/versions/0018_add_on_demand_research_queue.py`、`backend/app/on_demand_research.py`、`backend/app/personal_features.py`、`backend/app/tianyancha.py`、`scripts/run_on_demand_research_worker.py`、`frontend/app/watchlist/`、`frontend/app/reviews/`、`docs/DECISIONS/ADR-0015-on-demand-company-research.md` 及相关测试。
- 实际命令：Ruff 逻辑和格式检查；完整 Pytest；SQLite `base → 0018 → 0017 → 0018 → base`；全新 PostgreSQL 16 的 `base → 0018 → 0017 → 0018`、Schema 漂移、虚构数据、受限应用账户和完整 RLS/API 套件；前端依赖审计、TypeScript 和 Next.js 生产构建；生产 Compose 解析、API/前端/备份镜像构建和预检；本地虚构数据浏览器冒烟；Codex Security 差异扫描；`git diff --check` 和定点 Secret 扫描。
- 测试结果：Ruff 和格式通过；默认离线 Pytest 248 项通过、14 项 PostgreSQL 测试按预期跳过，全新 PostgreSQL 16 下 262 项全部通过，只有既有 FastAPI TestClient 上游弃用警告；SQLite/PostgreSQL 迁移往返和 Schema 漂移通过；前端 0 个已知高危漏洞、类型检查、生产构建、三类生产镜像和失败关闭预检通过。安全扫描发现并修复了 3 个低级别信息泄露/限速问题，同时修复事务后 RLS 上下文、个人取消全局任务权限和缓存先于预算闸门的正确性问题。本轮真实天眼查、付费 API、模型 Token、费用和自动发布均为 0，所有外部开关保持关闭。
- 未解决阻塞：无。PR 1 强制单 Worker；多 Worker 前需增加跨进程原子预算预留与限速。执行中的六大模块取消检查、分级事实和一家新公司真实验收属于 PR 2 及后续闸门。

## 2026-08-25：取消关注的数据库最小权限修复

- 任务：修复香港邀请测试环境中“可添加关注、但取消关注失败”的问题；保留 PostgreSQL 最小权限，只允许应用角色删除 `personal_watchlist_items`，并由 RLS 限制为当前用户自己的记录。
- 关键文件：`scripts/bootstrap_local_database.py`、`tests/integration/test_postgres_rls.py`、`docs/12-operations-runbook.md`。
- 实际命令：Ruff 与格式检查；临时 PostgreSQL 16 空库迁移、Schema 检查、虚构数据导入、应用角色收敛及完整 Pytest；隔离 SQLite `base → 0017 → base`；前端依赖审计、TypeScript 和生产构建；生产 Compose 解析、API/前端镜像构建和生产预检；`git diff --check`。
- 测试结果：真实 PostgreSQL 限制角色下 231 项测试通过；接口层完成“关注 → 取消关注”回归；其他用户删除目标记录时受 RLS 隔离，关注表以外的业务表仍无 `DELETE` 权限。SQLite 迁移往返、前端 0 个已知高危依赖、TypeScript、生产构建、Compose 安全检查和预检均通过。本轮业务外部调用、付费调用、模型 Token 和自动发布均为 0。
- 未解决阻塞：代码合并后仍需在香港服务器重新执行 `prod run --rm bootstrap-role`，再用真实账号从公司详情和关注列表各完成一次取消与恢复关注验收；本 PR 不直接修改生产数据库权限。

## 2026-08-20：CloudBase 手机号验证码登录 V1

- 任务：在保留邮箱验证码和既有 PostgreSQL 授权模型的前提下，增加默认关闭的中国大陆手机号验证码入口；手机号只允许登录已绑定同一 CloudBase `subject` 的受邀账户，不开放注册、不按邮箱猜测合并、不在业务数据库保存完整手机号。
- 关键文件：`backend/app/auth.py`、`backend/app/main.py`、`backend/app/config.py`、`frontend/app/login/`、`frontend/lib/auth-api.ts`、`frontend/lib/auth-session.ts`、`compose.production.yml`、环境变量示例、认证测试及认证/实施/运维文档。
- 实际命令：完整 Ruff 与格式检查；完整 Pytest；隔离 SQLite `base → 0017`、Schema 漂移和 `0017 → base`；隔离 PostgreSQL 16 升级、Schema 漂移、受限应用账户和完整 RLS 套件；前端依赖审计、TypeScript 和生产构建；生产 Compose 解析、后端/前端/备份工具镜像构建和生产预检；本地浏览器分别以手机号开关开启和关闭完成入口、输入、失败提示和默认隐藏验收；`git diff --check` 与定点敏感信息扫描。
- 测试结果：Ruff 与格式检查通过；默认 Pytest 221 项通过、10 项 PostgreSQL 测试按预期跳过，显式 PostgreSQL RLS 10 项通过，仅有既有 FastAPI TestClient 上游弃用警告；SQLite/PostgreSQL 迁移和 Schema 漂移通过；前端 0 个已知漏洞、TypeScript 和两种生产构建通过；生产预检确认 CloudBase、PostgreSQL 和初始业务安全开关关闭。确定性测试覆盖手机号格式、固定 CloudBase 端点与 `target=USER`、默认关闭、冷却/单号码/环境总量、账户存在性不泄露、同一 subject 复用、未绑定 subject 拒绝和成功登录审计。本轮没有真实短信、业务外部查询、付费 Provider、模型 Token、自动刷新或自动发布。
- 未解决阻塞：代码 PR 合并前香港环境继续保持 `PHONE_LOGIN_ENABLED=false`。合并后仍须由项目负责人在同一既有 CloudBase 测试账户上安全绑定手机号，核对邮箱与手机号查询得到同一 UID，再执行最少一次真实短信、原 `user_id`/tenant/角色、退出和邮箱回退验收。应用限流为邀请规模下的单进程内存保护，重启会重置；多实例或用户规模扩大前需要共享持久限流，但当前不提前引入 Redis。微信扫码继续后置。

## 2026-08-17 至 2026-08-20：M6A 创始人自测与真实内容价值基线

- 任务：在没有独立外部测试用户的情况下，固定创始人自测范围、真实公司样本规则、登录/内容/证据/权限指标、缺陷分级和停止条件；明确自控多个邮箱不等于多名独立用户。
- 关键文件：`docs/13-m6a-founder-validation-playbook.md`、`docs/templates/m6a-test-session.md`、`docs/10-implementation-plan.md`、`docs/11-test-strategy.md`。
- 实际命令：`git status --short --branch`、`rg` 定点核对 M6 看板、认证与测试文档；`uv run pytest -q tests/integration/test_personal_company_query.py tests/integration/test_personal_changes_reports.py tests/integration/test_cloudbase_authentication.py`；生产环境只读核对镜像提交、数据库版本、安全开关、共享公司/事件/证据数量、个人与机构账户授权；真实 CloudBase 个人账户浏览器执行登录、10 家固定公司搜索与详情、证据、无结果和固定报告检查；5 条公开证据链接人工免费 HTTPS 检查；第二个受控账号通过生产后端完成登录、共享详情、个人报告越权负向检查和注销。
- 测试结果：针对性自动测试 13 项通过，仅有既有 FastAPI TestClient 上游弃用警告。生产后端和前端镜像均为提交 `81e7bc92307b64d15e625356b243934f7bf3ceb4`，数据库为 `0017`，四个业务安全开关关闭。账户 A 验证码首次提交成功；10/10 公司主体展示正确且未发现机构私有字段，拒绝候选和撤回事件未错误展示；合理简称搜索 9/10 通过，确认“法奥机器人”无法直接命中为 P1；3/10 公司共展示 5 条共享事件和 5 条证据引用；项目负责人确认其中 3 条有价值、2 条价值有限，只有 2/10 家至少有一条有价值事件。新报告正常中文排版，无 Markdown、内部英文枚举或私有投资字段；不存在公司查询未自动创建公司。5 条证据链接人工检查均为 HTTP 200，共下载 287,625 字节。账户 B 可读取同一博腾共享档案和 3 条共享事件，投资、私有事件和未确认线索均为 0；博腾不在其关注或报告中，账户 A 报告对账户 B 返回 404，注销返回 204。应用业务外部调用、付费调用、模型 Token、费用和自动发布均为 0。本次未修改业务代码、数据库或部署配置；逐项私有运营记录保存在 Git 忽略目录。
- 未解决阻塞：M6A 结论为 `先补内容生产`，未达到至少 5/10 家各有一条有价值事件的 M6B 邀请门槛。账户 B 的邮件验证码约延迟 1—2 分钟，使用较早邮件中的旧码会失败，等待最新邮件后可以恢复；后续认证体验任务应改善提示，但当前不抢占内容生产优先级。取消并恢复关注因 Codex 应用内浏览器把生产地址错误改写到本地而未执行，机构浏览器路径本轮未重复验收，逐家公司人工耗时未逐项打点；这些限制如实保留，不用数据库直改或事后估算。CloudBase 手机号与微信方案仍为独立后续任务，不混入 M6A。

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

## 2026-08-27｜按需刷新 RLS 与授权数据记录数修正

- 日期：2026-08-27
- 任务：修复按需研究开启时，普通用户无法为已核验平台共享公司创建 `research_queued` 刷新请求的 PostgreSQL RLS 缺陷；同时修正授权数据嵌套响应中无关 `0` 计数遮蔽有效正数、以及元数据列表被误当业务记录数的问题。
- 关键文件：`migrations/versions/0019_fix_personal_refresh_request_rls.py`、`backend/app/tianyancha.py`、`tests/integration/test_postgres_rls.py`、`tests/integration/test_migrations.py`和 `tests/unit/test_tianyancha_identity_provider.py`。
- 实际命令：针对性 Pytest；一次性 PostgreSQL 16 空库 `base → 0019 → 0018 → 0019`、`alembic check`、虚构数据导入和 `NOBYPASSRLS` 应用账户 RLS 套件；完整 Pytest；Ruff 与格式检查；TypeScript 和 Next.js 生产构建；Codex Security 工作区差异安全扫描。
- 测试结果：针对性测试 22 项通过；PostgreSQL RLS 14 项通过，确认共享已核验公司可入队，新公司不能跳过身份核验；完整 Pytest 254 项通过、14 项未配置 PostgreSQL 的测试按预期跳过；Ruff、格式、TypeScript 和生产构建通过。安全差异扫描覆盖 2 个变更源文件，未发现可报告问题；TAC 状态因连接器未登录而无法验证。本修复的自动验证没有外部调用、模型 Token、付费或自动发布。
- 未解决阻塞：需在代码 PR 合并、生产库升级到 `0019` 后，才能继续完成真实账号的取消查询和零调用缓存重放验收；本 PR 不自动合并或部署。

## 2026-08-28｜投资者重要变化事实基础

- 日期：2026-08-28
- 任务：依据 ADR-0017，把授权供应商模块从投资者展示逻辑中解耦；利用既有版本化公司快照保存可比较字段，确定性识别前后变化并按版本化重要性规则生成独立、有证据的变化事件。修正真实嵌套股东字段映射，工商历史详情只展示最近同批变更，并从默认研究中移除无法说明投资风险的人员概览。
- 关键文件：`backend/app/change_detection.py`、`backend/app/tianyancha.py`、`backend/app/on_demand_research.py`、`backend/app/services.py`、`tests/unit/test_change_detection.py`、`tests/unit/test_tianyancha_identity_provider.py`、`tests/integration/test_on_demand_research.py`、`docs/DECISIONS/ADR-0017-investor-material-change-layer.md` 和实施计划。
- 实际命令：针对性变化识别、天眼查映射和按需研究 Pytest；完整 Ruff 与格式检查；完整 Pytest；前端 TypeScript 与生产构建；`git diff --check`。全部测试使用 Mock 或已有缓存契约，没有访问真实 Provider 或模型。
- 测试结果：完整 Pytest 266 项通过、14 项未配置 PostgreSQL 的显式测试按预期跳过，仅有既有 FastAPI TestClient 上游弃用警告；Ruff、格式、TypeScript 和 Next.js 生产构建通过。首次快照不产生变化，相同缓存重放保持幂等；20%→25% 的工商登记持股比例生成一条跨租户可复用且有证据的共享变化事件；普通快照刷新不会丢失比较基线；不完整股东分页不推断退出；低价值知识产权数量变化只随快照归档；风险数量变化只形成非风险结论的待核实线索。默认研究减少一次人员概览调用；外部调用、模型 Token、费用和自动发布增量均为 0。
- 未解决阻塞：本 PR 不调用真实数据验证未来响应分页是否可返回完整股东清单，因此完整清单之外不自动判断股东新增或退出；PostgreSQL RLS 表结构未变，显式 PostgreSQL 套件与远端 CI 结果以 PR 检查为准。证据约束分析 Agent 和投资者变化卡片属于连续 PR 2，本 PR 不提前实现。

## 2026-08-28｜证据约束的投资者变化解读

- 日期：2026-08-28
- 任务：在确定性重要变化事实之上增加独立、默认关闭的异步解读 Worker；仅处理中高重要性的已发布平台共享变化，使用严格 JSON Schema、前后值、可展示证据 ID、数字和投资建议禁语校验。公司页面分开呈现重要变化、当前资料基线和模型辅助解读；模型不改写事件、不读取私有原始文档、不在同步请求中运行，也不自动生成报告。
- 关键文件：`backend/app/investor_analysis.py`、`backend/app/investor_analysis_schema.py`、`backend/app/deepseek.py`、`migrations/versions/0021_add_investor_change_analyses.py`、`scripts/run_investor_analysis_worker.py`、`schemas/investor_change_analysis.schema.json`、公司详情前端、部署配置、RLS/Provider/Worker 测试和 ADR-0017 配套文档。
- 实际命令：针对性和完整 Ruff/Pytest；SQLite 与一次性 PostgreSQL 16 的 `0020 → 0021 → 0020 → 0021`、`alembic check`、应用角色/RLS；前端依赖审计、TypeScript 和生产构建；单机 analysis profile Compose 解析；虚构共享变化和无基金个人身份的桌面及 390px 浏览器验收；`git diff --check`。
- 测试结果：完整 Pytest 288 项通过、16 项未配置 PostgreSQL 时按预期跳过；独立 PostgreSQL 16 的 16 项 RLS/API/真实提交测试全部通过；前端构建、类型检查和依赖审计通过，0 个已知漏洞。浏览器确认重要变化、前后对比、解读和证据分层展示，桌面/窄屏无横向溢出，无基金用户看不到投资字段。自查补上每次事务提交后的 RLS 上下文重绑、证据撤下即隐藏关联解读，以及异常历史输入安全跳过。所有业务外部调用、真实模型调用、模型 Token、费用和自动发布均为 0。
- 未解决阻塞：真实 DeepSeek 输出的中文质量和供应商实际 Token 计费尚未调用验证；合并部署前保持专用开关和价格配置为 0/false。结构化校验能阻止明显数字、证据和前后值越界，但不能证明模型每句语义都正确，下一闸门只用一家具名新公司做受控价值验收，不扩充样本或自动监测范围。

## 2026-09-02｜供应商中立受限公开网络研究 ADR

- 日期：2026-09-02
- 任务：因天眼查额度与商业可持续性不再支持产品路线，暂停绑定旧供应商的 PR #42；用 ADR-0018 确认“天眼查安全退役 → 搜索源准入实测 → 受限公开网络研究 V1 → 恢复 M6B 复测”的闸门顺序，并明确身份、同步请求、Agent 工具、证据、权限、严重负面、成本和停止边界。本任务只修改文档，不改业务代码、数据库或生产环境。
- 关键文件：`docs/DECISIONS/ADR-0018-provider-neutral-bounded-web-research.md`、ADR 索引与历史状态、`AGENTS.md`、`README.md`、系统架构、数据源、安全、API、运维手册和实施计划。
- 实际命令：`gh pr ready 42 --undo`；文档本地链接检查；敏感信息差异检查；`git diff --check`；完整 Ruff 与格式检查；SQLite `base → 0021` 和 `alembic check`；完整 Pytest；前端依赖审计、TypeScript 和 Next.js 生产构建。
- 测试结果：本地链接和敏感信息检查通过；Ruff 与格式检查通过；一次性 SQLite 升级和 Schema 漂移检查通过；Pytest 291 项通过、16 项按环境条件跳过，仅有既有 FastAPI TestClient 上游弃用警告；前端依赖审计为 0 个已知漏洞，类型检查和生产构建通过。业务外部调用、模型 Token、项目/生产数据库写入和生产部署均为 0。
- 未解决阻塞：搜索 Provider 尚未通过中文真实公司覆盖、许可、稳定性和成本准入；天眼查活动代码、Secret、缓存、专用 RLS 和用户可见事实尚未退役，必须由下一个独立代码里程碑处理。PR #42 保持 Draft，不能按原方案合并。

## 2026-09-02｜R1 旧商业数据供应商安全退役

- 日期：2026-09-02
- 任务：依据 ADR-0018 删除旧商业数据供应商的活动适配器、身份 CLI、按需研究 Worker、配置、缓存挂载、CI 契约和用户入口；保留通用个人申请、取消与断线恢复状态。新增 `0022` 保守向前迁移：没有独立官方核验的旧来源身份恢复待核验，没有独立证据的事实撤回，旧供应商证据引用停止展示，历史来源、原始文档、核验、用量、任务和血缘继续保存；历史商业候选不能从审核工作台重新激活。
- 关键文件：`migrations/versions/0022_retire_legacy_business_data_provider.py`、`backend/app/config.py`、`backend/app/providers.py`、`backend/app/personal_features.py`、`backend/app/services.py`、`compose.production.yml`、个人申请前端、生产示例配置、退役迁移/RLS/个人申请测试和受影响文档；旧供应商适配器、CLI、Worker、样例清单及专用测试已删除。
- 实际命令：Ruff 与格式检查；SQLite `base → 0022 → base` 和 Schema 漂移；一次性 PostgreSQL 16 的带数据 `0021 → 0022 → 0021 → 0022`、专用 RLS 策略检查和完整应用/RLS 测试；前端依赖审计、TypeScript 和 Next.js 生产构建；生产 Compose 解析、配置预检及 API/前端/备份镜像构建；活动入口与敏感信息检索；生产数据库只读影响统计；Codex Security 差异审查；`git diff --check`。
- 测试结果：Ruff、格式、SQLite/PostgreSQL 迁移、Schema 漂移和生产配置通过；PostgreSQL 带数据验证确认旧来源独占事实撤回、旧身份降级、旧证据停止展示、历史记录保留且 downgrade 不会重新发布；完整 PostgreSQL 测试 246 项通过，仅有既有 FastAPI TestClient 上游弃用警告。前端依赖审计为 0 个已知漏洞，类型检查、生产构建和三类生产镜像构建通过。本机没有旧供应商 CLI 或 skill，用户级凭据文件已删除；仓库活动运行目录不再包含旧 Provider、Worker、CLI、开关或缓存挂载。业务外部调用、模型 Token、费用、自动刷新和自动发布均为 0。
- 未解决阻塞：PR #44 尚未合并，生产仍在 `0021`。只读核查确认预计处置 12 个旧依据身份、21 条旧来源独占非撤回事件和 4 个关联已完成申请；4 份个人报告均不含旧供应商名称且未引用这些事件。生产 Secret、13 个私有缓存文件和旧部署环境变量必须等合并后完成升级前备份、`0022` 部署、真实账号/RLS 回归及升级后备份，再无输出地删除；必要审计和最近可回滚 release 不提前清理。搜索源准入与 R3 研究 Agent 继续后置。

# 新版网站核心使用链路与受控发布

## 发布对象与结论边界

现网 `47c4a5c` 与候选基线 main `140ff6a5` 之间包含 #106—#112：运维执行契约、研究完成状态、融资/事项与来源处理、正式事务身份、固定业务日期、Next.js 安全修补、CI 和独立业务 MCP。不能把这次升级描述为只更新 MCP，也不能把 main 已合并写成网站已发布。

两版 migration 文件无差异，都是 `0036`；本轮没有数据库结构或业务角色升级需求。正常 production Compose、Caddy 与安全降级控制文件无变更。新 MCP overlay 不纳入网站启动命令。候选已有 Next.js `16.3.8`，现网仍为 `16.3.4`；执行日前仍须重新核对安全公告和锁文件审计，不因旧 CI 成功忽略新漏洞。

本轮发布缺陷仅涉及报告界面的真实性：`source_event_count` 是引用事项总数，可能包含获准待核线索。列表与详情必须使用中性标签，正文继续区分已确认、历史、待核与研究完成状态；不改事项状态、正文、额度或许可。

## 共享快照与来源链

网站正式 `report_evidence_permission` 可依据字段完整、许可有效的共享 display snapshot 判断可展示性。业务 MCP 另外要求沿 raw/parent 找到 grant 中获准的 source；用户看不到原始链时会拒绝。它不能通过读取其他用户的 raw、扩大 SELECT 或提升角色来解决。

本轮负责人确认既有获准报告在生产网站可完整阅读。两种虚构表示（直接 raw 引用、父 evidence 引用）经普通用户/non-owner PG 验证：网站可读且重载一致，MCP source gate 拒绝；许可撤回后两边均拒绝，读取不改变业务表摘要。生产正文未由运维脚本读取。RLS 不可见只能写“当前 principal 不可见”，不能写“记录不存在”。

## 网站验收内容

| 链路 | 必须验证的行为 |
| --- | --- |
| 公司、事项与证据 | 已有公司读取；历史、待核、纠正/撤回/反证保持含义；普通角色和跨用户边界 |
| 已有报告 | 真实旧应用生成并提交后，新 API 独立读取、同键重试、正文/hash 不变；真实新前端展示与 reload |
| 报告界面 | 待核与混合内容不被总数或说明升级为已确认；撤权优先于 URL 成功提示 |
| 初始资料维护 | 正式 CuratedWorkbookProvider preview/apply、幂等、证据补充、不同事项/阶段、不可变事实血缘 |
| 研究历史读取 | complete/partial/failed/not_run/预算及未检查范围如实显示；固定业务日期不受 UTC 或读取日移动 |
| 运行与权限 | 正式会话工厂、commit/rollback 后身份重绑、显式重读、非 owner PG/RLS、首错保留；外部边界为 Mock |
| 制品与降级 | Linux/amd64 新旧 API/前端、实际 Caddy gate、只读角色、业务完整内容摘要、安全恢复与显式 Worker 恢复边界 |

基础网站发布和自动研究启用分别判定。Mock 链路通过不代表真实研究价值或网络已获验证，不启动新留出、不复活旧 attempt、不重跑求分。

## MCP 职责（只记录，不建设）

| 边界 | 现有能力 | 仍缺内容 |
| --- | --- | --- |
| business_read | find_company、get_company_matters、list_company_reports、get_saved_report；公司/报告/source grant 与正式许可 | 合法共享快照和不可见源链的授权兼容方案；飞书/ChatGPT 实际激活与撤销闭环 |
| ops_read | 原有五工具：find_company、get_company_diagnostic、get_run_trace、get_event_lineage、get_diagnostic_summary；受限视图与元数据白名单 | 经批准的全站必要运行元数据范围、无越权的诊断原因投影、独立 principal 与审计合同；当前公司白名单不能冒充全站权限 |

两者不共用超级权限，不提供任意 SQL/Shell/File/URL 抓取，不以业务拒绝为由读取私有原文。本轮不增加工具、角色、DNS、grant 或 MCP 生产进程。

## 集中切换批准所需对象

最终私有清单必须填实候选 commit/tree、API/前端 registry 不可变 digest、固定 safety-control、配置 SHA、目标主机、当前镜像、备份位置/加密方式和验收账号/公司/已有报告。仅本地 image ID 或 build metadata 不能冒充可拉取的 registry digest。

1. 正常审查并合并最小候选；准确合并 main 的 Verify 成功后，冻结私有 amd64 API/前端制品并按 digest 拉取核验。不得以旧 main 的 CI 代替新 head。
2. 切换前只读重验现场版本、schema0036、普通应用角色、关闭开关、资源与所有写任务。当前用户 RLS 下的零任务不等于全站零任务；全站有界运行计数的批准范围须写明。
3. 使用已验证的完全静态维护入口；停止本应用写入口，制作当次加密备份和异机副本，双 SHA 一致。不得停止无关云系统代理。
4. 只启动获批新 API/前端，正常入口保持维护；不运行 migration、改角色或部署 MCP。检查 health/ready、版本、连接与权限。若角色状态不符，停止，不临时放宽权限。
5. 负责人真实 CloudBase 登录/已有会话与匿名拒绝；在明确指定对象上验收已有报告、重载、公司与必要回访。报告生成、回访、额度等写入必须单列对象、幂等键和数量，不把只读批准当写入批准。
6. 全部门槛通过才恢复正常 Caddy，并按统一执行契约核对 actual/current/public 和回执封存；任何一项未确认不写已发布。私有临时入口随后清理。
7. 失败保持/恢复完全静态 maintenance；停 API/前端/Worker，固定 safety-control restrict→verify，schema 保留0036。旧镜像只可在只读角色与维护门后作必要诊断，不把它直接放回公网读取新版本报告。禁止自动 downgrade、覆盖备份或重算历史语义。
8. 成功后自动研究、Worker、Provider、模型、Watchlist、自动刷新/发布、MCP 仍关闭。回执记录实际镜像、网站结果、关键摘要、额度/副作用、维护窗口和回退状态；其他能力另行批准。

当前没有生产切换授权。受限私有证据、主体 ID 和真实备份不得进入公开仓库。原工作区、文档分支及历史封存材料不覆盖、不重写。

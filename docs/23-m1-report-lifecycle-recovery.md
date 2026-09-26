# M1 报告生命周期恢复工程

本轮仅修复报告许可一致性、交付/额度语义和页面状态。基线 `main@6d9398b1f3f6f838acb8bb123d1d6d84afa36545`；本分支不自动合并或部署。此前 Gate C 在报告验收失败后进入静态维护，生产保留 `0036`、只读应用角色及停止的应用/Worker；M1 尚未关闭。本轮没有生产操作或真实研究调用。

## 根因与修改

首次生成直接序列化正文，独立读取/重试/复用另用许可闸门。旧原文链只允许 public，新展示投影只允许 public/permission_confirmed，因此两种合法 synthetic_demo 均被拒绝。页面同时只看 URL 的 result，不看当前服务端 history_status。之前回归以 public 夹具为主，覆盖撤权，却没有“合法演示资料生成→提交→独立读取→实际正文”正向闭环；报告 ID、复用跳转不等于正文验收。

- 报告统一使用正文许可闸门；首次不可交付则事务回滚、返回 403 `report_content_restricted`，不保存报告、不计额度、不写成功请求映射。受限历史报告仍返回受限占位，不改变原存储或历史用量。
- 原文链必须是无私有 owner 的共享文档/证据，document/source 均有合法 public/permission_confirmed 许可；synthetic_demo 必须有两端一致的既有共享来源链。新演示投影另须有效 demo-evidence-v1 payload 与必要展示字段，单独填写演示标记不能授权。
- 正文许可、外链许可和事实过时分开处理。演示正文含“虚构演示”提示；现有外链政策继续拒绝演示外链。纠正/撤回事项在许可仍有效时为 stale，撤权为 restricted；多来源丢失仍按保存引用和当前许可保守阻断历史正文。
- 同键重试及同内容复用经过相同检查；成功复用不新增额度，受限复用不写新的成功请求映射。无事件/过滤后无已确认事项时允许明确说明缺口的公司基础报告，不能伪装成有已确认事项。
- Server Action 使用真实响应状态；详情页以当前服务端 restricted/stale 优先，URL 不能覆盖。首次拒绝在公司页明确提示未生成、未计额度。

## 验收与证据边界

新增生命周期矩阵在 SQLite 与非 owner PostgreSQL 上调用真实 HTTP API，覆盖两种演示表示、混合来源、public、permission_confirmed、无事件/过滤后为空、事务提交后读取、同键/同内容复用、额度耗尽、跨用户、许可撤销/未知/缺失、伪造演示标记、缺展示字段、多来源隐藏/删除及纠正/撤回。PostgreSQL 验证同键并发只生成/计费一次；SQLite 行锁并发分支明确跳过。前端通过真实页面组件和 Server Action 测试成功、复用、受限、过时及伪造 URL。

失败后的现有 age 备份只在本机内部网络、tmpfs PostgreSQL 中恢复，直接使用 0036，不重跑迁移。原失败 amd64 镜像重现首次成功而随后受限；修复 amd64 镜像通过完整读取/复用和编译后的真实页面。对 42 张表按原有主键逐行比较完整内容 SHA，历史报告/事项/证据/观测/语义基线/receipt first_seen_at 不改写；允许验收新建报告、请求映射和正常额度记录。原件、完整摘要及日志仅在忽略的私有证据目录，未提交真实数据或密钥。

Safe Degrade 既有行为通过本地实际旧镜像与修复镜像跨版本回归。安全控制源码、Caddy overlay、迁移、模型/Provider/研究策略均不改变。正式测试和最终 PR/CI 精确状态以统一私有 `M1 Report Lifecycle Recovery Package` 为准，不以本地 Docker ID 冒充可拉取 registry digest。

## 修复制品与下一次授权

合并前提交本 PR 的精确 head/base/Verify 审查。合并需另行批准；之后从最终合并 SHA/tree 构建并发布 API/frontend 的 linux/amd64 不可变 digest，重新冻结 Manifest。未修改的 safety-control 可复用既有批准 digest，记录其独立来源及配置 SHA；不能假设三个制品必须同源码 SHA。

恢复生产须另行集中批准：复核 0036/角色/无写任务/静态维护及备份；保持旧应用/Worker 停止，从新 Manifest 启动修复应用并先做只读 health/ready，再显式恢复普通应用角色及共享函数权限/RLS；不执行 upgrade/downgrade、语义重算或历史报告重建。先完成报告生命周期，再验 CloudBase 登录/已有会话/匿名拒绝、公司详情、回访/并发、report reuse/quota、evidence permission 和 RLS。真实验收用户/公司及正常写入需明确授权；撤权/删证据/耗尽额度模拟留在隔离库。全部通过才切正常 Caddy、做公网 smoke、更新 current 并封存新加密备份与回执。失败则静态维护、停止应用、固定 safety-control restrict+verify，保留 0036；不自动覆盖库或临时修改代码。

本轮生产验收、CloudBase、发布/合并均未执行。工程通过后停止，下一步应审查并批准有界恢复，而不是增加 M1 优化；成功恢复后才能关闭 M1，M2 另行授权，M3/MCP 保持独立关闭。

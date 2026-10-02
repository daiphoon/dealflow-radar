# CI 设施减负与风险覆盖

本切片绑定 main `d6edae70bb4201584d69ef0c4cfa1ba6bcd50e82`；不改变业务、schema、融资、归并、Provider 或权限规则。历史 40 分钟超时仅证明未在时限内结束；没有原 CPU/PG wait 证据，不把当前结构性成本冒称历史唯一根因。

## 测量与模板

同一 Mac / Python 3.12 / PostgreSQL 16 隔离环境，原 72 项融资整链测试 90.27 秒，setup 49.486 秒，72 次迁移累计 44.893 秒；只更换模板后同一 72 项 43.57 秒，setup 2.789 秒，仅 2 次迁移累计 1.214 秒。wall 减少 51.7%，业务 call 40.061→39.800 秒。它是本机证据，不是 Ubuntu runner 的耗时保证。

模板只从当前正式 migration + demo seed 构建一次，绑定源码及 PG 版本。SQLite 每例独立文件，PG 每例 CREATE DATABASE TEMPLATE，应用角色每例独立；真实 commit/rollback、独立重读和并发无 SAVEPOINT 包装。迁移/旧数据演练仍从空库或指定旧 schema 开始，不套模板。双副本并发污染测试验证另一副本和原模板不变。

`tests/support/ci_profile.py` 为可选诊断插件，逐例落盘 setup/call/teardown、迁移/seed/角色和资源汇总，不记录 env、SQL 参数或正文。PR 首轮在 Python/PG 两台 runner 上先执行同节点原 fixture 子集；优化后对应节点可从完整分组证据比较。完整日志与 top20 用 artifact 保存，不进入源码。

## 风险覆盖映射

| 风险 | 保留/替代验证 |
| --- | --- |
| 融资情态、金额、日期、否定对象 | 广泛变体调用正式解析/validate_field；5 种代表机制 × rules/正确 Mock/过度确定 Mock × SQLite/PG 保留存储→API→报告整链，modal family 整链保持 |
| 四候选、集团主体、SAME/待核 | concept identity、core lifecycle、matter comparison/storage 回归保持；只移动辅助函数 |
| 反证、旧转载、撤权/旧 v4/v5 报告 | core lifecycle、report lifecycle、report permissions、runner reference 完整回归保持 |
| commit/rollback/并发/non-owner/RLS | 原实际事务与角色回归保持；新模板双副本真实提交、回滚、显式重读、线程隔离 |
| 未知账本、driver 首错、固定业务日期 | web budget、runner reference、usage/research 回归保持 |
| SSRF/robots429/许可/source | 原 fetcher/网络/有效证据回归保持 |
| migration/制品/安全降级 | release job 独立完整迁移、Compose、固定旧 amd64 镜像和新制品跨版本演练，未删除 opt-in 分支 |
| 前端/依赖安全 | 独立 static_frontend，npm audit 仅网络故障允许 OSV fallback，真实高危发现仍失败 |

未删除风险类别；辅助代码从 tender、curated、incremental、research matter、core lifecycle 测试移至 support。金额/日期近义句下移到正式校验层，避免每个词形都重复生成报告。

## 分组与 Verify

静态/前端、Python、PostgreSQL、release 四个职责 job 加最终 `Verify`。按**实际 pytest collection**分类；所有节点恰好出现一次。PG 参数及 postgres marker 入 PG，安全降级/Compose/容器入 release，其余入 Python。最终 Verify 比较各组 collection、SHA、并集与交集；任意必需 job 非 success、缺组、skip/cancel/fail、漂移或漏节点均失败。无 PR 标题/路径跳过必需覆盖。

30/30/60 分钟是诊断后给完整覆盖的独立超时余量；20–25 分钟只作反馈目标。最终准确 head 的 CI、runner minutes、条件 skip 与制品分支实际执行另在统一交付包留证；不以测试总数代替交付。

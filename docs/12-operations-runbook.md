# 12 运维手册（设计）

当前已有可本地运行的 Demo；本手册定义当前能力和后续部署的最小操作边界。

## 1. 部署前检查

1. 使用云服务器或合作者服务器；不使用家庭 Ubuntu 服务器，不依赖 Tailscale。
2. 从 `.env.example` 创建部署 Secret，确认四个默认开关仍为关闭，禁止提交 `.env`。
3. 检查镜像版本、PostgreSQL 持久卷、TLS、允许来源、备份目标和磁盘余量。
4. 先执行迁移备份与 `alembic upgrade` 演练，再部署 API/Worker/Cron。
5. 用两个租户/基金的虚构数据运行权限冒烟测试。
6. 外部 Provider 逐个完成许可、价格、预算和 `dry-run` 审批后才启用。

Docker Compose 文件只依赖标准容器、环境变量和卷，可迁移到不同主机；反向代理与证书由部署环境提供，不绑定云厂商 SDK。

## 2. 日常健康指标

监控 API 错误/延迟、数据库连接和存储余量、任务队列年龄、租约过期、Provider 失败率、无变化任务占比、缓存命中率、LLM Schema 成功率、待审核高风险数量、预算消耗和备份结果。同步查询外部调用数应恒为 0。

## 3. 安全的更新操作

更新前先运行 `dry-run`，核对公司、原因、Provider、预计搜索/Token/商业数据调用和费用。只在变更单明确授权的范围内开启 `EXTERNAL_CALLS_ENABLED`；付费能力还需 `PAID_API_CALLS_ENABLED` 与正预算。任务完成后立即核对 `usage_ledger` 和有效产出，不长期保留调试日志正文。

### Mock Worker V1（仅 Demo）

先确认数据库中已有 `mock_refresh` 任务，再按虚构租户运行一次：

```bash
export WORKER_TENANT_ID="$(uv run python -c 'from backend.app.demo import ALPHA_TENANT_ID; print(ALPHA_TENANT_ID)')"
APP_MODE=demo uv run python -m scripts.run_mock_worker
```

命令每次最多领取一个任务；无任务返回 `idle`。必须显式设置 `APP_MODE=demo`，并使用代码中固定的两个虚构租户之一；缺失模式、非 Demo 模式和其他租户均在连接数据库前拒绝。该入口不会加载 Provider 或访问网络：有当前快照时模拟无变化检查并保留 `data_as_of`，无快照时保持 `unknown`。运行后核对任务已完成、租约已释放、心跳存在，且 `usage_ledger.external_calls = 0`、`estimated_cost = 0`。它不能代替真实公司信息检查，也不是常驻 Worker 或 Cron。

### 人工研究导入 V1（本机、公开来源）

先确认数据库已迁移到 head、目标公司主数据已存在、操作者是 active 机构管理员。将 JSON 放在 Git 忽略目录后执行：

```bash
mkdir -p data/private/research_imports
cp data/sample/manual_research_import.json data/private/research_imports/manual-example.json
export RESEARCH_IMPORT_FILE=manual-example.json
export IMPORT_USER_ID="$(uv run python -c 'from backend.app.demo import ALPHA_USER_ID; print(ALPHA_USER_ID)')"
uv run python -m scripts.import_research_json
```

输出只包含批次 ID、状态和计数。完成后核对：解析成功记录只形成 `in_review` 事件及事件审核项；未解析记录只形成实体提及审核项；没有公司快照变化；`usage_ledger` 的外部调用、Token 和费用均为 0。重复同一文件应返回 `duplicate`。批次号复用但内容改变、来源代码元数据冲突或外部记录内容冲突时整批失败并回滚，不要绕过去重键手工改库。实体提及审核当前不能用通用事件审核接口直接批准，应保持 pending，等待专用身份解析流程。

V1 只接收不超过 1 MiB、最多 500 条且许可为 `public` 的 JSON。不得放入内部财务、投资协议、投委会材料、API Key、Cookie 或商业数据库受限内容；原始文件由操作者在私有目录管理，不进入 Git，也不会被系统复制到存储。当前没有网页/API 上传入口。

## 4. 常见事件处置

| 事件 | 立即动作 | 恢复条件 |
| --- | --- | --- |
| 预算超限 | 停止新外部调用，任务标记 `budget_deferred`，保留查询 | 管理员调整预算或进入新周期；不得自动换贵源 |
| Provider 故障/限流 | 记录错误与 `retry_after`，有限重试；必要时使用已批准低成本替代 | 健康检查恢复，积压在预算内消化 |
| Worker 崩溃 | 不手工重复创建任务；等待租约过期后重领 | 检查点、幂等键和用量记录一致 |
| 严重负面误报 | 从当前快照撤下，标记待审/撤回，保留证据与审计 | 审核员完成纠错或驳回，生成新快照 |
| Secret 疑似泄漏 | 关闭 Provider、吊销并轮换、检索日志和提交历史 | 新 Secret 通过最小权限验证，完成事件复盘 |
| 跨基金越权 | 立即禁用相关账户/服务身份，保全审计，关闭受影响入口 | 修复 RLS/授权并通过负向回归，通知义务已评估 |
| 许可到期/撤稿 | 停止抓取和展示受限正文，标记来源与相关事件 | 获得新授权或完成撤回/替代证据审核 |

## 5. 备份与恢复

每日加密 PostgreSQL 备份，文件存储按许可和变化量备份，备份与主机隔离并设保留期。每月至少演练一次恢复到隔离环境：恢复数据库与文件 → 校验迁移版本 → 校验哈希和证据外键 → 重置过期租约 → 验证 RLS → 用虚构用户冒烟测试。Demo 规划目标为 RPO 24 小时、RTO 8 小时；正式 SLA 需另定。

## 6. 删除与退出

删除先冻结访问并生成范围清单，区分用户账户、租户私密数据、共享公开事实和依法/依约需保留的审计。异步清除后验证数据库、存储、缓存和备份生命周期；出具不含私密内容的审计结果。停止服务时先关闭外部调用和 Cron，再排空/终止任务、备份、撤销 Secret，最后关闭 API。

## 7. 变更控制

更新频率、事件分类、评分、权限、Provider、成本上限、重大负面规则、财务口径或 Kimi 边界发生变化时，先更新 ADR、迁移/配置和测试，再部署。历史迁移不回改。实施命令、结果和阻塞只写入简洁的[实施记录](IMPLEMENTATION_LOG.md)。

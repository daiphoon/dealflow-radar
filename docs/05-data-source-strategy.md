# 05 数据源与 Provider 策略

## 1. 来源优先级与保存边界

优先级依次为：官方政府/法院/监管系统；公司官网与正式公告；经许可商业 API；主流财经和行业媒体；Kimi 等联网研究工具；自媒体线索。不得绕过登录、验证码、付费墙、频率限制或反爬措施，也不得抓取受限商业数据库前端。

普通新闻默认只保存 URL、标题、来源、发布时间、观察时间、内容哈希、许可状态、最小必要证据片段和可选存储引用。关键官方证据可按配置保存快照。每个来源记录许可、允许用途、保留期、是否可缓存/再分发和删除要求；许可未知时只保存定位元数据并进入审核。

## 2. 统一 Provider 协议

业务层只依赖以下版本化协议，Provider 名称和价格来自配置与依赖注入。

| 接口 | 方法与输入 | 输出 | 职责边界 |
| --- | --- | --- | --- |
| `SearchProvider` | `search(SearchRequest)` | `SearchResultBatch` | 返回标题、URL、摘要、时间和来源记录 ID；不判定事实 |
| `StructuredDataProvider` | `fetch(StructuredDataRequest)` | `StructuredRecordBatch` | 返回有许可的工商、司法、监管等结构化记录 |
| `ResearchImportProvider` | `parse(ImportRequest)` | `ResearchImportBatch` | 解析 JSON/CSV/Excel/Markdown；只产生候选 |
| `DocumentFetcher` | `fetch(FetchRequest)` | `FetchedDocument` | 遵守访问许可获取内容，返回哈希、响应元数据和存储建议 |
| `LLMProvider` | `extract(EventExtractionRequest)` | `StrictEventCandidate` | 只做证据约束的结构化抽取，不直接发布 |
| `StorageProvider` | `put/get/delete(StorageObject)` | `StorageReference` | 保存许可允许的文件；业务库只存引用和哈希 |
| `NotificationProvider` | `send(NotificationRequest)` | `DeliveryReceipt` | 只发送已批准内容，使用投递幂等键 |
| `JobQueueProvider` | `enqueue/lease/ack/fail(Job)` | `JobReceipt` | Demo 映射 PostgreSQL 任务表，隔离未来队列实现 |

所有请求携带 `request_id`、`tenant_id` 或公共范围、预算上下文、许可用途、超时、Provider 配置版本；所有响应携带 Provider、外部记录 ID、观察时间、缓存状态和估算用量。

## 3. 错误、重试和降级

统一错误：`ProviderDisabled`、`BudgetExceeded`、`RateLimited`、`TransientFailure`、`PermanentFailure`、`PermissionDenied`、`SchemaMismatch`。权限、预算、永久错误和确定性 Schema 错误不重试；网络或限流按 `retry_after` 或有上限退避，默认最多两次。LLM 解析/校验失败最多修复重试一次，仍失败则保留失败记录且不发布。

降级顺序由配置明确给出：缓存 → 已有结构化数据 → 许可允许且不更贵的替代 Provider → 保留旧快照并延迟。禁止静默切换到更昂贵模型、未授权来源或消费端接口。

## 4. 预留实现及启用条件

| 实现 | 当前状态 | 启用前提 |
| --- | --- | --- |
| `MockSearchProvider` | 第 2 阶段建议实现 | 只读虚构 fixture，无网络 |
| `ManualResearchImportProvider` | 已实现 V1 | 仅本机 JSON、公开来源元数据和机构管理员；身份例外审核，其余由确定性策略自动路由 |
| `KimiAgentImportProvider` | 预留 | 用户人工导出、许可明确，不调用未公开接口 |
| `KimiScheduledResearchImportProvider` | 预留 | Kimi Work/Claw 等官方导出能力、授权和稳定格式已确认 |
| `KimiOpenPlatformProvider` | 预留 | 正式 API 文档、账号授权、价格和数据条款确认 |
| `LicensedBusinessDataProvider` | 预留 | 单独合同允许 API 调用、缓存和目标用途 |
| `OfficialIdentityProvider` | 已实现 V1 边界与本机导入 | 当前只导入已核对的政府/GSXT JSON；自动查询要求正式 API 授权 |

## 受控可信来源监测 V1

V1 不是通用爬虫或搜索引擎。平台管理员先把已核验公司的官网、政府页、RSS/Atom、Sitemap、列表页或单页登记为 `trusted_sources`，后台 Worker 才能访问；同步搜索和详情请求永不联网。列表页可明确设置内容路径前缀，以排除同域导航和无关栏目。

检查结果先进入 tenant 私有的 `candidate_documents`。许可不明时只保存 URL、标题、日期、哈希和定位元数据，不默认保存全文；`public_access` 仅表示无需登录可访问，不代表允许商业再分发。候选必须经人工判断并进入既有研究导入、证据审核和共享事实晋升流程，监测本身不创建事件、不调用 LLM、不自动发布。
| `OfficialPublicDataProvider` | 预留 | 非工商身份的官方接口或合法下载路径、频率和保留边界确认 |
| `DeepSeekLLMProvider` | 第 3 阶段候选 | API 能力、模型名、Schema 支持、价格和预算确认 |

## 5. Kimi 能力边界

Kimi 消费端会员/Agent、Kimi Work 或 Kimi Claw、Kimi Code、开放平台 API、以及商业数据库授权是彼此独立的授权面：

- 消费端会员额度不视为网站 API 额度；不得抓取账户页、Cookie 或未公开接口。
- Agent/Work/Claw 的结果只能通过官方导出或人工导入进入候选层，保存和再分发取决于来源许可。
- Kimi Code 是开发辅助能力，不自动赋予生产数据访问、模型 API 或商业数据库权利。
- 开放平台只有在正式文档、密钥、价格、速率和条款确认后才能作为 Provider。
- 天眼查、iFind 等数据必须有单独授权；Kimi 结果不能替代工商、司法或监管原始来源。

系统在完全没有 Kimi 时仍须通过 Mock 和人工导入完成闭环。

## 6. 统一 ResearchImport 格式

人工研究导入 V1 只接受 `data/private/research_imports/` 下的 JSON。批次输入包含 `schema_version`、`batch_id`、`queried_at`、`research_tool`、`agent_name`、`original_query`、`target_company_hint`、`license_status` 和 `records`；系统另行生成 `file_hash`、`parser_version` 与 `imported_by`。`license_status` 当前只能为 `public`，文件上限 1 MiB，每批最多 500 条。

每条记录包含：

- `external_record_id` 和 `company_identity_evidence`（工商全称必填，信用代码、地区、官网可选）；
- `source_code`、`source_name`、`canonical_url`、可空的 `source_published_at` 或日期精度 `source_published_on`、可空且独立的 `occurred_at`、`title` 与最小必要 `evidence_excerpt`；
- `event_type`、`event_subtype`、`direction`、重要性、风险、置信度和来源质量；
- 结构化 `facts`、`uncertainties`；旧文件中的 `requires_human_review` 仅作为兼容字段接受，不覆盖当前发布策略。

目标公司必须预先存在。信用代码优先，其次使用工商全称、地区和已核验官网；未唯一解析或官网冲突时保留原始文档与实体提及并创建身份审核项，不生成事件。解析成功后检查 URL 可用性、来源 A/B、官方域名一致性、可信度和风险：全部满足时自动发布并更新快照；否则以 `unconfirmed_lead` 保存且不创建逐条事件审核任务。同一候选事件的新独立来源会追加证据；已发布事件的新安全证据可直接附加，其他证据保持未确认且不改变既有事实。相同租户内的文件哈希/解析器版本和批次编号分别幂等；同一外部记录内容冲突时整批回滚。

来源发布日期与事件实际发生时间必须分开；仅知道日期时使用 `source_published_on`，不得虚构时刻。未知保持 `null`，不得用查询时间或观察时间替代来源/事件时间。所有非空时间戳必须带时区。

V1 不复制保存原始文件字节，只保存受 RLS 保护的批次元数据、文件哈希，以及许可允许的公开来源定位、最小证据片段和结构化记录。CSV、Excel、Markdown、网页上传、内部财务和投资协议等敏感材料均未实现，启用前需另行设计格式、恶意内容隔离、正式认证与存储许可。实体提及不能由通用事件审核接口批准；只能在专用流程中选择有效关联的官方候选，由程序重跑解析、事件生成和发布路由。

官方工商导入另使用 `data/private/identity_imports/`。它只接受 HTTPS 政府/GSXT 域名、`license_status=public`、带时区核验时间和通过校验位的统一社会信用代码。官方全称/地区冲突记为 `conflict`，无现有公司记为 `unmatched`，两者都不自动改主数据或创建公司。详见 ADR-0008。

```mermaid
sequenceDiagram
  actor A as 管理员
  participant K as Kimi/人工研究导出
  participant I as ResearchImportProvider
  participant D as 去重与身份解析
  participant G as 证据与风险闸门
  participant Q as 身份例外队列
  A->>K: 在授权产品中完成研究并导出文件
  A->>I: 从私有目录导入 JSON
  I->>I: 校验格式、文件哈希、许可和批次元数据
  I->>D: 创建公开证据记录，不发布
  D->>D: 文档去重并解析公司主体
  alt 主体未唯一解析
    D->>Q: 创建实体提及审核项，不生成事件
  else 主体已验证
    D->>G: 创建事件候选与证据
    alt URL、来源、可信度和风险均通过
      G->>D: 自动发布并更新快照
    else 命中质量或风险条件
      G->>D: 保存 unconfirmed_lead，不创建人工任务
    end
  end
```

URL 验证是受控外部读取：`EXTERNAL_CALLS_ENABLED=false` 时不发请求并将记录降级为未确认；开启后仍受 `SOURCE_URL_MAX_CHECKS_PER_IMPORT` 限制，且拒绝私网、回环和非 HTTP(S) 目标。该步骤不调用模型或付费 API，请求数写入 `usage_ledger`。

## 7. DeepSeek 低成本抽取

`DeepSeekLLMProvider` 默认关闭。只有文档哈希为新、规则判定相关、主体候选已准备、预算通过时才调用；输入仅含必要身份、标题、证据片段和 Schema，采用低温度、受限输出和非推理/低成本配置（具体能力确认后再定）。响应必须通过[严格事件 Schema](08-api-design.md)，记录 provider、model、prompt/schema 版本、参数、Token、延迟和估算费用。不得补全缺失金额、营收、利润、现金、估值或持股。

## 8. 缓存与变化检测

搜索请求以 Provider、规范查询、身份版本和时间窗为缓存键；文档按外部记录 ID、规范 URL、ETag/Last-Modified 和内容哈希判断变化。缓存未过期或内容哈希未变时直接复用结果，不调用 LLM、不重建报告。缓存命中、无变化运行和有效事件均写入用量台账，支持成本归因。

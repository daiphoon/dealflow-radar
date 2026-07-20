# 08 API 与结构化事件 Schema

## 1. API 原则

版本前缀 `/api/v1`；JSON 字段使用英文；写操作要求认证、授权、审计和幂等键。列表使用游标分页。同步读取不得调用外部搜索或模型。所有公司响应包含 `data_as_of`、`last_checked_at`、`freshness_status`、`information_gaps`；平台共享基础层与个人/机构私有叠加层分别授权，私密数据不能混入共享快照或共享缓存。

认证有两个明确模式：`AUTH_PROVIDER=demo` 仅在本地和 CI 接受 `X-Demo-User-Id`；`AUTH_PROVIDER=cloudbase` 只接受有效 Bearer token，并完全忽略 Demo Header。CloudBase 只证明身份，API 仍从本地用户、角色、基金授权和 RLS 判断业务权限。无效或过期身份统一返回 `401`，有效 CloudBase 身份但没有唯一 active 本地邀请返回 `403 invitation_required`，身份服务故障返回 `503 authentication_unavailable`。

邀请制认证接口为：

- `POST /api/v1/auth/email/verification`：固定向 CloudBase 已存在账户发送邮箱验证码；不存在账户返回不可区分的通用响应；
- `POST /api/v1/auth/email/login`：验证码换 token，并在该受控路径中完成唯一的首次本地邀请映射和登录审计；
- `POST /api/v1/auth/token/refresh`：轮换 token 并按已绑定 subject 重新检查本地账户状态，不允许首次邮箱绑定；
- `GET /api/v1/auth/me`：返回本地 user/tenant 身份，不返回 CloudBase group 作为业务角色；
- `POST /api/v1/auth/logout`：撤销 CloudBase 会话并追加退出审计。

前端仅通过 Next.js 服务端动作调用登录接口，token 保存为 `HttpOnly` Cookie；不能进入 URL、浏览器 JavaScript、数据库或日志。同步公司查询仍不调用天眼查、搜索或模型。

## 2. 端点草案

| 方法与路径 | 用途 | 关键行为 |
| --- | --- | --- |
| `GET /companies` | 当前授权公司列表 | 当前仍按基金权限；后续个人列表与 watchlist 单独设计 |
| `GET /companies/search?q=` | 搜索平台共享公司 | 已实现；信用代码、工商全称或已核实别名精确匹配，只查数据库，不自动建公司 |
| `GET /companies/{id}` | 公司详情 | 已实现共享基础层独立读取，并按基金/owner 授权叠加私有层 |
| `GET /companies/{id}/changes?since=` | 上次查看后变化 | 只返回版本化事实变化和纠正撤回 |
| `GET /companies/{id}/events` | 事件时间线 | 按有效权益和记录作用域返回事件；业务状态不代替授权 |
| `GET /events/{id}/evidence` | 证据 | 对证据引用和原始文档分别授权，只返回许可允许的最小内容 |
| `GET /companies/{id}/metrics` | 指标历史 | 返回来源性质、期间、单位和审核状态 |
| `POST /companies/{id}/refresh` | 请求更新或 `dry-run` | 检查开关/预算/冷却，返回已有或新任务 ID |
| `GET /refresh-jobs/{id}` | 查看后台状态 | 不暴露 Secret 或 Provider 原始敏感响应 |
| `POST /research-imports` | 创建导入批次 | 文件哈希幂等；身份通过后自动发布或保留未确认线索（尚未实现网页/API 上传） |
| `GET /reviews` | 身份例外及历史审核队列 | 按角色/范围排序；新导入不为每条事件创建任务 |
| `GET /reviews/workbench` | 审核工作台详情 | V1 仅在显式开关开启后返回身份例外与既有历史候选 |
| `POST /reviews/{id}/decision` | 既有事件审核决定 | 兼容批准或驳回；理由必填，事务发布并保留决定记录 |
| `POST /reviews/{id}/identity-resolution` | 选择官方工商候选 | 要求审核员+机构管理员；更新身份、重建原事件/证据并按版本化策略重路由 |
| `GET /sharing-candidates` | 平台共享候选工作台 | 仅平台管理员；读取可晋升的私有候选、身份/风险/来源/证据状态及既有决定 |
| `POST /events/{id}/sharing/promotion` | 晋升独立共享事实 | 仅平台管理员；谨慎表述、理由、所选证据和必要确认必填；幂等创建或复用共享事件 |
| `POST /events/{id}/sharing/rejection` | 拒绝私有候选晋升 | 仅平台管理员；保留私有候选并追加理由与策略审计 |
| `POST /shared-events/{id}/retraction` | 撤回共享事实 | 仅平台管理员；共享事件停止展示，保留私有来源、撤回理由和全部血缘 |
| `GET /reports/portfolio-weekly` | 固定模板周报 | 按事实水位读取已生成结果 |
| `GET /usage` | 成本仪表盘 | 聚合租户/公司/Provider/有效事件成本 |

人工研究导入 V1 仅实现本机命令 `python -m scripts.import_research_json`；政府身份 JSON 通过 `python -m scripts.import_official_identity_json` 导入；天眼查授权身份通过 `python -m scripts.import_tianyancha_identities` 在独立受控进程查询并导入。三者均未开放网页上传或同步查询 API；即使 CloudBase 身份已接入，上传隔离、文件审计和许可校验仍须单独验收。`GET /companies` 继续返回基金授权列表；`GET /companies/search?q=` 和共享公司详情不要求基金关系，但要求有效身份，且永不触发天眼查调用。

当前详情响应使用 `events` 表示平台共享已审核事实、`private_events` 表示当前机构可见的已确认信息、`unconfirmed_leads` 表示当前个人或机构 owner 可见的未确认线索，`investments` 只在基金授权存在时返回记录。证据引用和原始文档分别做作用域过滤；共享事件只序列化独立展示引用的来源名称、URL、日期、状态和许可短摘录，不读取私有原文档。无基金用户不会因共享详情请求触发机构私有刷新状态或任务。`GET /reviews/workbench` 默认返回 `404`，仅在本机受控环境设置 `REVIEW_WORKBENCH_ENABLED=true` 后开放；普通审核区要求 `reviewer`，共享晋升区另要求 `platform_admin`，该开关不能替代认证。

链接展示按 `link_display_allowed` 和检查状态决定：健康链接显示检查时间；合法但未检查的 URL 可点击并明确警告；失效链接只保留历史来源信息；不安全或许可受限链接不返回为可点击链接。链接可点击不表示证据内容已完成实质核验。

`POST /refresh` 的响应明确区分 `fresh_noop`、`queued`、`merged`、`cooldown_deferred`、`budget_deferred`、`external_disabled`。`dry_run=true` 时只返回计划 Provider、搜索数、Token 上界和预计费用，不产生外部调用。

## 受控来源运营 API

以下接口仅限当前 tenant 的 `platform_admin`，个人用户和普通机构用户返回统一权限错误；所有检查都只入队，不在 API 请求内联网：

- `POST/GET/PATCH /api/v1/trusted-sources`：登记、查看、启停来源，维护列表内容路径、许可依据和保留策略；
- `POST /api/v1/trusted-sources/{id}/runs`：单来源 dry-run 或真实检查入队；
- `POST /api/v1/companies/{id}/trusted-source-runs` 与 `POST /api/v1/trusted-source-runs/batch`：受限小批量入队；
- `GET /api/v1/trusted-source-runs`：读取请求、字节、变化、robots、错误和费用审计；
- `GET /api/v1/candidate-documents` 与 `POST /api/v1/candidate-documents/{id}/decision`：查看候选并标记值得研究、无关、重复或来源失效；
- `POST /api/v1/candidate-documents/{id}/research-import`：对已标记值得研究的候选录入结构化事实，并复用现有研究导入服务生成本租户私有文档、证据和候选事件。

候选决定本身不创建文档或事件。只有管理员另行提交结构化研究表单后，才生成 `organization_private` 底稿和候选事件；该接口幂等，保留 `candidate_document_id` 血缘，不自动创建平台共享事实。来源为 `unclear/restricted`、身份未核验或链接失效时失败关闭；`public_access` 底稿不具备共享晋升资格。

按需缓存 V1 的 `freshness_status` 由当前快照 `last_checked_at` 与配置 TTL 动态计算。首次过期详情请求返回 `stale` 并完成入队；已有活动任务时返回 `refreshing`。`AUTO_REFRESH_ENABLED=false` 时只返回状态，不创建任务；无论开关如何，同步请求都不调用 Provider。

## 3. 双通道搜索与详情契约

### 搜索

- 第一版只支持统一社会信用代码精确搜索、工商全称和已核实别名搜索；模糊名称不自动绑定或创建公司。
- 搜索只返回当前用户具备有效访问资格的平台共享目录，不返回个人或机构私有主体、线索或计数。
- 搜索无结果时未来可返回“请求收录”入口，但同步请求不访问外部数据源。
- 公司是否在个人 watchlist 或某基金中，不影响其共享目录读取资格。

### 详情组合

当前最小响应逻辑分为：

- `shared_profile`：已核验身份、共享事件、允许展示的证据引用、新鲜度和来源状态；
- `personal_overlay`：仅当前用户的关注、备注、查看水位和个人线索；
- `organization_overlays`：仅当前用户有机构及资源授权的基金投资、机构线索和私有资料。

watchlist、个人备注和正式 organization overlay 尚未实现；授权顺序已经固定为“共享基础层独立判断，私有层逐层叠加”。无基金授权不再导致共享公司详情整体不可访问。

### 防枚举

- 无权访问与不存在的私有对象采用不可区分的响应；
- 搜索总数、字段是否存在和错误详情不得暴露其他客户的关注、投资、线索或资料；
- 用户缺少通用共享权益时返回一致的权益错误，不返回资源特定的私有信息；
- 私有字段在服务端授权后拼装，不允许依靠前端隐藏。

## 4. Pydantic v2 事件候选 Schema

以下是设计契约，不是本阶段应用代码。`event_subtype` 还需按 taxonomy 版本校验；时间必须带时区。证据支持性和重大负面规则由风险闸门做语义校验。

```python
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventType(StrEnum):
    FINANCIAL_OPERATION = "financial_operation"
    FINANCING_CAP_TABLE = "financing_cap_table"
    CONTRACT_COMMERCIAL = "contract_commercial"
    PRODUCT_TECHNOLOGY = "product_technology"
    GOVERNANCE_PEOPLE = "governance_people"
    LEGAL_COMPLIANCE = "legal_compliance"
    CAPACITY_ASSETS = "capacity_assets"
    EXIT_LIQUIDITY = "exit_liquidity"
    INFORMATION_QUALITY = "information_quality"


class Direction(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class RiskSeverity(StrEnum):
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class SourceQuality(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class PublishDecision(StrEnum):
    AUTO_PUBLISH = "auto_publish"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=1000)
    unit: str | None = Field(default=None, max_length=32)


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: UUID
    quote: str = Field(min_length=1, max_length=1000)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)


class StrictEventCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    prompt_version: str = Field(min_length=1, max_length=64)
    matched_company_id: UUID | None
    company_match_confidence: float = Field(ge=0, le=1)
    event_type: EventType
    event_subtype: str = Field(min_length=1, max_length=64)
    occurred_at: datetime | None
    published_at: datetime | None
    direction: Direction
    materiality_score: int = Field(ge=0, le=100)
    risk_severity: RiskSeverity
    confidence_score: float = Field(ge=0, le=1)
    source_quality: SourceQuality
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    facts: list[Fact] = Field(min_length=1, max_length=50)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    counterparty: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    evidence_spans: list[EvidenceSpan] = Field(min_length=1, max_length=20)
    publish_decision: PublishDecision
    requires_human_review: bool

    @model_validator(mode="after")
    def validate_publication(self) -> "StrictEventCandidate":
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency must be provided together")
        if self.publish_decision == PublishDecision.AUTO_PUBLISH:
            if self.matched_company_id is None:
                raise ValueError("auto-publish requires a resolved company")
            if self.requires_human_review:
                raise ValueError("review-required event cannot auto-publish")
            if self.risk_severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}:
                raise ValueError("high-risk event cannot auto-publish")
        return self
```

`occurred_at`、`published_at` 和 `matched_company_id` 可空，是为了如实表达未知或待解析；自动发布时公司必须已解析。金额未知就保持 `null`，不能从上下文估算。`summary` 只能压缩 `facts` 和 `evidence_spans` 已支持的事实；不确定内容放入 `uncertainties`。解析失败、额外字段、评分越界或交叉校验失败都不得写入已发布事件。

模型给出的评分只是候选输入。发布层根据来源注册表和确定性规则复核 `source_quality`，计算并保存重大性/可信度组成因素；许可状态始终是独立闸门，不由模型评分决定。

## 4. 版本与审计

Schema 版本、taxonomy 版本、事件指纹版本和 prompt 版本分别保存；模型调用记录 Provider、模型配置、Token、延迟、费用和原始响应哈希。版本升级先兼容读取旧事件，再用新 prompt 生成新候选，不能静默重写历史。

## 5. 错误与安全响应

使用稳定错误码：`unauthorized`、`forbidden_scope`、`not_found`、`validation_failed`、`conflict_active_job`、`cooldown_deferred`、`budget_deferred`、`external_disabled`、`provider_deferred`。响应不暴露租户是否存在、Secret、私密字段、原始全文或内部异常堆栈；所有写请求带请求关联 ID并进入审计日志。

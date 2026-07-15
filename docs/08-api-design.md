# 08 API 与结构化事件 Schema

## 1. API 原则

版本前缀 `/api/v1`；JSON 字段使用英文；写操作要求认证、授权、审计和幂等键。列表使用游标分页。同步读取不得调用搜索或模型。所有公司响应包含 `data_as_of`、`last_checked_at`、`freshness_status`、`information_gaps`；私密投资数据放入独立授权字段，不能混入公共快照。

第 2 阶段仅使用 `X-Demo-User-Id` 注入固定虚构测试身份，用于验证 RBAC 与基金隔离；缺失或无效身份返回 `401`。该 Header 不是生产认证方案，真实数据验证前必须替换为正式身份系统。

## 2. 端点草案

| 方法与路径 | 用途 | 关键行为 |
| --- | --- | --- |
| `GET /companies` | 公司列表 | 按权限展示新鲜度和风险；不因列表访问批量入队 |
| `GET /companies/{id}` | 公司详情 | 读快照/事件/指标；过期且自动刷新开启时创建或合并后台任务 |
| `GET /companies/{id}/changes?since=` | 上次查看后变化 | 只返回版本化事实变化和纠正撤回 |
| `GET /companies/{id}/events` | 事件时间线 | 仅按权限返回发布状态与证据元数据 |
| `GET /events/{id}/evidence` | 证据 | 按许可返回最小片段或受控存储引用 |
| `GET /companies/{id}/metrics` | 指标历史 | 返回来源性质、期间、单位和审核状态 |
| `POST /companies/{id}/refresh` | 请求更新或 `dry-run` | 检查开关/预算/冷却，返回已有或新任务 ID |
| `GET /refresh-jobs/{id}` | 查看后台状态 | 不暴露 Secret 或 Provider 原始敏感响应 |
| `POST /research-imports` | 创建导入批次 | 文件哈希幂等；只生成候选 |
| `GET /reviews` | 审核队列 | 按角色/范围和风险排序 |
| `POST /reviews/{id}/decision` | 通过、纠正、驳回、撤回 | 幂等、理由必填、事务发布和审计 |
| `GET /reports/portfolio-weekly` | 固定模板周报 | 按事实水位读取已生成结果 |
| `GET /usage` | 成本仪表盘 | 聚合租户/公司/Provider/有效事件成本 |

人工研究导入 V1 仅实现本机命令 `python -m scripts.import_research_json`，尚未实现 `POST /research-imports`。原因是当前 `X-Demo-User-Id` 只适用于虚构测试，不足以保护真实文件上传；网页/API 导入须等正式认证、上传隔离、文件审计和许可校验完成后再实现。`POST /reviews/{id}/decision` 当前只处理事件审核；实体提及审核会明确拒绝直接批准，后续需新增“选择公司并重建候选”的专用身份解析契约。

`POST /refresh` 的响应明确区分 `fresh_noop`、`queued`、`merged`、`cooldown_deferred`、`budget_deferred`、`external_disabled`。`dry_run=true` 时只返回计划 Provider、搜索数、Token 上界和预计费用，不产生外部调用。

按需缓存 V1 的 `freshness_status` 由当前快照 `last_checked_at` 与配置 TTL 动态计算。首次过期详情请求返回 `stale` 并完成入队；已有活动任务时返回 `refreshing`。`AUTO_REFRESH_ENABLED=false` 时只返回状态，不创建任务；无论开关如何，同步请求都不调用 Provider。

## 3. Pydantic v2 事件候选 Schema

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

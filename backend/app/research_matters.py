"""从证据形成有类型的事项候选；字段校验不授予事实发布资格。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VERSION = "matter-v1"
PROMPT_VERSION = "research-matter-extraction-v2"
EXTRACTION_VERSION = "matter-extraction-v2"
# 每类均有实际动作契约；不把整篇文章标题当作事件动作。
ACTIONS = (
    (
        "financing_cap_table",
        "fund_commitment",
        r"认缴.{0,35}?(?:基金|合伙企业)|(?:基金|合伙企业).{0,35}?认缴",
    ),
    (
        "financing_cap_table",
        "outbound_investment",
        r"(?:战略投资|收购|购买).{1,45}?(?:股权|股份|科技|公司)|(?:对外|战略)投资",
    ),
    ("exit_liquidity", "acquisition_target", r"被.{0,35}?(?:收购|并购)"),
    ("financing_cap_table", "registered_capital", r"注册资本.{0,30}?(?:增加|增至|变更|由|从)"),
    (
        "financing_cap_table",
        "company_financing",
        r"(?:完成|获得|获).{0,35}?(?:融资|[A-F]轮)|完成[A-F](?:\+)?轮"
        r"|(?:获得|获).{0,35}?(?:战略投资|股权投资)",
    ),
    ("exit_liquidity", "ipo_guidance_agreement", r"(?:签署|签订).{0,25}?辅导协议"),
    (
        "exit_liquidity",
        "ipo_guidance_completed",
        r"(?:完成|通过).{0,10}?(?:IPO|上市)?辅导(?:验收)?(?!备案)",
    ),
    (
        "exit_liquidity",
        "ipo_guidance",
        r"(?:启动|开启|完成).{0,10}?辅导备案|(?:IPO|上市)辅导(?:备案|登记)|启动.{0,5}(?:IPO|上市)辅导",
    ),
    (
        "exit_liquidity",
        "ipo_application",
        r"(?:提交|递交|递表).{0,20}?(?:上市申请|招股书)|向.{0,12}?递表",
    ),
    ("exit_liquidity", "ipo_hearing", r"(?:通过|获).{0,12}?(?:聆讯|聆聽)"),
    ("exit_liquidity", "ipo_inquiry", r"(?:进入|收到|回复).{0,12}?问询"),
    (
        "exit_liquidity",
        "ipo_accepted",
        r"(?:IPO|上市申请|发行申请).{0,20}?(?:获受理|已受理)|受理.{0,15}?上市申请",
    ),
    ("exit_liquidity", "ipo_filing", r"(?:境外|全流通).{0,50}?备案|备案通知书"),
    (
        "exit_liquidity",
        "ipo_offering",
        r"(?:今起|启动|开始|公开)招股|本次IPO.{0,15}?发行|(?:拟|计划)发行.{0,20}?股",
    ),
    (
        "exit_liquidity",
        "ipo_listed",
        r"(?:正式|成功)(?:在.{1,10})?(?:挂牌上市|登陆港交所|上市)|已上市|挂牌上市",
    ),
    (
        "exit_liquidity",
        "ipo_listing_plan",
        r"(?:启动|计划|拟|将).{0,25}?(?:赴港上市|港股IPO|A股上市|挂牌上市)|(?:否认|不属实).{0,20}?上市|上市.{0,20}?(?:否认|不属实)",
    ),
    ("exit_liquidity", "ipo_withdrawn", r"(?:撤回|终止).{0,15}?(?:IPO|上市申请)"),
    ("contract_commercial", "contract_award", r"中标|签订.{0,30}?合同|签署.{0,30}?合作协议"),
    (
        "product_technology",
        "product_milestone",
        r"发布.{1,30}?(?:产品|机器人|系统)|(?:获得|通过).{0,25}?(?:认证|注册证)|获批",
    ),
    (
        "financial_operation",
        "operating_disclosure",
        r"(?:营收|营业收入|净利润|出货量).{0,20}?(?:亿元|万元|增长|下降|达到|台)|停产|欠薪",
    ),
    (
        "governance_people",
        "management_change",
        r"(?:董事长|总经理|高管|创始人).{0,20}?(?:离职|辞任|变更)|任命.{0,20}?(?:董事长|总经理)",
    ),
    (
        "legal_compliance",
        "legal_development",
        r"被.{0,15}?(?:起诉|处罚|立案|执行)|(?:签署|签订).{0,15}?补充协议|(?:义务|条款).{0,20}?终止|破产",
    ),
    (
        "capacity_assets",
        "capacity_development",
        r"(?:工厂|生产线|项目).{0,20}?(?:投产|开工|停工|延期)|新建.{0,20}?(?:工厂|生产线)",
    ),
)
LABELS = {
    "fund_commitment": "基金认缴",
    "outbound_investment": "对外投资",
    "acquisition_target": "被收购",
    "registered_capital": "注册资本变化",
    "ipo_guidance_agreement": "辅导协议签署",
    "company_financing": "获得融资",
    "ipo_guidance_completed": "辅导完成",
    "ipo_guidance": "辅导备案",
    "ipo_hearing": "上市聆讯",
    "ipo_application": "上市申请提交",
    "ipo_inquiry": "上市问询",
    "ipo_accepted": "上市申请受理",
    "ipo_filing": "境外上市备案",
    "ipo_offering": "招股发行",
    "ipo_listed": "挂牌上市",
    "ipo_listing_plan": "上市计划",
    "ipo_withdrawn": "上市申请撤回",
    "contract_award": "合同与中标",
    "product_milestone": "产品技术进展",
    "operating_disclosure": "经营披露",
    "management_change": "治理人员变化",
    "legal_development": "司法合规进展",
    "capacity_development": "产能资产进展",
}
STATUS_LABELS = {
    "denied": "否认或更正",
    "planned": "计划",
    "committed": "认缴承诺",
    "conditional": "附条件安排",
    "reported": "来源报道",
}
FIELD_LABELS = {
    "share_quantity": "股份数量",
    "valuation": "估值",
    "registered_capital_before": "变更前注册资本",
    "registered_capital_after": "变更后注册资本",
    "commitment": "认缴金额",
    "investment": "对外投资金额",
    "financing": "融资金额",
    "proposed_proceeds": "拟募集金额",
    "reported_amount": "来源金额（性质待核）",
    "round": "融资轮次",
    "investors": "投资方（来源口径）",
    "date": "原文日期",
    "transaction_id": "交易标识",
    "project_id": "项目标识",
}

AMOUNT = re.compile(
    r"(?:约|近|超|超过|不超过|不多于|至少|至多|不足)?\s*(?:\d[\d,.]*|[一二两三四五六七八九十百千数几]+)\s*(?:亿|万)?\s*(?:人民币|港元|美元|元|股)"
)
DATE = re.compile(r"(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})日?")


def digest(value):
    from backend.app.evidence_integrity import hash_canonical_object

    return hash_canonical_object(value)


def compact(value):
    return re.sub(r"\s+", "", value or "")


@dataclass
class Matter:
    category: str
    subtype: str
    subject: str
    scope: str
    action: str
    status: str
    fields: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def payload(self):
        return asdict(self)

    def facts(self):
        return [
            {"name": "事项阶段", "value": LABELS[self.subtype], "unit": None},
            {"name": "动作状态", "value": STATUS_LABELS[self.status], "unit": None},
            *[
                {"name": FIELD_LABELS.get(key, key), "value": item["value"], "unit": None}
                for key, item in self.fields.items()
                if key not in {"date"}
            ],
        ]


class ProposedField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=1000)
    role: Literal[
        "financing",
        "valuation",
        "investment",
        "commitment",
        "share_quantity",
        "round",
        "investors",
        "registered_capital_before",
        "registered_capital_after",
        "proposed_proceeds",
        "occurred",
        "disclosed",
        "planned",
        "transaction_id",
        "project_id",
    ]
    currency: Literal["CNY", "HKD", "USD", "shares"] | None = None


class ProposedMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=200)
    # None 仅兼容已封存的 v1 输出；新提示必须提供这些类型。
    subtype: str | None = Field(default=None, max_length=60)
    category: str | None = Field(default=None, max_length=60)
    subject_role: Literal["fundraiser", "investor", "buyer", "target", "issuer", "actor"] | None = (
        None
    )
    scope: str | None = Field(default=None, max_length=240)
    status: Literal["reported", "planned", "denied", "committed", "conditional"] | None = None
    action_quote: str = Field(min_length=1, max_length=1500)
    fields: dict[str, ProposedField] = Field(default_factory=dict, max_length=12)


class ProposedMatters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matters: list[ProposedMatter] = Field(max_length=24)


class TypedProposedMatter(ProposedMatter):
    subtype: str = Field(min_length=1, max_length=60)
    subject_role: Literal["fundraiser", "investor", "buyer", "target", "issuer", "actor"]
    status: Literal["reported", "planned", "denied", "committed", "conditional"]


class TypedProposedMatters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matters: list[TypedProposedMatter] = Field(max_length=24)


def names_in_document(subject, text):
    names = [subject.legal_name, *subject.aliases]
    # 文内“工商全称（以下简称 X）”只在该文有效，不写回公司身份。
    for name in list(names):
        for m in re.finditer(
            re.escape(name)
            + r"[（(](?:以下简称|简称)[：:、\s]*[“\"「]?([^”\"」）)]{2,30})[”\"」]?[）)]",
            text,
        ):
            names.append(m.group(1).strip())
    return tuple(sorted(set(names), key=len, reverse=True))


def clean_body(text):
    # 仅删有明确界限的站内生成解读，普通正文和日期保留。
    text = re.sub(
        r"AI投资人解读.*?(?=投资界（|投资界\(|文章来源：)",
        lambda m: " " * len(m.group()),
        text,
        flags=re.S,
    )
    for marker in ("相关阅读", "相关推荐", "相关文章", "精彩推荐", "你可能也喜欢"):
        text = text.split(marker, 1)[0]
    return text.rstrip()


def classification(action):
    matches = [
        (category, subtype)
        for category, subtype, pattern in ACTIONS
        if re.search(pattern, action, re.I)
    ]
    if not matches:
        return None
    if any(sub.startswith("ipo_") for _, sub in matches):
        matches = [(cat, sub) for cat, sub in matches if sub.startswith("ipo_")]
    category, subtype = min(
        matches,
        key=lambda pair: next(
            re.search(pattern, action, re.I).start()
            for cat, sub, pattern in ACTIONS
            if (cat, sub) == pair and re.search(pattern, action, re.I)
        ),
    )
    if subtype.startswith("ipo_"):
        # 阶段优先取确定动作，计划不升级；备案与辅导备案分开。
        if "辅导备案" in action:
            subtype = "ipo_guidance"
        elif "聆讯" in action:
            subtype = "ipo_hearing"
        elif "问询" in action:
            subtype = "ipo_inquiry"
        elif (
            re.search(r"(?:计划|拟|将).{0,40}(?:挂牌上市|上市)", action) and subtype == "ipo_listed"
        ):
            subtype = "ipo_listing_plan"
    return category, subtype


def infer_fields(action, subtype):
    fields = {}
    for match in AMOUNT.finditer(action):
        raw = match.group()
        before = action[max(0, match.start() - 12) : match.start()]
        role = (
            "share_quantity"
            if raw.endswith("股")
            else "valuation"
            if "估值" in before or "市值" in before
            else "registered_capital"
            if subtype == "registered_capital"
            else "commitment"
            if subtype == "fund_commitment"
            else "investment"
            if subtype == "outbound_investment"
            else "financing"
            if subtype == "company_financing"
            else "proposed_proceeds"
            if "募资" in before
            else "reported_amount"
        )
        if role == "registered_capital":
            role = (
                "registered_capital_before"
                if re.search(r"由|从", before) and not re.search(r"至|到|为", before)
                else "registered_capital_after"
            )
        if role in fields and compact(fields[role]["value"]) != compact(raw):
            # 一个角色有多个金额时保留原文，不能任取其一。
            fields[role] = {
                "value": "多个金额，待核对",
                "quote": action,
                "role": "ambiguous_amount",
            }
        else:
            fields.setdefault(role, {"value": raw, "quote": action, "role": role})
    round_match = re.search(r"(?:Pre[-‑ ]?)?[A-F](?:\+)?\s*轮|天使轮|种子轮", action, re.I)
    if round_match and subtype == "company_financing":
        fields["round"] = {"value": round_match.group(), "quote": action, "role": "round"}
    if subtype == "company_financing":
        investors = []
        for match in re.finditer(
            r"(?:由|[，,])([^。；;，,]{1,100}?)(?:联合领投|领投|跟投|参与投资)", action
        ):
            investors.extend(
                re.sub(r"^(?:由)?(?:老股东|新股东|现有股东)?\s*", "", v.strip())
                for v in re.split(r"、|及|和|与", match.group(1))
                if v.strip()
            )
        if investors:
            fields["investors"] = {
                "value": "、".join(dict.fromkeys(investors)),
                "quote": action,
                "role": "investors",
            }
    for role, label in (
        ("transaction_id", "交易编号|交易标识"),
        ("project_id", "项目编号|项目代码"),
    ):
        identity = re.search(r"(?:" + label + r")[：:]?\s*([A-Za-z0-9_-]+)", action)
        if identity:
            fields[role] = {"value": identity.group(1), "quote": action, "role": role}
    match = DATE.search(action)
    if match:
        action_start = next(
            (
                re.search(pattern, action).start()
                for _, s, pattern in ACTIONS
                if s == subtype and re.search(pattern, action)
            ),
            0,
        )
        # 动作之后的协议日期不能赋给先前已完成的事项。
        if match.start() < action_start:
            role = (
                "disclosed"
                if re.search(r"披露|报道|消息|显示|日电", action[:action_start])
                else "occurred"
            )
            value = match.group()
            fields["date"] = {"value": value, "quote": action, "role": role}
            if match.group(1) and role == "occurred":
                try:
                    fields["date"]["iso"] = date(*map(int, match.groups())).isoformat()
                except ValueError:
                    fields.pop("date")
    return fields


def comparison_text(value):
    return re.sub(r"重磅|最新|新一轮", "", compact(value))


def subject_mentions(names, legal_name, text):
    """简称必须是独立主体，不能截取另一家公司的全称或英文名称。"""
    for name in names:
        for match in re.finditer(re.escape(name), text):
            if name != legal_name:
                tail = text[match.end() :]
                head = text[: match.start()]
                if re.match(
                    r"[a-zA-Z0-9]|(?:科技|设备|集团|控股)?(?:有限|股份|子公司|的子公司)",
                    tail,
                ):
                    continue
                if name[0].isascii() and head and re.search(r"[a-zA-Z0-9]$", head):
                    continue
            yield match, name


def extract_matters(subject, text):
    body = clean_body(text)
    names = names_in_document(subject, body)
    matters = []
    local_names = set(names) - {subject.legal_name, *subject.aliases}
    from backend.app.matter_validation import actor_supported, scoped_status

    for block in body.splitlines():
        last_name = None
        pieces = []
        for sentence in re.split(r"[。；;]+", block):
            parts = re.split(
                r"[，,](?:同时|并且|并(?=向|通过|提交|完成)|另(?:外)?(?:还)?|但)", sentence
            )
            pieces.extend(parts)
        for paragraph in pieces:
            paragraph = paragraph.strip()
            valid_mentions = list(subject_mentions(names, subject.legal_name, paragraph))
            name = valid_mentions[0][1] if valid_mentions else None
            # 只允许明确代词延续上一句主体，不能把整页其他公司的动作归入目标。
            pronoun = re.match(
                r"(?:(?:通知书|公告|报告)显示[，,]?)?(?:该公司|公司|其|本轮)", paragraph
            )
            if name is None and pronoun and last_name:
                name = last_name
            elif (
                name is None
                and last_name
                and paragraph in pieces
                and re.match(r"向|通过|提交|完成|拟|否认", paragraph)
            ):
                name = last_name
            elif name is None:
                last_name = None
                continue
            last_name = name
            classified = classification(paragraph)
            if not classified or len(paragraph) > 1500:
                if (
                    pronoun
                    and matters
                    and matters[-1].subject == name
                    and matters[-1].subtype == "company_financing"
                ):
                    old = matters[-1]
                    begin, end = block.find(old.action), block.find(paragraph) + len(paragraph)
                    if begin >= 0 and 0 < end - begin <= 1500:
                        old.action = block[begin:end]
                        old.fields = infer_fields(old.action, old.subtype)
                continue
            category, subtype = classified
            if name not in paragraph:
                begin, end = block.find(name), block.find(paragraph) + len(paragraph)
                if begin < 0 or end - begin > 1500:
                    continue
                paragraph = block[begin:end]
            if not actor_supported(name, paragraph, subtype):
                if subtype == "acquisition_target" and actor_supported(
                    name, paragraph, "outbound_investment"
                ):
                    category, subtype = "financing_cap_table", "outbound_investment"
                else:
                    continue
            action_pos = next(
                (
                    re.search(pattern, paragraph, re.I).start()
                    for cat, sub, pattern in ACTIONS
                    if sub == subtype and re.search(pattern, paragraph, re.I)
                ),
                len(paragraph),
            )
            mentions = [(m.end(), n) for m, n in valid_mentions if m.end() <= action_pos]
            if mentions:
                name = max(mentions)[1]
                last_name = name
            tail = paragraph.split(name, 1)[-1] if name in paragraph else paragraph
            if re.match(r"(?:的|旗下)?(?:子公司|母公司|集团)", tail):
                continue
            if subtype == "company_financing" and re.search(
                r"(?:投资的|旗下|子公司|参股的).{0,35}(?:完成|获得)", tail
            ):
                continue
            # 文内法定简称与已确认品牌不同，不升级品牌/集团为法人。
            scope = (
                "legal_entity"
                if name == subject.legal_name
                or name in local_names
                or name in getattr(subject, "legal_aliases", ())
                else f"brand:{compact(name)}"
            )
            status = scoped_status(paragraph, subtype)
            fields = infer_fields(paragraph, subtype)
            if status in {"planned", "denied", "conditional"} and "date" in fields:
                fields["date"].pop("iso", None)
                fields["date"]["role"] = "planned" if status == "planned" else "disclosed"
            issues = []
            if "人工智能生成" in body or "AI生成" in body:
                issues.append("generated_source_not_fact")
            if status == "denied":
                issues.append("denial_or_correction_requires_review")
            if "date" not in fields or "iso" not in fields["date"]:
                issues.append("occurrence_date_unknown")
            matters.append(
                Matter(category, subtype, name, scope, paragraph, status, fields, issues)
            )
    distinct = []
    for matter in matters:
        old = next(
            (
                m
                for m in distinct
                if compatible(m, matter)
                or (
                    m.scope == matter.scope
                    and m.subtype == matter.subtype
                    and m.status == matter.status
                    and (
                        comparison_text(m.action) in comparison_text(matter.action)
                        or comparison_text(matter.action) in comparison_text(m.action)
                    )
                )
            ),
            None,
        )
        if old is None:
            distinct.append(matter)
        else:
            # 仅合并同文重复叙述；保留更完整原文，不把两个金额冲突静默覆盖。
            if all(
                k not in old.fields or compact(old.fields[k]["value"]) == compact(v["value"])
                for k, v in matter.fields.items()
                if k != "date"
            ):
                if len(matter.action) > len(old.action):
                    distinct[distinct.index(old)] = matter
            else:
                distinct.append(matter)
    return distinct


def validate_proposals(subject, text, proposals, *, legacy_compat=True):
    """候选生成不参与验证；仅检验定位、主体角色、动作和逐字段证据。"""
    from backend.app.matter_validation import (
        VALIDATION_VERSION,
        action_supported,
        actor_supported,
        normalize_amount,
        scoped_status,
        validate_field,
    )

    body = clean_body(text)
    names = names_in_document(subject, body)
    accepted, rejected = [], []
    raw_matters = proposals.get("matters", []) if isinstance(proposals, dict) else []
    if not isinstance(raw_matters, list):
        return [], [{"reason": "invalid_matter_schema"}]
    for index, raw in enumerate(raw_matters[:24]):
        # 字段格式错误仅拒绝该字段，不能吞掉有依据的整项。
        if not isinstance(raw, dict):
            rejected.append({"index": index, "reason": "invalid_matter_schema"})
            continue
        raw_fields = raw.get("fields", {})
        bad_fields, fields = [], {}
        if isinstance(raw_fields, dict):
            for key, item in list(raw_fields.items())[:12]:
                try:
                    fields[key] = ProposedField.model_validate(item)
                except ValueError:
                    bad_fields.append(key)
        try:
            proposal_class = ProposedMatter if legacy_compat else TypedProposedMatter
            proposal = proposal_class.model_validate({**raw, "fields": fields})
        except ValueError:
            rejected.append({"index": index, "reason": "invalid_matter_schema"})
            continue
        quote = proposal.action_quote
        mentions = {name for _, name in subject_mentions(names, subject.legal_name, quote)}
        if quote not in body or proposal.subject not in mentions:
            rejected.append({"index": index, "reason": "unsupported_subject_or_action"})
            continue
        # v1 封存响应没有类型，只能在兼容入口解析类型；v2 类型直接独立校验。
        legacy = classification(quote) if proposal.subtype is None else None
        subtype = proposal.subtype or (legacy[1] if legacy else None)
        category = next((c for c, st, _ in ACTIONS if st == subtype), None)
        if (
            not category
            or not action_supported(quote, subtype)
            or not actor_supported(proposal.subject, quote, subtype)
        ):
            rejected.append({"index": index, "reason": "action_or_actor_not_supported"})
            continue
        expected_role = (
            "fundraiser"
            if subtype == "company_financing"
            else "buyer"
            if subtype == "outbound_investment"
            else "target"
            if subtype == "acquisition_target"
            else "issuer"
            if subtype.startswith("ipo_")
            else "actor"
        )
        if proposal.subject_role and proposal.subject_role not in {
            expected_role,
            "investor" if subtype in {"outbound_investment", "fund_commitment"} else expected_role,
        }:
            rejected.append({"index": index, "reason": "subject_role_mismatch"})
            continue
        local_names = set(names) - {subject.legal_name, *subject.aliases}
        scope = (
            "legal_entity"
            if proposal.subject
            in {subject.legal_name, *local_names, *getattr(subject, "legal_aliases", ())}
            else f"brand:{compact(proposal.subject)}"
        )
        if (proposal.category and proposal.category != category) or (
            proposal.scope and proposal.scope != scope
        ):
            rejected.append({"index": index, "reason": "category_or_scope_mismatch"})
            continue
        context = next((p for p in body.splitlines() if quote in p), quote)
        status = scoped_status(quote, subtype)
        # 紧邻明确指回“该消息”的否认保留；其他事项的否认不传播。
        suffix = context[context.find(quote) + len(quote) :]
        if re.match(
            r"[。；;\s]*" + re.escape(proposal.subject) + r"否认(?:该消息|上述消息)", suffix
        ):
            status = "denied"
            quote = context[context.find(quote) :].split("\n", 1)[0][:1500]
        targeted_denial = re.match(
            r"[，,；;。\s]*(?:但|然而)[，,]?(?:该公司|公司|"
            + re.escape(proposal.subject)
            + r")?(?:已)?(?:否认|澄清)([^。；;]*)",
            suffix,
        )
        if targeted_denial and (
            (
                subtype.startswith("ipo_")
                and re.search(r"上市|聆讯|IPO|该消息", targeted_denial.group(1))
            )
            or (
                subtype == "company_financing"
                and re.search(r"融资|该消息", targeted_denial.group(1))
            )
        ):
            status = "denied"
            quote = context[
                context.find(proposal.action_quote) : context.find(proposal.action_quote)
                + len(proposal.action_quote)
                + targeted_denial.end()
            ]
        issues = [f"rejected_field:{k}" for k in bad_fields]
        if proposal.status and proposal.status != status:
            issues.append("status_corrected_from_context")
        if "人工智能生成" in body or "AI生成" in body:
            issues.append("generated_source_not_fact")
        if status == "denied":
            issues.append("denial_or_correction_requires_review")
        verified = {}
        for key, item in proposal.fields.items():
            ok, reason = validate_field(item.role, item.value, item.quote, quote, subtype)
            normalized = normalize_amount(item.value)
            if item.currency and normalized.get("currency") != item.currency:
                ok, reason = False, "currency_not_supported"
            if not ok:
                issues.append(f"rejected_field:{key}")
                rejected.append({"index": index, "field": key, "reason": reason})
                continue
            field_key = "date" if item.role in {"occurred", "disclosed", "planned"} else item.role
            value = {
                "value": item.value,
                "quote": item.quote,
                "role": item.role,
                "quote_start": body.find(quote) + quote.find(item.quote),
                "quote_end": body.find(quote) + quote.find(item.quote) + len(item.quote),
                "validation_version": VALIDATION_VERSION,
                "coordinate_space": "stored_excerpt",
                "normalized": normalized,
            }
            if item.role == "occurred" and status == "reported":
                match = DATE.fullmatch(item.value)
                if match and match.group(1):
                    try:
                        value["iso"] = date(*map(int, match.groups())).isoformat()
                    except ValueError:
                        pass
            verified[field_key] = value
        if not verified.get("date", {}).get("iso"):
            issues.append("occurrence_date_unknown")
        accepted.append(
            Matter(category, subtype, proposal.subject, scope, quote, status, verified, issues)
        )
    return accepted, rejected


def compatible(left, right):
    from backend.app.matter_comparison import compare_matters

    return compare_matters(left, right).same_matter

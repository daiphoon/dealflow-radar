"""从证据形成有类型的事项候选；字段校验不授予事实发布资格。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field

VERSION = "matter-v1"
PROMPT_VERSION = "research-matter-extraction-v1"
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
    "registered_capital": "注册资本变化",
    "ipo_guidance_agreement": "辅导协议签署",
    "company_financing": "获得融资",
    "ipo_guidance_completed": "辅导完成",
    "ipo_guidance": "辅导备案",
    "ipo_hearing": "上市聆讯",
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
}

AMOUNT = re.compile(
    r"(?:约|近|超|超过|不超过|不多于|至少|至多|不足)?\s*(?:\d[\d,.]*|[一二两三四五六七八九十百千数几]+)\s*(?:亿|万)?\s*(?:人民币|港元|美元|元|股)"
)
DATE = re.compile(r"(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})日?")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


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
    role: str = Field(max_length=40)


class ProposedMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(max_length=200)
    action_quote: str = Field(min_length=1, max_length=1500)
    fields: dict[str, ProposedField] = Field(default_factory=dict, max_length=12)


class ProposedMatters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matters: list[ProposedMatter] = Field(max_length=24)


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
    text = text[:12000]
    # 仅删有明确界限的站内生成解读，普通正文和日期保留。
    text = re.sub(r"AI投资人解读.*?(?=投资界（|投资界\(|文章来源：)", "", text, flags=re.S)
    for marker in ("相关阅读", "相关推荐", "相关文章", "精彩推荐", "你可能也喜欢"):
        text = text.split(marker, 1)[0]
    return text.strip()


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
        for match in re.finditer(r"由([^。；;]{1,100}?)(?:联合领投|领投|跟投|参与投资)", action):
            investors.extend(
                re.sub(r"^(?:老股东|新股东|现有股东)\s*", "", v.strip())
                for v in re.split(r"、|及|和|与", match.group(1))
                if v.strip()
            )
        if investors:
            fields["investors"] = {
                "value": "、".join(dict.fromkeys(investors)),
                "quote": action,
                "role": "investors",
            }
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
    for block in body.splitlines():
        last_name = None
        for paragraph in re.split(r"[。；;]+", block):
            paragraph = paragraph.strip()
            valid_mentions = list(subject_mentions(names, subject.legal_name, paragraph))
            name = valid_mentions[0][1] if valid_mentions else None
            # 只允许明确代词延续上一句主体，不能把整页其他公司的动作归入目标。
            pronoun = re.match(
                r"(?:(?:通知书|公告|报告)显示[，,]?)?(?:该公司|公司|其|本轮)", paragraph
            )
            if name is None and pronoun and last_name:
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
            status = (
                "denied"
                if re.search(r"否认|不属实|澄清|更正|尚未|未完成", paragraph)
                else "planned"
                if re.search(r"拟|计划|预计|将于", paragraph)
                else "committed"
                if subtype == "fund_commitment"
                else "reported"
            )
            if subtype == "ipo_listing_plan":
                status = "planned"
            if subtype.startswith("ipo_") and re.search(r"否认|不属实|澄清", block):
                status = "denied"
            if re.search(r"若|如果|如发生|之日起自动", paragraph):
                status = "conditional"
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


def validate_proposals(subject, text, proposals):
    """按事项和字段独立校验；无依据字段剔除，有依据事项不整篇吞掉。"""
    body = clean_body(text)
    names = names_in_document(subject, body)
    accepted, rejected = [], []
    for index, raw in enumerate(proposals.get("matters", [])[:24]):
        try:
            proposal = ProposedMatter.model_validate(raw)
        except ValueError:
            rejected.append({"index": index, "reason": "invalid_matter_schema"})
            continue
        if proposal.action_quote not in body or proposal.subject not in names:
            rejected.append({"index": index, "reason": "unsupported_subject_or_action"})
            continue
        local_subject = SimpleNamespace(
            legal_name=subject.legal_name,
            aliases=names,
            legal_aliases=getattr(subject, "legal_aliases", ()),
        )
        candidates = extract_matters(local_subject, proposal.action_quote)
        for candidate in candidates:
            if candidate.subject in set(names) - {subject.legal_name, *subject.aliases}:
                candidate.scope = "legal_entity"
        if not candidates:
            rejected.append({"index": index, "reason": "action_not_supported_by_contract"})
            continue
        for candidate in candidates:
            # 取原段落保留否认等相邻语境，不能只引用肯定半句。
            context = next(
                (p for p in body.splitlines() if proposal.action_quote in p), proposal.action_quote
            )
            if re.search(r"否认|不属实|澄清", context):
                candidate.status = "denied"
                candidate.issues.append("denial_or_correction_requires_review")
            if "人工智能生成" in body or "AI生成" in body:
                candidate.issues.append("generated_source_not_fact")
            if candidate.status == "denied" and "date" in candidate.fields:
                candidate.fields["date"].pop("iso", None)
                candidate.fields["date"]["role"] = "disclosed"
            for key, value in proposal.fields.items():
                expected = candidate.fields.get(value.role)
                if value.role in {"occurred", "disclosed", "planned"}:
                    expected = candidate.fields.get("date")
                if not (
                    value.quote in body
                    and value.value in value.quote
                    and expected
                    and compact(value.value) == compact(expected["value"])
                    and value.role == expected["role"]
                ):
                    candidate.issues.append(f"rejected_field:{key}")
            accepted.append(candidate)
    return accepted, rejected


def compatible(left, right):
    """只有明确同主体/阶段及辨别字段时才关联；相同类别不足以归并。"""
    if (left.category, left.subtype, left.scope) != (right.category, right.subtype, right.scope):
        return False
    if left.status != right.status and right.status != "denied":
        return False
    a, b = left.fields, right.fields
    if (
        a.get("date", {}).get("iso")
        and b.get("date", {}).get("iso")
        and a["date"]["iso"] != b["date"]["iso"]
    ):
        return False
    if left.subtype.startswith("ipo_"):

        def markets(t):
            return (
                "港"
                if re.search(r"港交所|香港|赴港|港股", t)
                else "A"
                if re.search(r"A股|创业板|科创板|证监局", t)
                else None
            )

        return markets(left.action) == markets(right.action) and markets(left.action) is not None
    if left.subtype == "company_financing":
        if not (a.get("round") and b.get("round")):
            return bool(
                a.get("financing")
                and b.get("financing")
                and compact(a["financing"]["value"]) == compact(b["financing"]["value"])
                and set(a.get("investors", {}).get("value", "").split("、"))
                & set(b.get("investors", {}).get("value", "").split("、")) - {""}
            )
        return bool(
            a.get("round")
            and b.get("round")
            and compact(a["round"]["value"]) == compact(b["round"]["value"])
            and a.get("financing")
            and b.get("financing")
        )
    return compact(left.action) == compact(right.action)

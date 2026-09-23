"""字段语义支持独立于候选发现；supported 不决定事实发布或来源可靠性。"""

import re
from decimal import Decimal, InvalidOperation

VALIDATION_VERSION = "matter-field-validation-v1"


def normalize_amount(value):
    text = re.sub(r"\s+|,", "", value)
    digits = {c: str(i) for i, c in enumerate("零一二三四五六七八九")}
    text = re.sub(r"[一二三四五六七八九](?=[亿万元])", lambda m: digits[m.group()], text)
    text = text.replace("两亿", "2亿").replace("两万", "2万")
    m = re.fullmatch(
        r"(约|近|超|超过|不超过|不多于|至少|至多|不足)?(\d+(?:\.\d+)?)(亿|万)?(人民币|港元|美元|元|股)",
        text,
    )
    if not m:
        return {"raw": value, "precision": "unparsed"}
    qualifier, number, scale, unit = m.groups()
    try:
        amount = Decimal(number) * {None: 1, "万": 10000, "亿": 100000000}[scale]
    except InvalidOperation:
        return {"raw": value, "precision": "unparsed"}
    return {
        "amount": format(amount, "f"),
        "currency": {"元": "CNY", "人民币": "CNY", "港元": "HKD", "美元": "USD", "股": "shares"}[
            unit
        ],
        "precision": qualifier or "exact",
        "raw": value,
    }


def equivalent_value(left, right):
    a, b = normalize_amount(left), normalize_amount(right)
    if a["precision"] != "unparsed" and b["precision"] != "unparsed":
        return Decimal(a["amount"]) == Decimal(b["amount"]) and all(
            a[k] == b[k] for k in ("currency", "precision")
        )
    return re.sub(r"\s+", "", left) == re.sub(r"\s+", "", right)


def actor_supported(subject, action, subtype):
    pos = action.find(subject)
    if pos < 0:
        return False
    tail = action[pos + len(subject) :]
    if re.match(r"(?:的|旗下)?(?:子公司|母公司|集团)", tail):
        return False
    if subtype == "company_financing":
        local = re.split(r"[，,。；;]", tail, maxsplit=1)[0]
        if re.search(r"领投|跟投|参投|参与投资|投资的|参股的", local):
            return False
        match = re.search(r"完成|获得|获|筹得|募得|募集|融资", action)
        if not match or pos > match.start():
            return False
    if subtype == "outbound_investment":
        match = re.search(r"收购|购买|投资", action)
        if match and (pos > match.start() or re.search(r"被|获", action[pos : match.start()])):
            return False
    if subtype == "acquisition_target":
        # 主体须直接处于被动宾语位置；“乙被甲收购”中的甲是买方。
        target = (
            re.escape(subject)
            + r"(?:于(?:\d{4}年)?\d{1,2}月\d{1,2}日)?"
            + r"(?:已|将|正式|整体|全部|近日|宣布|的|股权|股份|控制权|\d+(?:\.\d+)?%)*"
            + r"被[^，,。；;]{0,35}(?:收购|并购)"
        )
        return bool(re.search(target, action))
    return True


def validate_field(role, value, quote, action, subtype):
    """引用先逐字定位，再检查金额邻近的业务角色，不以数字共现放行。"""
    if not quote or quote not in action or value not in quote:
        return False, "field_locator_invalid"
    pos = quote.find(value)
    before = quote[max(0, pos - 16) : pos]
    after = quote[pos + len(value) : pos + len(value) + 14]
    if role == "financing":
        ok = (
            subtype == "company_financing"
            and not re.search(r"估值|注册资本|累计|总计", before)
            and bool(re.search(r"融资|募集|筹得|募得", before + after))
        )
    elif role == "valuation":
        ok = bool(re.search(r"估值", before))
    elif role == "investment":
        ok = (
            subtype == "outbound_investment"
            and not re.search(r"估值|融资", before)
            and bool(re.search(r"收购|投资|购买|对价", before + after))
        )
    elif role == "commitment":
        ok = "认缴" in before + after
    elif role == "share_quantity":
        ok = value.endswith("股")
    elif role == "round":
        ok = bool(
            re.fullmatch(r"(?:Pre[- ]?)?[A-F](?:\+{1,2})?轮|天使轮|种子轮|战略融资", value, re.I)
        )
    elif role == "investors":
        ok = bool(re.search(re.escape(value) + r".{0,8}(?:领投|跟投|参与投资|参投)", quote))
    elif role in {"registered_capital_before", "registered_capital_after"}:
        capital_start = quote.rfind("注册资本", 0, pos)
        capital_prefix = quote[capital_start:pos] if capital_start >= 0 else ""
        if role == "registered_capital_before":
            ok = bool(capital_prefix and re.search(r"(?:由|从)\s*$", capital_prefix))
        else:
            ok = bool(
                capital_prefix
                and (
                    re.search(
                        r"(?:变更为|变更至|调整为|增加至|减少至|提高至|降低至|增至|减至)\s*$",
                        capital_prefix,
                    )
                    or (
                        re.search(r"由|从", capital_prefix)
                        and re.search(r"(?:至|到|为)\s*$", capital_prefix)
                    )
                )
            )
    elif role == "proposed_proceeds":
        ok = bool(re.search(r"拟|计划", quote)) and "募" in quote
    elif role in {"transaction_id", "project_id"}:
        ok = bool(re.search(r"交易编号|交易标识|项目编号|项目代码", before))
    elif role in {"occurred", "disclosed", "planned"}:
        ok = bool(re.search(r"\d{4}年|\d{1,2}月|\d{4}-\d{2}", value))
        if role == "occurred":
            action_match = re.search(
                r"完成|获得|通过|提交|签署|签订|收购|中标|发布|投产|开工|筹得|募得", quote
            )
            ok = ok and bool(action_match) and pos < action_match.start()
            if action_match:
                ok = ok and not re.search(
                    r"披露|报道|公告|消息|计划|预计", quote[pos : action_match.start()]
                )
        if role == "planned":
            ok = ok and bool(re.search(r"拟|将|计划", quote))
        if role == "disclosed":
            ok = ok and bool(re.search(r"披露|报道|公告|消息", quote))
    else:
        ok = False
    return ok, "typed_field_supported" if ok else "field_role_not_supported"


def assess_matter_fact(fact, evidence, context):
    """返回 typed 字段判断；未知契约不退化为数字/标签共现支持。"""
    from backend.app.research_matters import FIELD_LABELS, LABELS, STATUS_LABELS

    payload = evidence.display_detail_payload or {}
    if payload.get("schema_version") != "matter-v1":
        return None
    matter = payload.get("matter_observation", {})
    action = matter.get("excerpt", "")
    checks = {
        "typed_validation_version": VALIDATION_VERSION,
        "source_date_status": "known" if context.source_date else "unknown",
    }
    if action != context.excerpt or not actor_supported(
        matter.get("subject", ""), action, matter.get("subtype")
    ):
        return "unsupported", checks, ["matter_subject_or_locator_not_supported"]
    if fact.name == "事项阶段":
        ok = fact.value == LABELS.get(matter.get("subtype"))
    elif fact.name == "动作状态":
        ok = fact.value == STATUS_LABELS.get(matter.get("status"))
    else:
        ok = False
        for key, item in matter.get("fields", {}).items():
            if FIELD_LABELS.get(key, key) != fact.name or not equivalent_value(
                item.get("value", ""), fact.value
            ):
                continue
            ok, reason = validate_field(
                item.get("role"),
                item.get("value", ""),
                item.get("quote", ""),
                action,
                matter.get("subtype"),
            )
            checks.update(
                {
                    "field_role": item.get("role"),
                    "raw_value": item.get("value"),
                    "normalized_value": normalize_amount(item.get("value", "")),
                    "conversion_version": "amount-decimal-v1",
                    "quote_start": action.find(item.get("quote", "")),
                }
            )
            break
    # 分类仍需动作依据，不能仅相信写入的规范标签。
    if fact.name in {"事项阶段", "动作状态"}:
        ok = ok and action_supported(action, matter.get("subtype"))
        if fact.name == "动作状态":
            ok = ok and matter.get("status") == scoped_status(action, matter.get("subtype"))
    # 同一事项两方有效数值互相冲突，不能让后到的一方单独成为当前事实。
    for key, item in matter.get("fields", {}).items():
        if FIELD_LABELS.get(key, key) == fact.name and not equivalent_value(
            item.get("value", ""), fact.value
        ):
            valid, _ = validate_field(
                item.get("role"),
                item.get("value", ""),
                item.get("quote", ""),
                action,
                matter.get("subtype"),
            )
            if valid:
                return "conflicting", checks, ["counter_evidence_field_conflict"]
    if matter.get("status") == "denied" and (ok or fact.name == "动作状态"):
        # 否认中的金额/轮次是在复述被否认主张，不能成为该主张的正向支持。
        return "conflicting", checks, ["counter_evidence_denial"]
    return (
        ("supported" if ok else "unsupported"),
        checks,
        ["typed_field_supported" if ok else "field_role_not_supported"],
    )


def action_supported(action, subtype):
    """证据动作契约：独立于规则候选生成器，不要求命中其抽取正则。"""
    terms = {
        "company_financing": r"(?:融资|股权投资|战略投资|募集资金|筹得|募得)",
        "outbound_investment": r"收购|购买|对外投资|战略投资",
        "fund_commitment": r"认缴",
        "acquisition_target": r"被.{0,35}(?:收购|并购)",
        "registered_capital": r"注册资本",
        "ipo_guidance_agreement": r"辅导协议",
        "ipo_guidance_completed": r"(?:完成|通过).{0,12}辅导(?!备案)",
        "ipo_guidance": r"辅导(?:备案|登记)|启动.{0,5}(?:IPO|上市)辅导",
        "ipo_application": r"(?:提交|递交|递表).{0,25}(?:上市申请|招股书)|向.{0,12}递表",
        "ipo_accepted": r"(?:申请|IPO).{0,15}(?:受理)|受理.{0,15}申请",
        "ipo_hearing": r"(?:通过|获).{0,12}聆讯",
        "ipo_inquiry": r"(?:进入|收到|回复).{0,12}问询",
        "ipo_filing": r"境外.{0,50}备案|全流通.{0,50}备案|备案通知书",
        "ipo_offering": r"招股|(?:拟|计划|IPO).{0,25}发行",
        "ipo_listed": r"(?:正式|成功|已).{0,15}上市|挂牌上市",
        "ipo_listing_plan": r"(?:拟|计划|将|启动|否认).{0,30}(?:上市|IPO)",
        "ipo_withdrawn": r"(?:撤回|终止).{0,15}(?:上市|IPO)",
        "contract_award": r"中标|(?:合同|合作协议)",
        "product_milestone": r"发布|认证|注册证|获批",
        "operating_disclosure": r"营收|营业收入|净利润|出货量|停产|欠薪",
        "management_change": r"(?:董事长|总经理|高管|创始人).{0,20}(?:离职|辞任|变更)|任命",
        "legal_development": r"起诉|处罚|立案|执行|破产|补充协议|(?:义务|条款).{0,20}终止",
        "capacity_development": r"(?:工厂|生产线|项目).{0,20}(?:投产|开工|停工|延期)|新建",
    }
    return bool(re.search(terms.get(subtype, r"(?!)"), action, re.I))


def scoped_status(action, subtype):
    # 并列的不同事项分别取局部动作语境；否认不跨事项传播。
    parts = re.split(r"[，,](?:同时|并且|另(?:外)?|但)", action)
    local = next((part for part in reversed(parts) if action_supported(part, subtype)), action)
    if re.search(r"否认|不属实|澄清|更正|尚未|未完成", local):
        return "denied"
    if re.search(r"若|如果|如发生|之日起自动", local):
        return "conditional"
    if re.search(r"拟|计划|预计|将于", local) or subtype == "ipo_listing_plan":
        return "planned"
    if subtype == "fund_commitment":
        return "committed"
    return "reported"

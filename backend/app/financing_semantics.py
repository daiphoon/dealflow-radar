"""融资动作、否定对象与发生阶段分开；陈述立场不授予事实确认。"""

import re

VERSION = "financing-statement-v2"

FINANCING = r"融资|增资款|融资款|本轮|该轮"
FUNDS_USE = r"(?:资金|款项)[^，,。；;]{0,12}(?:用途|使用|用于|投入)"
UNFINISHED = r"(?:尚未|还未|并未|没有|未能|未)(?:正式|实际)?(?:完成|交割)$"
NEGATION = r"(?:尚未|还未|并未|未能|没有|并没有|未)(?:正式|实际|全部|足额)?$"
MODAL = r"(?:预计|预期|有望|拟|计划|将|若|如果|条件[^，,。；;]{0,8}后)[^，,。；;]{0,30}$"
PREDICATES = (
    ("registration_completed", r"工商(?:变更|登记)[^，,。；;]{0,8}完成"),
    (
        "funds_received",
        r"(?:收到|到账)[^，,。；;]{0,25}(?:增资款|融资款|投资款|款项)"
        r"|(?:增资款|融资款|投资款|款项)[^，,。；;]{0,15}(?:收到|到账)",
    ),
    ("agreement_signed", r"(?:签署|签订)[^，,。；;]{0,25}(?:投资协议|融资协议)"),
    (
        "completion_reported",
        r"完成[^，,。；;]{0,35}(?:融资|轮)"
        r"|融资[^，,。；;]{0,15}(?:完成|已交割)"
        r"|已交割[^，,。；;]{0,10}融资|" + UNFINISHED,
    ),
)


def clauses(action):
    # 并列业务先分开；情态和否定只能修饰本分句的融资谓词。
    return [p for p in re.split(r"[，,。；;]|但是|然而|不过|但", action) if p]


def financing_clauses(action):
    previous_financing = False
    for part in clauses(action):
        explicit = bool(re.search(FINANCING, part))
        continuation = previous_financing and bool(
            (
                re.search(r"交割|工商(?:变更|登记)|投资协议|(?:该|上述)(?:消息|报道|说法)", part)
                or re.fullmatch(r"(?:仍|目前)?" + UNFINISHED, part.strip())
            )
            and not re.search(r"采购|产品|工厂|投产|停产|" + FUNDS_USE, part)
        )
        if explicit or continuation:
            yield part
        # 资金用途属于另一主张，“该消息”不能跨过它回指融资完成。
        previous_financing = (explicit or continuation) and not re.search(FUNDS_USE, part)


def predicate_status(part, match):
    verbs = list(re.finditer(r"完成|交割|收到|到账|签署|签订", match.group()))
    prefixes = [part[: match.start() + verb.start()] for verb in verbs]
    if any(re.search(r"是否|未(?:披露|说明|明确)[^，,。；;]{0,12}$", p) for p in prefixes):
        return "unknown"
    if any(re.search(NEGATION, p) or re.search(MODAL, p) for p in prefixes):
        return "planned"
    return "reported"


def predicate_claims(part):
    for phase, pattern in PREDICATES:
        for match in re.finditer(pattern, part):
            # “完成资金使用/投产”不是融资完成，哪怕分句提及融资资金。
            if phase == "completion_reported" and re.search(
                r"完成(?:了)?(?:资金使用|融资资金使用|工厂投产)",
                match.group() + part[match.end() :],
            ):
                continue
            yield phase, predicate_status(part, match)


def statement(action):
    local = list(financing_clauses(action))
    for part in local:
        denied = re.search(r"否认", part)
        if re.search(FUNDS_USE, part[denied.end() :] if denied else part):
            continue
        if (
            denied
            and not re.search(NEGATION, part[: denied.start()])
            and re.search(r"融资|(?:该|上述)(?:消息|报道|说法)", part[denied.end() :])
        ) or re.search(r"(?:报道|消息|说法)[^，,。；;]{0,12}(?:不实|不属实|错误)", part):
            return "denied", "completion_claim_disputed"
    claims = [claim for part in local for claim in predicate_claims(part)]
    if ("completion_reported", "planned") in claims:
        return "planned", "fundraising_in_progress"
    # 未到账不否认已明示的完成披露，也不能自己成为到账事实。
    if ("completion_reported", "reported") in claims:
        for phase in ("registration_completed", "funds_received"):
            if (phase, "reported") in claims:
                return "reported", phase
        return "reported", "completion_reported"
    if any(status == "planned" for _, status in claims) or any(
        re.search(r"拟(?:募|融)|计划(?:募|融)|启动|正在募集|正与", part) for part in local
    ):
        return "planned", "fundraising_in_progress"
    for phase, status in claims:
        if status == "reported":
            return "reported", phase
    return "reported", "completion_unknown"


def realized_amount_supported(action, value):
    """预计/目标/未收到的金额不能成为已取得融资额；否认引用仍由反证入口处理。"""
    if statement(action)[0] in {"planned", "conditional"}:
        return False
    for part in clauses(action):
        if value not in part:
            continue
        prefix = part[: part.find(value)]
        if re.search(r"目标|拟募|预计募集|计划募集", prefix):
            return False
        if any(status == "planned" for _, status in predicate_claims(part)):
            return False
    return True


def claim_reference(action):
    """仅定位明确被否认的完成主张；引用字段不是融资事实。"""
    from backend.app.matter_dates import DATE_PATTERN, normalize_date
    from backend.app.research_matters import infer_fields

    if statement(action)[0] != "denied":
        return {}
    values = infer_fields(action, "company_financing")
    reference = {
        k: v
        for k, v in values.items()
        if k in {"round", "financing", "transaction_id", "investors"}
    }
    dates = [
        m
        for m in DATE_PATTERN.finditer(action)
        if re.search(r"完成[^。；;]{0,45}(?:融资|轮)", action[m.end() :].split("。", 1)[0])
    ]
    if len(dates) == 1:
        reference["occurred"] = {
            "value": dates[0].group(),
            "quote": action,
            "role": "denied_claim_reference",
            **normalize_date(dates[0].group()),
        }
    return reference

"""融资动作、否定对象与发生阶段分开；陈述立场不授予事实确认。"""

import re

VERSION = "financing-statement-v1"


def statement(action):
    parts = re.split(r"[，,。；;]", action)
    local = "；".join(p for p in parts if re.search(r"融资|增资款|本轮|该轮", p)) or action
    # 紧邻分句只继承融资交割谓词，不继承资金使用或其他业务动作。
    if re.search(r"融资[^。；;]*[，,](?:尚未|还未|未能)(?:正式)?(?:交割|完成)", action):
        local += "；融资尚未交割"
    denial = bool(
        re.search(r"否认[^。；;]*(?:融资|该消息|上述消息)", action)
        or re.search(r"(?:报道|消息|说法)[^。；;]{0,12}(?:不实|不属实|错误)", local)
    )
    if denial:
        return "denied", "completion_claim_disputed"
    unknown = bool(re.search(r"未(?:披露|说明|明确)|是否完成", local))
    # 否定只作用于交割/完成，不传播到使用资金、工厂或其他业务动作。
    unfinished = bool(re.search(r"(?:尚未|还未|并未|未能|未)(?:正式)?(?:交割|完成)", local))
    if unfinished or re.search(
        r"拟(?:募|融|完成)|计划(?:募|融|完成)|将(?:于[^，,。；;]{0,15})?(?:完成|募集|融资)|启动|正在募集|正与",
        local,
    ):
        return "planned", "fundraising_in_progress"
    if re.search(r"工商(?:变更|登记)[^。；;]{0,8}完成", local):
        return "reported", "registration_completed"
    if re.search(
        r"(?:收到|收到全部|到账)[^。；;]{0,15}(?:增资款|融资款)|(?:增资款|融资款)[^。；;]{0,8}到账",
        local,
    ):
        return "reported", "funds_received"
    if re.search(r"(?:签署|签订)[^。；;]{0,25}(?:投资协议|融资协议)", local):
        return "reported", "agreement_signed"
    if not unknown and re.search(
        r"完成[^。；;]{0,35}(?:融资|轮)|融资[^。；;]{0,15}(?:完成|已交割)|已交割[^。；;]{0,10}融资",
        local,
    ):
        return "reported", "completion_reported"
    return "reported", "completion_unknown"


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

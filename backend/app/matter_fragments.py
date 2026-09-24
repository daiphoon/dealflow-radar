"""片段坐标只对保留表示有效；非连续窗口不能成为单条连续引文。"""


def continuous_quote(document, quote):
    body = str(document.payload.get("excerpt") or "")
    metadata = document.payload.get("content_extraction", {})
    spans = metadata.get("retained_spans")
    if spans is None:
        # 旧单片段只有存储文本坐标，不推断其全文坐标。
        return bool(quote) and quote in body
    return bool(quote) and any(
        quote in body[s["stored_start"] : s["stored_end"]]
        for s in spans
        if 0 <= s["stored_start"] < s["stored_end"] <= len(body)
    )

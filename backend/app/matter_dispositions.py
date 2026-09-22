"""将研究各阶段处置追加到已有原文元数据，零额外平台或外部调用。"""

from backend.app.evidence_integrity import hash_canonical_object

VERSION = "matter-disposition-v1"


def record(document, stage, outcome, reason, **detail):
    row = {"version": VERSION, "stage": stage, "outcome": outcome, "reason": reason, **detail}
    key = hash_canonical_object(row)
    existing = list(getattr(document, "_matter_dispositions", []))
    if not any(r.get("key") == key for r in existing):
        document._matter_dispositions = [*existing, {**row, "key": key}]
        job = getattr(document, "_matter_job", None)
        if job is not None:
            records = list(job.coverage.get("matter_dispositions", []))
            if not any(
                r.get("key") == key and r.get("document_id") == str(document.id) for r in records
            ):
                job.coverage = {
                    **job.coverage,
                    "matter_dispositions": [
                        *records,
                        {**row, "key": key, "document_id": str(document.id)},
                    ],
                }
    return row


def relevant_windows(text, names, *, max_chars=4000):
    """按主体定位挑选有上下文的连续窗口，保留源文偏移，不默截前缀。"""
    import re

    spans = []
    for match in re.finditer("|".join(re.escape(n) for n in names if n) or r"(?!)", text):
        start = max(0, match.start() - 180)
        end = min(len(text), match.end() + 900)
        # 优先按段落边界，不截断局部否认、计划或条件句。
        line_start = text.rfind("\n", start, match.start())
        if line_start >= 0:
            start = line_start + 1
        line_end = text.find("\n", match.end(), end)
        if line_end >= 0:
            end = line_end
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    if not spans:
        spans = [(0, min(len(text), max_chars))]
    selected = []
    used = 0
    for start, end in spans:
        end = min(end, start + max_chars - used)
        if end <= start:
            break
        selected.append({"start": start, "end": end, "text": text[start:end]})
        used += end - start
    return selected, used < len(text)

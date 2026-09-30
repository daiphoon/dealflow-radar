"""文内主体及同一披露依据；只生成派生视图，不修改公司别名或原始观测。"""

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from backend.app.research_subject import normalize

IDENTITY_VERSION = "matter-identity-context-v1"
DECLARATION = r"[（(](?:以下简称|简称|下称)[：:、\s]*[“\"「]?([^”\"」）)]{2,30})[”\"」]?[）)]"


def _name_pattern(name):
    return r"\s*".join(re.escape(c) for c in name if not c.isspace())


def legal_declarations(subject, body):
    declarations = {}
    for name in (subject.legal_name, *getattr(subject, "legal_aliases", ())):
        for match in re.finditer(_name_pattern(name) + DECLARATION, body):
            alias = match.group(1).strip()
            declarations.setdefault(alias, []).append(
                {"quote": match.group(), "start": match.start(), "end": match.end()}
            )
    return declarations


@dataclass(frozen=True)
class MatterContext:
    company_key: str
    document_id: str
    excerpt: str


def context_for(subject, body, *, document_id=None):
    return MatterContext(
        str(getattr(subject, "id", subject.legal_name)),
        str(document_id or "excerpt:" + hashlib.sha256(body.encode()).hexdigest()),
        body,
    )


def _claim_text(subject, text, body, matter=None):
    names = {subject.legal_name, *subject.aliases, *legal_declarations(subject, body)}
    text = re.sub(DECLARATION, "", text)
    text = normalize(text)
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(normalize(name), "@actor")
    text = re.sub(r"@actor宣布", "@actor", text)
    if matter is not None and matter.subtype == "company_financing" and matter.status == "reported":
        text = re.sub(r"@actor获得(?=[^。；;]{0,25}融资)", "@actor完成", text)
    return re.sub(r"[，,。；;：:（）()“”\"「」]", "", text)


def claim_lines(subject, matter, context):
    if context is None or context.company_key != str(getattr(subject, "id", subject.legal_name)):
        return []
    body = context.excerpt
    if matter.action not in body:
        return []
    lines = [line for line in body.splitlines() if matter.action in line]
    # 标题只能指回同篇唯一的同动作正文，不能把整篇模板/背景当身份锚点。
    short = _claim_text(subject, matter.action.split("，", 1)[0].split(",", 1)[0], body, matter)
    if "@actor" not in short:
        return lines
    tail = short.split("@actor", 1)[1]
    if len(tail) < 8:
        return lines
    from backend.app.matter_validation import actor_supported

    references = [
        line
        for line in body.splitlines()
        if line not in lines
        and tail in _claim_text(subject, line, body, matter)
        and any(
            actor_supported(name, line, matter.subtype)
            for name in (subject.legal_name, *legal_declarations(subject, body))
        )
        and _claim_text(subject, line, body, matter).count(tail) == 1
    ]
    if len(references) == 1:
        lines += references
    return lines


def resolved_identity(subject, matter, context):
    legal_names = {
        normalize(subject.legal_name),
        *map(normalize, getattr(subject, "legal_aliases", ())),
    }
    evidence = []
    scope = matter.scope if context is None else f"brand:{normalize(matter.subject)}"
    if normalize(matter.subject) in legal_names:
        scope = "legal_entity"
        evidence.append({"basis": "formal_legal_name", "name": matter.subject})
    if context is not None:
        declarations = legal_declarations(subject, context.excerpt)
        for name, locations in declarations.items():
            if normalize(name) == normalize(matter.subject):
                scope = "legal_entity"
                evidence += [{"basis": "document_legal_declaration", **r} for r in locations]
        if scope != "legal_entity":
            for line in claim_lines(subject, matter, context):
                from backend.app.matter_validation import actor_supported

                if actor_supported(subject.legal_name, line, matter.subtype):
                    scope = "legal_entity"
                    start = context.excerpt.find(line)
                    evidence.append(
                        {
                            "basis": "same_document_legal_claim",
                            "quote": line,
                            "start": start,
                            "end": start + len(line),
                        }
                    )
        # 验证原文定位及公司绑定；外部传入其他公司 context 不授予身份。
        if (
            context.company_key != str(getattr(subject, "id", subject.legal_name))
            or matter.action not in context.excerpt
        ):
            return {"scope": matter.scope, "evidence": [], "version": IDENTITY_VERSION}
    return {
        "scope": scope,
        "evidence": evidence,
        "company_key": str(getattr(subject, "id", subject.legal_name)),
        "document_id": context.document_id if context else None,
        "excerpt_sha256": hashlib.sha256(context.excerpt.encode()).hexdigest() if context else None,
        "version": IDENTITY_VERSION,
    }


def shared_claim(subject, left, right, left_context, right_context):
    """只比较候选指向的连续正文，不比较整篇共有页脚或任意文本相似度。"""
    if subject is None or left_context is None or right_context is None:
        return None
    left_lines = claim_lines(subject, left, left_context)
    right_lines = claim_lines(subject, right, right_context)
    for a in left_lines:
        for b in right_lines:
            av, bv = (
                _claim_text(subject, text, ctx.excerpt, m)
                for text, ctx, m in ((a, left_context, left), (b, right_context, right))
            )
            same_document = left_context.document_id == right_context.document_id
            if same_document and (av in bv or bv in av):
                return "same_document_continuous_claim"
            overlap = SequenceMatcher(None, av, bv, autojunk=False).find_longest_match()
            common = av[overlap.a : overlap.a + overlap.size]
            if overlap.size >= 48 and "@actor" in common:
                return "shared_continuous_announcement_claim"
    return None

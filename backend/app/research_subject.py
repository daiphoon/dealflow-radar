"""业务研究使用的共享身份投影；不读取初始业务答案或私有别名。"""

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Company, CompanyAlias

QUERY_STRATEGY_VERSION = "verified-business-names-v1"
SHORT_QUERY_STRATEGY_VERSION = "verified-short-business-topics-v2"


def normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


@dataclass(frozen=True)
class ResearchSubject:
    company: Company
    aliases: tuple[str, ...]
    legal_aliases: tuple[str, ...] = ()

    def __getattr__(self, name):
        return getattr(self.company, name)


def load_subject(session: Session, company: Company) -> ResearchSubject:
    # Worker 的管理员上下文可见全部共享关系，冲突不能由当前页面过滤掩盖。
    rows = list(
        session.scalars(
            select(CompanyAlias).where(
                CompanyAlias.visibility_scope == "platform_shared",
                CompanyAlias.owner_user_id.is_(None),
                CompanyAlias.owner_tenant_id.is_(None),
                CompanyAlias.verification_status == "verified",
            )
        )
    )
    companies = list(
        session.scalars(
            select(Company).where(
                Company.tenant_id.is_(None),
                Company.visibility_scope == "public",
            )
        )
    )
    aliases = []
    for row in rows:
        if row.company_id != company.id or row.alias_type not in {
            "brand",
            "short_name",
            "former_name",
            "trade_name",
        }:
            continue
        key = normalize(row.alias)
        if not 2 <= len(key) <= 120 or not re.fullmatch(r"[\w\u4e00-\u9fff（）()·+ -]+", row.alias):
            continue
        if any(other.company_id != company.id and normalize(other.alias) == key for other in rows):
            continue
        if any(
            other.id != company.id and normalize(other.legal_name) == key for other in companies
        ):
            continue
        aliases.append(row.alias)
    selected = tuple(sorted(set(aliases), key=normalize)[:3])
    legal_aliases = tuple(
        row.alias
        for row in rows
        if row.company_id == company.id
        and row.alias in selected
        and row.alias_type == "former_name"
    )
    return ResearchSubject(company, selected, legal_aliases)


def names(company) -> tuple[str, ...]:
    return (company.legal_name, *(company.aliases if isinstance(company, ResearchSubject) else ()))


def matched_name(company, value: str) -> str | None:
    haystack = normalize(value)
    for name in names(company):
        key = normalize(name)
        for match in re.finditer(re.escape(key), haystack):
            tail = haystack[match.end() :]
            head = haystack[: match.start()]
            if name != company.legal_name:
                # 相近名称、子公司/投资方叙述不等于品牌本身发生变化。
                if re.match(
                    r"[a-z0-9]|(?:科技|设备|集团|控股)?(?:有限|股份|子公司|的子公司)", tail
                ):
                    continue
                if key[0].isascii() and head and re.search(r"[a-z0-9]$", head):
                    continue
            return name
    if company.credit_code and normalize(company.credit_code) in haystack:
        return company.legal_name
    return None


def query_subject(company) -> str:
    values = names(company)
    if len(values) == 1:
        return f'"{values[0]}"'
    return "(" + " OR ".join(f'"{value}"' for value in values) + ")"


def short_business_query(company, topic: str) -> str:
    aliases = (
        [name for name in company.aliases if name not in company.legal_aliases]
        if isinstance(company, ResearchSubject)
        else []
    )
    name = (
        min(aliases, key=lambda value: (len(normalize(value)), normalize(value)))
        if aliases
        else company.legal_name
    )
    return f'"{name}" {topic}'


class BusinessExcerptSelector:
    """从已限长的干净正文选择连续业务段；位置相对规范化正文。"""

    preserve_identity_fields = False

    def __init__(self, company):
        self.company = company
        self.metadata: dict[str, object] = {}

    def __call__(self, body: str) -> str:
        from backend.app.web_research_service import _event_classification

        spans = list(re.finditer(r"[^\n]+", body))
        chosen = next(
            (
                m
                for m in spans
                if matched_name(self.company, m.group()) and _event_classification("", m.group())
            ),
            None,
        )
        if chosen is None:
            chosen = next((m for m in spans if matched_name(self.company, m.group())), None)
        start = chosen.start() if chosen else 0
        # 单一长段落也可定位后部；保持一个连续片段，避免拼接不同主体。
        if chosen and len(chosen.group()) > 1500:
            for sentence in re.finditer(r"[^。！？!?；;]+[。！？!?；;]?", chosen.group()):
                if matched_name(self.company, sentence.group()) and _event_classification(
                    "", sentence.group()
                ):
                    start += sentence.start()
                    break
        excerpt = body[start : start + 1500]
        self.metadata = {
            "excerpt_start": start,
            "excerpt_end": start + len(excerpt),
            "excerpt_location_basis": "normalized_clean_body",
            "selector_version": QUERY_STRATEGY_VERSION,
        }
        return excerpt

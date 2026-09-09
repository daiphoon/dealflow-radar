"""Bounded identity discovery before a company is admitted to the shared catalog."""

import hashlib
import re
import unicodedata
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from backend.app.models import (
    Company,
    IdentityResearchState,
    PersonalCompanyRequest,
    UsageLedger,
    utc_now,
)
from backend.app.providers import validate_unified_credit_code
from backend.app.source_fetcher import SourceFetchError, TrustedSourceFetcher
from backend.app.web_search import SearchProviderError, SearchRequest

POLICY_VERSION = "public-identity-v1"


def normalized(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def identity_evidence(name: str, code: str, text: str, url: str) -> dict:
    """Require adjacent labelled identity fields, never a search snippet or brand inference."""
    body = normalized(text)
    expected = normalized(name)
    position = body.find(expected)
    window = body[max(0, position - 100) : position + len(expected) + 350] if position >= 0 else ""
    codes = set(re.findall(r"(?<![a-z0-9])[0-9abcdefghjklmnpqrtuwxy]{18}(?![a-z0-9])", window))
    # Chinese characters do not separate an ASCII token in \b expressions.
    labelled = "统一社会信用代码" in window
    pair = labelled and codes == {code.lower()}
    conflict = labelled and bool(codes) and code.lower() not in codes
    host = (urlsplit(url).hostname or "").lower()
    return {
        "url": url,
        "host": host,
        "government": host == "gov.cn" or host.endswith(".gov.cn"),
        "name_match": position >= 0,
        "pair_match": pair,
        "conflict": conflict,
        "excerpt": window if position >= 0 else body[:300],
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "checked_at": utc_now().isoformat(),
    }


def corroborated(evidence: list[dict]) -> bool:
    if any(item.get("conflict") for item in evidence):
        return False
    # A government page must independently corroborate the exact legal name.
    # No pair of arbitrary websites is sufficient to admit a company.
    for pair in evidence:
        if not pair.get("pair_match"):
            continue
        for official in evidence:
            if not (official.get("government") and official.get("name_match")):
                continue
            a, b = pair["host"], official["host"]
            if a != b and not a.endswith("." + b) and not b.endswith("." + a):
                return True
    return False


def _save(session, request, state, progress, status="identity_queued", error=None, **updates):
    state.progress = progress
    flag_modified(state, "progress")
    # No request fields are mutated during HTTP; re-read with a lock before applying results.
    session.refresh(request, with_for_update=True)
    if request.cancel_requested_at or request.status in {"cancel_requested", "cancelled"}:
        status = "cancelled"
        request.cancelled_at = utc_now()
    else:
        for key, value in updates.items():
            setattr(request, key, value)
    request.status = status
    if status in {"cancelled", "in_review", "needs_input", "research_queued"}:
        progress.pop("robots", None)
    request.last_error_code = error
    request.leased_until = None
    request.heartbeat_at = utc_now()
    session.commit()


def _usage(session, user, request, provider, operation, token, calls, metrics):
    record = UsageLedger(
        tenant_id=user.tenant_id,
        company_id=None,
        provider=provider,
        operation=operation,
        external_calls=calls,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=Decimal("0"),
        idempotency_key=hashlib.sha256(f"identity:{request.id}:{token}".encode()).hexdigest(),
        metrics={
            "request_id": str(request.id),
            "policy_version": POLICY_VERSION,
            "stage": "identity",
            **metrics,
        },
    )
    session.add(record)
    return record


def run_identity_step(session, user, providers, policy, fetcher_factory=None):
    # Local import keeps the existing research service as the owner of budgets and URL rules.
    from backend.app import web_research_service as research

    if not research.user_has_role(session, user.id, "platform_admin"):
        raise research.WebResearchAccessError("platform_admin role required")
    now = utc_now()
    request = session.scalar(
        select(PersonalCompanyRequest)
        .where(
            PersonalCompanyRequest.request_type == "inclusion",
            PersonalCompanyRequest.company_id.is_(None),
            PersonalCompanyRequest.status.in_(
                ("pending", "identity_queued", "identity_checking", "cancel_requested")
            ),
            or_(
                PersonalCompanyRequest.leased_until.is_(None),
                PersonalCompanyRequest.leased_until < now,
            ),
        )
        .order_by(PersonalCompanyRequest.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if request is None:
        return None
    state = session.get(IdentityResearchState, request.id)
    if state is None:
        state = IdentityResearchState(request_id=request.id, progress={})
        session.add(state)
    progress = dict(state.progress)
    if request.cancel_requested_at or request.status == "cancel_requested":
        _save(session, request, state, progress, "cancelled")
        return research.WebResearchWorkerResult(status="cancelled", stage="identity")
    name, code = request.requested_name, request.requested_credit_code
    try:
        if not name or not code or len(name.strip()) < 4:
            raise ValueError("exact name and credit code required")
        validate_unified_credit_code(code)
    except ValueError:
        _save(session, request, state, progress, "needs_input", "identity_input_required")
        return research.WebResearchWorkerResult(status="partial", stage="identity")
    fingerprint = hashlib.sha256(f"{normalized(name)}|{code}".encode()).hexdigest()
    if progress and progress.get("input_hash") != fingerprint:
        _save(session, request, state, progress, "needs_input", "identity_input_changed")
        return research.WebResearchWorkerResult(status="partial", stage="identity")
    progress.setdefault("input_hash", fingerprint)
    progress.setdefault("policy_version", POLICY_VERSION)
    # Coalesce equal input before any network reservation. Only platform-collected
    # identity evidence is reused; requester ownership and request IDs never cross APIs.
    older = session.scalar(
        select(PersonalCompanyRequest)
        .where(
            PersonalCompanyRequest.id != request.id,
            PersonalCompanyRequest.requested_credit_code == code,
            PersonalCompanyRequest.requested_name == name,
            PersonalCompanyRequest.created_at < request.created_at,
            PersonalCompanyRequest.status.in_(("pending", "identity_queued", "identity_checking")),
        )
        .order_by(PersonalCompanyRequest.created_at)
        .limit(1)
    )
    if older is not None:
        request.leased_until = now + timedelta(seconds=30)
        session.commit()
        return research.WebResearchWorkerResult(status="partial", stage="identity")
    if not progress.get("search_calls") and not progress.get("reused_evidence"):
        previous = session.scalar(
            select(IdentityResearchState)
            .join(PersonalCompanyRequest)
            .where(
                IdentityResearchState.request_id != request.id,
                PersonalCompanyRequest.requested_credit_code == code,
                PersonalCompanyRequest.requested_name == name,
                IdentityResearchState.created_at
                >= now - timedelta(days=policy.document_cache_ttl_days),
            )
            .order_by(IdentityResearchState.created_at.desc())
            .limit(1)
        )
        if previous and previous.progress.get("input_hash") == fingerprint:
            progress = deepcopy(previous.progress)
            progress.pop("robots", None)
            progress["reused_evidence"] = True
            request.cache_hits += 1
            session.flush()
    # Check known companies without revealing private existence to the requester.
    matches = list(
        session.scalars(
            select(Company).where(or_(Company.credit_code == code, Company.legal_name == name))
        )
    )
    if matches:
        company = matches[0]
        if (
            len(matches) == 1
            and research._public_company(company)
            and company.credit_code == code
            and normalized(company.legal_name) == normalized(name)
        ):
            _save(session, request, state, progress, "research_queued", company_id=company.id)
        else:
            _save(session, request, state, progress, "in_review", "identity_review_required")
        return research.WebResearchWorkerResult(status="partial", stage="identity")
    if progress.get("in_flight"):
        # An interrupted HTTP call has an unknown outcome; never silently spend it again.
        _save(session, request, state, progress, "in_review", "identity_interrupted")
        return research.WebResearchWorkerResult(status="partial", stage="identity")
    evidence = list(progress.get("evidence", []))
    if any(item.get("conflict") for item in evidence):
        _save(session, request, state, progress, "needs_input", "identity_conflict")
        return research.WebResearchWorkerResult(status="partial", stage="identity")
    if corroborated(evidence):
        # This step holds the request lock and makes no network calls.
        company = Company(
            legal_name=name,
            credit_code=code,
            tenant_id=None,
            visibility_scope="public",
            identity_status="verified",
            identity_verification_basis="public_crosscheck",
            last_identity_checked_at=now,
        )
        try:
            with session.begin_nested():
                session.add(company)
                session.flush()
        except IntegrityError:
            _save(session, request, state, progress, "in_review", "identity_review_required")
            return research.WebResearchWorkerResult(status="partial", stage="identity")
        progress["company_id"] = str(company.id)
        progress["decision"] = "public_crosscheck"
        progress["decided_at"] = now.isoformat()
        progress["operator_id"] = str(user.id)
        _save(
            session,
            request,
            state,
            progress,
            "research_queued",
            company_id=company.id,
            resolved_legal_name=name,
            resolved_credit_code=code,
            identity_checked_at=now,
            confirmed_at=now,
        )
        return research.WebResearchWorkerResult(
            status="partial", stage="identity", company_id=company.id
        )
    search_calls = int(progress.get("search_calls", 0))
    candidates = list(progress.get("candidates", []))
    index = int(progress.get("candidate_index", 0))
    fetch_calls = int(progress.get("fetch_calls", 0))
    downloaded = int(progress.get("downloaded_bytes", 0))
    if index >= len(candidates):
        if search_calls >= min(2, policy.max_search_calls_per_job):
            _save(session, request, state, progress, "in_review", "identity_evidence_missing")
            return research.WebResearchWorkerResult(status="partial", stage="identity")
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if (
            research._search_call_count(session, since=day) >= policy.daily_search_call_limit
            or research._search_call_count(session, since=day.replace(day=1))
            >= policy.monthly_search_call_limit
        ):
            _save(session, request, state, progress, "identity_queued", "identity_budget_deferred")
            return research.WebResearchWorkerResult(status="budget_deferred", stage="identity")
        provider_code = policy.primary_provider
        if progress.get("primary_failed"):
            provider_code = policy.fallback_provider
        query = (
            f'"{name}" {code}'
            if search_calls == 0 or progress.get("primary_failed")
            else f'"{name}" site:gov.cn'
        )
        token = f"search:{search_calls}"
        progress.update(in_flight=token, search_calls=search_calls + 1)
        request.external_calls += 1
        request.status = "identity_checking"
        request.leased_until = now + timedelta(seconds=policy.worker_lease_seconds)
        state.progress = progress
        usage = _usage(
            session,
            user,
            request,
            f"web_search_{provider_code}",
            "company_discovery",
            token,
            1,
            {"stage": "identity", "status": "reserved"},
        )
        session.commit()
        research._restore_worker_context(session, user)
        try:
            response = providers[provider_code].search(
                SearchRequest(query, policy.max_results_per_search, recent_only=False)
            )
            actual_calls = response.external_calls
            usage.metrics = {**usage.metrics, "status": "completed"}
            if not response.results:
                progress["primary_failed"] = True
            seen = {item["url"] for item in candidates}
            results = sorted(
                response.results,
                key=lambda item: not ((urlsplit(item.url).hostname or "").endswith(".gov.cn")),
            )
            for result in results:
                url = research._canonical_candidate_url(result.url)
                if url and url not in seen and len(candidates) < policy.max_candidate_urls:
                    candidates.append({"url": url, "title": result.title})
                    seen.add(url)
            progress["candidates"] = candidates
            progress.setdefault("responses", []).append(
                {
                    "provider": provider_code,
                    "request_id": response.request_id,
                    "response_hash": response.response_hash,
                    "results": len(response.results),
                    "checked_at": utc_now().isoformat(),
                }
            )
        except SearchProviderError as error:
            actual_calls = error.external_calls
            usage.metrics = {**usage.metrics, "status": "failed", "error_code": error.code}
            progress["primary_failed"] = True
            progress.setdefault("failures", []).append({"code": error.code, "stage": "search"})
        progress.pop("in_flight", None)
        usage.external_calls = actual_calls
        _save(
            session,
            request,
            state,
            progress,
            external_calls=request.external_calls - 1 + actual_calls,
        )
        return research.WebResearchWorkerResult(
            status="partial", stage="identity", external_calls=actual_calls
        )
    if (
        fetch_calls >= policy.max_fetch_requests_per_job
        or downloaded >= policy.max_download_bytes_per_job
    ):
        _save(session, request, state, progress, "in_review", "identity_evidence_missing")
        return research.WebResearchWorkerResult(status="partial", stage="identity")
    candidate = candidates[index]
    token = f"fetch:{index}"
    reserved = min(2, policy.max_fetch_requests_per_job - fetch_calls)
    progress.update(in_flight=token, candidate_index=index + 1, fetch_calls=fetch_calls + reserved)
    state.progress = progress
    request.status = "identity_checking"
    request.leased_until = now + timedelta(seconds=policy.worker_lease_seconds)
    request.external_calls += reserved
    _usage(
        session,
        user,
        request,
        "public_identity_fetch",
        "identity_document",
        token,
        reserved,
        {"status": "reserved"},
    )
    session.commit()
    research._restore_worker_context(session, user)
    fetch_policy = research._fetch_policy(
        policy,
        remaining_requests=reserved,
        remaining_bytes=policy.max_download_bytes_per_job - downloaded,
    )
    fetcher = (fetcher_factory or TrustedSourceFetcher)(fetch_policy)
    cache = research.normalized_robots_rule_cache(progress.get("robots"), policy.user_agent)
    fetcher.bind_job_robots_cache(cache)
    try:
        batch = fetcher.check(
            source_type="single_page",
            root_domain=urlsplit(candidate["url"]).hostname,
            start_url=candidate["url"],
            retention_policy="minimal_excerpt",
            conditional_state={},
        )
        for document in batch.documents:
            if document.excerpt:
                evidence.append(
                    identity_evidence(name, code, document.excerpt, document.canonical_url)
                )
        progress["evidence"] = evidence
    except SourceFetchError as error:
        progress.setdefault("failures", []).append({"url": candidate["url"], "code": error.code})
    finally:
        progress["fetch_calls"] = fetch_calls + fetcher.request_count
        progress["downloaded_bytes"] = downloaded + fetcher.downloaded_bytes
        progress["robots"] = cache
        usage = session.scalar(
            select(UsageLedger).where(
                UsageLedger.idempotency_key
                == hashlib.sha256(f"identity:{request.id}:{token}".encode()).hexdigest()
            )
        )
        usage.external_calls = fetcher.request_count
        usage.metrics = {**usage.metrics, "status": "completed", "bytes": fetcher.downloaded_bytes}
        fetcher.close()
    progress.pop("in_flight", None)
    _save(
        session,
        request,
        state,
        progress,
        external_calls=request.external_calls - reserved + fetcher.request_count,
    )
    return research.WebResearchWorkerResult(
        status="partial", stage="identity", external_calls=fetcher.request_count
    )

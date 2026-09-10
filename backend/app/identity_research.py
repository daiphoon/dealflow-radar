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

from backend.app import web_research_budget as budget
from backend.app.models import (
    Company,
    IdentityResearchState,
    PersonalCompanyRequest,
    utc_now,
)
from backend.app.providers import validate_unified_credit_code
from backend.app.source_fetcher import SourceFetchError, TrustedSourceFetcher
from backend.app.web_search import SearchProviderError, SearchRequest

POLICY_VERSION = "public-identity-v2"


def normalized(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def identity_evidence(name: str, code: str, text: str, url: str) -> dict:
    """Require adjacent labelled identity fields, never a search snippet or brand inference."""
    body = normalized(text)
    expected = normalized(name)
    windows = [
        body[max(0, match.start() - 100) : match.end() + 350]
        for match in re.finditer(re.escape(expected), body)
    ]
    # Search every occurrence: the first may be navigation, while the labelled
    # registry fields occur later. Never join distant fields across companies.
    paired = []
    conflict = False
    for window in windows:
        codes = set(re.findall(r"(?<![a-z0-9])[0-9abcdefghjklmnpqrtuwxy]{18}(?![a-z0-9])", window))
        if "统一社会信用代码" in window and codes:
            conflict = conflict or code.lower() not in codes
            if codes == {code.lower()}:
                paired.append(window)
    window = (paired or windows or [body[:300]])[0]
    host = (urlsplit(url).hostname or "").lower()
    return {
        "url": url,
        "host": host,
        "government": host == "gov.cn" or host.endswith(".gov.cn"),
        "name_match": bool(windows),
        "pair_match": bool(paired),
        "conflict": conflict,
        "excerpt": window,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "checked_at": utc_now().isoformat(),
    }


def _identity_search(name, code, search_calls, evidence, policy):
    """Bounded complementary queries; search snippets remain discovery-only."""
    if search_calls == 0:
        return policy.primary_provider, f'"{name}" {code}'
    if search_calls == 1:
        return policy.primary_provider, f'"{name}" site:gov.cn'
    if search_calls == 2:
        return policy.primary_provider, f'"{code}"'
    missing_pair = not any(item.get("pair_match") for item in evidence)
    query = f'"{code}"' if missing_pair else f'"{name}" site:gov.cn'
    if search_calls == 3:
        return policy.fallback_provider, query
    query = f'"{name}" 统一社会信用代码' if missing_pair else f'"{name}" 政府 公示'
    return (policy.primary_provider if search_calls == 4 else policy.fallback_provider), query


def _candidate_rank(candidate):
    url = candidate["url"]
    host = urlsplit(url).hostname or ""
    return (
        not host.endswith(".gov.cn"),
        any(part in url.lower() for part in ("/salary", "/wage", "/job/", "/zhaopin")),
    )


def _discovery_subject_match(name, code, result):
    text = normalized(f"{result.title} {result.snippet}")
    return normalized(name) in text or code.lower() in text


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
    if "external_calls" in updates:
        request.external_calls = updates["external_calls"]
    request.status = status
    if status in {"cancelled", "in_review", "needs_input", "research_queued"}:
        progress.pop("robots", None)
    request.last_error_code = error
    request.leased_until = None
    request.heartbeat_at = utc_now()
    session.commit()


def _reserve_identity(
    session,
    user,
    request,
    state,
    progress,
    policy,
    *,
    provider,
    operation,
    token,
    calls,
    unit_price,
    metrics,
):
    baseline = progress.setdefault(
        "budget_baseline",
        {
            "search_calls": int(progress.get("search_calls", 0)),
            "fetch_calls": int(progress.get("fetch_calls", 0)),
        },
    )
    state.progress = progress
    flag_modified(state, "progress")
    search = operation == "company_discovery"
    usage = budget.reserve(
        session,
        user,
        policy,
        task_key=f"identity:{request.id}",
        subject=budget.subject_key(request.requested_credit_code, request.id),
        company_id=None,
        provider=provider,
        operation=operation,
        token=token,
        calls=calls,
        unit_price=unit_price,
        task_limit=min(6, policy.identity_max_search_calls)
        if search
        else policy.identity_max_fetch_requests,
        task_baseline=baseline["search_calls" if search else "fetch_calls"],
        metrics={
            "request_id": str(request.id),
            "policy_version": POLICY_VERSION,
            "stage": "identity",
            **metrics,
        },
    )
    session.refresh(request, with_for_update=True)
    return usage


def _dispatch_identity(session, user, request, usage, policy):
    # Persist progress and dispatch together; the row lock serializes a concurrent cancellation.
    session.flush()
    budget.dispatch(
        session,
        user,
        usage,
        policy,
        cancelled=lambda: (
            request.cancel_requested_at is not None
            or request.status in {"cancel_requested", "cancelled"}
        ),
    )


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

    def _result(**kwargs):
        research._restore_worker_context(session, user)
        progress = state.progress or {}
        baseline = progress.get("budget_baseline", progress)
        legacy = int(baseline.get("search_calls", 0)) + int(baseline.get("fetch_calls", 0))
        return research.WebResearchWorkerResult(
            **kwargs,
            cost_summary=budget.summary(session, f"identity:{request.id}", legacy_calls=legacy),
        )

    state = session.get(IdentityResearchState, request.id)
    if state is None:
        state = IdentityResearchState(request_id=request.id, progress={})
        session.add(state)
    progress = dict(state.progress)
    if request.cancel_requested_at or request.status == "cancel_requested":
        budget.recover(session, task_key=f"identity:{request.id}", cancelled=True)
        _save(session, request, state, progress, "cancelled")
        return _result(status="cancelled", stage="identity")
    name, code = request.requested_name, request.requested_credit_code
    try:
        if not name or not code or len(name.strip()) < 4:
            raise ValueError("exact name and credit code required")
        validate_unified_credit_code(code)
    except ValueError:
        _save(session, request, state, progress, "needs_input", "identity_input_required")
        return _result(status="partial", stage="identity")
    fingerprint = hashlib.sha256(f"{normalized(name)}|{code}".encode()).hexdigest()
    if progress and progress.get("input_hash") != fingerprint:
        _save(session, request, state, progress, "needs_input", "identity_input_changed")
        return _result(status="partial", stage="identity")
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
        return _result(status="partial", stage="identity")
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
    if progress.get("policy_version") != POLICY_VERSION:
        progress.setdefault("previous_policy_versions", []).append(progress.get("policy_version"))
        progress["policy_version"] = POLICY_VERSION
    progress["budget_limits"] = {
        "search_calls": min(6, policy.identity_max_search_calls),
        "fetch_calls": policy.identity_max_fetch_requests,
        "downloaded_bytes": policy.identity_max_download_bytes,
    }
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
        return _result(status="partial", stage="identity")
    if progress.get("in_flight"):
        budget.recover(session, task_key=f"identity:{request.id}")
        # An interrupted HTTP call has an unknown outcome; never silently spend it again.
        _save(session, request, state, progress, "in_review", "identity_interrupted")
        return _result(status="partial", stage="identity")
    evidence = list(progress.get("evidence", []))
    if any(item.get("conflict") for item in evidence):
        _save(session, request, state, progress, "needs_input", "identity_conflict")
        return _result(status="partial", stage="identity")
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
            return _result(status="partial", stage="identity")
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
        return _result(status="partial", stage="identity", company_id=company.id)
    search_calls = int(progress.get("search_calls", 0))
    candidates = list(progress.get("candidates", []))
    index = int(progress.get("candidate_index", 0))
    fetch_calls = int(progress.get("fetch_calls", 0))
    downloaded = int(progress.get("downloaded_bytes", 0))
    # No paid discovery when no capacity remains to read its results.
    if (
        fetch_calls >= policy.identity_max_fetch_requests
        or downloaded >= policy.identity_max_download_bytes
    ):
        _save(session, request, state, progress, "in_review", "identity_evidence_missing")
        return _result(status="partial", stage="identity")
    attempted = {(item["provider"], item["query"]) for item in progress.get("search_attempts", [])}
    next_search = None
    for plan_index in range(int(progress.get("search_plan_index", search_calls)), 6):
        provider_code, query = _identity_search(name, code, plan_index, evidence, policy)
        if progress.get("primary_failed") and provider_code == policy.primary_provider:
            provider_code = policy.fallback_provider
        if (provider_code, query) not in attempted:
            next_search = (plan_index, provider_code, query)
            break
    can_search = search_calls < policy.identity_max_search_calls and next_search is not None
    if index >= len(candidates) or (
        can_search and index - int(progress.get("last_search_index", 0)) >= 2
    ):
        if not can_search:
            _save(session, request, state, progress, "in_review", "identity_evidence_missing")
            return _result(status="partial", stage="identity")
        plan_index, provider_code, query = next_search
        token = f"search:{search_calls}"
        try:
            usage = _reserve_identity(
                session,
                user,
                request,
                state,
                progress,
                policy,
                provider=f"web_search_{provider_code}",
                operation="company_discovery",
                token=token,
                calls=1,
                unit_price=budget.search_price(providers[provider_code], policy),
                metrics={"query": query},
            )
        except budget.WebResearchBudgetDeferred:
            _save(session, request, state, progress, "identity_queued", "identity_budget_deferred")
            return _result(status="budget_deferred", stage="identity")
        if request.cancel_requested_at or request.status in {"cancel_requested", "cancelled"}:
            budget.recover(session, task_key=f"identity:{request.id}", cancelled=True)
            _save(session, request, state, progress, "cancelled")
            return _result(status="cancelled", stage="identity")
        progress.update(
            in_flight=token,
            search_calls=search_calls + 1,
            last_search_index=index,
            search_plan_index=plan_index + 1,
        )
        progress.setdefault("search_attempts", []).append(
            {"provider": provider_code, "query": query}
        )
        request.external_calls += 1
        request.status = "identity_checking"
        request.leased_until = now + timedelta(seconds=policy.worker_lease_seconds)
        state.progress = progress
        flag_modified(state, "progress")
        _dispatch_identity(session, user, request, usage, policy)
        try:
            response = providers[provider_code].search(
                SearchRequest(query, policy.max_results_per_search, recent_only=False)
            )
            actual_calls = response.external_calls
            usage.metrics = {**usage.metrics, "status": "completed"}
            qualified_results = [
                result
                for result in response.results
                if _discovery_subject_match(name, code, result)
            ]
            if not qualified_results and provider_code == policy.primary_provider:
                progress["primary_failed"] = True
            seen = {item["url"] for item in candidates}
            pending = candidates[index:]
            for result in qualified_results:
                url = research._canonical_candidate_url(result.url)
                if url and url not in seen:
                    pending.append({"url": url, "title": result.title, "subject_match": True})
                    seen.add(url)
            candidates = (
                candidates[:index]
                + sorted(pending, key=_candidate_rank)[: policy.max_candidate_urls]
            )
            progress["candidates"] = candidates
            progress.setdefault("responses", []).append(
                {
                    "provider": provider_code,
                    "query": query,
                    "request_id": response.request_id,
                    "response_hash": response.response_hash,
                    "results": len(response.results),
                    "subject_results": len(qualified_results),
                    "rejected_subject_results": len(response.results) - len(qualified_results),
                    "checked_at": utc_now().isoformat(),
                }
            )
        except SearchProviderError as error:
            actual_calls = error.external_calls
            usage.metrics = {**usage.metrics, "status": "failed", "error_code": error.code}
            progress["primary_failed"] = True
            progress.setdefault("failures", []).append({"code": error.code, "stage": "search"})
        progress.pop("in_flight", None)
        budget.settle(session, usage, calls=actual_calls, metrics=usage.metrics)
        _save(
            session,
            request,
            state,
            progress,
            external_calls=request.external_calls - 1 + actual_calls,
        )
        return _result(status="partial", stage="identity", external_calls=actual_calls)
    candidate = candidates[index]
    title_code = normalized(candidate.get("title", ""))
    if candidate.get("subject_match") is False or (
        re.fullmatch(r"[0-9abcdefghjklmnpqrtuwxy]{18}", title_code) and title_code != code.lower()
    ):
        # Legacy queues may contain exact-code queries that the engine fuzzy-matched
        # to another company. Reject those deterministically without another HTTP call.
        progress["candidate_index"] = index + 1
        progress.setdefault("failures", []).append(
            {"url": candidate["url"], "code": "discovery_subject_mismatch"}
        )
        _save(session, request, state, progress)
        return _result(status="partial", stage="identity")
    token = f"fetch:{index}"
    # robots + page + up to three permitted same-domain redirects.
    reserved = min(5, policy.identity_max_fetch_requests - fetch_calls)
    try:
        usage = _reserve_identity(
            session,
            user,
            request,
            state,
            progress,
            policy,
            provider="public_identity_fetch",
            operation="identity_document",
            token=token,
            calls=reserved,
            unit_price=Decimal("0"),
            metrics={"download_bytes_limit": policy.identity_max_download_bytes - downloaded},
        )
    except budget.WebResearchBudgetDeferred:
        _save(session, request, state, progress, "identity_queued", "identity_budget_deferred")
        return _result(status="budget_deferred", stage="identity")
    if request.cancel_requested_at or request.status in {"cancel_requested", "cancelled"}:
        budget.recover(session, task_key=f"identity:{request.id}", cancelled=True)
        _save(session, request, state, progress, "cancelled")
        return _result(status="cancelled", stage="identity")
    progress.update(in_flight=token, candidate_index=index + 1, fetch_calls=fetch_calls + reserved)
    state.progress = progress
    flag_modified(state, "progress")
    request.status = "identity_checking"
    request.leased_until = now + timedelta(seconds=policy.worker_lease_seconds)
    request.external_calls += reserved
    _dispatch_identity(session, user, request, usage, policy)
    fetch_policy = research._fetch_policy(
        policy,
        remaining_requests=reserved,
        remaining_bytes=policy.identity_max_download_bytes - downloaded,
    )
    fetcher = (fetcher_factory or TrustedSourceFetcher)(fetch_policy)
    cache = research.normalized_robots_rule_cache(progress.get("robots"), policy.user_agent)
    fetcher.bind_job_robots_cache(cache)
    located = []

    def locate_identity(text):
        item = identity_evidence(name, code, text, candidate["url"])
        located.append(item)
        return item["excerpt"]

    try:
        batch = fetcher.check(
            source_type="single_page",
            root_domain=urlsplit(candidate["url"]).hostname,
            start_url=candidate["url"],
            retention_policy="minimal_excerpt",
            conditional_state={},
            excerpt_selector=locate_identity,
        )
        for document in batch.documents:
            if located:
                item = located.pop(0)
                # Trust the fetched/canonical host, not an unverified redirect target.
                host = (urlsplit(document.canonical_url).hostname or "").lower()
                item.update(
                    url=document.canonical_url,
                    host=host,
                    government=host == "gov.cn" or host.endswith(".gov.cn"),
                    content_hash=document.content_hash,
                )
                evidence.append(item)
        progress["evidence"] = evidence
    except SourceFetchError as error:
        progress.setdefault("failures", []).append({"url": candidate["url"], "code": error.code})
    finally:
        fetcher.close()
    progress["fetch_calls"] = fetch_calls + fetcher.request_count
    progress["downloaded_bytes"] = downloaded + fetcher.downloaded_bytes
    progress["robots"] = cache
    budget.settle(
        session,
        usage,
        calls=fetcher.request_count,
        metrics={
            "status": "completed"
            if not progress.get("failures")
            or progress["failures"][-1].get("url") != candidate["url"]
            else "failed",
            "bytes": fetcher.downloaded_bytes,
        },
    )
    progress.pop("in_flight", None)
    _save(
        session,
        request,
        state,
        progress,
        external_calls=request.external_calls - reserved + fetcher.request_count,
    )
    return _result(status="partial", stage="identity", external_calls=fetcher.request_count)

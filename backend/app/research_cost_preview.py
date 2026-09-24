"""按配置计算计划调用的保守上界；免费配额未核验时不假设免费。"""

from decimal import Decimal


def preview_cost(policy, companies=1):
    providers = tuple(dict.fromkeys((policy.primary_provider, policy.fallback_provider)))
    operations = []
    prices = [getattr(policy.cost, name + "_price_per_call") for name in providers]
    effective = [p if p is not None else policy.cost.unknown_price_upper_bound for p in prices]
    total = (
        max(effective) * policy.max_search_calls_per_job
        if all(p is not None for p in effective)
        else None
    )
    operations.append(
        {
            "operation": "search",
            "providers": list(providers),
            "calls": policy.max_search_calls_per_job,
            "cash_upper_bound": str(total) if total is not None else None,
        }
    )
    operations.append(
        {
            "operation": "public_http_fetch",
            "providers": ["http"],
            "calls": policy.max_fetch_requests_per_job,
            "cash_upper_bound": "0",
            "cost_basis": "no_provider_fee_excludes_infrastructure",
        }
    )
    if policy.tavily_enabled:
        extract = policy.cost.tavily_extract_price_per_call
        if extract is None:
            extract = policy.cost.unknown_price_upper_bound
        bound = extract * policy.max_fetch_requests_per_job if extract is not None else None
        operations.append(
            {
                "operation": "professional_extract",
                "providers": ["tavily"],
                "calls": policy.max_fetch_requests_per_job,
                "cash_upper_bound": str(bound) if bound is not None else None,
            }
        )
        total = total + bound if total is not None and bound is not None else None
    calls = policy.max_model_calls_per_job if policy.matter_model_enabled else 0
    input_tokens, output_tokens = (
        calls * policy.max_model_input_tokens,
        calls * policy.max_model_output_tokens,
    )
    model = (
        input_tokens * policy.model_input_price_per_million
        + output_tokens * policy.model_output_price_per_million
    ) / Decimal(1_000_000)
    if calls:
        operations.append(
            {
                "operation": "matter_extraction",
                "providers": [policy.extraction_model],
                "calls": calls,
                "cash_upper_bound": str(model),
            }
        )
    total = (total + model) * companies if total is not None else None
    return {
        "operations_per_company": operations,
        "providers": list(providers),
        "input_tokens": input_tokens * companies,
        "output_tokens": output_tokens * companies,
        "planned_cost_upper_bound": str(total) if total is not None else None,
        "cost_status": "bounded" if total is not None else "unknown",
        "free_quota": "not_verified",
        "provider_credits": "not_verified",
        "external_calls": 0,
    }

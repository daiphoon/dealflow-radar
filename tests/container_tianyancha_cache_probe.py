from __future__ import annotations

import json
import sys

import httpx

from backend.app.config import TianyanchaIdentityPolicy
from backend.app.tianyancha import TianyanchaIdentityProvider

LEGAL_NAME = "示例容器缓存科技有限公司"
CREDIT_CODE = "91310000MA1K000006"


def _registration_content() -> dict[str, object]:
    return {
        "sources": {
            "base": {
                "name": LEGAL_NAME,
                "creditCode": CREDIT_CODE,
                "regStatus": "存续",
                "city": "上海市",
                "district": "浦东新区",
                "regInstitute": "示例登记机关",
                "updateTimes": "2026-08-29 09:00:00",
            }
        }
    }


def main() -> None:
    mode = sys.argv[1]
    if mode not in {"populate", "replay"}:
        raise RuntimeError("mode must be populate or replay")

    def handler(_: httpx.Request) -> httpx.Response:
        if mode == "replay":
            raise RuntimeError("persistent cache replay unexpectedly attempted a network call")
        return httpx.Response(
            200,
            json={"content": _registration_content()},
            headers={"content-type": "application/json"},
        )

    provider = TianyanchaIdentityProvider(
        None,
        authorization="non-secret-container-cache-probe",
        policy=TianyanchaIdentityPolicy(min_request_interval_ms=0),
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    try:
        result = provider.lookup_identity(company_name=None, credit_code=CREDIT_CODE)
        assert result.legal_name == LEGAL_NAME
        if mode == "populate":
            assert provider.external_calls == 1
            assert provider.cache_hits == 0
        else:
            assert provider.external_calls == 0
            assert provider.cache_hits == 1
        print(
            json.dumps(
                {
                    "mode": mode,
                    "external_calls": provider.external_calls,
                    "cache_hits": provider.cache_hits,
                },
                separators=(",", ":"),
            )
        )
    finally:
        provider.close()


if __name__ == "__main__":
    main()

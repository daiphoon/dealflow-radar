from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5


def demo_uuid(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"dealflow-radar:demo:{name}")


ALPHA_TENANT_ID = demo_uuid("tenant-alpha")
BETA_TENANT_ID = demo_uuid("tenant-beta")
DEMO_TENANT_IDS: frozenset[UUID] = frozenset({ALPHA_TENANT_ID, BETA_TENANT_ID})
ALPHA_USER_ID = demo_uuid("user-alpha-admin")
BETA_USER_ID = demo_uuid("user-beta-investor")
NO_ACCESS_USER_ID = demo_uuid("user-alpha-no-access")
ALPHA_FUND_ID = demo_uuid("fund-alpha")
BETA_FUND_ID = demo_uuid("fund-beta")
MOCK_SOURCE_ID = demo_uuid("source-mock-official")

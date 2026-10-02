"""仅显式启用的独立进程；私有配置与受限控制卷，不复用网站登录或 Worker。"""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from backend.app.business_mcp import create_business_mcp
from backend.app.business_mcp_auth import BusinessOAuth, FeishuGateway
from backend.app.business_read import BusinessReadService, build_business_engine
from backend.app.mcp_control import ControlStore, Grant


def private_file(path, *, max_bytes=32768):
    p = Path(path)
    if p.is_symlink() or p.stat().st_mode & 0o077 or p.stat().st_size > max_bytes:
        raise ValueError("private_configuration_rejected")
    return p.read_text().strip()


def build():
    if os.getenv("MCP_BUSINESS_ENABLED") != "true":
        raise ValueError("business_mcp_disabled")
    config = json.loads(private_file(os.environ["MCP_BUSINESS_CONFIG_FILE"]))
    url = urlsplit(config["resource"])
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.path
        or url.query
        or url.fragment
    ):
        raise ValueError("dedicated_canonical_https_host_required")
    engine = build_business_engine(os.environ["MCP_BUSINESS_DATABASE_URL"])
    store = ControlStore(os.environ["MCP_BUSINESS_CONTROL_PATH"])
    try:
        for value in config["identity_grants"]:
            grant = Grant(**value["grant"])
            existing = store.get("grant", grant.id, include_used=True)
            if existing is None:
                store.approve_identity(
                    config["feishu_app_id"], value["tenant_key"], value["open_id"], grant
                )
            elif Grant(**existing).version != grant.version:
                raise ValueError("approval_configuration_drift")
        service = BusinessReadService(
            engine,
            store,
            private_file(config["cursor_key_file"]).encode(),
            minute=config.get("calls_per_minute", 60),
            day=config.get("calls_per_day", 1000),
        )
        gateway = FeishuGateway(
            config["feishu_app_id"],
            private_file(config["feishu_secret_file"]),
            config["resource"] + "/feishu/callback",
        )
        oauth = BusinessOAuth(
            store, config["resource"], config["client_redirect_uris"], gateway, service.active_user
        )
        return create_business_mcp(service, oauth)
    except BaseException:
        engine.dispose()
        raise


if __name__ == "__main__":
    # TLS 由同专用主机的 Caddy 终止；只信任显式的反代来源，无 URL/query access log。
    uvicorn.run(
        build(),
        host="0.0.0.0",
        port=8050,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("MCP_TRUSTED_PROXY_IPS", "127.0.0.1"),
    )

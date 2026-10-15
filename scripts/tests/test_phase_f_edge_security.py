from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "deploy" / "nginx"


def _read(name: str) -> str:
    return (NGINX / name).read_text(encoding="utf-8")


def _active_lines(name: str) -> list[str]:
    return [
        line.strip()
        for line in _read(name).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_internal_listener_requires_explicit_cidrs_and_overwrites_forwarding_headers() -> None:
    internal = _read("internal.conf")
    allowlist = _active_lines("internal-allowlist.conf.example")
    trusted = _read("trusted-proxies.conf.example")

    assert "include /etc/nginx/tc/internal-allowlist.conf;" in internal
    assert allowlist[-1] == "deny all;"
    assert not any(line.startswith("allow 0.0.0.0/0") for line in allowlist)
    assert not any(line.startswith("allow ::/0") for line in allowlist)
    assert "set_real_ip_from 0.0.0.0/0" not in trusted
    assert "set_real_ip_from ::/0" not in trusted
    assert "$proxy_add_x_forwarded_for" not in internal
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in internal
    assert "proxy_set_header Forwarded \"\";" in internal
    assert "location = /integrations/slack/events" in internal
    assert "return 404;" in internal


def test_public_listener_routes_only_exact_slack_post_with_bounded_resources() -> None:
    common = _read("00-security-common.conf")
    public = _read("slack-http.conf")

    assert public.count("location = /integrations/slack/events") == 1
    assert "location / {\n        return 404;" in public
    assert "location /api" not in public
    assert "if ($request_method != POST) { return 405; }" in public
    assert "limit_except POST { deny all; }" in public
    assert "if ($is_args != \"\") { return 400; }" in public
    assert "if ($tc_slack_json_content_type = 0) { return 415; }" in public
    assert "client_max_body_size 256k;" in public
    assert "client_body_timeout 5s;" in public
    assert "limit_req zone=tc_slack_events_per_ip burst=30 nodelay;" in public
    assert "limit_conn tc_slack_events_connections 10;" in public
    assert "proxy_connect_timeout 2s;" in public
    assert "proxy_read_timeout 3s;" in public
    assert "proxy_send_timeout 3s;" in public
    assert "proxy_next_upstream off;" in public
    assert "limit_req_zone" in common
    assert "limit_conn_zone" in common


def test_slack_proxy_preserves_body_and_does_not_leak_general_api_or_headers() -> None:
    public = _read("slack-http.conf")

    assert "proxy_pass http://tc_slack_api;" in public
    assert "proxy_pass http://tc_slack_api/" not in public
    assert "proxy_pass_request_body on;" in public
    assert "proxy_pass_request_headers on;" in public
    assert "proxy_request_buffering on;" in public
    assert "proxy_set_body" not in public
    assert "auth_request" not in public
    assert "$proxy_add_x_forwarded_for" not in public
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in public
    assert "proxy_set_header Forwarded \"\";" in public


def test_logs_omit_queries_credentials_cookies_bodies_and_slack_signatures() -> None:
    common = _read("00-security-common.conf")

    assert '"path":"$uri"' in common
    for forbidden in (
        "$request_uri",
        "$args",
        "$query_string",
        "$request_body",
        "$http_authorization",
        "$http_cookie",
        "$http_x_slack_signature",
        "$http_x_slack_request_timestamp",
    ):
        assert forbidden not in common


def test_security_headers_cover_internal_ui_api_and_public_errors() -> None:
    internal = _read("internal.conf")
    internal_headers = _read("internal-security-headers.conf.example")
    public = _read("slack-http.conf")

    assert "include /etc/nginx/tc/internal-security-headers.conf;" in internal
    for header in (
        "Content-Security-Policy",
        "Permissions-Policy",
        "Referrer-Policy",
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
    ):
        assert f"add_header {header}" in internal_headers
        assert f"add_header {header}" in public
    assert "connect-src 'self'" in internal_headers
    assert "connect-src 'self' https:" not in internal_headers
    assert "always;" in internal_headers
    assert "always;" in public

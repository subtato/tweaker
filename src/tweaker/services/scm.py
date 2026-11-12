from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def is_gitlab(provider: str) -> bool:
    return "gitlab" in (provider or "").lower()


def is_github_enterprise(provider: str, base_url: str) -> bool:
    if "enterprise" in (provider or "").lower():
        return True
    host = (urlparse(base_url).hostname or "").lower()
    return bool(host) and host not in {"github.com", "www.github.com"}


def _origin(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid SCM base URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def github_api_base(base_url: str) -> str:
    origin = _origin(base_url)
    parsed = urlparse(origin)
    if parsed.hostname in {"github.com", "www.github.com"} and parsed.path in {"", "/"}:
        return "https://api.github.com"
    return f"{origin}/api/v3"


def gitlab_api_base(base_url: str) -> str:
    return f"{_origin(base_url)}/api/v4"


def repo_remote_url(
    *,
    provider: str,
    owner: str,
    name: str,
    provider_raw: str | None,
    github_base_url: str,
    gitlab_base_url: str,
) -> str:
    if provider_raw and provider_raw.startswith(("http://", "https://")):
        raw = provider_raw.rstrip("/")
        if raw.endswith(".git"):
            return raw
        base = _origin(raw)
    else:
        base = _origin(gitlab_base_url if is_gitlab(provider) else github_base_url)
    return f"{base}/{owner}/{name}.git"


def token_for(provider: str, settings: Any, *, base_url: str | None = None) -> str | None:
    if is_gitlab(provider):
        host = (urlparse(base_url or settings.gitlab_base_url).hostname or "").lower()
        if host not in {"gitlab.com", "www.gitlab.com"}:
            return settings.gitlab_self_hosted_token or settings.gitlab_token
        return settings.gitlab_token
    if is_github_enterprise(provider, base_url or settings.github_base_url):
        return settings.github_enterprise_token or settings.github_token
    return settings.github_token

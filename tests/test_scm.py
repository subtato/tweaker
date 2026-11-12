from __future__ import annotations

from types import SimpleNamespace

from tweaker.services.scm import (
    github_api_base,
    github_api_base_for_repo,
    gitlab_api_base,
    gitlab_api_base_for_repo,
    is_github_enterprise,
    repo_remote_url,
    token_for,
)


def test_github_api_base_cloud_and_enterprise():
    assert github_api_base("https://github.com") == "https://api.github.com"
    assert (
        github_api_base("https://ghe.example.com:8443")
        == "https://ghe.example.com:8443/api/v3"
    )
    assert (
        github_api_base("https://ghe.example.com/github")
        == "https://ghe.example.com/github/api/v3"
    )


def test_api_base_from_repository_url():
    assert (
        github_api_base_for_repo("https://github.com/acme/store.git")
        == "https://api.github.com"
    )
    assert (
        github_api_base_for_repo("https://ghe.example.com:8443/acme/store.git")
        == "https://ghe.example.com:8443/api/v3"
    )
    assert (
        gitlab_api_base_for_repo("https://gitlab.example.com/group/store.git")
        == "https://gitlab.example.com/api/v4"
    )


def test_gitlab_api_base_self_hosted():
    assert gitlab_api_base("https://gitlab.com") == "https://gitlab.com/api/v4"
    assert (
        gitlab_api_base("https://gitlab.example.com:8443")
        == "https://gitlab.example.com:8443/api/v4"
    )


def test_remote_urls_for_enterprise_and_self_hosted():
    assert (
        repo_remote_url(
            provider="integrations:github_enterprise",
            owner="acme",
            name="store",
            provider_raw=None,
            github_base_url="https://ghe.example.com",
            gitlab_base_url="https://gitlab.com",
        )
        == "https://ghe.example.com/acme/store.git"
    )
    assert (
        repo_remote_url(
            provider="integrations:github_enterprise",
            owner="acme",
            name="store",
            provider_raw="https://ghe.example.com/acme/store",
            github_base_url="https://github.com",
            gitlab_base_url="https://gitlab.com",
        )
        == "https://ghe.example.com/acme/store"
    )
    assert (
        repo_remote_url(
            provider="integrations:gitlab",
            owner="acme",
            name="store",
            provider_raw=None,
            github_base_url="https://github.com",
            gitlab_base_url="https://gitlab.example.com:8443",
        )
        == "https://gitlab.example.com:8443/acme/store.git"
    )


def test_tokens_prefer_enterprise_and_self_hosted():
    settings = SimpleNamespace(
        github_token="cloud-gh",
        github_enterprise_token="ghe-token",
        gitlab_token="cloud-gl",
        gitlab_self_hosted_token="gl-token",
        github_base_url="https://ghe.example.com",
        gitlab_base_url="https://gitlab.example.com",
    )
    assert token_for("integrations:github_enterprise", settings) == "ghe-token"
    assert token_for("integrations:gitlab", settings) == "gl-token"
    assert token_for("github", settings, base_url="https://github.com/acme/store.git") == "cloud-gh"
    assert token_for("gitlab", settings, base_url="https://gitlab.com/acme/store.git") == "cloud-gl"
    assert is_github_enterprise("github", "https://ghe.example.com") is True

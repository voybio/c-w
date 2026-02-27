from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchResult:
    accepted: bool
    reason: str
    status_code: int


class GitHubDispatchClient:
    def __init__(
        self,
        repo_env: str = "LOOM_GITHUB_REPO",
        token_env: str = "LOOM_GITHUB_TOKEN",
        event_type_env: str = "LOOM_GITHUB_EVENT_TYPE",
    ) -> None:
        self.repo_env = repo_env
        self.token_env = token_env
        self.event_type_env = event_type_env

    def configured(self) -> bool:
        return bool(os.getenv(self.repo_env, "").strip()) and bool(os.getenv(self.token_env, "").strip())

    def dispatch_trace(
        self,
        *,
        agent_id: str,
        message: str,
        trace_id: str,
        source: str,
        page_url: str | None,
        user_agent: str | None,
    ) -> DispatchResult:
        repo = os.getenv(self.repo_env, "").strip()
        token = os.getenv(self.token_env, "").strip()
        event_type = os.getenv(self.event_type_env, "agent_trace").strip() or "agent_trace"

        if not repo:
            return DispatchResult(False, "missing_repo", 503)
        if "/" not in repo:
            return DispatchResult(False, "misconfigured_repo", 503)
        if not token:
            return DispatchResult(False, "missing_token", 503)

        owner, name = repo.split("/", 1)
        url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/dispatches"

        payload = {
            "event_type": event_type,
            "client_payload": {
                "agent_id": agent_id,
                "message": message,
                "trace_id": trace_id,
                "source": source,
                "page_url": page_url or "",
                "user_agent": user_agent or "",
            },
        }

        req = urllib.request.Request(
            url=url,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "loom-engine-dispatch/1.0",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15):
                return DispatchResult(True, "accepted", 204)
        except urllib.error.HTTPError as exc:
            return DispatchResult(False, f"http_{exc.code}", int(exc.code))
        except (urllib.error.URLError, TimeoutError):
            return DispatchResult(False, "dispatch_unreachable", 502)

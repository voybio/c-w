from __future__ import annotations

import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitIngestResult:
    accepted: bool
    changed: bool
    reason: str


class GitLedgerIngest:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self._lock = threading.Lock()

    def ingest_trace(self, *, agent_id: str, message: str, trace_id: str, source: str) -> GitIngestResult:
        with self._lock:
            if not self.repo_path.exists():
                return GitIngestResult(False, False, "repo_path_missing")

            try:
                self._run(["git", "-C", str(self.repo_path), "checkout", "main"])
                self._run(["git", "-C", str(self.repo_path), "pull", "--rebase", "origin", "main"])

                add_cmd = [
                    "python3",
                    str(self.repo_path / "scripts" / "board_engine.py"),
                    "add",
                    "--board",
                    str(self.repo_path / "board.json"),
                    "--agent-id",
                    agent_id,
                    "--message",
                    message,
                    "--tier",
                    "ephemeral",
                    "--source",
                    source,
                    "--trace-id",
                    trace_id,
                    "--max-message-len",
                    "280",
                ]
                add_out = self._run(add_cmd).stdout.strip().lower()
                if add_out == "ignored":
                    return GitIngestResult(True, False, "duplicate_or_empty")

                diff = self._run(["git", "-C", str(self.repo_path), "diff", "--name-only", "--", "board.json"]).stdout.strip()
                if not diff:
                    return GitIngestResult(True, False, "no_change")

                self._run(["git", "-C", str(self.repo_path), "add", "board.json"])
                self._run(
                    [
                        "git",
                        "-C",
                        str(self.repo_path),
                        "-c",
                        "user.name=loom-bridge[bot]",
                        "-c",
                        "user.email=loom-bridge[bot]@users.noreply.github.com",
                        "commit",
                        "-m",
                        f"board: ingest trace for {agent_id}",
                    ]
                )
                self._run(["git", "-C", str(self.repo_path), "push", "origin", "main"])
                return GitIngestResult(True, True, "committed")
            except subprocess.CalledProcessError as exc:
                return GitIngestResult(False, False, f"git_ingest_failed:{exc.returncode}")

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=str(self.repo_path),
            check=True,
            text=True,
            capture_output=True,
            encoding="utf-8",
        )

    @staticmethod
    def format_cmd(cmd: list[str]) -> str:
        return " ".join(shlex.quote(p) for p in cmd)

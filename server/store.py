from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.models import Ribbon


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class RibbonStore:
    def __init__(self, snapshot_path: Path = Path("board.json")) -> None:
        self._snapshot_path = snapshot_path
        self._lock = asyncio.Lock()
        self._ribbons: list[Ribbon] = []
        self._load_snapshot()

    def _load_snapshot(self) -> None:
        if not self._snapshot_path.exists():
            return
        raw = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            self._ribbons = [Ribbon(**item) for item in raw if isinstance(item, dict)]

    def _save_snapshot(self) -> None:
        payload = [r.model_dump() for r in self._ribbons]
        self._snapshot_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    async def add(self, ribbon: Ribbon) -> None:
        async with self._lock:
            if ribbon.purchase_id and ribbon.provider:
                for current in self._ribbons:
                    if current.purchase_id == ribbon.purchase_id and current.provider == ribbon.provider:
                        return
            self._ribbons.append(ribbon)
            self._ribbons.sort(key=lambda r: (r.pin_rank, r.weight, r.timestamp), reverse=True)
            self._save_snapshot()

    async def list(self) -> list[Ribbon]:
        async with self._lock:
            pruned = self._prune_locked()
            if pruned:
                self._save_snapshot()
            return list(self._ribbons)

    async def prune(self) -> int:
        async with self._lock:
            before = len(self._ribbons)
            self._prune_locked()
            removed = before - len(self._ribbons)
            if removed:
                self._save_snapshot()
            return removed

    def _prune_locked(self) -> bool:
        before = len(self._ribbons)
        now = datetime.now(timezone.utc)
        kept: list[Ribbon] = []
        for ribbon in self._ribbons:
            if ribbon.expires_at:
                if parse_iso(ribbon.expires_at) > now:
                    kept.append(ribbon)
                continue
            kept.append(ribbon)
        self._ribbons = kept
        return len(self._ribbons) != before


def compute_expires_at(ttl_hours: int | None) -> str | None:
    if ttl_hours is None:
        return None
    return iso_z(datetime.now(timezone.utc) + timedelta(hours=ttl_hours))

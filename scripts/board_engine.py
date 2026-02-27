#!/usr/bin/env python3
"""Pure-Git ledger utilities for Loom Engine board.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TierSpec:
    tier_id: str
    price_usd: float
    ttl_hours: int | None
    base_weight: int
    pin_rank: int


TIER_SPECS: dict[str, TierSpec] = {
    "ephemeral": TierSpec("ephemeral", 0.0, None, 1, 0),
    "day": TierSpec("day", 0.0, None, 2, 0),
    "3day": TierSpec("3day", 0.0, None, 3, 0),
    "permanent": TierSpec("permanent", 0.0, None, 5, 1),
    "featured": TierSpec("featured", 0.0, None, 8, 2),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def normalize_message(message: str, max_len: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", message.strip())
    return cleaned[:max_len]


def deterministic_hash(agent_id: str, message: str) -> str:
    payload = f"{agent_id}|{message}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:8].upper()


def load_board(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("board.json must be a JSON array")
    return raw


def sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
        pin_rank = int(item.get("pin_rank", 0))
        weight = int(item.get("weight", 1))
        ts = str(item.get("timestamp", ""))
        return (pin_rank, weight, ts)

    return sorted(entries, key=sort_key, reverse=True)


def save_board(path: Path, entries: list[dict[str, Any]]) -> None:
    normalized = sort_entries(entries)
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def infer_weight(tier: str, amount_usd: float | None, explicit_weight: int | None) -> int:
    if explicit_weight is not None:
        return max(1, int(explicit_weight))

    spec = TIER_SPECS[tier]
    weight = spec.base_weight

    if amount_usd is not None and amount_usd > 0 and spec.price_usd > 0:
        multiplier = int(amount_usd // spec.price_usd)
        if multiplier > 1:
            weight += min(multiplier - 1, 4)

    return max(1, weight)


def compute_expires_at(tier: str, now: datetime) -> str | None:
    ttl = TIER_SPECS[tier].ttl_hours
    if ttl is None:
        return None
    return iso_z(now + timedelta(hours=ttl))


def add_entry(
    board_path: Path,
    agent_id: str,
    message: str,
    tier: str,
    source: str,
    amount_usd: float | None,
    weight: int | None,
    trace_id: str | None,
    max_message_len: int,
    provider: str | None,
    purchase_id: str | None,
) -> bool:
    if tier not in TIER_SPECS:
        raise ValueError(f"Unknown tier: {tier}")

    entries = load_board(board_path)
    normalized_message = normalize_message(message, max_len=max_message_len)
    if not normalized_message:
        return False
    if trace_id:
        for current in entries:
            if str(current.get("trace_id", "")) == trace_id:
                return False

    now = utc_now()
    entry: dict[str, Any] = {
        "agent_id": agent_id,
        "hash": deterministic_hash(agent_id, normalized_message),
        "message": normalized_message,
        "tier": tier,
        "timestamp": iso_z(now),
        "weight": infer_weight(tier, amount_usd, weight),
        "pin_rank": TIER_SPECS[tier].pin_rank,
        "source": source,
        "expires_at": compute_expires_at(tier, now),
        "trace_id": trace_id,
    }

    if amount_usd is not None:
        entry["amount_usd"] = round(float(amount_usd), 2)
    if provider:
        entry["provider"] = provider
    if purchase_id:
        entry["purchase_id"] = purchase_id

    # Do not keep null values in persisted ledger.
    compact = {k: v for k, v in entry.items() if v is not None}

    entries.append(compact)
    save_board(board_path, entries)
    return True


def prune_expired(board_path: Path, tier_selector: str) -> int:
    entries = load_board(board_path)
    before = len(entries)
    now = utc_now()

    if tier_selector == "expiring":
        tracked_tiers = {tier for tier, spec in TIER_SPECS.items() if spec.ttl_hours is not None}
    elif tier_selector == "all":
        tracked_tiers = set(TIER_SPECS.keys())
    else:
        tracked_tiers = {tier_selector}

    kept: list[dict[str, Any]] = []
    for entry in entries:
        tier = str(entry.get("tier", ""))
        if tier not in tracked_tiers:
            kept.append(entry)
            continue

        spec = TIER_SPECS.get(tier)
        if spec is None:
            kept.append(entry)
            continue

        expires_at = entry.get("expires_at")
        if expires_at:
            if parse_iso(str(expires_at)) > now:
                kept.append(entry)
            continue

        if spec.ttl_hours is None:
            kept.append(entry)
            continue

        timestamp = entry.get("timestamp")
        if not timestamp:
            continue
        if parse_iso(str(timestamp)) + timedelta(hours=spec.ttl_hours) > now:
            kept.append(entry)

    removed = before - len(kept)
    if removed > 0:
        save_board(board_path, kept)
    return removed


def cmd_add(args: argparse.Namespace) -> int:
    changed = add_entry(
        board_path=args.board,
        agent_id=args.agent_id,
        message=args.message,
        tier=args.tier,
        source=args.source,
        amount_usd=args.amount_usd,
        weight=args.weight,
        trace_id=args.trace_id,
        max_message_len=args.max_message_len,
        provider=args.provider,
        purchase_id=args.purchase_id,
    )
    print("added" if changed else "ignored")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    removed = prune_expired(args.board, tier_selector=args.tier)
    print(f"removed={removed}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Loom board ledger")
    sub = parser.add_subparsers(required=True)

    add = sub.add_parser("add", help="Add entry")
    add.add_argument("--board", type=Path, default=Path("board.json"))
    add.add_argument("--agent-id", required=True)
    add.add_argument("--message", required=True)
    add.add_argument("--tier", choices=list(TIER_SPECS.keys()), required=True)
    add.add_argument("--source", default="manual")
    add.add_argument("--amount-usd", type=float)
    add.add_argument("--weight", type=int)
    add.add_argument("--trace-id")
    add.add_argument("--provider")
    add.add_argument("--purchase-id")
    add.add_argument("--max-message-len", type=int, default=280)
    add.set_defaults(func=cmd_add)

    prune = sub.add_parser("prune", help="Prune expired entries")
    prune.add_argument("--board", type=Path, default=Path("board.json"))
    prune.add_argument("--tier", choices=["expiring", "all", *TIER_SPECS.keys()], default="expiring")
    prune.set_defaults(func=cmd_prune)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

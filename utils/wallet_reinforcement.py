from __future__ import annotations

import logging
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests
from utils.db import (
    get_conn,
    get_latest_wallet_behavior_for_mints,
    get_latest_wallet_reinforcement_for_mints,
    get_tracked_wallets,
    get_wallet_signal_accuracy,
    record_mint_wallet_behavior_snapshots,
    record_mint_wallet_reinforcement_snapshots,
    record_wallet_live_txs,
    record_wallet_observations,
    upsert_tracked_wallets,
    upsert_wallet_quality_scores,
)

log = logging.getLogger(__name__)

_STABLE_OR_BASE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "So11111111111111111111111111111111111111112",   # WSOL
    "So11111111111111111111111111111111111111111",   # SOL placeholder
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime | None = None) -> str:
    use = dt or _utc_now()
    return use.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_candidates(candidates: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in list(candidates or []):
        mint = str((item or {}).get("mint") or "").strip()
        if not mint or mint in seen:
            continue
        seen.add(mint)
        out.append(
            {
                "mint": mint,
                "symbol": str((item or {}).get("symbol") or "").upper() or None,
            }
        )
    return out


def get_wallet_tracking_settings() -> dict:
    return {
        "enabled": str(os.getenv("WALLET_TRACKING_ENABLED", "false")).lower() in ("1", "true", "yes"),
        "cohort_size": max(3, int(os.getenv("WALLET_TRACKING_COHORT_SIZE", "10"))),
        "min_quality_score": float(os.getenv("WALLET_TRACKING_MIN_QUALITY_SCORE", "65")),
        "independent_max_wallets": max(1, int(os.getenv("WALLET_INDEPENDENT_MAX_WALLETS", "6"))),
        "independent_tx_limit": max(3, int(os.getenv("WALLET_INDEPENDENT_TX_LIMIT", "12"))),
        "failure_quarantine_threshold": max(1, int(os.getenv("WALLET_FAILURE_QUARANTINE_THRESHOLD", "3"))),
        "source": "bootstrapped_quality",
    }


def get_active_tracked_wallets(limit: int | None = None) -> list[dict]:
    return list(get_tracked_wallets(enabled_only=True, limit=limit))


def seed_wallet_tracking_cohort(
    *,
    target_size: int | None = None,
    min_quality_score: float | None = None,
) -> dict:
    settings = get_wallet_tracking_settings()
    use_target = max(1, int(target_size or settings["cohort_size"]))
    min_quality = float(min_quality_score if min_quality_score is not None else settings["min_quality_score"])
    tracked = get_tracked_wallets(enabled_only=False)
    tracked_by_wallet = {str(item.get("wallet_address") or ""): item for item in tracked}
    seeded = 0
    skipped_existing = 0

    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT wallet_address, quality_score, overlap_count, positive_outcomes, negative_outcomes,
                       neutral_outcomes, last_seen_utc, updated_at_utc, metadata_json
                FROM wallet_quality_scores
                WHERE quality_score >= ?
                ORDER BY quality_score DESC, overlap_count DESC, updated_at_utc DESC
                LIMIT ?
                """,
                (min_quality, use_target * 3),
            ).fetchall()
            fallback_used = False
            if not rows:
                rows = conn.execute(
                    """
                    SELECT wallet_address, quality_score, overlap_count, positive_outcomes, negative_outcomes,
                           neutral_outcomes, last_seen_utc, updated_at_utc, metadata_json
                    FROM wallet_quality_scores
                    WHERE wallet_address IS NOT NULL AND wallet_address != ''
                    ORDER BY quality_score DESC, overlap_count DESC, updated_at_utc DESC
                    LIMIT ?
                    """,
                    (use_target * 3,),
                ).fetchall()
                fallback_used = True
    except Exception:
        rows = []
        fallback_used = False

    payloads: list[dict] = []
    for row in rows:
        wallet_address = str(row["wallet_address"] or "").strip()
        if not wallet_address:
            continue
        existing = tracked_by_wallet.get(wallet_address)
        if existing:
            skipped_existing += 1
            continue
        payloads.append(
            {
                "wallet_address": wallet_address,
                "label": None,
                "cohort": "exploratory" if fallback_used else "core",
                "enabled": True,
                "quality_score": float(row["quality_score"] or 0.0),
                "confidence_score": min(100.0, 35.0 + float(row["overlap_count"] or 0) * 6.0),
                "source": "independent_exploratory_quality" if fallback_used else str(settings["source"]),
                "notes": (
                    "auto-seeded as exploratory wallet flow; insufficient proven wallet history"
                    if fallback_used else
                    "auto-seeded from wallet_quality_scores"
                ),
                "last_seen_utc": str(row["last_seen_utc"] or "") or None,
                "metadata": {
                    "overlap_count": int(row["overlap_count"] or 0),
                    "positive_outcomes": int(row["positive_outcomes"] or 0),
                    "negative_outcomes": int(row["negative_outcomes"] or 0),
                    "neutral_outcomes": int(row["neutral_outcomes"] or 0),
                },
            }
        )
        if len(payloads) >= use_target:
            break

    if payloads:
        seeded = int(upsert_tracked_wallets(payloads) or 0)

    active = get_tracked_wallets(enabled_only=True, limit=use_target)
    return {
        "enabled": bool(settings["enabled"]),
        "target_size": use_target,
        "min_quality_score": min_quality,
        "seeded": seeded,
        "skipped_existing": skipped_existing,
        "active_count": len(active),
        "wallets": active,
    }


def record_live_wallet_tracking_events(rows: list[dict]) -> int:
    return int(record_wallet_live_txs(rows) or 0)


def _helius_api_key() -> str:
    return str(os.getenv("HELIUS_API_KEY") or "").strip()


def _parse_dt(value) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _fetch_helius_wallet_swaps(wallet_address: str, *, since_ts: float | None, limit: int) -> tuple[list[dict], int | None]:
    api_key = _helius_api_key()
    if not api_key:
        return [], None
    try:
        response = requests.get(
            f"https://api.helius.xyz/v0/addresses/{wallet_address}/transactions",
            params={"api-key": api_key, "type": "SWAP", "limit": max(1, min(int(limit), 100))},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if response.status_code != 200:
            log.warning("[WALLET] Helius wallet flow HTTP %s for %s", response.status_code, wallet_address[:10])
            return [], int(response.status_code)
        payload = response.json()
        if not isinstance(payload, list):
            return [], int(response.status_code)
        if since_ts:
            payload = [tx for tx in payload if float(tx.get("timestamp") or 0) > float(since_ts)]
        return payload, int(response.status_code)
    except Exception as exc:
        log.debug("[WALLET] Helius wallet flow failed for %s: %s", wallet_address[:10], exc)
        return [], None


def _wallet_metadata(wallet: dict) -> dict:
    meta = wallet.get("metadata")
    if isinstance(meta, dict):
        return dict(meta)
    raw = wallet.get("metadata_json")
    if raw:
        try:
            parsed = json.loads(str(raw))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _record_wallet_poll_result(
    wallet: dict,
    *,
    status_code: int | None,
    event_count: int,
    threshold: int,
) -> bool:
    wallet_address = str(wallet.get("wallet_address") or "").strip()
    if not wallet_address:
        return False

    meta = _wallet_metadata(wallet)
    now_iso = _utc_iso()
    failures = int(meta.get("helius_failure_count") or 0)
    should_quarantine = False
    if status_code in (404, 410):
        failures += 1
        should_quarantine = failures >= int(threshold)
        meta.update({
            "helius_failure_count": failures,
            "last_helius_status": status_code,
            "last_helius_error_at": now_iso,
        })
    elif status_code == 200:
        failures = 0
        meta.update({
            "helius_failure_count": 0,
            "last_helius_status": 200,
            "last_helius_ok_at": now_iso,
            "last_helius_event_count": int(event_count),
        })
    elif status_code is not None:
        meta.update({
            "last_helius_status": status_code,
            "last_helius_error_at": now_iso,
        })

    try:
        with get_conn() as conn:
            if should_quarantine:
                conn.execute(
                    """
                    UPDATE tracked_wallets
                    SET enabled=0,
                        cohort=COALESCE(cohort, 'quarantined'),
                        notes=?,
                        updated_at_utc=?,
                        metadata_json=?
                    WHERE wallet_address=?
                    """,
                    (
                        f"auto-quarantined: Helius returned {status_code} {failures} consecutive time(s)",
                        now_iso,
                        json.dumps(meta, separators=(",", ":")),
                        wallet_address,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE tracked_wallets
                    SET updated_at_utc=?,
                        metadata_json=?
                    WHERE wallet_address=?
                    """,
                    (now_iso, json.dumps(meta, separators=(",", ":")), wallet_address),
                )
    except Exception:
        return False
    return should_quarantine


def _tx_token_amount(transfer: dict) -> float:
    for key in ("tokenAmount", "amount"):
        try:
            value = transfer.get(key)
            if value is not None:
                return float(value)
        except Exception:
            pass
    raw = transfer.get("rawTokenAmount")
    if isinstance(raw, dict):
        try:
            amount = float(raw.get("tokenAmount") or 0.0)
            decimals = int(raw.get("decimals") or 0)
            return amount / (10 ** decimals) if decimals >= 0 else amount
        except Exception:
            return 0.0
    return 0.0


def _token_symbol_from_transfer(transfer: dict) -> str | None:
    for key in ("symbol", "tokenSymbol"):
        value = str(transfer.get(key) or "").strip().upper()
        if value:
            return value
    return None


def _wallet_events_from_helius_tx(tx: dict, *, wallet_address: str, wallet_quality: float) -> list[dict]:
    signature = str(tx.get("signature") or "")
    ts = int(tx.get("timestamp") or 0)
    if not signature or ts <= 0:
        return []
    observed_at = _utc_iso(datetime.fromtimestamp(ts, tz=timezone.utc))
    out: list[dict] = []
    for transfer in list(tx.get("tokenTransfers") or []):
        mint = str(transfer.get("mint") or "").strip()
        if not mint or mint in _STABLE_OR_BASE_MINTS:
            continue
        to_user = str(transfer.get("toUserAccount") or "").strip()
        from_user = str(transfer.get("fromUserAccount") or "").strip()
        side = None
        if to_user == wallet_address:
            side = "BUY"
        elif from_user == wallet_address:
            side = "SELL"
        if side is None:
            continue
        token_amount = _tx_token_amount(transfer)
        out.append(
            {
                "observed_at_utc": observed_at,
                "wallet_address": wallet_address,
                "mint": mint,
                "symbol": _token_symbol_from_transfer(transfer),
                "tx_hash": f"{signature}:{mint}:{side}",
                "side": side,
                "amount_usd": 0.0,
                "token_amount": token_amount,
                "token_price": 0.0,
                "source": "helius_wallet_flow",
                "pool_address": None,
                "counterparty_symbol": None,
                "counterparty_address": None,
                "volume_usd": 0.0,
                "block_unix_time": ts,
                "metadata": {
                    "signature": signature,
                    "wallet_quality_score": round(float(wallet_quality or 0.0), 1),
                    "description": str(tx.get("description") or "")[:240],
                    "fee": tx.get("fee"),
                },
            }
        )
    return out


def sync_independent_wallet_flow(
    candidates: list[dict] | None = None,
    *,
    max_wallets: int | None = None,
    tx_limit: int | None = None,
) -> dict:
    settings = get_wallet_tracking_settings()
    if not settings.get("enabled"):
        return {
            "watchdog": "INDEPENDENT_WALLET_FLOW",
            "status": "PAUSED",
            "tracking_enabled": False,
            "detail": "Wallet tracking disabled by env.",
        }
    if not _helius_api_key():
        return {
            "watchdog": "INDEPENDENT_WALLET_FLOW",
            "status": "ERROR",
            "tracking_enabled": True,
            "detail": "HELIUS_API_KEY missing; independent wallet flow cannot poll tracked wallets.",
        }

    use_max_wallets = max(1, int(max_wallets or settings.get("independent_max_wallets") or 6))
    use_tx_limit = max(3, int(tx_limit or settings.get("independent_tx_limit") or 12))
    active_wallets = get_active_tracked_wallets(limit=use_max_wallets)
    seed_result = None
    if not active_wallets:
        seed_result = seed_wallet_tracking_cohort(
            target_size=use_max_wallets,
            min_quality_score=float(settings.get("min_quality_score") or 65.0),
        )
        active_wallets = get_active_tracked_wallets(limit=use_max_wallets)

    live_rows: list[dict] = []
    observation_rows: list[dict] = []
    polled = 0
    failed_wallets = 0
    quarantined_wallets: list[str] = []
    latest_seen_by_wallet: dict[str, str] = {}
    for wallet in active_wallets[:use_max_wallets]:
        wallet_address = str(wallet.get("wallet_address") or "").strip()
        if not wallet_address:
            continue
        last_seen_dt = _parse_dt(wallet.get("last_seen_utc"))
        # Poll a small recent window every cycle. Duplicate-safe inserts keep it cheap.
        since_ts = None
        if last_seen_dt and (_utc_now() - last_seen_dt.astimezone(timezone.utc)).total_seconds() < 12 * 3600:
            since_ts = last_seen_dt.timestamp()
        txs, status_code = _fetch_helius_wallet_swaps(wallet_address, since_ts=since_ts, limit=use_tx_limit)
        polled += 1
        if status_code and status_code != 200:
            failed_wallets += 1
        wallet_quality = float(wallet.get("quality_score") or 0.0)
        wallet_event_count = 0
        for tx in txs:
            for event in _wallet_events_from_helius_tx(tx, wallet_address=wallet_address, wallet_quality=wallet_quality):
                wallet_event_count += 1
                live_rows.append(event)
                observation_rows.append(
                    {
                        "observed_at_utc": event["observed_at_utc"],
                        "wallet_address": wallet_address,
                        "mint": event["mint"],
                        "symbol": event.get("symbol"),
                        "tx_hash": event["tx_hash"],
                        "side": event["side"],
                        "source": "helius_wallet_flow",
                        "volume_usd": 0.0,
                        "block_unix_time": event.get("block_unix_time"),
                        "metadata": event.get("metadata") or {},
                    }
                )
                latest_seen_by_wallet[wallet_address] = max(
                    latest_seen_by_wallet.get(wallet_address, ""),
                    str(event.get("observed_at_utc") or ""),
                )
        if _record_wallet_poll_result(
            wallet,
            status_code=status_code,
            event_count=wallet_event_count,
            threshold=int(settings.get("failure_quarantine_threshold") or 3),
        ):
            quarantined_wallets.append(wallet_address[:10])

    inserted_live = int(record_wallet_live_txs(live_rows) or 0)
    inserted_obs = int(record_wallet_observations(observation_rows) or 0)

    if latest_seen_by_wallet:
        try:
            with get_conn() as conn:
                for wallet_address, last_seen in latest_seen_by_wallet.items():
                    conn.execute(
                        """
                        UPDATE tracked_wallets
                        SET last_seen_utc = MAX(COALESCE(last_seen_utc, ''), ?),
                            updated_at_utc = ?
                        WHERE wallet_address = ?
                        """,
                        (last_seen, _utc_iso(), wallet_address),
                    )
        except Exception:
            pass

    candidate_rows = _coerce_candidates(candidates)
    behavior_map = build_live_wallet_behavior_map(candidate_rows, max_age_minutes=180) if candidate_rows else {}
    reinforcement_map = build_wallet_reinforcement_map(
        candidate_rows,
        max_snapshot_age_minutes=20,
        trade_limit=0,
        lookback_days=21,
        skip_external_sync=True,
    ) if candidate_rows else {}

    payload = {
        "watchdog": "INDEPENDENT_WALLET_FLOW",
        "status": "ACTIVE",
        "checked_at": _utc_iso(),
        "tracking_enabled": True,
        "tracked_count": len(active_wallets),
        "tracked_wallets": [str(w.get("wallet_address") or "")[:10] for w in active_wallets[:10]],
        "polled_wallets": polled,
        "failed_wallets": failed_wallets,
        "quarantined_wallets": quarantined_wallets,
        "tx_limit": use_tx_limit,
        "inserted_live_events": inserted_live,
        "inserted_observations": inserted_obs,
        "candidate_count": len(candidate_rows),
        "behavior_mints": len(behavior_map),
        "reinforcement_mints": len(reinforcement_map),
        "seeded": seed_result,
        "source": "helius_enhanced_transactions",
        "detail": f"Polled {polled} tracked wallet(s) through Helius; inserted {inserted_live} live event(s).",
    }
    try:
        from utils.db import set_kv  # type: ignore

        set_kv("independent_wallet_flow_status", payload)
    except Exception:
        try:
            import json
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                    ("independent_wallet_flow_status", json.dumps(payload, separators=(",", ":"))),
                )
        except Exception:
            pass
    return payload


def build_live_wallet_behavior_map(
    candidates: list[dict] | None,
    *,
    max_age_minutes: int = 180,
) -> dict[str, dict]:
    candidate_rows = _coerce_candidates(candidates)
    if not candidate_rows:
        return {}

    tracked_wallets = get_tracked_wallets(enabled_only=True)
    if not tracked_wallets:
        mints = [c["mint"] for c in candidate_rows]
        return get_latest_wallet_behavior_for_mints(mints, max_age_minutes=max_age_minutes)

    tracked_by_wallet = {str(row.get("wallet_address") or "").strip(): row for row in tracked_wallets}
    tracked_addresses = sorted(tracked_by_wallet.keys())
    mints = [c["mint"] for c in candidate_rows]
    candidate_map = {c["mint"]: c for c in candidate_rows}
    cutoff = (_utc_now() - timedelta(minutes=max(15, int(max_age_minutes)))).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        with get_conn() as conn:
            mint_placeholders = ",".join("?" for _ in mints)
            wallet_placeholders = ",".join("?" for _ in tracked_addresses)
            rows = conn.execute(
                f"""
                SELECT
                    observed_at_utc,
                    wallet_address,
                    mint,
                    symbol,
                    tx_hash,
                    side,
                    amount_usd,
                    token_amount,
                    token_price
                FROM wallet_live_txs
                WHERE mint IN ({mint_placeholders})
                  AND wallet_address IN ({wallet_placeholders})
                  AND observed_at_utc >= ?
                ORDER BY observed_at_utc DESC
                """,
                [*mints, *tracked_addresses, cutoff],
            ).fetchall()
    except Exception as exc:
        log.debug("[WALLET] live behavior query failed: %s", exc)
        return get_latest_wallet_behavior_for_mints(mints, max_age_minutes=max_age_minutes)

    by_mint: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_mint[str(row["mint"] or "")].append(dict(row))

    snapshots: list[dict] = []
    latest_by_mint: dict[str, dict] = {}
    snapshot_ts = _utc_iso()

    for mint in mints:
        events = by_mint.get(mint, [])
        if not events:
            continue
        buy_wallets: set[str] = set()
        sell_wallets: set[str] = set()
        wallet_event_counts: Counter = Counter()
        wallet_quality_sum = 0.0
        buy_volume = 0.0
        sell_volume = 0.0
        symbols: set[str] = set()

        for event in events:
            wallet = str(event.get("wallet_address") or "").strip()
            if not wallet:
                continue
            tracked = tracked_by_wallet.get(wallet) or {}
            wallet_quality = float(tracked.get("quality_score") or 0.0)
            wallet_quality_sum += wallet_quality
            wallet_event_counts[wallet] += 1
            side = str(event.get("side") or "").upper()
            amount_usd = float(event.get("amount_usd") or 0.0)
            symbol = str(event.get("symbol") or "").upper()
            if symbol:
                symbols.add(symbol)
            if side == "BUY":
                buy_wallets.add(wallet)
                buy_volume += amount_usd
            elif side == "SELL":
                sell_wallets.add(wallet)
                sell_volume += amount_usd

        tracked_wallet_count = len(set(list(buy_wallets) + list(sell_wallets)))
        repeat_wallet_count = sum(1 for n in wallet_event_counts.values() if n >= 2)
        avg_quality = wallet_quality_sum / max(len(events), 1)
        net_flow = buy_volume - sell_volume
        buy_share = buy_volume / max(buy_volume + sell_volume, 1.0)

        conviction = 0.0
        conviction += min(30.0, tracked_wallet_count * 8.0)
        conviction += min(18.0, repeat_wallet_count * 5.0)
        conviction += min(22.0, max(0.0, net_flow) / 10000.0 * 4.0)
        conviction += min(15.0, max(0.0, avg_quality - 55.0) * 0.5)
        if buy_share >= 0.7:
            conviction += 8.0
        conviction = max(0.0, min(100.0, round(conviction, 1)))

        cluster = 0.0
        cluster += min(40.0, len(buy_wallets) * 9.0)
        cluster += min(20.0, len(sell_wallets) * 4.0)
        cluster += min(16.0, repeat_wallet_count * 4.0)
        if len(buy_wallets) >= 2 and buy_share >= 0.65:
            cluster += 10.0
        cluster = max(0.0, min(100.0, round(cluster, 1)))

        if buy_volume > sell_volume * 1.5 and len(buy_wallets) >= 2:
            behavior_state = "ADDING" if repeat_wallet_count > 0 else "ENTERING"
        elif sell_volume > buy_volume * 1.5 and len(sell_wallets) >= 1:
            behavior_state = "EXITING"
        elif tracked_wallet_count >= 2 and abs(net_flow) <= max(1000.0, (buy_volume + sell_volume) * 0.2):
            behavior_state = "HOLDING"
        else:
            behavior_state = "MIXED"

        if conviction >= 78.0 and cluster >= 55.0:
            quality = "STRONG"
        elif conviction >= 55.0 and cluster >= 35.0:
            quality = "MODERATE"
        elif conviction >= 28.0:
            quality = "LIGHT"
        else:
            quality = "NONE"

        reasons: list[str] = []
        if buy_wallets:
            reasons.append(f"{len(buy_wallets)} tracked wallet{'s' if len(buy_wallets) != 1 else ''} buying")
        if repeat_wallet_count > 0:
            reasons.append(f"{repeat_wallet_count} wallet{'s' if repeat_wallet_count != 1 else ''} showed repeat flow")
        if net_flow > 0:
            reasons.append(f"net buy flow ${net_flow:,.0f}")
        elif net_flow < 0:
            reasons.append(f"net sell flow ${abs(net_flow):,.0f}")
        if avg_quality >= 70.0:
            reasons.append("tracked wallet quality is strong")
        if not reasons:
            reasons.append("live wallet flow is still mixed")

        payload = {
            "mint": mint,
            "symbol": candidate_map.get(mint, {}).get("symbol") or (next(iter(symbols)) if symbols else None),
            "ts_utc": snapshot_ts,
            "wallet_conviction_score": conviction,
            "wallet_cluster_score": cluster,
            "wallet_behavior_state": behavior_state,
            "smart_money_quality": quality,
            "tracked_wallet_count": tracked_wallet_count,
            "buy_wallet_count": len(buy_wallets),
            "sell_wallet_count": len(sell_wallets),
            "buy_volume_usd": round(buy_volume, 2),
            "sell_volume_usd": round(sell_volume, 2),
            "repeat_wallet_count": repeat_wallet_count,
            "net_flow_usd": round(net_flow, 2),
            "reasons": reasons[:5],
            "inputs": {
                "avg_tracked_wallet_quality": round(avg_quality, 1),
                "buy_share": round(buy_share * 100.0, 1),
                "event_count": len(events),
            },
        }
        snapshots.append(payload)
        latest_by_mint[mint] = payload

    if snapshots:
        record_mint_wallet_behavior_snapshots(snapshots)
        return latest_by_mint
    return get_latest_wallet_behavior_for_mints(mints, max_age_minutes=max_age_minutes)


def _latest_wallet_snapshot_ages(mints: list[str]) -> dict[str, float]:
    clean_mints = [str(m or "").strip() for m in mints if str(m or "").strip()]
    if not clean_mints:
        return {}
    try:
        with get_conn() as conn:
            placeholders = ",".join("?" for _ in clean_mints)
            rows = conn.execute(
                f"""
                SELECT mint, MAX(ts_utc) AS ts_utc
                FROM mint_wallet_reinforcement_snapshots
                WHERE mint IN ({placeholders})
                GROUP BY mint
                """,
                clean_mints,
            ).fetchall()
    except Exception:
        return {}

    now = _utc_now()
    ages: dict[str, float] = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row["ts_utc"]).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ages[str(row["mint"] or "")] = max(0.0, (now - ts.astimezone(timezone.utc)).total_seconds() / 60.0)
        except Exception:
            continue
    return ages


def sync_wallet_observations_for_candidates(
    candidates: list[dict] | None,
    *,
    max_snapshot_age_minutes: int = 20,
    trade_limit: int = 35,
) -> dict:
    candidate_rows = _coerce_candidates(candidates)
    if not candidate_rows:
        return {"requested": 0, "synced": 0, "inserted": 0, "skipped_fresh": 0}

    latest_ages = _latest_wallet_snapshot_ages([c["mint"] for c in candidate_rows])
    inserted = 0
    synced = 0
    skipped = 0
    for candidate in candidate_rows:
        mint = candidate["mint"]
        age_minutes = latest_ages.get(mint)
        if age_minutes is not None and age_minutes < max(5, int(max_snapshot_age_minutes)):
            skipped += 1
            continue
        try:
            from data.birdeye import fetch_birdeye_token_trades  # type: ignore
        except Exception:
            continue
        trades = fetch_birdeye_token_trades(mint, limit=trade_limit)
        if not trades:
            continue
        observed: list[dict] = []
        for trade in trades:
            block_unix_time = int(trade.get("block_unix_time") or 0)
            observed.append(
                {
                    "observed_at_utc": _utc_iso(datetime.fromtimestamp(block_unix_time, tz=timezone.utc)),
                    "wallet_address": str(trade.get("wallet_address") or ""),
                    "mint": mint,
                    "symbol": candidate.get("symbol"),
                    "tx_hash": str(trade.get("tx_hash") or ""),
                    "side": str(trade.get("side") or "") or None,
                    "source": str(trade.get("source") or "birdeye_token_txs"),
                    "volume_usd": float(trade.get("volume_usd") or 0.0),
                    "block_unix_time": block_unix_time,
                    "metadata": {
                        "block_number": trade.get("block_number"),
                        "tx_type": trade.get("tx_type"),
                        "alias": trade.get("alias"),
                    },
                }
            )
        inserted += int(record_wallet_observations(observed) or 0)
        synced += 1
    return {
        "requested": len(candidate_rows),
        "synced": synced,
        "inserted": inserted,
        "skipped_fresh": skipped,
    }


def _mint_outcome_buckets(lookback_days: int = 21) -> dict[str, str]:
    cutoff = (_utc_now() - timedelta(days=max(3, int(lookback_days)))).strftime("%Y-%m-%d %H:%M:%S")
    outcome_by_mint: dict[str, str] = {}
    try:
        with get_conn() as conn:
            trade_rows = conn.execute(
                """
                SELECT mint, pnl_pct
                FROM memecoin_trades
                WHERE mint IS NOT NULL
                  AND mint != ''
                  AND status='CLOSED'
                  AND closed_ts_utc >= ?
                """,
                (cutoff,),
            ).fetchall()
            for row in trade_rows:
                mint = str(row["mint"] or "").strip()
                if not mint:
                    continue
                pnl_pct = float(row["pnl_pct"] or 0.0)
                if pnl_pct >= 5.0:
                    outcome_by_mint[mint] = "POSITIVE"
                elif pnl_pct <= -10.0:
                    outcome_by_mint[mint] = "NEGATIVE"
                else:
                    outcome_by_mint.setdefault(mint, "NEUTRAL")

            scanner_rows = conn.execute(
                """
                WITH latest AS (
                    SELECT
                        mint,
                        MAX(id) AS max_id
                    FROM memecoin_signal_outcomes
                    WHERE mint IS NOT NULL
                      AND mint != ''
                      AND scanned_at >= ?
                    GROUP BY mint
                )
                SELECT
                    s.mint,
                    s.rug_label,
                    s.return_4h_pct,
                    s.return_24h_pct
                FROM memecoin_signal_outcomes s
                INNER JOIN latest l ON l.max_id = s.id
                """,
                (cutoff,),
            ).fetchall()
    except Exception:
        return outcome_by_mint

    for row in scanner_rows:
        mint = str(row["mint"] or "").strip()
        if not mint or mint in outcome_by_mint and outcome_by_mint[mint] == "NEGATIVE":
            continue
        rug = str(row["rug_label"] or "").upper()
        ret4 = row["return_4h_pct"]
        ret24 = row["return_24h_pct"]
        ret4_val = float(ret4) if ret4 is not None else None
        ret24_val = float(ret24) if ret24 is not None else None
        if rug in ("DANGER", "RUGGED"):
            outcome_by_mint[mint] = "NEGATIVE"
        elif ret24_val is not None and ret24_val >= 15.0:
            outcome_by_mint[mint] = "POSITIVE"
        elif ret4_val is not None and ret4_val >= 8.0:
            outcome_by_mint[mint] = "POSITIVE"
        elif ret24_val is not None and ret24_val <= -20.0:
            outcome_by_mint[mint] = "NEGATIVE"
        elif ret4_val is not None and ret4_val <= -10.0:
            outcome_by_mint[mint] = "NEGATIVE"
        else:
            outcome_by_mint.setdefault(mint, "NEUTRAL")
    return outcome_by_mint


def refresh_wallet_quality_scores(*, lookback_days: int = 21, wallet_filter: set[str] | None = None) -> int:
    outcome_buckets = _mint_outcome_buckets(lookback_days=lookback_days)
    cutoff = (_utc_now() - timedelta(days=max(3, int(lookback_days)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with get_conn() as conn:
            if wallet_filter:
                placeholders = ",".join("?" for _ in wallet_filter)
                params = [cutoff, *sorted(wallet_filter)]
                rows = conn.execute(
                    f"""
                    SELECT wallet_address, mint, MAX(observed_at_utc) AS last_seen_utc
                    FROM wallet_token_observations
                    WHERE observed_at_utc >= ?
                      AND wallet_address IN ({placeholders})
                    GROUP BY wallet_address, mint
                    """,
                    params,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT wallet_address, mint, MAX(observed_at_utc) AS last_seen_utc
                    FROM wallet_token_observations
                    WHERE observed_at_utc >= ?
                    GROUP BY wallet_address, mint
                    """,
                    (cutoff,),
                ).fetchall()
    except Exception:
        return 0

    by_wallet: dict[str, dict] = defaultdict(
        lambda: {
            "mints": set(),
            "positive_outcomes": 0,
            "negative_outcomes": 0,
            "neutral_outcomes": 0,
            "last_seen_utc": None,
        }
    )
    for row in rows:
        wallet = str(row["wallet_address"] or "").strip()
        mint = str(row["mint"] or "").strip()
        if not wallet or not mint:
            continue
        bucket = outcome_buckets.get(mint, "NEUTRAL")
        state = by_wallet[wallet]
        state["mints"].add(mint)
        state["last_seen_utc"] = max(str(state.get("last_seen_utc") or ""), str(row["last_seen_utc"] or ""))
        if bucket == "POSITIVE":
            state["positive_outcomes"] += 1
        elif bucket == "NEGATIVE":
            state["negative_outcomes"] += 1
        else:
            state["neutral_outcomes"] += 1

    accuracy_by_wallet = get_wallet_signal_accuracy(list(by_wallet.keys()))

    payloads: list[dict] = []
    now_iso = _utc_iso()
    for wallet, state in by_wallet.items():
        overlap_count = len(state["mints"])
        positive = int(state["positive_outcomes"] or 0)
        negative = int(state["negative_outcomes"] or 0)
        neutral = int(state["neutral_outcomes"] or 0)
        quality = 45.0
        quality += min(overlap_count, 6) * 3.0
        quality += positive * 8.0
        quality -= negative * 10.0
        if positive >= 2 and negative == 0:
            quality += 5.0
        if negative >= positive + 2:
            quality -= 6.0
        accuracy = dict(accuracy_by_wallet.get(wallet) or {})
        accuracy_trades = int(accuracy.get("trades_tagged") or 0)
        accuracy_wins = int(accuracy.get("wins") or 0)
        accuracy_losses = int(accuracy.get("losses") or 0)
        accuracy_avg_return = float(accuracy.get("avg_return_pct") or 0.0)
        if accuracy_trades >= 2:
            win_rate = accuracy_wins / max(accuracy_trades, 1)
            if win_rate >= 0.65 and accuracy_avg_return > 0:
                quality += min(12.0, 4.0 + accuracy_trades * 1.5)
            elif accuracy_losses >= accuracy_wins and accuracy_avg_return < 0:
                quality -= min(14.0, 4.0 + accuracy_trades * 1.75)
        quality = max(5.0, min(95.0, round(quality, 1)))
        payloads.append(
            {
                "wallet_address": wallet,
                "quality_score": quality,
                "overlap_count": overlap_count,
                "positive_outcomes": positive,
                "negative_outcomes": negative,
                "neutral_outcomes": neutral,
                "last_seen_utc": state["last_seen_utc"],
                "updated_at_utc": now_iso,
                "metadata": {
                    "lookback_days": int(lookback_days),
                    "tracked_mints": overlap_count,
                    "wallet_signal_accuracy_trades": accuracy_trades,
                    "wallet_signal_accuracy_avg_return_pct": round(accuracy_avg_return, 2),
                },
            }
        )
    return int(upsert_wallet_quality_scores(payloads) or 0)


def build_wallet_reinforcement_map(
    candidates: list[dict] | None,
    *,
    max_snapshot_age_minutes: int = 20,
    trade_limit: int = 35,
    lookback_days: int = 21,
    skip_external_sync: bool = False,
) -> dict[str, dict]:
    candidate_rows = _coerce_candidates(candidates)
    if not candidate_rows:
        return {}

    independent_mode = str(os.getenv("NO_BIRDEYE_MODE", "") + "," + os.getenv("INDEPENDENT_SOURCE_MODE", "")).lower()
    if not skip_external_sync and "true" not in independent_mode and int(trade_limit or 0) > 0:
        sync_wallet_observations_for_candidates(
            candidate_rows,
            max_snapshot_age_minutes=max_snapshot_age_minutes,
            trade_limit=trade_limit,
        )

    target_wallets: set[str] = set()
    mints = [c["mint"] for c in candidate_rows]
    try:
        with get_conn() as conn:
            placeholders = ",".join("?" for _ in mints)
            obs_rows = conn.execute(
                f"""
                SELECT DISTINCT wallet_address
                FROM wallet_token_observations
                WHERE mint IN ({placeholders})
                  AND observed_at_utc >= datetime('now', '-21 days')
                """,
                mints,
            ).fetchall()
            for row in obs_rows:
                wallet = str(row["wallet_address"] or "").strip()
                if wallet:
                    target_wallets.add(wallet)
    except Exception:
        target_wallets = set()

    refresh_wallet_quality_scores(lookback_days=lookback_days, wallet_filter=target_wallets or None)

    snapshots: list[dict] = []
    latest_by_mint: dict[str, dict] = {}
    now = _utc_now()
    cutoff_obs = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    candidate_map = {c["mint"]: c for c in candidate_rows}
    try:
        with get_conn() as conn:
            placeholders = ",".join("?" for _ in mints)
            obs_rows = conn.execute(
                f"""
                SELECT
                    o.mint,
                    o.wallet_address,
                    o.symbol,
                    o.observed_at_utc,
                    o.volume_usd,
                    COALESCE(q.quality_score, 45.0) AS quality_score
                FROM wallet_token_observations o
                LEFT JOIN wallet_quality_scores q
                  ON q.wallet_address = o.wallet_address
                WHERE o.mint IN ({placeholders})
                  AND o.observed_at_utc >= ?
                ORDER BY o.observed_at_utc DESC
                """,
                [*mints, cutoff_obs],
            ).fetchall()
    except Exception as exc:
        log.debug("[WALLET] reinforcement query failed: %s", exc)
        return get_latest_wallet_reinforcement_for_mints(mints, max_age_minutes=max_snapshot_age_minutes * 6)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in obs_rows:
        grouped[str(row["mint"] or "")].append(dict(row))

    snapshot_ts = _utc_iso(now)
    for mint in mints:
        rows = grouped.get(mint, [])
        wallet_counts: Counter = Counter()
        wallet_quality: dict[str, float] = {}
        latest_obs: str | None = None
        total_volume = 0.0
        for row in rows:
            wallet = str(row.get("wallet_address") or "").strip()
            if not wallet:
                continue
            wallet_counts[wallet] += 1
            wallet_quality[wallet] = max(float(row.get("quality_score") or 45.0), wallet_quality.get(wallet, 0.0))
            total_volume += float(row.get("volume_usd") or 0.0)
            observed_at = str(row.get("observed_at_utc") or "")
            if observed_at and (latest_obs is None or observed_at > latest_obs):
                latest_obs = observed_at

        unique_wallets = len(wallet_counts)
        repeat_wallets = sum(1 for n in wallet_counts.values() if n >= 2)
        high_quality_wallets = sum(1 for q in wallet_quality.values() if q >= 70.0)
        avg_quality = (
            sum(wallet_quality.values()) / max(len(wallet_quality), 1)
            if wallet_quality else 0.0
        )
        recent_wallet_activity = False
        if latest_obs:
            try:
                latest_dt = datetime.fromisoformat(latest_obs.replace("Z", "+00:00"))
                if latest_dt.tzinfo is None:
                    latest_dt = latest_dt.replace(tzinfo=timezone.utc)
                recent_wallet_activity = (now - latest_dt.astimezone(timezone.utc)).total_seconds() <= 6 * 3600
            except Exception:
                recent_wallet_activity = False

        overlap_score = 0.0
        overlap_score += min(unique_wallets, 6) * 6.0
        overlap_score += min(repeat_wallets, 4) * 8.0
        overlap_score += min(high_quality_wallets, 3) * 10.0
        if recent_wallet_activity:
            overlap_score += 6.0
        if avg_quality > 0:
            overlap_score += max(0.0, min(8.0, (avg_quality - 55.0) * 0.2))
        overlap_score = max(0.0, min(100.0, round(overlap_score, 1)))

        wallet_confidence = 20.0
        wallet_confidence += min(unique_wallets, 6) * 7.0
        wallet_confidence += min(sum(wallet_counts.values()), 12) * 2.5
        if high_quality_wallets > 0:
            wallet_confidence += 8.0
        wallet_confidence = max(0.0, min(100.0, round(wallet_confidence, 1)))

        if overlap_score >= 75.0 and (high_quality_wallets >= 2 or repeat_wallets >= 2):
            level = "STRONG"
        elif overlap_score >= 50.0 and unique_wallets >= 3:
            level = "MODERATE"
        elif overlap_score >= 28.0 and unique_wallets >= 2:
            level = "LIGHT"
        else:
            level = "NONE"

        reasons: list[str] = []
        if unique_wallets > 0:
            reasons.append(f"{unique_wallets} recent wallet{'s' if unique_wallets != 1 else ''} traded this mint")
        if repeat_wallets > 0:
            reasons.append(f"{repeat_wallets} wallet{'s' if repeat_wallets != 1 else ''} came back for repeat flow")
        if high_quality_wallets > 0:
            reasons.append(f"{high_quality_wallets} higher-quality wallet{'s' if high_quality_wallets != 1 else ''} have positive history")
        if recent_wallet_activity:
            reasons.append("wallet activity is still fresh")
        if not reasons:
            reasons.append("wallet activity is still too thin")

        inputs = {
            "avg_wallet_quality": round(avg_quality, 1) if wallet_quality else 0.0,
            "total_recent_volume_usd": round(total_volume, 2),
            "wallet_tx_count": int(sum(wallet_counts.values())),
            "latest_observed_at": latest_obs,
        }
        payload = {
            "mint": mint,
            "symbol": candidate_map.get(mint, {}).get("symbol"),
            "ts_utc": snapshot_ts,
            "wallet_overlap_score": overlap_score,
            "wallet_confidence": wallet_confidence,
            "wallet_support_level": level,
            "unique_wallets": unique_wallets,
            "repeat_wallets": repeat_wallets,
            "high_quality_wallets": high_quality_wallets,
            "recent_wallet_activity": recent_wallet_activity,
            "reasons": reasons,
            "inputs": inputs,
        }
        snapshots.append(payload)
        latest_by_mint[mint] = payload

    record_mint_wallet_reinforcement_snapshots(snapshots)
    return latest_by_mint

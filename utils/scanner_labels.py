from __future__ import annotations

import logging
import re
import sqlite3

log = logging.getLogger(__name__)


def _norm_symbol(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _clean_label(value: str | None) -> str:
    return str(value or "").strip().upper()


def _derive_conservative_labels(row: dict) -> tuple[str, str, str]:
    regime = _clean_label(row.get("scanner_regime") or "NORMAL")
    status = _clean_label(row.get("status") or "PENDING")
    score = float(row.get("score") or 0.0)
    proof_score = float(row.get("proof_score") or 0.0)

    if regime == "RELAXED_NEAR_MISS":
        return "LOW_TRUST", "BLOCKED", "DERIVED_RELAXED"
    if proof_score >= 95.0 or score >= 90.0:
        return "CONDITIONAL_TRUST", "MONITOR", "DERIVED_ELEVATED"
    if proof_score >= 80.0 or score >= 75.0 or (status == "PENDING" and score >= 70.0):
        return "LOW_TRUST", "MONITOR", "DERIVED_WATCH"
    return "LOW_TRUST", "BLOCKED", "DERIVED_DEFAULT"


def _merge_labels(row: dict, fallback: sqlite3.Row | dict | None) -> tuple[str, str]:
    trust = _clean_label(row.get("trust_label"))
    triage = _clean_label(row.get("triage_state"))
    if fallback:
        trust = trust or _clean_label(fallback["trust_label"])
        triage = triage or _clean_label(fallback["triage_state"])
    return trust, triage


def _symbol_is_unambiguous(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    mint: str,
    lookback_days: int = 14,
) -> bool:
    if not symbol:
        return False
    rows = conn.execute(
        """
        SELECT DISTINCT mint
        FROM memecoin_signal_outcomes
        WHERE source='SCANNER'
          AND symbol=?
          AND mint IS NOT NULL
          AND mint != ''
          AND scanned_at >= datetime('now', ?)
        """,
        (symbol, f"-{int(lookback_days)} days"),
    ).fetchall()
    mints = {str(r["mint"] or "").strip() for r in rows if str(r["mint"] or "").strip()}
    if not mints:
        return True
    if mint:
        return len(mints) == 1 and mint in mints
    return len(mints) == 1


def resolve_scanner_labels(
    conn: sqlite3.Connection,
    row: dict | sqlite3.Row,
    *,
    persist: bool = False,
) -> dict:
    row_data = dict(row)
    row_id = int(row_data.get("id") or 0)
    mint = str(row_data.get("mint") or "").strip()
    symbol = str(row_data.get("symbol") or "").strip().upper()
    scanned_at = str(row_data.get("scanned_at") or "").strip()

    trust = _clean_label(row_data.get("trust_label"))
    triage = _clean_label(row_data.get("triage_state"))
    label_source = "EXISTING"

    if trust and triage:
        return {
            "trust_label": trust,
            "triage_state": triage,
            "label_source": label_source,
            "symbol_unambiguous": True,
        }

    exact_fallback = None
    if mint:
        exact_where = [
            "source='SCANNER'",
            "mint=?",
            "id != ?",
            "(NULLIF(trust_label, '') IS NOT NULL OR NULLIF(triage_state, '') IS NOT NULL)",
        ]
        exact_params: list[object] = [mint, row_id]
        if scanned_at:
            exact_where.append("scanned_at <= ?")
            exact_params.append(scanned_at)
        exact_fallback = conn.execute(
            f"""
            SELECT trust_label, triage_state
            FROM memecoin_signal_outcomes
            WHERE {' AND '.join(exact_where)}
            ORDER BY scanned_at DESC, id DESC
            LIMIT 1
            """,
            tuple(exact_params),
        ).fetchone()
        trust, triage = _merge_labels(row_data, exact_fallback)
        if exact_fallback and (trust or triage):
            label_source = "MINT_HISTORY"

    symbol_unambiguous = False
    if (not trust or not triage) and symbol:
        try:
            symbol_unambiguous = _symbol_is_unambiguous(conn, symbol=symbol, mint=mint)
        except Exception:
            symbol_unambiguous = False
        if symbol_unambiguous:
            symbol_where = [
                "source='SCANNER'",
                "symbol=?",
                "id != ?",
                "(NULLIF(trust_label, '') IS NOT NULL OR NULLIF(triage_state, '') IS NOT NULL)",
            ]
            symbol_params: list[object] = [symbol, row_id]
            if scanned_at:
                symbol_where.append("scanned_at <= ?")
                symbol_params.append(scanned_at)
            symbol_fallback = conn.execute(
                f"""
                SELECT trust_label, triage_state
                FROM memecoin_signal_outcomes
                WHERE {' AND '.join(symbol_where)}
                ORDER BY scanned_at DESC, id DESC
                LIMIT 1
                """,
                tuple(symbol_params),
            ).fetchone()
            trust, triage = _merge_labels(
                {"trust_label": trust, "triage_state": triage},
                symbol_fallback,
            )
            if symbol_fallback and (trust or triage):
                label_source = "SYMBOL_HISTORY"

    if not trust or not triage:
        derived_trust, derived_triage, derived_source = _derive_conservative_labels(row_data)
        trust = trust or derived_trust
        triage = triage or derived_triage
        label_source = derived_source if label_source == "EXISTING" else f"{label_source}+{derived_source}"

    if persist and row_id > 0:
        try:
            conn.execute(
                """
                UPDATE memecoin_signal_outcomes
                SET trust_label = COALESCE(NULLIF(trust_label, ''), ?),
                    triage_state = COALESCE(NULLIF(triage_state, ''), ?),
                    labeled_at = COALESCE(labeled_at, datetime('now'))
                WHERE id = ?
                """,
                (trust or None, triage or None, row_id),
            )
        except Exception as exc:
            log.debug("[LABELS] persist failed for row=%s: %s", row_id, exc)

    return {
        "trust_label": trust,
        "triage_state": triage,
        "label_source": label_source,
        "symbol_unambiguous": symbol_unambiguous,
    }

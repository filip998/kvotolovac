"""Snapshot labeled team_review_cases to a JSONL regression fixture.

The team matcher pipeline (slot + global-search) is evaluated against a
gold-standard dataset of hand-labeled decisions from the ``team_review_cases``
table. This script emits two newline-delimited JSON fixtures:

* ``team_matcher_labels.jsonl`` — one record per labeled case row. Carries
  full fixture context (bookmaker, sport, start_time, matched_counterpart_team,
  reason_code) plus the suggestion / candidate snapshot taken at the time of
  the human decision, and the resolved approved_target_id (or declined flag).
  This is the *raw* labels file. NOT committed to git (~50 MB) — regenerated
  from the live DB.

* ``team_matcher_labels_decisions.jsonl`` — one record per unique
  ``(bookmaker_id, raw_team_name, start_time, matched_counterpart_team)``
  decision key. Collapses the per-case rows when multiple cases share the
  same decision context. This is the *de-duplicated* fixture consumed by
  ``team_matcher_replay.py`` for regression validation. Committed to git
  (~8 MB) because it represents the cleaned ground-truth corpus.

All canonical team IDs in both files are resolved through ``merged_into_team_id``
chains before being written, so downstream consumers can compare ID-equality
without worrying about historical merges.

Approved targets are derived from the ``team_aliases`` table: the bookmaker's
normalized alias for the raw team name points at the canonical id that the
approval actually bound the alias to (which may differ from
``suggested_team_id_at_decision`` if the operator manually overrode the
suggestion). If the alias is missing (a rare edge case), the snapshot falls
back to ``suggested_team_id_at_decision``. Cross-sport binding is treated as
data corruption and the case is skipped.

Usage
=====
    cd backend
    KVOTOLOVAC_DB_PATH=$(pwd)/kvotolovac.db ./venv/bin/python -m \
        tests.snapshot_team_matcher_labels --sport football
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path


def _normalize_identity(text: str | None) -> str:
    """Lightweight reimplementation of ``text_normalizer.normalize_identity_text``
    so this script can run without importing the full app module.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", stripped.lower().replace("_", " "))
    return " ".join(cleaned.split())


def _build_merge_resolver(conn: sqlite3.Connection):
    merge_into = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT id, merged_into_team_id FROM canonical_teams "
            "WHERE merged_into_team_id IS NOT NULL"
        )
    }

    def resolve(team_id, depth: int = 0):
        if team_id is None:
            return None
        if team_id in merge_into and depth < 30:
            return resolve(merge_into[team_id], depth + 1)
        return team_id

    return resolve


def snapshot(db_path: Path, sport: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_out = output_dir / "team_matcher_labels.jsonl"
    decisions_out = output_dir / "team_matcher_labels_decisions.jsonl"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    resolve = _build_merge_resolver(conn)

    alias_to_canonical: dict[tuple[str, str], int | None] = {}
    for row in conn.execute(
        "SELECT bookmaker_id, normalized_alias, canonical_team_id "
        "FROM team_aliases WHERE sport=?",
        (sport,),
    ):
        alias_to_canonical[(row[0], row[1])] = resolve(row[2])

    canonical_sport = {
        row[0]: row[1] for row in conn.execute("SELECT id, sport FROM canonical_teams")
    }

    approved_count = declined_count = skipped_count = 0
    labels: list[dict] = []

    for row in conn.execute(
        """
        SELECT id, bookmaker_id, sport, raw_team_name, normalized_raw_team_name,
               start_time, matched_counterpart_team, reason_code,
               suggested_team_id, suggested_team_name, similarity_score,
               candidate_teams, status, approved_at, declined_at
        FROM team_review_cases
        WHERE sport = ? AND status IN ('approved', 'declined')
        """,
        (sport,),
    ):
        record = dict(row)
        raw = record["raw_team_name"]
        bookmaker = record["bookmaker_id"]
        counterpart = record["matched_counterpart_team"]
        counterpart_canonical_id = None
        if counterpart:
            counterpart_canonical_id = alias_to_canonical.get(
                (bookmaker, _normalize_identity(counterpart))
            )

        try:
            raw_candidates = json.loads(record["candidate_teams"] or "[]")
        except Exception:
            raw_candidates = []
        resolved_candidates = []
        for cand in raw_candidates:
            cand_team_id = resolve(cand.get("team_id"))
            if cand_team_id is None:
                continue
            resolved_candidates.append(
                {
                    "team_id": cand_team_id,
                    "team_name": cand.get("team_name"),
                    "score": cand.get("score"),
                    "matched_alias": cand.get("matched_alias"),
                }
            )

        suggested_resolved = resolve(record["suggested_team_id"])

        label = {
            "case_id": record["id"],
            "bookmaker_id": bookmaker,
            "sport": record["sport"],
            "raw_team_name": raw,
            "normalized_raw_team_name": record["normalized_raw_team_name"],
            "start_time": record["start_time"],
            "matched_counterpart_team": counterpart,
            "matched_counterpart_team_id": counterpart_canonical_id,
            "reason_code": record["reason_code"],
            "suggested_team_id_at_decision": suggested_resolved,
            "suggested_team_name_at_decision": record["suggested_team_name"],
            "similarity_score_at_decision": record["similarity_score"],
            "candidate_teams_at_decision": resolved_candidates,
            "status": record["status"],
        }

        if record["status"] == "approved":
            approved_id = alias_to_canonical.get(
                (bookmaker, _normalize_identity(raw))
            ) or suggested_resolved
            if approved_id is None:
                skipped_count += 1
                continue
            if canonical_sport.get(approved_id) != sport:
                skipped_count += 1
                continue
            label["approved_target_id"] = approved_id
            approved_count += 1
        else:
            label["declined"] = True
            declined_count += 1

        labels.append(label)

    labels.sort(key=lambda r: r["case_id"])

    with raw_out.open("w") as fp:
        for label in labels:
            fp.write(json.dumps(label, ensure_ascii=False) + "\n")
    print(f"Wrote {len(labels):,} labels to {raw_out}")
    print(f"  approved: {approved_count:,}")
    print(f"  declined: {declined_count:,}")
    print(f"  skipped (no alias / cross-sport): {skipped_count:,}")

    by_decision: dict[tuple, list[dict]] = defaultdict(list)
    for label in labels:
        key = (
            label["bookmaker_id"],
            label["raw_team_name"],
            label["start_time"] or "",
            label["matched_counterpart_team"] or "",
        )
        by_decision[key].append(label)

    decisions = []
    for key, cases in by_decision.items():
        rep = cases[0]
        statuses = {c["status"] for c in cases}
        decision = {
            "decision_key": list(key),
            "bookmaker_id": rep["bookmaker_id"],
            "sport": rep["sport"],
            "raw_team_name": rep["raw_team_name"],
            "start_time": rep["start_time"],
            "matched_counterpart_team": rep["matched_counterpart_team"],
            "matched_counterpart_team_id": rep["matched_counterpart_team_id"],
            "reason_code": rep["reason_code"],
            "suggested_team_id_at_decision": rep["suggested_team_id_at_decision"],
            "candidate_teams_at_decision": rep["candidate_teams_at_decision"],
            "status": rep["status"],
            "case_count": len(cases),
            "all_statuses": sorted(statuses),
        }
        if rep["status"] == "approved":
            decision["approved_target_id"] = rep["approved_target_id"]
        else:
            decision["declined"] = True
        decisions.append(decision)

    decisions.sort(key=lambda d: (d["bookmaker_id"], d["raw_team_name"], d["start_time"] or ""))
    with decisions_out.open("w") as fp:
        for decision in decisions:
            fp.write(json.dumps(decision, ensure_ascii=False) + "\n")
    print(f"Wrote {len(decisions):,} decisions to {decisions_out}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        default="kvotolovac.db",
        help="Path to the SQLite DB (default: %(default)s).",
    )
    ap.add_argument(
        "--sport",
        default="football",
        help="Sport to snapshot (default: %(default)s).",
    )
    ap.add_argument(
        "--output-dir",
        default="tests/fixtures",
        help="Directory to write fixtures into (default: %(default)s).",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    snapshot(Path(args.db), args.sport, Path(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

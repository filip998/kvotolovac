"""Replay harness for team-matcher improvements.

Loads the labeled fixture written by `snapshot_labels.py` and runs each labeled
decision through the current matcher code. Emits a side-by-side metrics report
covering BOTH precision (declines correctly removed) and recall (approvals
preserved) — bucketed by candidate path (slot vs global).

Usage
-----
    cd backend && ./venv/bin/python -m tests.team_matcher_replay
    cd backend && ./venv/bin/python -m tests.team_matcher_replay --baseline baseline.json
    cd backend && ./venv/bin/python -m tests.team_matcher_replay --output current.json

The replay calls `search_canonical_team_candidates` (global path) directly. The
slot path requires reconstructing the full RawOddsData/slot context, which is
out of scope for this fast replay — slot-path coverage is provided by the
existing test_normalizer.py tests using real labels from the fixture.

Metrics emitted (per path, plus combined):
    approvals_count                  — total approved decisions replayed
    approval_target_in_top3_rate     — approved canonical present in top-3 candidates
    approval_target_in_top5_rate     — approved canonical present in top-5 candidates
    approval_target_rank1_rate       — approved canonical was the #1 candidate
    declines_count                   — total declined decisions replayed
    decline_suggested_removed_rate   — the previously-suggested team is NOT in current top-3
    no_suggestion_rate               — no candidates returned at all (zero recall floor)
    avg_top1_score                   — sanity check on score distribution
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# Ensure we can import the backend services
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.team_registry import search_canonical_team_candidates


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
DECISIONS_FIXTURE = FIXTURE_DIR / "team_matcher_labels_decisions.jsonl"


@dataclass
class PathMetrics:
    approvals_count: int = 0
    approval_target_in_top3: int = 0
    approval_target_in_top5: int = 0
    approval_target_rank1: int = 0
    declines_count: int = 0
    decline_suggested_removed_top3: int = 0
    decline_suggested_removed_top5: int = 0
    no_suggestion: int = 0
    top1_score_sum: float = 0.0
    top1_score_count: int = 0

    def add_approval(self, approved_id: int, candidate_ids: list[int]) -> None:
        self.approvals_count += 1
        if not candidate_ids:
            self.no_suggestion += 1
            return
        top3 = candidate_ids[:3]
        top5 = candidate_ids[:5]
        if approved_id in top3:
            self.approval_target_in_top3 += 1
        if approved_id in top5:
            self.approval_target_in_top5 += 1
        if top3 and top3[0] == approved_id:
            self.approval_target_rank1 += 1

    def add_decline(self, suggested_id: int | None, candidate_ids: list[int]) -> None:
        self.declines_count += 1
        if not candidate_ids:
            self.no_suggestion += 1
            self.decline_suggested_removed_top3 += 1
            self.decline_suggested_removed_top5 += 1
            return
        top3 = candidate_ids[:3]
        top5 = candidate_ids[:5]
        if suggested_id is None or suggested_id not in top3:
            self.decline_suggested_removed_top3 += 1
        if suggested_id is None or suggested_id not in top5:
            self.decline_suggested_removed_top5 += 1

    def add_score(self, score: float) -> None:
        self.top1_score_sum += score
        self.top1_score_count += 1

    def as_dict(self) -> dict:
        d = asdict(self)
        # Compute rates
        if self.approvals_count > 0:
            d["approval_target_in_top3_rate"] = self.approval_target_in_top3 / self.approvals_count
            d["approval_target_in_top5_rate"] = self.approval_target_in_top5 / self.approvals_count
            d["approval_target_rank1_rate"] = self.approval_target_rank1 / self.approvals_count
        if self.declines_count > 0:
            d["decline_suggested_removed_top3_rate"] = self.decline_suggested_removed_top3 / self.declines_count
            d["decline_suggested_removed_top5_rate"] = self.decline_suggested_removed_top5 / self.declines_count
        total = self.approvals_count + self.declines_count
        if total > 0:
            d["no_suggestion_rate"] = self.no_suggestion / total
        if self.top1_score_count > 0:
            d["avg_top1_score"] = self.top1_score_sum / self.top1_score_count
        return d


def iter_decisions(path: Path) -> Iterable[dict]:
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def run_replay(
    decisions_path: Path,
    *,
    sport: str = "football",
    limit: int | None = None,
    candidates_per_query: int = 5,
) -> dict:
    """Replay each decision through the current matcher; return metrics dict."""
    overall = PathMetrics()
    by_path: dict[str, PathMetrics] = {
        "slot": PathMetrics(),
        "search": PathMetrics(),
    }

    # Track cluster-level metrics so we can detect per-cluster regressions
    by_cluster: dict[str, PathMetrics] = defaultdict(PathMetrics)

    processed = 0
    for d in iter_decisions(decisions_path):
        if d.get("sport") != sport:
            continue
        if limit is not None and processed >= limit:
            break
        processed += 1

        raw = d["raw_team_name"]
        reason_code = d.get("reason_code", "")
        path_key = "slot" if reason_code == "candidate_team_match_same_start_time" else "search"
        cluster_key = _cluster_for_decision(d)

        # Call the current matcher
        try:
            candidates = search_canonical_team_candidates(raw, sport=sport, limit=candidates_per_query)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"error scoring '{raw}': {exc}\n")
            continue
        candidate_ids = [c.team_id for c in candidates]
        top1_score = candidates[0].score if candidates else None

        if d.get("status") == "approved":
            approved = d.get("approved_target_id")
            if approved is None:
                continue
            overall.add_approval(approved, candidate_ids)
            by_path[path_key].add_approval(approved, candidate_ids)
            by_cluster[cluster_key].add_approval(approved, candidate_ids)
        elif d.get("status") == "declined":
            sug = d.get("suggested_team_id_at_decision")
            overall.add_decline(sug, candidate_ids)
            by_path[path_key].add_decline(sug, candidate_ids)
            by_cluster[cluster_key].add_decline(sug, candidate_ids)

        if top1_score is not None:
            overall.add_score(top1_score)
            by_path[path_key].add_score(top1_score)
            by_cluster[cluster_key].add_score(top1_score)

    return {
        "fixture": str(decisions_path),
        "decisions_replayed": processed,
        "overall": overall.as_dict(),
        "by_path": {k: v.as_dict() for k, v in by_path.items()},
        "by_cluster": {k: v.as_dict() for k, v in by_cluster.items()},
    }


def _cluster_for_decision(d: dict) -> str:
    """Heuristic cluster tag matching the labels' origin clusters from cleanup."""
    raw = (d.get("raw_team_name") or "").lower()
    sug = (d.get("suggested_team_name_at_decision") or "").lower()
    sim = d.get("similarity_score_at_decision") or 0.0
    cp = d.get("matched_counterpart_team")

    has_women_marker = any(t in raw for t in ("(ž)", "(w)", " w ", "frauen", "damen", "dff", "feminin"))
    has_youth_marker = any(t in raw for t in ("u19", "u20", "u21", "u23", "m19", "m21", "m23", "youth", "mladi", "primavera"))
    has_reserve_marker = any(t in raw for t in (" ii", " 2 ", " b ", "(r)", "(b)", " reserve", " res.", "(am)"))

    sug_has_women = any(t in sug for t in ("(ž)", " w", "frauen", "damen", "dff", "feminin", "wom."))
    sug_has_youth = any(t in sug for t in ("u19", "u20", "u21", "u23", "youth"))
    sug_has_reserve = any(t in sug for t in (" ii", " 2", " b", "(r)", "(b)", " res.", " reserve"))

    if has_women_marker != sug_has_women: return "type_women_mismatch"
    if has_youth_marker != sug_has_youth: return "type_youth_mismatch"
    if has_reserve_marker != sug_has_reserve: return "type_reserve_mismatch"

    short_sug_traps = {"bra", "van", "asa", "bul", "apollon", "para", "aris", "inter", "goa", "lens", "boise", "asti", "apr", "atlas", "nice", "gas", "cham", "frem", "astana", "hjk", "auda", "sur", "tau", "floya", "pau", "lara", "gais", "asan", "apoel", "bayern", "bari", "eibar", "aue", "lyon", "mura", "mainz", "kobe", "magni", "lulea", "lazio", "mito", "wisla", "cosmos", "aragua", "aluminij", "alga", "erbil", "tokyo", "kari", "honved", "tsc", "skive", "skeid", "pisek", "goias", "ebk", "limonest", "liniers", "penza", "macara", "wilga", "banfield", "bochum", "verdy", "hamburger", "krka", "olimpia", "brno", "dinan", "imst", "wil", "ferro", "hard", "avai", "hercules", "vila", "ura fc", "avila", "marek", "rana", "berga", "boa"}
    if sug and sug.strip().lower() in short_sug_traps and (sim >= 95):
        return "substring_trap"

    if "." in raw and sim >= 85:
        return "period_abbrev"

    return "other"


def render_report(metrics: dict, baseline: dict | None = None) -> str:
    """Render side-by-side text report."""
    lines = []
    lines.append(f"Replay fixture: {metrics['fixture']}")
    lines.append(f"Decisions replayed: {metrics['decisions_replayed']:,}")
    lines.append("")

    def fmt_pct(x):
        if x is None: return "—"
        return f"{x * 100:5.1f}%"

    def fmt_int(x):
        if x is None: return "—"
        return f"{x:>7}"

    keys = [
        ("approvals_count", fmt_int, "Approvals replayed"),
        ("approval_target_in_top3_rate", fmt_pct, "  Target in top-3"),
        ("approval_target_in_top5_rate", fmt_pct, "  Target in top-5"),
        ("approval_target_rank1_rate", fmt_pct, "  Target rank #1"),
        ("declines_count", fmt_int, "Declines replayed"),
        ("decline_suggested_removed_top3_rate", fmt_pct, "  Old suggestion removed from top-3"),
        ("decline_suggested_removed_top5_rate", fmt_pct, "  Old suggestion removed from top-5"),
        ("no_suggestion_rate", fmt_pct, "No-suggestion rate"),
        ("avg_top1_score", lambda x: f"{x:5.1f}" if x is not None else "—", "Avg top-1 score"),
    ]

    def render_section(title: str, data: dict, baseline_data: dict | None) -> list[str]:
        out = [f"=== {title} ==="]
        if baseline_data:
            out.append(f"{'metric':45} {'baseline':>10}  {'current':>10}  {'delta':>8}")
            out.append("-" * 79)
        else:
            out.append(f"{'metric':45} {'current':>10}")
            out.append("-" * 60)
        for key, fmt, label in keys:
            cur = data.get(key)
            cur_s = fmt(cur)
            if baseline_data:
                base = baseline_data.get(key)
                base_s = fmt(base)
                delta = "—"
                if isinstance(cur, (int, float)) and isinstance(base, (int, float)):
                    delta_val = cur - base
                    if "rate" in key or "avg" in key:
                        delta = f"{delta_val * 100:+5.1f}pp" if "rate" in key else f"{delta_val:+5.1f}"
                    else:
                        delta = f"{int(delta_val):+d}"
                out.append(f"{label:45} {base_s:>10}  {cur_s:>10}  {delta:>8}")
            else:
                out.append(f"{label:45} {cur_s:>10}")
        return out

    lines.extend(render_section("OVERALL", metrics["overall"], (baseline or {}).get("overall")))
    lines.append("")
    for path_key in sorted(metrics["by_path"].keys()):
        lines.extend(render_section(
            f"PATH={path_key}",
            metrics["by_path"][path_key],
            ((baseline or {}).get("by_path") or {}).get(path_key),
        ))
        lines.append("")
    lines.append("=== BY CLUSTER (Counts) ===")
    cluster_data = metrics["by_cluster"]
    baseline_clusters = (baseline or {}).get("by_cluster") or {}
    lines.append(f"{'cluster':35} {'app':>5} {'top3_rate':>10} {'dec':>5} {'rem_rate':>10}")
    for cluster_key in sorted(cluster_data.keys(), key=lambda k: -(cluster_data[k].get("approvals_count", 0) + cluster_data[k].get("declines_count", 0))):
        cdata = cluster_data[cluster_key]
        app = cdata.get("approvals_count", 0)
        dec = cdata.get("declines_count", 0)
        top3 = cdata.get("approval_target_in_top3_rate", 0)
        rem = cdata.get("decline_suggested_removed_top3_rate", 0)
        lines.append(f"{cluster_key:35} {app:>5} {fmt_pct(top3):>10} {dec:>5} {fmt_pct(rem):>10}")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions", type=Path, default=DECISIONS_FIXTURE)
    ap.add_argument("--baseline", type=Path, help="Baseline JSON to diff against")
    ap.add_argument("--output", type=Path, help="Write metrics JSON to this path")
    ap.add_argument("--limit", type=int, help="Cap decisions for fast iteration")
    ap.add_argument("--sport", default="football")
    args = ap.parse_args()

    metrics = run_replay(args.decisions, sport=args.sport, limit=args.limit)
    baseline = None
    if args.baseline and args.baseline.exists():
        baseline = json.loads(args.baseline.read_text())

    print(render_report(metrics, baseline))

    if args.output:
        args.output.write_text(json.dumps(metrics, indent=2))
        print(f"\nWrote metrics to {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

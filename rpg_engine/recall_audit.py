"""Audit the active, derived NPR historical recall without replaying the story.

Reports coverage from every available accepted session source and warm lookup
timings on the local GitHub Actions runner. This does NOT measure the latency of
remote GitHub connector calls or the quality of a language model's conclusions.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import statistics
import sys
from time import perf_counter

from . import heuristic_history


def audit(state_dir):
    state = Path(state_dir)
    path = state / heuristic_history.DESTINATION
    t0 = perf_counter()
    index = heuristic_history.load(path)
    loaded = 1000 * (perf_counter() - t0)
    folders = {}
    for item in index["records"]:
        m = re.search(r"session-(\d{3})", item["source"])
        if m:
            folders.setdefault(int(m.group(1)), []).append(item)
    source_numbers = set()
    for p in (state / "archive").glob("session-*"):
        m = re.search(r"session-(\d{3})", p.name)
        if m and (re.fullmatch(r"session-\\d{3}(?:-source)?\\.md", p.name)
                  or re.fullmatch(r"session-\\d{3}-turns\\.jsonl", p.name)):
            source_numbers.add(int(m.group(1)))
    missing = sorted(source_numbers - set(folders))
    # A fixture or OOC-only journal may not contain played historical evidence.
    # Report missing coverage explicitly, but do not invalidate a correctly
    # folded campaign solely because a historical source has no playable turns.
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if sum(int(x["records"]) for x in manifest["shards"]) != index["documents"]:
        raise ValueError("Shard records do not match manifest")
    for group in manifest.get("routes", []):
        route_path = path.parent / group["route"]
        raw = route_path.read_bytes()
        from hashlib import sha256
        if sha256(raw).hexdigest() != group["sha256"]:
            raise ValueError("Search route hash mismatch")
        router = json.loads(raw)
        if sum(int(s["records"]) for s in router["shards"]) != group["records"]:
            raise ValueError("Source-group record count mismatch")
    # Generic probes chosen solely by term rarity and source provenance:
    # one representative from every session, no story-specific terms.
    durations, cases = [], []
    for session, rows in sorted(folders.items()):
        options = []
        for row in rows:
            options.extend((index["df"].get(t, 100000), t, row)
                           for t in set(row["terms"].split())
                           if len(t) >= 6)
        if not options:
            cases.append({"session": session, "status": "no_terms"})
            continue
        _df, token, target = min(options, key=lambda x: (x[0], x[1], x[2]["id"]))
        t1 = perf_counter()
        answer = heuristic_history.query(index, token, limit=10)
        elapsed = 1000 * (perf_counter() - t1)
        durations.append(elapsed)
        hit = any(item["source"] == target["source"] for item in answer["matches"])
        cases.append({"session": session, "query_terms": 1, "source_hit_at_10": hit,
                      "query_ms": round(elapsed, 2)})
    times = sorted(durations)
    return {"schema": "npr-recall-audit/1",
            "documents": index["documents"],
            "indexed_sessions": sorted(folders),
            "source_sessions": sorted(source_numbers),
            "unindexed_source_sessions": missing,
            "source_coverage_ok": not missing,
            "shards": len(manifest["shards"]),
            "groups": len(manifest.get("routes", [])),
            "load_ms": round(loaded, 2),
            "warm_query_median_ms": round(statistics.median(times), 2) if times else None,
            "warm_query_p95_ms": round(times[min(len(times)-1, int(len(times)*.95))], 2)
                                if times else None,
            "generic_probe_hit_at_10": sum(c.get("source_hit_at_10") is True for c in cases),
            "generic_probe_count": len(cases),
            "probes": cases,
            "limitations": [
                "Historical first session without a curated played/void index remains review_required",
                "Source recall is not semantic entailment or NPC witnessed knowledge",
                "Benchmark is warmed local file/cache timing, not remote GitHub network latency",
            ]}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m rpg_engine.recall_audit STATE_DIR", file=sys.stderr)
        return 2
    result = audit(args[0])
    path = Path(args[0]) / "archive/heuristic-recall/audit.json"
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

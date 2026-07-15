"""CLI entry point.

Usage:
    python -m krs_agent investigate <KRS> [--depth N] [--rejestr P|S]
"""
from __future__ import annotations

import argparse
import json
import sys

from . import config
from .graph import build_graph, cross_links, export_html
from .krs_api import KRSClient
from .llm_router import Router
from .red_flags import analyze, findings_to_dicts
from .report import generate_report
from .timeline import build_timeline, timeline_to_dicts


def investigate(krs: str, depth: int, rejestr: str) -> int:
    krs = str(krs).zfill(10)
    case_dir = config.CASES_DIR / krs
    case_dir.mkdir(parents=True, exist_ok=True)
    client = KRSClient(evidence_dir=case_dir / "evidence")
    router = Router(
        api_key=config.OPENROUTER_API_KEY,
        ladder=config.MODEL_LADDER,
        app_title=config.OPENROUTER_APP_TITLE,
    )
    print(f"[case] {krs} -> {case_dir}", file=sys.stderr)
    if not router.available:
        print("[warn] OPENROUTER_API_KEY empty — deterministic report only", file=sys.stderr)

    # 1. connection graph (fetches current extracts, recursing through
    #    corporate shareholders/predecessors)
    graph, facts_by_krs = build_graph(client, krs, depth=depth)
    facts = facts_by_krs.get(krs, {})
    links = cross_links(graph)
    print(
        f"[graph] {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, "
        f"{len(links)} cross-links",
        file=sys.stderr,
    )

    # 2. full-history timeline for the root entity
    full = client.get_extract(krs, full=True, rejestr=rejestr)
    events = build_timeline(full)
    (case_dir / "timeline.json").write_text(
        json.dumps(timeline_to_dicts(events), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"[timeline] {len(events)} events reconstructed", file=sys.stderr)

    # 3. rule-based red flags
    findings = analyze(facts, events, links)
    (case_dir / "findings.json").write_text(
        json.dumps(findings_to_dicts(findings), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"[red-flags] {len(findings)} findings", file=sys.stderr)

    # 4. graph visualization
    if export_html(graph, case_dir / "graph.html"):
        print(f"[graph] visualization -> {case_dir / 'graph.html'}", file=sys.stderr)

    # 5. LLM-narrated, citation-preserving memo (cost-routed)
    memo, triage = generate_report(router, facts, findings, events, links)
    (case_dir / "report.md").write_text(memo, encoding="utf-8")
    (case_dir / "triage.json").write_text(
        json.dumps(triage, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    if router.attempts:
        print(f"[router] summary: {json.dumps(router.summary())}", file=sys.stderr)
    print(f"\nReport: {case_dir / 'report.md'}")
    print(memo)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="krs_agent", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("investigate", help="run a full investigation for a KRS number")
    inv.add_argument("krs", help="KRS number, e.g. 0000006865")
    inv.add_argument("--depth", type=int, default=1, help="graph expansion depth (default 1)")
    inv.add_argument("--rejestr", choices=["P", "S"], default="P", help="register type")
    args = parser.parse_args(argv)
    return investigate(args.krs, args.depth, args.rejestr)


if __name__ == "__main__":
    raise SystemExit(main())

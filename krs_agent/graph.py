"""Connection graph: companies <-> people <-> addresses, expanded recursively
through corporate shareholders/predecessors that have their own KRS numbers."""
from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

from .entities import collect_people, collect_related_entities, company_facts
from .krs_api import KRSClient, KRSNotFound


def build_graph(client: KRSClient, root_krs: str, depth: int = 1) -> tuple[nx.Graph, dict]:
    """BFS from the root entity. Returns the graph and {krs: facts}."""
    g = nx.Graph()
    facts_by_krs: dict[str, dict] = {}
    queue: list[tuple[str, int]] = [(str(root_krs).zfill(10), 0)]
    seen: set[str] = set()

    while queue:
        krs, level = queue.pop(0)
        if krs in seen:
            continue
        seen.add(krs)
        try:
            current = client.get_extract(krs, full=False)
        except KRSNotFound:
            print(f"[graph] KRS {krs} not found, skipping", file=sys.stderr)
            continue

        dane = current.get("odpis", {}).get("dane", {})
        facts = company_facts(current)
        facts_by_krs[krs] = facts

        cid = f"company:{krs}"
        g.add_node(cid, kind="company", label=facts["name"] or krs, krs=krs)

        if facts["address"]:
            aid = f"address:{facts['address'].upper()}"
            g.add_node(aid, kind="address", label=facts["address"])
            g.add_edge(cid, aid, relation="registered at")

        for person in collect_people(dane):
            pid = f"person:{person.key}"
            g.add_node(pid, kind="person", label=person.name, identifier=person.identifier)
            g.add_edge(cid, pid, relation=", ".join(person.roles))

        for rel in collect_related_entities(dane):
            if rel.krs == krs:
                continue
            rid = f"company:{rel.krs}"
            g.add_node(rid, kind="company", label=rel.name, krs=rel.krs)
            g.add_edge(cid, rid, relation=rel.relation)
            if level < depth:
                queue.append((rel.krs, level + 1))

    return g, facts_by_krs


def cross_links(g: nx.Graph) -> list[dict]:
    """People or addresses connected to more than one company."""
    links = []
    for node, data in g.nodes(data=True):
        if data.get("kind") not in ("person", "address"):
            continue
        companies = [n for n in g.neighbors(node) if g.nodes[n].get("kind") == "company"]
        if len(companies) > 1:
            links.append(
                {
                    "kind": data["kind"],
                    "label": data["label"],
                    "companies": [g.nodes[c]["label"] for c in companies],
                }
            )
    return links


def export_html(g: nx.Graph, path: Path) -> bool:
    try:
        from pyvis.network import Network
    except ImportError:
        print("[graph] pyvis not installed, skipping HTML export", file=sys.stderr)
        return False

    colors = {"company": "#1f77b4", "person": "#d62728", "address": "#2ca02c"}
    net = Network(height="800px", width="100%", directed=False, cdn_resources="in_line")
    for node, data in g.nodes(data=True):
        net.add_node(
            node,
            label=data.get("label", node),
            color=colors.get(data.get("kind"), "#999"),
            shape="dot" if data.get("kind") == "company" else "triangle",
            title=f"{data.get('kind')}: {data.get('label')}",
        )
    for a, b, data in g.edges(data=True):
        net.add_edge(a, b, title=data.get("relation", ""))
    net.write_html(str(path), open_browser=False, notebook=False)
    return True

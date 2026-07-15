"""Reconstruct a chronological event log from a full KRS extract (OdpisPelny).

The full extract encodes history as versioned lists: every value carries
``nrWpisuWprow`` (the registry entry that introduced it) and, when it was
superseded, ``nrWpisuWykr`` (the entry that struck it out). The header
``naglowekP.wpis`` maps entry numbers to dates. Joining the two yields a
complete, citable timeline of everything that ever changed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict

VERSION_KEYS = {"nrWpisuWprow", "nrWpisuWykr"}

SECTION_LABELS = {
    "dzial1": "Section 1 (identity/capital/owners)",
    "dzial2": "Section 2 (representation/board)",
    "dzial3": "Section 3 (activity/financial filings)",
    "dzial4": "Section 4 (tax arrears/claims)",
    "dzial5": "Section 5 (receivership)",
    "dzial6": "Section 6 (liquidation/insolvency/restructuring)",
}


@dataclass
class Event:
    date: str  # ISO yyyy-mm-dd (may be "" when the entry header is missing)
    entry_no: str
    action: str  # introduced | struck_out
    section: str
    path: str
    value: str

    def cite(self) -> str:
        return f"[wpis {self.entry_no}, {self.date or 'date unknown'}]"


def _entry_dates(odpis: dict) -> dict[str, dict]:
    out = {}
    for w in (odpis.get("naglowekP") or {}).get("wpis", []) or []:
        no = str(w.get("numerWpisu", "")).strip()
        date = w.get("dataWpisu", "")
        if "." in date:  # dd.mm.yyyy -> ISO
            d, m, y = date.split(".")
            date = f"{y}-{m}-{d}"
        out[no] = {"date": date, "opis": w.get("opis", "")}
    return out


def _summarize(node: dict) -> str:
    payload = {k: v for k, v in node.items() if k not in VERSION_KEYS}
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return text[:400]


def _walk(node, path: str, events: list[tuple[str, str, str, str]]):
    """Collect (entry_no, action, path, value) tuples from versioned dicts."""
    if isinstance(node, dict):
        if "nrWpisuWprow" in node:
            value = _summarize(node)
            events.append((str(node["nrWpisuWprow"]), "introduced", path, value))
            if node.get("nrWpisuWykr"):
                events.append((str(node["nrWpisuWykr"]), "struck_out", path, value))
        for k, v in node.items():
            if k not in VERSION_KEYS:
                _walk(v, f"{path}.{k}", events)
    elif isinstance(node, list):
        for item in node:
            _walk(item, path, events)


def build_timeline(full_extract: dict) -> list[Event]:
    odpis = full_extract.get("odpis", full_extract)
    dates = _entry_dates(odpis)
    raw: list[tuple[str, str, str, str]] = []
    _walk(odpis.get("dane", {}), "dane", raw)

    events = []
    for entry_no, action, path, value in raw:
        meta = dates.get(entry_no, {})
        section = path.split(".")[1] if "." in path else path
        events.append(
            Event(
                date=meta.get("date", ""),
                entry_no=entry_no,
                action=action,
                section=SECTION_LABELS.get(section, section),
                path=path,
                value=value,
            )
        )
    events.sort(key=lambda e: (e.date or "9999", int(e.entry_no) if e.entry_no.isdigit() else 0))
    return events


def timeline_to_dicts(events: list[Event]) -> list[dict]:
    return [asdict(e) for e in events]

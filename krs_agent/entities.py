"""Extract people, corporate shareholders and key facts from a current extract.

Parsers are deliberately defensive/generic: the KRS schema differs by legal
form (S.A. vs sp. z o.o. vs foundations), so instead of hard-coding every
variant we walk the tree and recognize person-shaped dicts (``nazwisko``)
and entity-shaped dicts (``krs``/``nazwa``) wherever they appear.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Person:
    name: str
    identifier: str  # PESEL or birth date as published in KRS
    roles: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.name}|{self.identifier}" if self.identifier else self.name


@dataclass
class RelatedEntity:
    name: str
    krs: str
    relation: str


def _text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for v in value.values():
            t = _text(v)
            if t:
                return t
    if isinstance(value, list) and value:
        return _text(value[0])
    return ""


def _role_for_path(path: str) -> str:
    p = path.lower()
    if "organnadzoru" in p:
        return "supervisory board"
    if "prokurenci" in p:
        return "commercial proxy (prokurent)"
    if "reprezentacja" in p or "sklad" in p:
        return "management board"
    if "wspolnicy" in p or "akcjonariusz" in p:
        return "shareholder"
    if "likwidator" in p:
        return "liquidator"
    return path.rsplit(".", 1)[-1]


def collect_people(dane: dict) -> list[Person]:
    found: dict[str, Person] = {}

    def walk(node, path):
        if isinstance(node, dict):
            if "nazwisko" in node:
                surname = _text(node.get("nazwisko"))
                first = _text(node.get("imiona") or node.get("imie"))
                name = f"{first} {surname}".strip()
                ident = _text(node.get("identyfikator") or node.get("pesel"))
                if name:
                    role = _role_for_path(path)
                    person = found.setdefault(
                        f"{name}|{ident}", Person(name=name, identifier=ident)
                    )
                    if role not in person.roles:
                        person.roles.append(role)
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(dane, "dane")
    return list(found.values())


def collect_related_entities(dane: dict) -> list[RelatedEntity]:
    """Corporate parties (shareholders, merged/acquired entities) that carry a
    KRS number of their own — these are the recursion points for the graph."""
    found: dict[str, RelatedEntity] = {}

    def walk(node, path):
        if isinstance(node, dict):
            krs = _text(node.get("krs"))
            name = _text(node.get("nazwa"))
            if krs and name and krs.isdigit():
                found.setdefault(
                    krs.zfill(10),
                    RelatedEntity(name=name, krs=krs.zfill(10), relation=_role_for_path(path)),
                )
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(dane, "dane")
    return list(found.values())


def company_facts(current_extract: dict) -> dict:
    odpis = current_extract.get("odpis", current_extract)
    dane = odpis.get("dane", {})
    d1 = dane.get("dzial1", {})
    podmiot = d1.get("danePodmiotu", {})
    adres = (d1.get("siedzibaIAdres") or {}).get("adres", {})
    naglowek = odpis.get("naglowekA") or odpis.get("naglowekP") or {}

    address = ", ".join(
        str(adres.get(k, "")).strip()
        for k in ("ulica", "nrDomu", "kodPocztowy", "miejscowosc")
        if adres.get(k)
    )
    pkd_main = ""
    d3 = dane.get("dzial3", {})
    main = (d3.get("przedmiotDzialalnosci") or {}).get("przedmiotPrzewazajacejDzialalnosci")
    if main:
        pkd_main = _text(main)

    return {
        "krs": naglowek.get("numerKRS", ""),
        "name": _text(podmiot.get("nazwa")),
        "legal_form": _text(podmiot.get("formaPrawna")),
        "nip": ((podmiot.get("identyfikatory") or {}).get("nip", "")),
        "regon": ((podmiot.get("identyfikatory") or {}).get("regon", "")),
        "registered": naglowek.get("dataRejestracjiWKRS", ""),
        "last_entry_no": naglowek.get("numerOstatniegoWpisu", ""),
        "address": address,
        "pkd_main": pkd_main,
        "has_section4": bool(dane.get("dzial4")),
        "has_section5": bool(dane.get("dzial5")),
        "has_section6": bool(dane.get("dzial6")),
    }

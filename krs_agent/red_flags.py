"""Deterministic fraud-indicator rules over the timeline and graph.

Each finding carries citations (KRS entry numbers + dates) so every claim in
the final memo can be traced back to a registry entry — chain of evidence.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import date, datetime

from .timeline import Event


@dataclass
class Finding:
    rule: str
    severity: str  # low | medium | high
    description: str
    citations: list[str] = field(default_factory=list)


def _events_on(events: list[Event], path_fragment: str, action: str = "introduced") -> list[Event]:
    return [e for e in events if path_fragment in e.path and e.action == action]


def _years_between(iso_from: str, iso_to: str | None = None) -> float:
    try:
        start = datetime.strptime(iso_from[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0.0
    end = date.today() if iso_to is None else datetime.strptime(iso_to[:10], "%Y-%m-%d").date()
    return (end - start).days / 365.25


def analyze(facts: dict, events: list[Event], links: list[dict]) -> list[Finding]:
    findings: list[Finding] = []

    # --- identity instability ---------------------------------------------
    name_changes = _events_on(events, "danePodmiotu.nazwa")
    if len(name_changes) >= 3:
        findings.append(
            Finding(
                rule="frequent_name_changes",
                severity="medium",
                description=f"Podmiot zmieniał nazwę {len(name_changes) - 1} raz(y).",
                citations=[e.cite() for e in name_changes],
            )
        )

    address_changes = _events_on(events, "siedzibaIAdres.adres")
    if len(address_changes) >= 4:
        findings.append(
            Finding(
                rule="frequent_address_changes",
                severity="medium",
                description=f"Adres siedziby zmieniał się {len(address_changes) - 1} raz(y).",
                citations=[e.cite() for e in address_changes],
            )
        )

    # --- board churn --------------------------------------------------------
    board_events = [
        e
        for e in events
        if e.path.startswith("dane.dzial2")
        and ("sklad" in e.path or "prokurenci" in e.path or "nazwisko" in e.value)
    ]
    by_year = Counter(e.date[:4] for e in board_events if e.date)
    churn_years = {y: n for y, n in by_year.items() if n >= 8}
    if churn_years:
        cites = []
        for year in sorted(churn_years):
            cites += [e.cite() for e in board_events if e.date[:4] == year][:2]
        findings.append(
            Finding(
                rule="board_churn",
                severity="high",
                description=(
                    "Nietypowo intensywne zmiany w reprezentacji/zarządzie: "
                    + ", ".join(f"{n} zmian w {y}" for y, n in sorted(churn_years.items()))
                ),
                citations=cites[:16],
            )
        )

    # --- capital engineering -------------------------------------------------
    capital_changes = _events_on(events, "kapital.wysokoscKapitalu")
    if len(capital_changes) >= 6:
        findings.append(
            Finding(
                rule="frequent_capital_changes",
                severity="medium",
                description=f"Wysokość kapitału zakładowego zmieniała się {len(capital_changes) - 1} raz(y).",
                citations=[e.cite() for e in capital_changes][:12],
            )
        )

    # --- activity pivot (VAT-carousel pattern) -------------------------------
    pkd_main_changes = _events_on(events, "przedmiotPrzewazajacejDzialalnosci")
    if len(pkd_main_changes) >= 2:
        findings.append(
            Finding(
                rule="main_activity_pivot",
                severity="high",
                description=(
                    f"Deklarowany główny przedmiot działalności (PKD) zmieniał się "
                    f"{len(pkd_main_changes) - 1} raz(y) — nagłe zmiany profilu działalności "
                    "są znanym wskaźnikiem oszustw karuzelowych VAT."
                ),
                citations=[e.cite() for e in pkd_main_changes],
            )
        )

    # --- distress sections ----------------------------------------------------
    if facts.get("has_section4"):
        cites = [e.cite() for e in events if e.path.startswith("dane.dzial4")][:8]
        findings.append(
            Finding(
                rule="tax_arrears_or_claims",
                severity="high",
                description="Występują wpisy w dziale 4 (zaległości podatkowe/ZUS, wierzytelności egzekwowane).",
                citations=cites,
            )
        )
    section6 = [e for e in events if e.path.startswith("dane.dzial6")]
    insolvency = [e for e in section6 if any(w in e.value.upper() for w in ("UPADŁO", "LIKWIDAC", "RESTRUKTURYZAC"))]
    if insolvency:
        findings.append(
            Finding(
                rule="insolvency_liquidation",
                severity="high",
                description="Dział 6 zawiera wpisy o likwidacji/upadłości/restrukturyzacji.",
                citations=[e.cite() for e in insolvency][:8],
            )
        )

    # --- young entity -----------------------------------------------------------
    registered = facts.get("registered", "")
    if registered:
        iso = "-".join(reversed(registered.split("."))) if "." in registered else registered
        age = _years_between(iso)
        if 0 < age < 2:
            findings.append(
                Finding(
                    rule="young_entity",
                    severity="low",
                    description=f"Podmiot zarejestrowano zaledwie {age:.1f} roku/lat temu ({registered}).",
                    citations=[f"[rejestracja, {registered}]"],
                )
            )

    # --- network red flags --------------------------------------------------------
    for link in links:
        if link["kind"] == "person" and len(link["companies"]) >= 3:
            findings.append(
                Finding(
                    rule="person_in_many_entities",
                    severity="medium",
                    description=(
                        f"{link['label']} występuje w {len(link['companies'])} zebranych podmiotach: "
                        + ", ".join(link["companies"][:6])
                    ),
                    citations=["[analiza sieci zebranych wypisów]"],
                )
            )
        if link["kind"] == "address" and len(link["companies"]) >= 3:
            findings.append(
                Finding(
                    rule="shared_address_cluster",
                    severity="medium",
                    description=(
                        f"Adres {link['label']} współdzielony przez {len(link['companies'])} podmiotów "
                        "(możliwe wirtualne biuro)."
                    ),
                    citations=["[analiza sieci zebranych wypisów]"],
                )
            )

    return findings


def findings_to_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]

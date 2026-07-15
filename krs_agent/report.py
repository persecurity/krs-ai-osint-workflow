"""Investigation memo generation.

Two LLM stages, both cost-routed:

1. **Triage** (JSON, validated) — risk classification and choice of themes to
   narrate. The tiny model usually handles this; malformed JSON or a
   self-reported ESCALATE bumps it up the ladder.
2. **Memo** (markdown) — the court-style narrative. The router demands that
   every claim keeps its ``[wpis N, date]`` citation; a post-validator checks
   citations survived, otherwise the next tier takes over.

Without an API key the pipeline still emits a deterministic, fully cited
report — the LLM narrative is an overlay, never the source of facts.
"""
from __future__ import annotations

import json

from .llm_router import Router, LLMError, json_validator, parse_json
from .red_flags import Finding
from .timeline import Event

TRIAGE_PROMPT = """Jesteś asystentem analityka śledczego. Na podstawie danych o spółce
oraz wyników reguł wykrywających sygnały ostrzegawcze (red flags) z Krajowego
Rejestru Sądowego (KRS), zwróć obiekt JSON z kluczami:
  "risk" — jedno z: "low", "medium", "high",
  "rationale" — 2-3 zdania PO POLSKU,
  "themes" — lista maks. 4 krótkich fraz PO POLSKU nazywających wątki śledztwa warte opisania.
Odpowiedz wyłącznie obiektem JSON. Wartości tekstowe muszą być w języku polskim.

FAKTY:
{facts}

WYNIKI ANALIZY:
{findings}
"""

MEMO_PROMPT = """Piszesz notatkę wywiadu gospodarczego (memo) dla zespołu śledczego
(odbiorcy: prawnicy i regulatorzy). Napisz WYŁĄCZNIE PO POLSKU, w formacie markdown,
z sekcjami: Executive Summary; Profil podmiotu; Najważniejsze zdarzenia z historii
rejestru; Sygnały ostrzegawcze i ocena ryzyka; Obserwacje sieciowe; Ograniczenia analizy.

ŚCISŁA ZASADA DOWODOWA: każde stwierdzenie faktyczne musi zachować swoje odniesienie
w formie [wpis N, RRRR-MM-DD] dokładnie tak, jak podano w danych wejściowych. Nie
wymyślaj faktów, wpisów ani dat. Jeśli dane nie potwierdzają jakiegoś stwierdzenia,
nie formułuj go. Nazwy własne (spółka, osoby, adresy) pozostaw w oryginalnym brzmieniu.

OCENA RYZYKA (z wcześniejszej analizy): {triage}

FAKTY O SPÓŁCE:
{facts}

WYNIKI ANALIZY RED FLAGS (z cytowaniami):
{findings}

FRAGMENT OSI CZASU (chronologicznie, skrócony):
{timeline}

POWIĄZANIA MIĘDZY PODMIOTAMI:
{links}
"""


def _memo_validator(content: str) -> tuple[bool, str]:
    if len(content) < 400:
        return False, "memo suspiciously short"
    if "[wpis" not in content and "[registration" not in content and "[rejestracja" not in content:
        return False, "memo lost its citations"
    polish_markers = ("ą", "ę", "ł", "ś", "ż", "ź", "ć", "ń", "óż", " i ", " z ", " w ")
    if not any(m in content.lower() for m in polish_markers):
        return False, "memo does not appear to be in Polish"
    return True, ""


def _condense_timeline(events: list[Event], limit: int = 120) -> str:
    lines = [
        f"- {e.date or '????'} wpis {e.entry_no} [{e.section}] {e.action}: {e.value[:160]}"
        for e in events
    ]
    if len(lines) > limit:
        head, tail = lines[: limit // 2], lines[-limit // 2:]
        lines = head + [f"... ({len(lines) - limit} events omitted) ..."] + tail
    return "\n".join(lines)


def deterministic_report(facts: dict, findings: list[Finding], links: list[dict]) -> str:
    out = [f"# Raport z analizy: {facts.get('name', '?')} (KRS {facts.get('krs', '?')})", ""]
    out.append(f"- Forma prawna: {facts.get('legal_form')}  ")
    out.append(f"- NIP: {facts.get('nip')} | REGON: {facts.get('regon')}  ")
    out.append(f"- Data rejestracji: {facts.get('registered')} | Adres: {facts.get('address')}  ")
    out.append("")
    out.append("## Sygnały ostrzegawcze (red flags)")
    if not findings:
        out.append("Nie wykryto sygnałów ostrzegawczych na podstawie reguł.")
    for f in findings:
        out.append(f"- **[{f.severity.upper()}] {f.rule}** — {f.description} {' '.join(f.citations[:6])}")
    if links:
        out.append("")
        out.append("## Powiązania między podmiotami")
        for l in links:
            out.append(f"- {l['kind']}: {l['label']} ↔ {', '.join(l['companies'])}")
    return "\n".join(out)


def generate_report(
    router: Router,
    facts: dict,
    findings: list[Finding],
    events: list[Event],
    links: list[dict],
) -> tuple[str, dict]:
    """Returns (markdown_report, triage_dict)."""
    findings_json = json.dumps(
        [f.__dict__ for f in findings], ensure_ascii=False, indent=1, default=str
    )
    fallback = deterministic_report(facts, findings, links)
    if not router.available:
        return (
            fallback + "\n\n> Podsumowanie LLM pominięte: brak OPENROUTER_API_KEY._",
            {"risk": "unrated", "rationale": "LLM niedostępny", "themes": []},
        )

    try:
        triage_raw = router.run(
            task="triage",
            messages=[{
                "role": "user",
                "content": TRIAGE_PROMPT.format(
                    facts=json.dumps(facts, ensure_ascii=False), findings=findings_json
                ),
            }],
            validator=json_validator(["risk", "rationale", "themes"]),
            json_mode=True,
        )
        triage = parse_json(triage_raw)
    except LLMError as exc:
        triage = {"risk": "unrated", "rationale": f"ocena wstępna nieudana: {exc}", "themes": []}

    try:
        memo = router.run(
            task="memo",
            messages=[{
                "role": "user",
                "content": MEMO_PROMPT.format(
                    triage=json.dumps(triage, ensure_ascii=False),
                    facts=json.dumps(facts, ensure_ascii=False),
                    findings=findings_json,
                    timeline=_condense_timeline(events),
                    links=json.dumps(links, ensure_ascii=False),
                ),
            }],
            validator=_memo_validator,
            # narrative writing is the hard task: start one tier up if we have >1 tier
            start_tier=0,
        )
    except LLMError as exc:
        memo = fallback + f"\n\n> _Podsumowanie LLM nieudane na wszystkich poziomach: {exc}_"

    return memo, triage

# KRS Investigator

Agent AI OSINT do śledztw gospodarczych opartych na otwartym API Krajowego
Rejestru Sądowego (KRS).

Dla podanego numeru KRS agent:

1. pobiera wypis aktualny i pełny (każda odpowiedź jest zapisywana,
   haszowana SHA-256 i logowana w `evidence/manifest.jsonl` — jako element chain of custody),
2. odtwarza pełną **oś czasu** zmian w rejestrze (numer wpisu + data przy
   każdym zdarzeniu),
3. buduje **graf powiązań** (spółki ↔ osoby ↔ adresy), rekurencyjnie
   przechodząc przez wspólników oraz powiązane podmioty będące spółkami,
4. uruchamia opartą na regułach **analizę sygnałów ostrzegawczych** (rotacja
   zarządu, zmiany nazwy/adresu, zmiany PKD, wpisy o
   upadłości/likwidacji, klastry współdzielonych adresów i osób),
5. tworzy **notatkę w formie memo** podsumowaną przez LLM zgodnie ze
   ścisłą zasadą zachowania cytowań, wraz z interaktywnym `graph.html`.

## Instalacja

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # następnie wklej swój klucz OpenRouter
```

## Użycie

```bash
.venv/bin/python -m krs_agent investigate 0000006865 --depth 1
```

Flaga `--depth` określa, jak głęboko graf powiązań (`krs_agent/graph.py`) ma
rekurencyjnie sprawdzać powiązane podmioty (wspólników, poprzedników
prawnych, podmioty przejęte) posiadające własny numer KRS:

- `--depth 0` — pobiera tylko podmiot główny (zarząd, wspólnicy, adres),
  bez przechodzenia do innych spółek,
- `--depth 1` (domyślnie) — dodatkowo pobiera każdy powiązany podmiot
  korporacyjny znaleziony w danych podmiotu głównego (jeden „skok" dalej),
- `--depth N` — kontynuuje ekspansję o N skoków od podmiotu głównego.

Każdy kolejny poziom mnoży liczbę zapytań do API (i rozmiar plików sprawy)
przez liczbę powiązanych podmiotów na danej warstwie — warto zwiększać
głębokość tylko wtedy, gdy faktycznie potrzebne jest śledzenie łańcuchów
własności/powiązań dalej niż jeden krok.

Wyniki trafiają do `cases/<krs>/`: `report.md`, `timeline.json`,
`findings.json`, `triage.json`, `graph.html`, `evidence/`.

Bez klucza API pipeline nadal generuje deterministyczny, w pełni cytowany
raport — raport LLM jest jedynie nakładką w celu podsumowania, **nigdy źródłem faktów**.

Raport końcowy jest zawsze generowany w języku polskim (dotyczy to zarówno
wariantu deterministycznego, jak i w podsumowaniu przez LLM).

## Przykład
Przykład takiego raportu znajduje się w folderze `/cases`.

Folder zawiera przykładowy wynik analizy dla spółki CD PROJEKT S.A. o numerze KRS 0000006865
(`cases/0000006865/`):

- `report.md` — finalna notatka(executive summary,
  profil podmiotu, historia zmian w rejestrze, red flags, ocena
  ryzyka)
- `triage.json` — skrócona ocena ryzyka (`risk`, uzasadnienie, główne motywy
  ryzyka).
- `findings.json` — dane strukturalne z wykrytymi red flagami
- `timeline.json` — pełna chronologia wpisów w KRS (zmiany zarządu, kapitału,
  adresu, nazwy, PKD).
- `graph.html` — interaktywna wizualizacja grafu
  powiązań/sieci.
- `evidence/` — surowe dane źródłowe: pełny i aktualny odpis z KRS
  (`OdpisPelny_*.json`, `OdpisAktualny_*.json`) oraz manifest pobranych
  dowodów (`manifest.jsonl`).

## Routing modeli pod kątem kosztów (OpenRouter)

Każde zadanie LLM zaczyna od **najtańszego** modelu z drabinki zdefiniowanej
w `OPENROUTER_MODELS` (`.env`), np.:

```
google/gemini-2.5-flash-lite → anthropic/claude-haiku-4.5 → anthropic/claude-sonnet-4.5
```

Router (`krs_agent/llm_router.py`) eskaluje do kolejnego poziomu, gdy:

- model **sam zgłasza**, że nie jest w stanie obsłużyć zadania (jest
  poinstruowany, by odpowiedzieć `ESCALATE: <powód>` zamiast zgadywać —
  najwyższy poziom nie ma już takiej furtki),
- odpowiedź **nie przechodzi walidacji** (niepoprawny JSON w ocenie wstępnej;
  memo, które utraciło cytowania `[wpis N, data]`, jest podejrzanie krótkie
  albo nie jest po polsku),
- wywołanie zawiedzie na poziomie **API**.

Każda próba jest logowana wraz z modelem, wynikiem, liczbą tokenów i kosztem;
podsumowanie wypisuje się na końcu każdego uruchomienia:

```
[router] triage: google/gemini-2.5-flash-lite -> ok [210 tok, 1.4s]
[router] memo: google/gemini-2.5-flash-lite -> escalated_invalid (memo lost its citations)
[router] memo: anthropic/claude-haiku-4.5 -> ok [1834 tok, 11.2s]
[router] summary: {"attempts": 3, "escalations": 1, "total_cost_usd": 0.0041, ...}
```

## Źródło danych

Oficjalne otwarte API Ministerstwa Sprawiedliwości:
`https://api-krs.ms.gov.pl/api/krs/Odpis{Aktualny|Pelny}/{krs}?rejestr={P|S}&format=json`

Uwagi: wyszukiwanie odbywa się wyłącznie po numerze KRS (brak wyszukiwania
po nazwie); podmioty wykreślone z rejestru zwracają HTTP 204 i są pomijane
z odpowiednią adnotacją.

"""KRS open API client with a forensic evidence store.

Every raw response is written to disk exactly as received, hashed (SHA-256)
and logged in ``manifest.jsonl`` — chain-of-evidence style. Repeat requests
are served from the store so the ministry API is hit at most once per
document per case.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import KRS_API_BASE


class KRSNotFound(Exception):
    pass


class KRSClient:
    def __init__(self, evidence_dir: Path, delay_s: float = 0.5):
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self.evidence_dir / "manifest.jsonl"
        self.delay_s = delay_s
        self._last_request = 0.0

    def get_extract(self, krs: str, full: bool = False, rejestr: str = "P") -> dict:
        krs = str(krs).strip().zfill(10)
        kind = "OdpisPelny" if full else "OdpisAktualny"
        path = self.evidence_dir / f"{kind}_{krs}.json"
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))

        url = f"{KRS_API_BASE}/{kind}/{krs}?rejestr={rejestr}&format=json"
        raw = self._fetch(url)
        if raw is None and rejestr == "P":
            # entity may be registered as an association/foundation
            url = f"{KRS_API_BASE}/{kind}/{krs}?rejestr=S&format=json"
            raw = self._fetch(url)
        if raw is None:
            raise KRSNotFound(
                f"KRS {krs} not available (never registered, or struck off — API returns 204)"
            )

        path.write_bytes(raw)
        record = {
            "file": path.name,
            "url": url,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return json.loads(raw.decode("utf-8"))

    def _fetch(self, url: str) -> bytes | None:
        wait = self.delay_s - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()
        resp = httpx.get(url, timeout=60, follow_redirects=True)
        # 404 = wrong register, 204 = entity struck off the register
        if resp.status_code in (404, 204) or not resp.content:
            return None
        resp.raise_for_status()
        return resp.content

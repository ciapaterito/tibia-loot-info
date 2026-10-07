#!/usr/bin/env python3
"""
Generator danych dla Tibia Loot Info.

Pobiera (TibiaData v4 + TibiaWiki/Fandom) i zapisuje obok strony:

  data/meta.json       data generowania, liczniki, boosted creature
  data/creatures.json  katalog mobów (nazwa, race, liczba mnoga, obrazek)
  data/loot.json       race -> lista itemów (TibiaData + uzupełnienie z wiki)
  data/items.json      itemy: NPC, miasta, ceny sprzedaży + obrazek
  data/state.json      stan roboty (kanoniczne nazwy, daty odświeżeń) - strona go nie czyta
  data/img/mob/*.gif   grafiki mobów
  data/img/item/*.gif  grafiki itemów

Uruchomienie lokalne:   python scripts/build_data.py
Test na kilku mobach:   python scripts/build_data.py --limit 20
Pełne odświeżenie:      python scripts/build_data.py --full

Zasady:
  * Przyrostowo: itemy odświeżane są, gdy ich brakuje albo są starsze niż --max-age-days.
  * Nigdy nie pogarsza danych: gdy pobranie się nie uda, zostaje poprzednia wersja.
  * Kończy się kodem 1 tylko przy awarii krytycznej (katalog mobów / loot z TibiaData).
    Problemy z wiki/obrazkami to ostrzeżenia - strona ma wtedy fallback do API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
TIBIADATA = os.environ.get("TIBIADATA_API", "https://api.tibiadata.com/v4").rstrip("/")
WIKI = os.environ.get("TIBIAWIKI_BASE", "https://tibia.fandom.com").rstrip("/")
_repo = os.environ.get("GITHUB_REPOSITORY")
UA = "TibiaLootInfoDataBuilder/1.0" + (f" (+https://github.com/{_repo})" if _repo else "")
FAST = os.environ.get("TIBIA_FAST") == "1"  # tylko do testów: bez przerw między żądaniami

# minimalny odstęp między żądaniami do jednego hosta (sekundy)
INTERVALS = {"api.tibiadata.com": 0.15, "tibia.fandom.com": 0.40}
DEFAULT_INTERVAL = 0.15

IS_GHA = os.environ.get("GITHUB_ACTIONS") == "true"


def log(*a):
    print(*a, flush=True)


def warn(msg):
    log(f"::warning::{msg}" if IS_GHA else f"WARNING: {msg}")


# --------------------------------------------------------------------------- HTTP
class Throttle:
    def __init__(self, interval):
        self.interval = 0 if FAST else interval
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.interval
        if t > now:
            time.sleep(t - now)


_throttles: dict[str, Throttle] = {}
_throttles_lock = threading.Lock()


def _throttle(host):
    with _throttles_lock:
        if host not in _throttles:
            _throttles[host] = Throttle(INTERVALS.get(host, DEFAULT_INTERVAL))
        return _throttles[host]


class NotFound(Exception):
    pass


def fetch(url, *, tries=4, timeout=40, accept="application/json"):
    th = _throttle(urlparse(url).netloc)
    last = None
    for i in range(tries):
        th.wait()
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urlopen(req, timeout=timeout) as r:
                return r.read()
        except HTTPError as e:
            last = e
            if e.code in (404, 410):
                raise NotFound(f"HTTP {e.code} {url}") from e
            if e.code == 429 or e.code >= 500:
                ra = e.headers.get("Retry-After") if e.headers else None
                delay = float(ra) if ra and ra.isdigit() else 2 ** (i + 1)
                time.sleep(0 if FAST else min(delay, 60))
                continue
            raise  # np. 403 - nie ma sensu ponawiać
        except (URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            time.sleep(0 if FAST else 2 ** (i + 1))
    raise last if last else RuntimeError("fetch failed")


def get_json(url):
    return json.loads(fetch(url).decode("utf-8"))


# ------------------------------------------------------------------ helpers (port 1:1 z index.html)
WS = re.compile(r"\s+")


def slug_key(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def singularize(plural, race):
    n = str(plural).strip()
    if not race or slug_key(n) == race:
        return n
    m = re.match(r"^(.*?)(\S+)$", n)
    head, last = (m.group(1), m.group(2)) if m else ("", n)
    c = []
    if re.search(r"ies$", last, re.I):
        c.append(last[:-3] + "y")
    if re.search(r"ves$", last, re.I):
        c += [last[:-3] + "f", last[:-3] + "fe"]
    if re.search(r"men$", last, re.I):
        c.append(last[:-3] + "man")
    if re.search(r"es$", last, re.I):
        c.append(last[:-2])
    if re.search(r"s$", last, re.I):
        c.append(last[:-1])
    for w in c:
        if slug_key(head + w) == race:
            return head + w
    return n


def clean_npc(n):
    n = str(n or "").replace("\u200b", "").replace("[[", "").replace("]]", "")
    n = WS.sub(" ", n).strip()
    return re.sub(r"\s*\d+$", "", n)


def parse_num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    s = re.sub(r"\s", "", str(v).strip())
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None
    neg = s.startswith("-")
    s = s.replace("-", "")
    c, d = s.count(","), s.count(".")
    if c and d:
        last = max(s.rfind(","), s.rfind("."))
        frac = len(s) - last - 1
        s = (re.sub(r"[.,]", "", s[:last]) + "." + s[last + 1:]) if frac <= 2 else re.sub(r"[.,]", "", s)
    elif c:
        p = s.split(",")
        s = "".join(p) if len(p) > 2 or len(p[1]) == 3 else ".".join(p)
    elif d:
        p = s.split(".")
        s = "".join(p) if len(p) > 2 or len(p[1]) == 3 else s
    try:
        n = float(("-" if neg else "") + s)
    except ValueError:
        return None
    return int(n) if n == int(n) else n


def T(el):
    return WS.sub(" ", el.get_text() if el is not None else "").strip()


SMALL_WORDS = {"of", "the", "a", "an", "and", "in", "on", "to", "for", "with", "from", "at", "by", "or"}


def _cap(w):
    return re.sub(r"^([^A-Za-z]*)([A-Za-z])", lambda m: m.group(1) + m.group(2).upper(), w)


def smart_title(s):
    out = []
    for i, w in enumerate(s.strip().split(" ")):
        lw = w.lower()
        if i > 0 and lw in SMALL_WORDS:
            out.append(lw)
        else:
            out.append("-".join(_cap(p) for p in lw.split("-")))
    return " ".join(out)


def title_candidates(name):
    n = WS.sub(" ", name).strip()
    cands = [n, smart_title(n), n.title(), n[:1].upper() + n[1:]]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


_IRREG = {"men": "man", "women": "woman", "feet": "foot", "teeth": "tooth", "mice": "mouse", "geese": "goose", "knives": "knife", "wolves": "wolf", "leaves": "leaf", "halves": "half", "loaves": "loaf"}


def _sing_word(w):
    """Mozliwe liczby pojedyncze jednego slowa (angielski), od najbardziej prawdopodobnej."""
    lw = w.lower()
    if lw in _IRREG:
        return [_IRREG[lw]]
    out = []
    if lw.endswith("ies") and len(lw) > 4:
        out.append(lw[:-3] + "y")
    if lw.endswith("ves") and len(lw) > 4:
        out += [lw[:-3] + "f", lw[:-3] + "fe"]
    if re.search(r"(s|x|z|ch|sh|o)es$", lw):
        out.append(lw[:-2])
    if lw.endswith("s") and not lw.endswith("ss") and len(lw) > 3:
        out.append(lw[:-1])
    return out


def singular_candidates(name):
    """'bananas' -> ['banana']; 'pieces of cloth' -> ['piece of cloth']; 'amber sickles' -> ['amber sickle']."""
    words = WS.sub(" ", name).strip().split(" ")
    if not words or not words[0]:
        return []
    idx = words.index("of") - 1 if "of" in words[1:] else len(words) - 1
    if idx < 0:
        idx = 0
    res = []
    for sw in _sing_word(words[idx]):
        res.append(" ".join(words[:idx] + [sw] + words[idx + 1:]))
    return res



def file_slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or hashlib.md5(name.encode()).hexdigest()[:8]


# ------------------------------------------------------------------ parsery wiki
def parse_item_html(html):
    """Port fandomItemData() z index.html: tabele NPC (sell/buy) -> lista sprzedawców."""
    soup = BeautifulSoup(html, "html.parser")
    found, head = [], ""
    for el in soup.find_all(["h2", "h3", "h4", "table"]):
        if el.name != "table":
            head = T(el)
            continue
        first = el.select_one("thead tr") or el.find("tr")
        if first is None:
            continue
        hr = [T(c).lower() for c in first.find_all(["th", "td"], recursive=False)]

        def idx(rx):
            for i, x in enumerate(hr):
                if re.search(rx, x):
                    return i
            return -1

        ni, pi, ci = idx(r"npc|seller|buyer"), idx(r"price|value|gold"), idx(r"location|city|town")
        if ni < 0 or pi < 0:
            continue
        cap = el.find("caption")
        ctx = T(cap) + " " + head
        kind = "sell" if re.search("sell", ctx, re.I) else "buy" if re.search("buy", ctx, re.I) else "?"
        for tr in el.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if not cells or all(c.name == "th" for c in cells):
                continue
            tc = [T(c) for c in cells]
            link = None
            if ni < len(cells):
                for a in cells[ni].find_all("a"):
                    if a.has_attr("href") or a.has_attr("title"):
                        link = a
                        break
            n = clean_npc(link.get("title", "")) if link else ""
            if not n and link:
                m = re.search(r"/wiki/([^?#]+)", link.get("href", ""), re.I)
                if m:
                    n = clean_npc(unquote(m.group(1)).replace("_", " "))
            if not n:
                n = clean_npc(tc[ni] if ni < len(tc) else "")
            if n and not re.fullmatch(r"(npc|seller|buyer)", n, re.I):
                city = (tc[ci] or None) if 0 <= ci < len(tc) else None
                price = parse_num(tc[pi]) if pi < len(tc) else None
                found.append({"kind": kind, "name": n, "city": city, "price": price})
    if any(x["kind"] == "sell" for x in found):
        pick = [x for x in found if x["kind"] == "sell"]
    else:
        pick = [x for x in found if x["kind"] != "buy"]
    return merge_sellers(pick)


def merge_sellers(rows):
    m = {}
    for s in rows:
        n = clean_npc(s.get("name"))
        if not n:
            continue
        k = n.lower()
        o = m.get(k)
        m[k] = {
            "name": n,
            "city": s.get("city") or (o or {}).get("city"),
            "price": s["price"] if s.get("price") is not None else (o or {}).get("price"),
        }
    return list(m.values())


LOOT_RE = re.compile(r"\{\{\s*Loot Item\s*\|([^{}]*)\}\}", re.I)
RARITY = re.compile(r"^(always|common|uncommon|semi-?rare|rare|very rare)$", re.I)
NUMERIC = re.compile(r"^[\d\s.,+\-\u2013x*]+$")


def parse_wikitext_loot(wt):
    out = []
    for m in LOOT_RE.finditer(wt or ""):
        parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
        item = next((p for p in parts if not NUMERIC.match(p) and not RARITY.match(p) and "=" not in p), None)
        if item:
            item = item.replace("[[", "").replace("]]", "").strip()
            if item:
                out.append(item)
    return out


# ------------------------------------------------------------------ wiki API
def wiki_query(params):
    q = "&".join(f"{k}={quote(str(v), safe='|')}" for k, v in params.items())
    return get_json(f"{WIKI}/api.php?{q}")


def resolve_titles(d, titles):
    """Mapuje tytuł wejściowy -> strona (po normalizacji i przekierowaniach)."""
    q = d.get("query", {})
    norm = {x["from"]: x["to"] for x in q.get("normalized", [])}
    red = {x["from"]: x["to"] for x in q.get("redirects", [])}
    pages = {p["title"]: p for p in q.get("pages", [])}
    out = {}
    for t in titles:
        tt = norm.get(t, t)
        tt = red.get(tt, tt)
        if tt in pages:
            out[t] = pages[tt]
    return out


def wikitext_batch(titles):
    d = wiki_query({
        "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
        "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": "|".join(titles),
    })
    res = {}
    for t, p in resolve_titles(d, titles).items():
        if p.get("missing") or not p.get("revisions"):
            continue
        rev = p["revisions"][0]
        content = (rev.get("slots", {}).get("main", {}) or {}).get("content") or rev.get("content") or ""
        if content:
            res[t] = content
    return res


def existing_titles_batch(titles):
    """Które z tytułów istnieją na wiki -> {tytuł_wejściowy: kanoniczny_tytuł_strony}."""
    d = wiki_query({"action": "query", "format": "json", "formatversion": 2, "redirects": 1, "titles": "|".join(titles)})
    return {t: p["title"] for t, p in resolve_titles(d, titles).items() if not p.get("missing")}


def fetch_item_page(title):
    """Zwraca (kanoniczny_tytuł, html) albo None gdy strony nie ma."""
    d = wiki_query({
        "action": "parse", "format": "json", "formatversion": 2, "prop": "text", "redirects": 1,
        "disableeditsection": 1, "disablelimitreport": 1, "disabletoc": 1, "page": title.replace(" ", "_"),
    })
    if "error" in d:
        if d["error"].get("code") == "missingtitle":
            return None
        raise RuntimeError(f"wiki error: {d['error'].get('code')}")
    p = d.get("parse") or {}
    t = p.get("text")
    html = t if isinstance(t, str) else (t or {}).get("*", "")
    return (p.get("title") or title, html)


def image_urls_batch(names):
    """Port flushImages(): File:<Nazwa>.gif|png -> url. Zwraca {nazwa: url|''}."""
    titles = [f"File:{n}.{e}" for n in names for e in ("gif", "png")]
    d = wiki_query({
        "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
        "prop": "imageinfo", "iiprop": "url", "titles": "|".join(titles),
    })
    pages = resolve_titles(d, titles)
    res = {}
    for n in names:
        url = ""
        for e in ("gif", "png"):
            p = pages.get(f"File:{n}.{e}")
            if p and p.get("imageinfo"):
                url = p["imageinfo"][0].get("url", "")
                if url:
                    break
        res[n] = url
    return res


# ------------------------------------------------------------------ pliki / obrazki
def read_json(path, default):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True), "utf-8")
    os.replace(tmp, path)


def img_ext(url):
    m = re.search(r"\.(gif|png|jpe?g|webp)(?=/|\?|$)", urlparse(url).path, re.I)
    return (m.group(1).lower().replace("jpeg", "jpg")) if m else None


def looks_like_image(b):
    return b[:4] == b"GIF8" or b[:8] == b"\x89PNG\r\n\x1a\n" or b[:3] == b"\xff\xd8\xff" or (b[:4] == b"RIFF" and b[8:12] == b"WEBP")


def download_image(url, dest_noext: Path):
    ext = img_ext(url)
    if not ext:
        return None
    b = fetch(url, accept="image/*", timeout=40)
    if not looks_like_image(b):
        raise RuntimeError("to nie jest obrazek")
    dest = dest_noext.with_suffix("." + ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b)
    return dest


def existing_image(dir_: Path, stem):
    for e in ("gif", "png", "jpg", "webp"):
        p = dir_ / f"{stem}.{e}"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def pmap(fn, items, workers):
    """Równoległe mapowanie z zachowaniem błędów: zwraca [(item, wynik, wyjątek)]."""
    def run(x):
        try:
            return (x, fn(x), None)
        except Exception as e:  # noqa: BLE001
            return (x, None, e)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(run, items))


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ------------------------------------------------------------------ etapy
def step_catalog():
    d = get_json(f"{TIBIADATA}/creatures")
    c = d.get("creatures") or {}
    rows = list(c.get("creature_list") or [])
    boosted = c.get("boosted")
    by_race = {}
    for x in rows:
        race = str(x.get("race") or "").strip()
        raw = str(x.get("name") or "").strip()
        if not race:
            continue
        if not raw:  # znany błąd TibiaData: zombie ma pustą nazwę
            raw = race.title()
        name = singularize(raw, race)
        by_race[race] = {"n": name, "r": race, "p": raw if name != raw else "", "u": x.get("image_url") or ""}
    if boosted and str(boosted.get("race") or "").strip():
        race = str(boosted["race"]).strip()
        bname = str(boosted.get("name") or "").strip()
        cur = by_race.get(race)
        if cur is None:
            by_race[race] = {"n": singularize(bname or race.title(), race), "r": race, "p": "", "u": boosted.get("image_url") or ""}
        elif bname and bname != cur["n"]:
            # nazwa z "boosted" jest pojedyncza - lepsza, gdy singularize() nie dał rady
            if cur["p"] == "" or slug_key(cur["n"]) != race:
                cur["p"] = cur["p"] or cur["n"]
                cur["n"] = bname
    return by_race, (boosted or {}).get("race") or ""


def fetch_tibiadata_loot(race):
    d = get_json(f"{TIBIADATA}/creature/{quote(race)}")
    c = d.get("creature") or d
    out, seen = [], set()
    for row in c.get("loot_list") or []:
        if isinstance(row, str):
            item = row.strip()
        elif isinstance(row, dict):
            item = str(row.get("name") or row.get("item") or row.get("item_name") or row.get("itemName") or "").strip()
        else:
            item = ""
        if not item or re.fullmatch(r"!?empty", item, re.I) or item.lower() in seen:
            continue
        seen.add(item.lower())
        out.append(item)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "data"), help="katalog wyjściowy (domyślnie data/)")
    ap.add_argument("--full", action="store_true", help="odśwież wszystko (itemy i obrazki)")
    ap.add_argument("--limit", type=int, default=0, help="tylko N pierwszych mobów (test)")
    ap.add_argument("--no-images", action="store_true", help="pomiń pobieranie grafik")
    ap.add_argument("--max-age-days", type=int, default=35, help="po ilu dniach odświeżać dane itemu")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--budget-min", type=float, default=150, help="limit czasu na pobieranie itemów/grafik")
    ap.add_argument("--max-error-rate", type=float, default=0.25, help="powyżej tego odsetka błędów loot -> awaria")
    a = ap.parse_args()

    t0 = time.monotonic()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    today = int(time.time() // 86400)

    def budget_left():
        return (time.monotonic() - t0) / 60 < a.budget_min

    prev_loot = read_json(out / "loot.json", {})
    prev_items = read_json(out / "items.json", {})
    prev_npcs = prev_items.get("npcs", [])
    prev_item_map = {}
    for name, v in (prev_items.get("items") or {}).items():
        prev_item_map[name] = [
            {"name": prev_npcs[i][0], "city": prev_npcs[i][1], "price": p}
            for i, p in v.get("s", []) if 0 <= i < len(prev_npcs)
        ]
    state = read_json(out / "state.json", {})
    canon = state.get("canon", {})       # "gold coin" -> "Gold Coin"
    item_ts = state.get("items", {})     # "Gold Coin" -> dzień (epoch/86400) ostatniego pobrania
    if a.full:
        item_ts = {}

    # ---- 1) katalog mobów (krytyczny)
    log("[1/6] Katalog mobów (TibiaData)…")
    try:
        catalog, boosted = step_catalog()
    except Exception as e:  # noqa: BLE001
        log(f"BŁĄD krytyczny: nie udało się pobrać listy mobów: {e}")
        return 1
    if len(catalog) < 300 and not a.limit:
        log(f"BŁĄD krytyczny: podejrzanie krótka lista mobów ({len(catalog)}) - nie nadpisuję danych.")
        return 1
    races = sorted(catalog)
    if a.limit:
        races = races[: a.limit]
    log(f"      {len(races)} mobów (boosted: {boosted or '-'})")

    # ---- 2) loot z TibiaData (krytyczny, z fallbackiem do poprzednich danych)
    log("[2/6] Loot z TibiaData…")
    res = pmap(fetch_tibiadata_loot, races, a.workers)
    td_loot, failed = {}, []
    for race, loot, err in res:
        if err is None:
            td_loot[race] = loot
        else:
            failed.append((race, err))
    rate = len(failed) / max(1, len(races))
    log(f"      ok: {len(td_loot)}, błędy: {len(failed)} ({rate:.0%})")
    if rate > a.max_error_rate:
        log(f"BŁĄD krytyczny: zbyt wiele błędów TibiaData ({rate:.0%}) - nie nadpisuję danych. Przykład: {failed[0]}")
        return 1
    for race, err in failed[:10]:
        warn(f"TibiaData {race}: {err}")

    # ---- 3) loot uzupełniający z wiki (best effort) + kanoniczne nazwy
    log("[3/6] Loot uzupełniający z TibiaWiki…")
    wiki_loot = {}   # race -> [nazwy z wiki]
    wiki_fail = 0
    wiki_failed_races = set()

    def wiki_loot_batch(batch):
        titles, owner = [], {}
        for race in batch:
            for c in title_candidates(catalog[race]["n"])[:2]:
                if c not in owner:
                    owner[c] = race
                    titles.append(c)
        texts = wikitext_batch(titles)
        got = {}
        for t, wt in texts.items():
            items = parse_wikitext_loot(wt)
            if items:
                got.setdefault(owner[t], items)
        return got

    for batch, got, err in pmap(wiki_loot_batch, list(chunks(races, 20)), min(a.workers, 2)):
        if err is not None:
            wiki_fail += 1
            wiki_failed_races.update(batch)
            continue
        wiki_loot.update(got)
    if wiki_fail:
        warn(f"TibiaWiki (loot): nieudane paczki: {wiki_fail}")
    log(f"      moby z lootem z wiki: {len(wiki_loot)}")

    # kanoniczne nazwy: najpierw to, co napisano na wiki (poprawna wielkość liter)
    for items in wiki_loot.values():
        for it in items:
            canon.setdefault(it.lower(), it)

    names_needed = set()
    for race in races:
        names_needed.update(x.lower() for x in td_loot.get(race, []))
        names_needed.update(x.lower() for x in wiki_loot.get(race, []))
        if race in wiki_failed_races:  # wiki niedostępne: zachowaj to, co było wcześniej
            names_needed.update(x.lower() for x in prev_loot.get(race, []))
    tmp_canon = {}   # tylko na czas tego uruchomienia (niepotwierdzone na wiki)
    unknown = sorted(n for n in names_needed if n not in canon)
    if unknown:
        log(f"      ustalam kanoniczne nazwy dla {len(unknown)} itemów…")

        def canon_batch(batch):
            titles = []
            for n in batch:
                titles += title_candidates(n)
            exist = existing_titles_batch(titles)
            res_ = {}
            for n in batch:
                for c in title_candidates(n):
                    if c in exist:
                        res_[n] = exist[c]
                        break
            return res_

        for _, got, err in pmap(canon_batch, list(chunks(unknown, 12)), min(a.workers, 2)):
            if err is None:
                canon.update(got)
        # druga runda: TibiaData podaje czesto liczbe mnoga ("bananas") - szukamy liczby pojedynczej
        plural_left = [n for n in unknown if n not in canon and singular_candidates(n)]
        if plural_left:
            def sing_batch(batch):
                titles = []
                for n in batch:
                    for sc in singular_candidates(n):
                        titles += title_candidates(sc)
                exist = existing_titles_batch(titles)
                res_ = {}
                for n in batch:
                    hit = None
                    for sc in singular_candidates(n):
                        for c in title_candidates(sc):
                            if c in exist:
                                hit = exist[c]
                                break
                        if hit:
                            break
                    if hit:
                        res_[n] = hit
                return res_

            found = 0
            for _, got, err in pmap(sing_batch, list(chunks(plural_left, 8)), min(a.workers, 2)):
                if err is None:
                    canon.update(got)
                    found += len(got)
            log(f"      liczba pojedyncza dopasowana na wiki dla {found} z {len(plural_left)} itemów")
        still = [n for n in unknown if n not in canon]
        for n in still:  # nie potwierdzone na wiki - sensowna wielkość liter, ale nie zapisujemy tego na stałe
            tmp_canon[n] = smart_title(n)
        if still:
            warn(f"nie potwierdzono na wiki nazw {len(still)} itemów (użyto smart-title), np. {still[:5]}")

    def canonical(n):
        l = n.lower()
        return canon.get(l) or tmp_canon.get(l) or smart_title(n)

    # scal loot: TibiaData + wiki, kanoniczne nazwy, bez duplikatów
    loot = {}
    for race in races:
        base = td_loot.get(race)
        if base is None:
            if race in prev_loot:
                loot[race] = prev_loot[race]  # zostaje poprzednia wersja
            continue
        seen, lst = set(), []
        extra = list(prev_loot.get(race, [])) if race in wiki_failed_races else []
        for n in list(base) + list(wiki_loot.get(race, [])) + extra:
            c = canonical(n)
            if c.lower() in seen:
                continue
            seen.add(c.lower())
            lst.append(c)
        loot[race] = lst
    # moby spoza --limit zachowują poprzedni loot
    if a.limit:
        for race, v in prev_loot.items():
            loot.setdefault(race, v)

    universe = sorted({it for lst in loot.values() for it in lst})
    log(f"      unikalnych itemów: {len(universe)}")

    # ---- 4) itemy: NPC i ceny (best effort, przyrostowo)
    log("[4/6] Itemy: NPC i ceny z TibiaWiki…")
    max_age = a.max_age_days
    todo = [n for n in universe if n not in prev_item_map or today - item_ts.get(n, 0) > max_age]
    todo.sort(key=lambda n: (n in prev_item_map, item_ts.get(n, 0)))  # najpierw brakujące, potem najstarsze
    log(f"      do pobrania: {len(todo)} z {len(universe)}")
    items = {n: prev_item_map[n] for n in universe if n in prev_item_map}
    ok = errs = downgraded = missing_pages = 0
    new_empty = []
    last_err = None
    done = 0

    def get_item(name):
        if not budget_left():
            return "skipped"
        return fetch_item_page(name)

    for chunk in chunks(todo, 200):
        for name, r, err in pmap(get_item, chunk, a.workers):
            done += 1
            if err is not None:
                errs += 1
                last_err = err
                continue
            if r == "skipped":
                continue
            if r is None:  # strony nie ma na wiki
                missing_pages += 1
                items.setdefault(name, [])
                item_ts[name] = today
                continue
            _, html = r
            sellers = parse_item_html(html) if html else []
            if not sellers and prev_item_map.get(name):
                downgraded += 1  # nie nadpisuj dobrych danych pustymi
                continue
            items[name] = sellers
            item_ts[name] = today
            ok += 1
            if not sellers:
                new_empty.append(name)
        log(f"      {done}/{len(todo)} (ok {ok}, błędy {errs})")
        if not budget_left():
            warn("wyczerpany limit czasu - reszta itemów zostanie pobrana przy następnym uruchomieniu")
            break
    # zabezpieczenie: jeśli prawie wszystkie nowo pobrane itemy mają pustą listę NPC,
    # to najpewniej zmienił się układ strony - nie zapisuj tych pustych wyników
    if ok >= 50 and len(new_empty) > 0.8 * ok:
        warn(f"{len(new_empty)}/{ok} nowo pobranych itemów bez NPC - parser prawdopodobnie nie pasuje do układu wiki. "
             "Wyniki odrzucone (strona użyje fallbacku do API).")
        for n in new_empty:
            if not prev_item_map.get(n):
                items.pop(n, None)
            item_ts.pop(n, None)
    with_prices = sum(1 for n in universe if items.get(n))
    if errs:
        warn(f"TibiaWiki (itemy): {errs} błędów, ostatni: {last_err}")
        if isinstance(last_err, HTTPError) and last_err.code == 403:
            warn("403 z Fandom: wiki blokuje to IP/UA. Strona użyje wtedy fallbacku (API z przeglądarki).")
    if downgraded:
        warn(f"{downgraded} itemów zwróciło pustą listę NPC - zostawiono poprzednie dane")
    log(f"      itemy z NPC: {with_prices}/{len(universe)}, brakujące strony wiki: {missing_pages}")

    # ---- 5) grafiki
    imgdir = out / "img"
    mob_img, item_img = {}, {}
    if a.no_images:
        log("[5/6] Grafiki: pominięte (--no-images)")
    else:
        log("[5/6] Grafiki…")
        # moby: image_url z TibiaData (static.tibia.com)
        need = []
        for race in races:
            p = existing_image(imgdir / "mob", race)
            if p:
                mob_img[race] = f"img/mob/{p.name}"
            if (not p or a.full) and catalog[race]["u"]:
                need.append(race)

        def dl_mob(race):
            if not budget_left():
                return None
            p = download_image(catalog[race]["u"], imgdir / "mob" / race)
            return f"img/mob/{p.name}" if p else None

        mfail = 0
        for race, r, err in pmap(dl_mob, need, a.workers):
            if err is not None:
                mfail += 1
            elif r:
                mob_img[race] = r
        log(f"      moby: {len(mob_img)}/{len(races)} (nowe: {len(need) - mfail}, błędy: {mfail})")

        # itemy: adresy z imageinfo wiki
        stems = {}
        used = {}
        for n in universe:
            s = file_slug(n)
            if s in used and used[s] != n:
                s = f"{s}-{hashlib.md5(n.encode()).hexdigest()[:6]}"
            used[s] = n
            stems[n] = s
        need_i = []
        for n in universe:
            p = existing_image(imgdir / "item", stems[n])
            if p:
                item_img[n] = f"img/item/{p.name}"
            if not p or a.full:
                need_i.append(n)

        def url_batch(batch):
            if not budget_left():
                return {}
            return image_urls_batch(batch)

        urls, ufail = {}, 0
        for _, got, err in pmap(url_batch, list(chunks(need_i, 25)), min(a.workers, 2)):
            if err is None:
                urls.update(got)
            else:
                ufail += 1

        def dl_item(n):
            u = urls.get(n)
            if not u or not budget_left():
                return None
            p = download_image(u, imgdir / "item" / stems[n])
            return f"img/item/{p.name}" if p else None

        ifail = 0
        for n, r, err in pmap(dl_item, [n for n in need_i if urls.get(n)], a.workers):
            if err is not None:
                ifail += 1
            elif r:
                item_img[n] = r
        if ufail or ifail:
            warn(f"grafiki itemów: błędy zapytań {ufail}, błędy pobierania {ifail}")
        log(f"      itemy: {len(item_img)}/{len(universe)} grafik")

    # ---- 6) zapis
    log("[6/6] Zapis…")
    creatures_out = []
    for race in sorted(catalog):
        c = catalog[race]
        e = {"n": c["n"], "r": race}
        if c["p"]:
            e["p"] = c["p"]
        img = mob_img.get(race)
        if img:
            e["i"] = img
        elif c["u"]:
            e["i"] = c["u"]  # fallback: oryginalny adres z TibiaData
        creatures_out.append(e)

    npc_keys = sorted({(s["name"], s["city"] or "") for lst in items.values() for s in lst})
    npc_idx = {k: i for i, k in enumerate(npc_keys)}
    items_out = {}
    for n in universe:
        entry = {}
        if n in items:  # "s" = dane o NPC pobrane (może być [] = nikt nie kupuje); brak "s" = nieznane
            rows = sorted(items[n], key=lambda s: (-(s["price"] if s["price"] is not None else -1), s["name"]))
            entry["s"] = [[npc_idx[(s["name"], s["city"] or "")], s["price"]] for s in rows]
        if n in item_img:
            entry["i"] = item_img[n]
        if entry:
            items_out[n] = entry

    write_json(out / "creatures.json", {"v": 1, "boosted": boosted, "list": creatures_out})
    write_json(out / "loot.json", loot)
    write_json(out / "items.json", {"v": 1, "npcs": [[n, c or None] for n, c in npc_keys], "items": items_out})
    write_json(out / "state.json", {"canon": canon, "items": {n: item_ts[n] for n in universe if n in item_ts}})
    write_json(out / "meta.json", {
        "v": 1,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "creatures": len(creatures_out),
        "withLoot": len(loot),
        "items": len(items_out),
        "itemsWithNpc": with_prices,
        "boosted": boosted,
    })
    log(f"Gotowe w {(time.monotonic() - t0) / 60:.1f} min: {len(creatures_out)} mobów, {len(items_out)} itemów, {with_prices} z cenami NPC.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Holt Bundesliga-Nachrichten aus offenen RSS-Feeds und schreibt news.json.

Läuft als GitHub Action (siehe .github/workflows/news.yml). Übernimmt nur, was die Feeds selbst
anbieten: Überschrift, Anrisstext, Link und Zeit. Die Artikel bleiben bei den Anbietern.
"""
import html
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

FEEDS = [
    {"source": "kicker", "url": "https://newsfeed.kicker.de/news/bundesliga"},
    {"source": "Sportschau", "url": "https://www.sportschau.de/fussball/bundesliga/index~rss2.xml"},
]
OUT = Path(__file__).resolve().parent.parent / "news.json"
KEEP_DAYS = 21          # ältere Meldungen fliegen raus
MAX_ITEMS = 200
TEASER_LEN = 240
UA = "Mozilla/5.0 (compatible; Bundesliga-Seite/1.0; +https://schreil18.github.io/Bundesliga/)"

# Vereinserkennung: OpenLigaDB-Vereins-ID -> typische Schreibweisen in Überschriften und Anrisstexten
CLUBS = {
    40: [r"FC Bayern", r"Bayern München", r"\bBayern\b", r"\bFCB\b"],
    7: [r"\bBVB\b", r"Borussia Dortmund", r"\bDortmund"],
    1635: [r"RB Leipzig", r"\bLeipzig", r"\bRBL\b"],
    6: [r"Leverkusen", r"Bayer 04", r"Werkself"],
    16: [r"VfB", r"\bStuttgart"],
    91: [r"Eintracht Frankfurt", r"\bFrankfurt(?!er Rundschau)", r"\bSGE\b"],
    112: [r"SC Freiburg", r"\bFreiburg"],
    175: [r"Hoffenheim", r"\bTSG\b"],
    81: [r"\bMainz", r"\bFSV\b", r"05er"],
    87: [r"Gladbach", r"Mönchengladbach", r"\bFohlen"],
    134: [r"Werder", r"\bBremen"],
    95: [r"Augsburg", r"\bFCA\b"],
    80: [r"\bUnion\b(?! Berlin-)", r"Union Berlin", r"Eiserne"],
    65: [r"\bKöln", r"\bEffzeh", r"Geißböcke", r"\bFC Köln"],
    100: [r"\bHSV\b", r"Hamburger SV", r"\bHamburg(?!er Morgenpost)"],
    9: [r"Schalke", r"\bS04\b", r"Königsblau"],
    31: [r"Paderborn", r"\bSCP\b"],
    198: [r"Elversberg", r"\bSVE\b"],
}
CLUB_RX = {tid: re.compile("|".join(p), re.I) for tid, p in CLUBS.items()}

# Transfers, Verträge und Personalien (Spieler wie Trainer).
# TITLE_RX gilt nur für die Überschrift: Spielberichte erwähnen im Anrisstext oft beiläufig einen
# "Neuzugang" oder "Vertrag". Im Anrisstext zählen deshalb nur eindeutige Begriffe (STRONG_RX).
STRONG_RX = re.compile(
    r"transfer|ablöse|leihgeschäft|ausgeliehen|verpflichtet|verpflichtung|unterschreibt|unterschrieben|"
    r"wechselt (?:zu|nach|in|zum|zur)|wechsel (?:zu|nach|zum|zur)|vertragsverlängerung|verlängert (?:seinen|ihren|den) vertrag|"
    r"ausstiegsklausel|transferfenster|neuer (?:cheft|t)rainer|als nachfolger",
    re.I,
)
TITLE_RX = re.compile(
    r"transfer|wechsel|wechselt|ablöse|leihe|leihgeschäft|ausgeliehen|verlieh|verpflicht|neuzugang|zugang\b|abgang|"
    r"vertrag|verlänger|unterschreib|unterzeichn|kaderplanung|gerücht|interesse an|umworben|abwerb|"
    r"ausstiegsklausel|freigabe|trainersuche|nachfolger|entlass|freigestellt|trennt sich|trennung|übernimmt|"
    r"karriereende|rückkehr|zurück zu|kehrt zurück|winterpause.*kader|sommerpause.*kader",
    re.I,
)


def fetch(url, tries=3):
    for i in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml"}), timeout=25) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            print(f"  Versuch {i + 1} für {url} fehlgeschlagen: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))
    return None


def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def teaser(text):
    text = clean(text)
    if len(text) <= TEASER_LEN:
        return text
    cut = text[:TEASER_LEN].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-–") + " …"


def parse_date(value):
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def text_of(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def parse_feed(raw, source):
    items = []
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        title = clean(text_of(it, "title"))
        link = text_of(it, "link").split("#")[0]
        if not title or not link.startswith("http"):
            continue
        desc = text_of(it, "description", "{http://purl.org/rss/1.0/modules/content/}encoded")
        published = parse_date(text_of(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date"))
        items.append({
            "id": link,
            "title": title,
            "link": link,
            "source": source,
            "published": published.isoformat().replace("+00:00", "Z") if published else None,
            "teaser": teaser(desc),
        })
    return items


def classify(item):
    """Vereine und Thema aus Überschrift und Anriss bestimmen (auch für ältere Einträge neu)."""
    title, blob = item["title"], f"{item['title']} {item.get('teaser', '')}"
    item["clubs"] = [tid for tid, rx in CLUB_RX.items() if rx.search(blob)]
    item["transfer"] = bool(TITLE_RX.search(title) or STRONG_RX.search(blob))
    return item


def main():
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("items", [])
        except (ValueError, OSError):
            old = []
    fresh, ok_sources = [], []
    for feed in FEEDS:
        raw = fetch(feed["url"])
        if raw is None:
            continue
        try:
            got = parse_feed(raw, feed["source"])
        except ET.ParseError as e:
            print(f"  {feed['source']}: Feed nicht lesbar ({e})", file=sys.stderr)
            continue
        print(f"{feed['source']}: {len(got)} Meldungen")
        fresh += got
        ok_sources.append(feed["source"])

    # neue Meldungen ersetzen gleiche Links, ältere bleiben bis KEEP_DAYS erhalten
    by_id = {i["id"]: i for i in old}
    for i in fresh:
        by_id[i["id"]] = i
    limit = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    items = [i for i in by_id.values() if not i.get("published") or parse_date(i["published"]) >= limit]
    # gleiche Überschrift aus zwei Quellen nur einmal
    seen, unique = set(), []
    for i in sorted(items, key=lambda x: x.get("published") or "", reverse=True):
        key = re.sub(r"\W+", "", i["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(i)
    unique = [classify(i) for i in unique[:MAX_ITEMS]]

    if not ok_sources and old:
        print("Keine Quelle erreichbar, news.json bleibt unverändert.")
        return
    data = {
        "updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sources": [{"name": f["source"], "url": f["url"]} for f in FEEDS],
        "reachable": ok_sources,
        "items": unique,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"news.json: {len(unique)} Meldungen, davon {sum(i['transfer'] for i in unique)} zu Transfers/Personalien")


if __name__ == "__main__":
    main()

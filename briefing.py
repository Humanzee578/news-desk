#!/usr/bin/env python3
"""Helper for the Claude written briefing (top 3 per section).

  python3 briefing.py candidates      list recent stories per section, with ids
  python3 briefing.py write draft.json  validate a draft and write docs/briefing.json

Draft format:
{"sections": {"legal": [{"id": "abc123", "summary": "...", "basis": "full article|excerpt|headline only"}], ...},
 "markets_note": "optional one or two sentence read of the day"}
"""
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "docs", "data.json")
OUT = os.path.join(ROOT, "docs", "briefing.json")


def load():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def age_h(iso):
    return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(iso)).total_seconds() / 3600


def candidates():
    d = load()
    print("generated:", d["generated"])
    print("snapshot:", "; ".join(f'{x["label"]} {x["price"]} ({x["change_pct"]}%)' for x in d["snapshot"] if x["price"] is not None))
    for sec in d["sections"]:
        print(f'\n== {sec["id"]} ==')
        rows = [s for s in d["stories"] if s["section"] == sec["id"] and age_h(s["published"]) < 36
                and not s["title"].lower().startswith("sponsored")]
        for s in rows[:10]:
            flag = " [PAYWALLED]" if s.get("paywalled") else ""
            tk = f' tickers={",".join(s.get("tickers", []))}' if s.get("tickers") else ""
            print(f'{s["id"]} | {s["source"]}{flag}{tk} | {age_h(s["published"]):.0f}h | {s["title"]}')
            if s.get("summary"):
                print("    excerpt:", s["summary"][:240])
            print("    url:", s["url"][:140])


def write(path):
    d = load()
    by_id = {s["id"]: s for s in d["stories"]}
    draft = json.load(open(path, encoding="utf-8"))
    out = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(), "data_generated": d["generated"],
           "markets_note": draft.get("markets_note", ""), "sections": []}
    for sec in d["sections"]:
        items = []
        for it in draft["sections"].get(sec["id"], [])[:3]:
            s = by_id.get(it["id"])
            if not s:
                sys.exit(f'unknown id {it["id"]} in {sec["id"]}')
            text = it["summary"].strip()
            if any(ch in text for ch in ("—", "–")) or " - " in text:
                sys.exit(f'dash found in summary for {it["id"]}; use commas')
            items.append({"title": s["title"], "source": s["source"], "url": s["url"],
                          "summary": text, "basis": it.get("basis", "excerpt"),
                          "paywalled": bool(s.get("paywalled"))})
        out["sections"].append({"id": sec["id"], "title": sec["title"], "items": items})
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote", OUT, sum(len(s["items"]) for s in out["sections"]), "items")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "candidates":
        candidates()
    elif len(sys.argv) >= 3 and sys.argv[1] == "write":
        write(sys.argv[2])
    else:
        print(__doc__)

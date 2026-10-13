#!/usr/bin/env python3
"""Fetch feeds, cluster, score, summarize, and write docs/data.json."""
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
from calendar import timegm

import feedparser
import requests
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs", "data.json")
UA = {"User-Agent": "Mozilla/5.0 (compatible; NewsDeskBot/1.0)"}
NOW = dt.datetime.now(dt.timezone.utc)
STOP = set("the a an and or of to in on for with at by from is are was were be as it its this that "
           "after over new says say will has have had not but into out up about their his her they".split())


def load_cfg():
    with open(os.path.join(ROOT, "config.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_old():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get(url, timeout=25):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


def clean(text, limit=None):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "..."
    return text


def entry_time(e):
    for k in ("published_parsed", "updated_parsed"):
        if e.get(k):
            return dt.datetime.fromtimestamp(timegm(e[k]), dt.timezone.utc)
    return NOW


def get_tickers(cfg):
    tc = cfg["tickers"]
    found = []
    try:
        page = get(tc["signal_bench_url"]).text
        m = re.search(r'<script[^>]*id="payload"[^>]*>(.*?)</script>', page, re.S)
        data = json.loads(m.group(1))
        found += [row[0] for row in data.get("bench", [])]
        found += [b["ticker"] for b in data.get("buys", [])]
        found += [p["ticker"] for p in data.get("positions", [])]
    except Exception as ex:
        print("ticker fetch failed:", ex, file=sys.stderr)
        try:
            with open(os.path.join(ROOT, "tickers.txt"), encoding="utf-8") as f:
                found = [x.strip().upper() for x in f if x.strip() and not x.startswith("#")]
        except Exception:
            found = []
    seen, out = set(), []
    for t in found:
        t = str(t).upper().strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def source_url(s):
    if s.get("type") == "gnews":
        q = urllib.parse.quote_plus(s["query"] + " when:3d")
        return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    return s.get("url", "")


def fetch_source(s, health, limit=25, tickers=None):
    name = s["name"]
    if s.get("enabled") is False or not source_url(s):
        health.append({"source": name, "status": "disabled", "note": s.get("note", "")})
        return []
    try:
        feed = feedparser.parse(get(source_url(s)).content)
        if not feed.entries:
            raise ValueError("no entries")
    except Exception as ex:
        health.append({"source": name, "status": "error", "note": str(ex)[:120]})
        return []
    health.append({"source": name, "status": "ok", "count": len(feed.entries)})
    items = []
    for e in feed.entries[:limit]:
        title = clean(e.get("title"))
        link = e.get("link", "")
        if not title or not link:
            continue
        if s.get("type") == "gnews":
            title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title)
        items.append({
            "title": title,
            "url": link,
            "blurb": clean(e.get("summary") or e.get("description"), 500) if s.get("type") != "gnews" else "",
            "published": entry_time(e).isoformat(),
            "source": name,
            "section": s["section"],
            "paywalled": bool(s.get("paywalled")),
            "tickers": [],
        })
    return items


def fetch_ticker_news(cfg, tickers, health):
    tc = cfg["tickers"]
    items, ok, bad = [], 0, 0
    for t in tickers:
        try:
            feed = feedparser.parse(get(tc["ticker_news_url"].format(t=t), timeout=15).content)
            for e in feed.entries[: tc["items_per_ticker"]]:
                title = clean(e.get("title"))
                if title and e.get("link"):
                    items.append({
                        "title": title, "url": e["link"],
                        "blurb": clean(e.get("summary"), 400),
                        "published": entry_time(e).isoformat(),
                        "source": "Yahoo Finance", "section": "markets",
                        "paywalled": False, "tickers": [t],
                    })
            ok += 1
        except Exception:
            bad += 1
    health.append({"source": "Watchlist ticker news", "status": "ok" if ok else "error",
                   "count": len(items), "note": f"{ok} tickers ok, {bad} failed"})
    return items


def market_snapshot(cfg):
    out = []
    for m in cfg.get("market_snapshot", []):
        try:
            sym = urllib.parse.quote(m["symbol"])
            r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d", 15).json()
            meta = r["chart"]["result"][0]["meta"]
            price, prev = meta["regularMarketPrice"], meta.get("chartPreviousClose") or meta.get("previousClose")
            chg = (price - prev) / prev * 100 if prev else None
            out.append({"label": m["label"], "price": round(price, 2), "change_pct": round(chg, 2) if chg is not None else None})
        except Exception:
            out.append({"label": m["label"], "price": None, "change_pct": None})
    return out


def sid(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def tokens(title):
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2 and w not in STOP}


def score(st, cfg):
    age_h = max(0.0, (NOW - dt.datetime.fromisoformat(st["published"])).total_seconds() / 3600)
    s = 10 * (0.5 ** (age_h / 12))
    text = (st["title"] + " " + (st.get("blurb") or "")).lower()
    for kw, w in cfg.get("keywords", {}).get(st["section"], {}).items():
        if kw.lower() in text:
            s += w
    if st.get("tickers"):
        s += cfg.get("ticker_boost", 4)
    return round(s, 2)


def cluster(stories):
    by_sec = {}
    for st in stories:
        by_sec.setdefault(st["section"], []).append(st)
    leads = []
    for sec, items in by_sec.items():
        items.sort(key=lambda x: -x["score"])
        groups = []
        for st in items:
            tk = tokens(st["title"])
            placed = False
            for g in groups:
                if st["url"] == g["lead"]["url"]:
                    placed = True
                    break
                inter = len(tk & g["tok"])
                union = len(tk | g["tok"]) or 1
                if inter / union >= 0.55 and st["source"] != g["lead"]["source"]:
                    g["also"].append({"source": st["source"], "url": st["url"]})
                    placed = True
                    break
                if inter / union >= 0.55:
                    placed = True
                    break
            if not placed:
                groups.append({"lead": st, "tok": tk, "also": []})
        for g in groups:
            lead = dict(g["lead"])
            lead["also"] = g["also"][:4]
            leads.append(lead)
    return leads


def summarize(stories, cfg):
    key = os.environ.get("ANTHROPIC_API_KEY")
    cap = cfg["site"]["max_new_summaries_per_run"]
    todo = [s for s in stories if not s["paywalled"] and not s.get("ai")]
    todo.sort(key=lambda x: -x["score"])
    done = 0
    for st in todo:
        fallback = clean(st.get("blurb"), 220)
        if not key or done >= cap:
            st["summary"] = fallback
            st["ai"] = False
            continue
        try:
            body = {
                "model": cfg["site"]["model"], "max_tokens": 120,
                "messages": [{"role": "user", "content":
                    "Summarize this news item in one or two plain sentences for a busy reader. "
                    "Use only the text given. Do not add facts. Do not use dashes; use commas instead. "
                    "If there is too little text, restate the headline plainly in one sentence.\n\n"
                    f"Headline: {st['title']}\nSource: {st['source']}\nExcerpt: {st.get('blurb') or '(none)'}"}],
            }
            r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=40, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
            if r.status_code >= 400:
                print("API error", r.status_code, r.text[:300], file=sys.stderr)
                if done == 0 and r.status_code in (400, 401, 403, 404):
                    cap = 0
            r.raise_for_status()
            st["summary"] = r.json()["content"][0]["text"].strip()
            st["ai"] = True
            done += 1
        except Exception as ex:
            print("summary failed:", str(ex)[:100], file=sys.stderr)
            st["summary"] = fallback
            st["ai"] = False
    return done


def main():
    cfg = load_cfg()
    old = load_old()
    health = []
    tickers = get_tickers(cfg)
    items = []
    for s in cfg["sources"]:
        items += fetch_source(s, health)
    items += fetch_ticker_news(cfg, tickers, health)

    store = {st["id"]: st for st in old.get("store", [])}
    for it in items:
        i = sid(it["url"])
        if i in store:
            store[i]["tickers"] = sorted(set(store[i].get("tickers", []) + it["tickers"]))
            continue
        it["id"] = i
        store[i] = it
    cutoff = NOW - dt.timedelta(days=cfg["site"]["retention_days"])
    stories = [s for s in store.values() if dt.datetime.fromisoformat(s["published"]) >= cutoff]
    for s in stories:
        s["score"] = score(s, cfg)

    n = summarize(stories, cfg)
    leads = cluster(stories)
    leads.sort(key=lambda x: -x["score"])

    out = {
        "generated": NOW.isoformat(),
        "title": cfg["site"]["title"],
        "sections": cfg["sections"],
        "snapshot": market_snapshot(cfg),
        "tickers": tickers,
        "health": health,
        "stories": [{k: v for k, v in s.items() if k not in ("blurb",)} for s in leads],
        "store": [{k: v for k, v in s.items() if k not in ("also",)} for s in stories],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    ok = sum(1 for h in health if h["status"] == "ok")
    print(f"stories={len(leads)} stored={len(stories)} feeds_ok={ok}/{len(health)} new_summaries={n} tickers={len(tickers)}")


if __name__ == "__main__":
    main()

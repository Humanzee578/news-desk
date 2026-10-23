"""Fetch article text for recent stories so the written briefing can summarize full articles.

Writes docs/fulltext.json: {story id: {"text": "...", "fetched": iso}} for stories under 36 hours old.
Skips paywalled sources. Google News links are decoded to the publisher url when possible.
Never raises: a failure just means that story stays excerpt or headline only.
"""
import datetime as dt
import json
import os

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs", "fulltext.json")
UA = {"User-Agent": "Mozilla/5.0 (compatible; NewsDeskBot/1.0)"}
MAX_AGE_H = 36
MAX_CHARS = 3500
MAX_NEW_PER_RUN = 40


def _load():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _resolve(url):
    if "news.google.com" not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder
        r = gnewsdecoder(url, interval=1)
        if r.get("status") and r.get("decoded_url"):
            return r["decoded_url"]
    except Exception:
        pass
    return None


def _extract(url):
    import trafilatura
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    text = trafilatura.extract(r.text, include_comments=False, include_tables=False)
    if not text or len(text) < 300:
        return None
    return text[:MAX_CHARS].rsplit(" ", 1)[0]


def run(stories, now):
    store = _load()
    keep = {}
    for st in stories:
        age = (now - dt.datetime.fromisoformat(st["published"])).total_seconds() / 3600
        if age < MAX_AGE_H and st["id"] in store:
            keep[st["id"]] = store[st["id"]]
    fetched = tried = 0
    for st in sorted(stories, key=lambda s: -s.get("score", 0)):
        if tried >= MAX_NEW_PER_RUN:
            break
        age = (now - dt.datetime.fromisoformat(st["published"])).total_seconds() / 3600
        if age >= MAX_AGE_H or st.get("paywalled") or st["id"] in keep:
            continue
        tried += 1
        try:
            url = _resolve(st["url"])
            text = _extract(url) if url else None
        except Exception:
            text = None
        if text:
            keep[st["id"]] = {"text": text, "fetched": now.isoformat()}
            fetched += 1
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(keep, f, ensure_ascii=False, separators=(",", ":"))
    return fetched, tried

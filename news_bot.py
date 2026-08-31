"""
news_bot.py — Phase 1
ดึงข่าว AI + Crypto + หุ้น US → กรอง 1 ชม.ล่าสุด → dedupe → สรุปด้วย Claude → ยิงเข้า Slack

รันโดย GitHub Actions ทุกชั่วโมง (ดู .github/workflows/hourly-news.yml)
ตั้งค่าผ่าน env: ANTHROPIC_API_KEY, SLACK_WEBHOOK_URL, (option) CRYPTOPANIC_TOKEN, MARKETAUX_TOKEN
"""

import os
import json
import time
import hashlib
import datetime as dt
from pathlib import Path

import requests
import feedparser
from dateutil import parser as dateparser
import anthropic

# ────────────────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────────────────

# แกนหลัก: RSS ฟรี ไม่ต้องมี key — วิ่งได้ตั้งแต่วันแรก
RSS_FEEDS = {
    "crypto": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ],
    "stocks": [
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",  # CNBC Top News
        "https://www.cnbc.com/id/10000664/device/rss/rss.html",   # CNBC Markets
    ],
    "ai": [
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "https://venturebeat.com/category/ai/feed/",
    ],
}

WINDOW_MINUTES = 180          # เก็บข่าวที่เพิ่งออกใน 180 นาทีล่าสุด (เผื่อ buffer จาก 60)
MAX_ITEMS_PER_RUN = 8        # กัน Slack ท่วม — สรุปมากสุดกี่ข่าวต่อรอบ
SEEN_FILE = Path("seen.json")  # log ข่าวที่เคยส่งแล้ว กันส่งซ้ำข้ามชั่วโมง
MODEL = "claude-sonnet-5"    # เปลี่ยนรุ่นได้ตามต้องการ

# ────────────────────────────────────────────────────────────
# PROMPT สรุปข่าว
# ────────────────────────────────────────────────────────────

SUMMARY_SYSTEM = """คุณคือผู้ช่วยสรุปข่าวการเงิน/เทค สำหรับช่อง TikTok เทรดเดอร์ทันข่าว
รับข่าวดิบมา แล้วสรุปเป็นภาษาไทยตาม format นี้เป๊ะ:

[HEADLINE]
พาดหัวสั้น กระชับ มี hook — ใส่ ticker/ชื่อบริษัทในวงเล็บถ้ามี ห้ามเกิน 1 บรรทัด

[EXPLAINER]
- ย่อหน้า 1: เกิดอะไรขึ้น (ใคร ทำอะไร มูลค่าเท่าไหร่ เมื่อไหร่)
- ย่อหน้า 2: อธิบายศัพท์/คอนเซปต์ที่คนทั่วไปอาจไม่รู้ ("X คืออะไร?")
- ย่อหน้า 3: ทำไมมันสำคัญ / ส่งผลต่อตลาดยังไง (มุมเทรดเดอร์)
- ปิดท้าย: disclaimer

กฎ:
- โทนเป็นกันเอง อ่านง่าย ไม่ทางการเกิน แต่ไม่มั่วตัวเลข
- ตัวเลข/ชื่อ/วันที่ ต้องตรงกับข่าวต้นทางเท่านั้น ห้ามเดา
- โดยปกติสรุปจากหัวข้อข่าวได้เลย แม้เนื้อ teaser จะสั้นหรือไม่มี — ตอบ SKIP เฉพาะกรณีหัวข้อกำกวมจนไม่รู้ว่าข่าวเกี่ยวกับอะไรจริงๆ เท่านั้น
- ห้ามแต่งตัวเลข/ชื่อ/รายละเอียดที่ไม่มีในต้นทาง ถ้าไม่รู้รายละเอียดให้สรุปกว้างๆ จากหัวข้อแทน
- ไม่ต้องใส่ลิงก์หรือแหล่งที่มา (ระบบจะแปะให้เอง)
- disclaimer บังคับทุกครั้ง (เนื้อหาการเงิน)"""

# ────────────────────────────────────────────────────────────
# FETCH
# ────────────────────────────────────────────────────────────

def _fingerprint(title: str, url: str) -> str:
    key = (title.strip().lower() + "|" + url.strip().lower()).encode()
    return hashlib.sha1(key).hexdigest()[:16]

def _recent(published, window_min: int) -> bool:
    if not published:
        return False
    try:
        ts = dateparser.parse(published)
    except (ValueError, TypeError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    age = dt.datetime.now(dt.timezone.utc) - ts
    return dt.timedelta(0) <= age <= dt.timedelta(minutes=window_min)

def fetch_rss():
    items = []
    for category, feeds in RSS_FEEDS.items():
        for url in feeds:
            try:
                parsed = feedparser.parse(url)
            except Exception as e:
                print(f"[rss] error {url}: {e}")
                continue
            for entry in parsed.entries:
                published = entry.get("published") or entry.get("updated")
                if not _recent(published, WINDOW_MINUTES):
                    continue
                items.append({
                    "category": category,
                    "title": entry.get("title", "").strip(),
                    "url": entry.get("link", "").strip(),
                    "summary": (entry.get("summary", "") or "")[:1200],
                    "source": parsed.feed.get("title", url),
                })
    return items

def fetch_cryptopanic():
    token = os.getenv("CRYPTOPANIC_TOKEN")
    if not token:
        return []
    try:
        r = requests.get(
            "https://cryptopanic.com/api/v1/posts/",
            params={"auth_token": token, "kind": "news", "public": "true"},
            timeout=20,
        )
        r.raise_for_status()
        out = []
        for p in r.json().get("results", []):
            if not _recent(p.get("published_at"), WINDOW_MINUTES):
                continue
            out.append({
                "category": "crypto",
                "title": p.get("title", "").strip(),
                "url": p.get("url", "").strip(),
                "summary": "",  # CryptoPanic ให้ title เป็นหลัก
                "source": "CryptoPanic",
            })
        return out
    except Exception as e:
        print(f"[cryptopanic] error: {e}")
        return []

def fetch_marketaux():
    token = os.getenv("MARKETAUX_TOKEN")
    if not token:
        return []
    try:
        r = requests.get(
            "https://api.marketaux.com/v1/news/all",
            params={"api_token": token, "language": "en",
                    "filter_entities": "true", "limit": 10},
            timeout=20,
        )
        r.raise_for_status()
        out = []
        for a in r.json().get("data", []):
            if not _recent(a.get("published_at"), WINDOW_MINUTES):
                continue
            out.append({
                "category": "stocks",
                "title": a.get("title", "").strip(),
                "url": a.get("url", "").strip(),
                "summary": (a.get("description", "") or "")[:1200],
                "source": a.get("source", "Marketaux"),
            })
        return out
    except Exception as e:
        print(f"[marketaux] error: {e}")
        return []

# ────────────────────────────────────────────────────────────
# DEDUPE + SEEN LOG
# ────────────────────────────────────────────────────────────

def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    # เก็บแค่ 500 fingerprint ล่าสุด กันไฟล์บวม
    SEEN_FILE.write_text(json.dumps(list(seen)[-500:]))

def dedupe(items, seen):
    fresh, batch_fps = [], set()
    for it in items:
        if not it["title"] or not it["url"]:
            continue
        fp = _fingerprint(it["title"], it["url"])
        if fp in seen or fp in batch_fps:
            continue
        batch_fps.add(fp)
        it["_fp"] = fp
        fresh.append(it)
    return fresh

# ────────────────────────────────────────────────────────────
# SUMMARIZE (Claude)
# ────────────────────────────────────────────────────────────

def summarize(client, item):
    raw = (f"หัวข้อ: {item['title']}\n"
           f"แหล่ง: {item['source']}\n"
           f"เนื้อ (teaser): {item['summary']}\n"
           f"ลิงก์: {item['url']}")
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=800,
            system=SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": raw}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not text:
            print(f"[summarize] ได้ข้อความว่าง ข้าม: {item['title'][:60]}")
            return None
        if text.upper().startswith("SKIP"):
            print(f"[summarize] Claude ตอบ SKIP ข้าม: {item['title'][:60]}")
            return None
        return text
    except Exception as e:
        print(f"[summarize] error: {e}")
        return None

# ────────────────────────────────────────────────────────────
# SLACK
# ────────────────────────────────────────────────────────────

def post_slack(webhook, item, summary):
    tag = {"crypto": "🪙 Crypto", "stocks": "📈 หุ้น US", "ai": "🤖 AI"}.get(item["category"], "ข่าว")
    text = (f"*{tag}*\n{summary}\n\n"
            f"🔗 ที่มา: {item['url']}\n\n"
            f"_react ✅ เพื่อ approve ทำคลิป_")
    try:
        r = requests.post(webhook, json={"text": text}, timeout=15)
        if r.status_code >= 400:
            print(f"[slack] error: HTTP {r.status_code} - {r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"[slack] error: {e}")
        return False

# ────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────

def main():
    webhook = os.environ["SLACK_WEBHOOK_URL"]
    client = anthropic.Anthropic()  # อ่าน ANTHROPIC_API_KEY จาก env เอง

    items = fetch_rss() + fetch_cryptopanic() + fetch_marketaux()
    print(f"ดึงมาได้ {len(items)} ข่าว (ก่อน dedupe)")

    seen = load_seen()
    fresh = dedupe(items, seen)[:MAX_ITEMS_PER_RUN]
    print(f"เหลือ {len(fresh)} ข่าวใหม่จริง")

    posted = skipped = failed = 0
    for it in fresh:
        summary = summarize(client, it)
        if not summary:
            skipped += 1
            continue
        if post_slack(webhook, it, summary):
            seen.add(it["_fp"])
            posted += 1
            time.sleep(1)  # เว้นจังหวะ กัน rate limit
        else:
            failed += 1

    save_seen(seen)
    print(f"สรุปผล: ส่งสำเร็จ {posted} | ข้าม(สรุปไม่ได้) {skipped} | ส่ง Slack พลาด {failed}")

if __name__ == "__main__":
    main()

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
 
SUMMARY_SYSTEM = """คุณคือบรรณาธิการคัดข่าว สำหรับช่อง TikTok เทรดเดอร์ทันข่าว
ช่องนี้ไม่ได้ขายการ "รายงานข่าว" แต่ขาย "มุมมอง + การวิเคราะห์เชิงลึก"
งานของคุณมี 2 ขั้น: (1) คัดว่าข่าวนี้ต่อยอดเป็นบทวิเคราะห์ได้ไหม (2) ถ้าได้ ให้วางโครงมุมวิเคราะห์
 
━━ ขั้นที่ 1: คัดข่าว ━━
ผ่าน เมื่อข่าวมีอย่างน้อย 1 ข้อ:
- บอกถึงการเปลี่ยนแปลงเชิงโครงสร้าง/เทรนด์ใหญ่ (นโยบาย กฎเกณฑ์ ทิศทางอุตสาหกรรม การย้ายเงินทุน)
- ชวนให้ถาม "ทำไม?" หรือ "แล้วไง?" ต่อได้ และตอบได้ด้วยข้อมูลที่หาเพิ่มได้
- เอาไปเทียบได้ (ไทย vs สหรัฐฯ, ก่อน vs หลัง, บริษัท A vs B, ดัชนี vs ดัชนี)
- ส่งผลต่อกลุ่มอุตสาหกรรม/หุ้น/สินทรัพย์ที่คนดูถืออยู่หรือสนใจ
 
ตอบ SKIP (คำเดียว ไม่ต้องอธิบาย) เมื่อข่าวเป็นแค่:
- ราคาขึ้น/ลงรายวัน โดยไม่มีเหตุผลเชิงโครงสร้าง
- งบ/ผลประกอบการที่ออกตามคาด ไม่มีประเด็นใหม่
- PR เปิดตัวสินค้า ฟีเจอร์ พาร์ทเนอร์ชิปเล็กๆ ตั้งผู้บริหาร จัดอีเวนต์
- ข่าวลือ คลิกเบต บทความลิสต์ หรือความเห็นส่วนตัวที่ไม่มีข้อมูล
- ข่าวที่หัวข้อกำกวมจนไม่รู้ว่าเกี่ยวกับอะไร
ถ้าลังเล ให้ SKIP — คัดน้อยแต่คม ดีกว่าท่วม Slack
 
━━ ขั้นที่ 2: ถ้าผ่าน ตอบตาม format นี้เป๊ะ ━━
 
[HEADLINE]
พาดหัวแบบ "คำถามชวนคิด" ไม่ใช่พาดหัวข่าว — 1 บรรทัด
ตัวอย่างสไตล์: "ทำไมไทยต้องสร้างหุ้น New Economy มากขึ้น? เมื่อเทียบกับ S&P 500"
 
[ข่าวต้นเรื่อง]
1 ย่อหน้าสั้น: เกิดอะไรขึ้น ใคร ทำอะไร (ข้อมูลจากต้นทางเท่านั้น)
 
[คำถามที่ข่าวนี้ชวนให้ถามต่อ]
2–3 คำถาม ที่คนดูน่าจะสงสัย และจะเป็นแกนของบทความ
 
[มุมวิเคราะห์]
- มุมเทียบ: เอาไปเทียบกับอะไรได้บ้าง และความต่างนั้นบอกอะไร
- ภาพใหญ่: ข่าวนี้สะท้อนเทรนด์/ปัญหาเชิงโครงสร้างอะไร
- มุมที่คนมักมองข้าม หรือข้อโต้แย้ง (ไม่ได้แปลว่าอีกฝั่งผิด)
- ผลต่อคนดู: กลุ่มหุ้น/สินทรัพย์ไหนเกี่ยว และควรจับตาอะไรต่อ
 
[ข้อมูลที่ต้องไปหาเพิ่ม]
เช็กลิสต์ข้อมูลที่ต้องไปดึงมาประกอบ เช่น "สัดส่วนกลุ่มอุตสาหกรรมใน SET50", "หุ้น 10 อันดับแรกใน S&P 500"
 
[ความเสี่ยง/ข้อควรระวัง]
1–2 บรรทัด + ปิดด้วย "ผู้ลงทุนควรทำความเข้าใจลักษณะสินค้า เงื่อนไขผลตอบแทน และความเสี่ยงก่อนตัดสินใจลงทุน"
 
กฎ:
- ภาษาไทย โทนกันเอง แต่ไม่มั่วข้อมูล
- ตัวเลข/ชื่อ/วันที่ในส่วน [ข่าวต้นเรื่อง] ต้องมาจากต้นทางเท่านั้น ห้ามเดา
- ในส่วนมุมวิเคราะห์ ใช้ความรู้ทั่วไปได้ แต่ตัวเลขที่ไม่ได้อยู่ในข่าว ห้ามใส่ — ให้ไปอยู่ใน [ข้อมูลที่ต้องไปหาเพิ่ม] แทน
- ไม่ต้องใส่ลิงก์หรือแหล่งที่มา (ระบบจะแปะให้เอง)
- ไม่ชี้นำให้ซื้อ/ขายตัวไหน"""
 
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
            max_tokens=1500,
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
 

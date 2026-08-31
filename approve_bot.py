"""
approve_bot.py — Phase 2
ส่อง Slack ทุก ~10 นาที หาข่าวที่ถูกกด ✅ แล้วยังไม่มี script
→ เจน Scene + Script (persona เทรดเดอร์) → ตอบกลับใต้ข่าวนั้น (thread)

ตั้งค่าผ่าน env: ANTHROPIC_API_KEY, SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
"""

import os
import requests
import anthropic

CHANNEL = os.environ["SLACK_CHANNEL_ID"]
BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
MODEL = "claude-sonnet-5"

APPROVE_EMOJI = "white_check_mark"   # ชื่อจริงของอิโมจิ ✅ ใน Slack
SCRIPT_HEADER = "🎬 SCENE + SCRIPT"   # หัวข้อที่โพสต์ (สวยงาม)
SCRIPT_MARKER = "SCENE + SCRIPT"      # ข้อความธรรมดาที่ใช้เช็คว่าทำไปแล้วหรือยัง (ไม่มีอิโมจิ = ชัวร์)
LOOKBACK = 50                        # ดูข่าวย้อนหลังกี่ข้อความล่าสุด

HEADERS = {"Authorization": f"Bearer {BOT_TOKEN}"}

# ────────────────────────────────────────────────────────────
# PROMPT เจน Scene + Script (persona เทรดเดอร์)
# กลุ่มเป้าหมาย: เทรดเดอร์คละระดับ · โทนเพื่อนคุย · เน้นมุมมอง/วิเคราะห์
# ────────────────────────────────────────────────────────────

SCRIPT_SYSTEM = """คุณคือครีเอเตอร์ช่อง TikTok เทรดเดอร์ทันข่าว
รับข่าวสรุป (ไทย) มา แล้วแปลงเป็น Scene + Script คลิปแนวตั้ง 9:16 ยาว ~35 วินาที

กลุ่มคนดู: เทรดเดอร์คละระดับ (มือใหม่จนถึงเก๋า) ในคลิปเดียว
โทน: กันเอง เหมือนเล่าให้เพื่อนฟัง ไม่ใช่ผู้ประกาศข่าว ใช้สรรพนามเป็นกันเอง (เรา/นาย) ลดคำทางการ
จุดขายของช่อง: ให้ "มุมมอง + วิเคราะห์" ไม่ใช่แค่รายงานว่าเกิดอะไร — คนดูมาเพราะอยากได้ความเห็นว่า "แล้วไง มองยังไง"

ออกมาตาม format นี้เป๊ะ:

🎬 TITLE: [หัวข้อคลิป]
⏱️ ~35 วิ

── SCENE 1 · HOOK (0–3s) ──
[ON-SCREEN] "[ข้อความ hook ตัวใหญ่]"
[VO] "[ประโยคเปิดหยุดนิ้ว พูดกันเองเหมือนคุยกับเพื่อน]"

── SCENE 2 · CONTEXT (3–10s) ──
[ON-SCREEN] "[ตัวเลข/ชื่อ key]"
[VO] "[เกิดอะไรขึ้น สั้นๆ 1–2 ประโยค — ถ้ามีศัพท์เทคนิค ใส่วงเล็บอธิบายสั้นๆ ให้มือใหม่ตามได้]"
[B-ROLL] [ภาพประกอบที่ควรใช้]

── SCENE 3 · มุมมอง/วิเคราะห์ (10–30s) — พระเอกของคลิป ──
[ON-SCREEN] "[insight/ความเห็นสั้น]"
[VO] "[ส่วนนี้ยาวสุดและสำคัญสุด: ให้มุมมองว่าข่าวนี้มองยังไง แปลว่าอะไรกับตลาด มีมุมที่คนอื่นไม่ค่อยพูดไหม จับตาอะไรต่อ — วิเคราะห์แบบมีจุดยืน ไม่ใช่เล่าข่าวซ้ำ]"
[B-ROLL] [ภาพประกอบ เช่น กราฟ/เทียบคู่แข่ง]

── SCENE 4 · CTA (30–35s) ──
[ON-SCREEN] "ไม่ใช่คำแนะนำการลงทุน · DYOR"
[VO] "[สรุปมุมมอง 1 ประโยค + ชวนคุยต่อ/ถามความเห็นคนดู แบบเพื่อนชวนคุย]"

📝 CAPTION: [แคปชั่น + hashtag]

กฎ:
- ใช้แต่ข้อมูลจากข่าวที่ให้มา ห้ามแต่งตัวเลข/รายละเอียดเพิ่ม (แต่ให้ "มุมมอง/การตีความ" ได้)
- ศัพท์เทคนิคใส่วงเล็บอธิบายสั้นๆ ครั้งแรกที่พูด (เพราะคนดูคละระดับ)
- โทนกันเองตลอด แต่ยังน่าเชื่อถือ ไม่มั่ว
- ใส่ disclaimer "ไม่ใช่คำแนะนำการลงทุน" ใน CTA เสมอ"""


def get_recent_messages():
    r = requests.get(
        "https://slack.com/api/conversations.history",
        headers=HEADERS,
        params={"channel": CHANNEL, "limit": LOOKBACK},
        timeout=20,
    )
    data = r.json()
    if not data.get("ok"):
        print(f"[slack] history error: {data.get('error')}")
        return []
    return data.get("messages", [])


def has_approve_reaction(msg):
    for rc in msg.get("reactions", []):
        if rc.get("name") == APPROVE_EMOJI and rc.get("count", 0) > 0:
            return True
    return False


def already_has_script(ts):
    # ดู thread ใต้ข่าว ว่ามี reply ของบอทที่เป็น script แล้วหรือยัง
    r = requests.get(
        "https://slack.com/api/conversations.replies",
        headers=HEADERS,
        params={"channel": CHANNEL, "ts": ts, "limit": 50},
        timeout=20,
    )
    data = r.json()
    if not data.get("ok"):
        print(f"[slack] replies error: {data.get('error')} (เช็คซ้ำไม่ได้)")
        return False
    for m in data.get("messages", []):
        if SCRIPT_MARKER in m.get("text", ""):
            return True
    return False


def make_script(client, news_text):
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=SCRIPT_SYSTEM,
        messages=[{"role": "user", "content": news_text}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def post_reply(ts, text):
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers=HEADERS,
        json={"channel": CHANNEL, "thread_ts": ts, "text": text},
        timeout=20,
    )
    data = r.json()
    if not data.get("ok"):
        print(f"[slack] reply error: {data.get('error')}")
        return False
    return True


def main():
    client = anthropic.Anthropic()
    messages = get_recent_messages()
    print(f"ดูข้อความล่าสุด {len(messages)} อัน")

    done = 0
    for msg in messages:
        ts = msg.get("ts")
        text = msg.get("text", "")
        if not ts or not text:
            continue
        if not has_approve_reaction(msg):
            continue
        if already_has_script(ts):
            print(f"ข่าวนี้มี script แล้ว ข้าม: {text[:50]}")
            continue

        print(f"เจอข่าว approve ใหม่: {text[:50]}")
        script = make_script(client, text)
        reply = f"{SCRIPT_HEADER}\n\n{script}"
        if post_reply(ts, reply):
            done += 1

    print(f"เจน script ใหม่ {done} ข่าว")


if __name__ == "__main__":
    main()

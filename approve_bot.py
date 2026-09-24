"""
approve_bot.py — Phase 2
ส่อง Slack ทุก ~10 นาที หาข่าวที่ถูก approve แล้วยังไม่มีงาน → เจนงานตามที่สั่ง → ตอบใต้ข่าว (thread)
 
วิธีสั่งงาน (ใช้ได้ทั้ง 2 แบบ ผสมกันก็ได้):
  1) กดอิโมจิเลือกรูปแบบ
       📱 :iphone:        → คลิปสั้น (30–60 วิ)
       🎬 :clapper:       → คลิปยาว (5–10 นาที)
       📝 :memo:          → บทความ
       ✅ :white_check_mark: → ไม่ระบุ = คลิปสั้น (เหมือนเดิม) หรือดูจากคำสั่งใน thread
  2) พิมพ์คำสั่งใน thread ใต้ข่าว เช่น
       "ทำบทความ ใช้ไอเดียเทียบ SET50 กับ S&P 500 เน้น DELTA"
       "คลิปยาว + คลิปสั้น โทนจริงจังขึ้นหน่อย"
     ถ้าในคำสั่งมีคำว่า คลิปสั้น / คลิปยาว / บทความ บอทจะทำรูปแบบนั้นให้ด้วย
     ⚠️ แนะนำให้พิมพ์คำสั่งก่อน แล้วค่อยกดอิโมจิ (บอทอาจรันก่อนพิมพ์เสร็จ)
 
แต่ละรูปแบบทำครั้งเดียวต่อข่าว (กันซ้ำด้วย marker ใน reply ของบอท)
 
ตั้งค่าผ่าน env: ANTHROPIC_API_KEY, SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
"""
 
import os
import requests
import anthropic
 
CHANNEL = os.environ["SLACK_CHANNEL_ID"]
BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
MODEL = "claude-sonnet-5"
LOOKBACK = 50                        # ดูข่าวย้อนหลังกี่ข้อความล่าสุด
 
HEADERS = {"Authorization": f"Bearer {BOT_TOKEN}"}
 
# รูปแบบงาน: อิโมจิ (ชื่อจริงใน Slack), คำที่จับในคำสั่ง, หัวข้อ reply, marker กันซ้ำ, max_tokens
FORMATS = {
    "short": {
        "emoji": "iphone",
        "keywords": ["คลิปสั้น"],
        "header": "📱 คลิปสั้น · SCENE + SCRIPT",
        "marker": "[OUT:short]",
        "max_tokens": 2000,
    },
    "long": {
        "emoji": "clapper",
        "keywords": ["คลิปยาว"],
        "header": "🎬 คลิปยาว · SCRIPT",
        "marker": "[OUT:long]",
        "max_tokens": 8000,
    },
    "article": {
        "emoji": "memo",
        "keywords": ["บทความ"],
        "header": "📝 บทความ",
        "marker": "[OUT:article]",
        "max_tokens": 5000,
    },
}
APPROVE_EMOJI = "white_check_mark"   # ✅ = approve แบบไม่ระบุรูปแบบ
DEFAULT_FORMAT = "short"
LEGACY_MARKER = "SCENE + SCRIPT"     # reply รุ่นเก่า ถือว่าทำคลิปสั้นไปแล้ว
 
# ────────────────────────────────────────────────────────────
# PROMPTS
# กลุ่มเป้าหมาย: เทรดเดอร์คละระดับ · โทนเพื่อนคุย · เน้นมุมมอง/วิเคราะห์
# ────────────────────────────────────────────────────────────
 
COMMON_RULES = """
━━ วิธีใช้ข้อมูลที่ได้รับ ━━
- ข้อความที่ได้รับคือโพสต์ข่าวใน Slack ซึ่งมี [ข่าวต้นเรื่อง] [มุมวิเคราะห์] [ข้อมูลที่ต้องไปหาเพิ่ม] และ [ไอเดียคอนเทนต์ต่อยอด]
- ให้ใช้ไอเดียในหมวดรูปแบบเดียวกับงานนี้เป็นตั้งต้น (เช่น งานคลิปยาว → ไอเดีย 🎬)
- ถ้ามี "คำสั่งจาก Pete" แนบมา คำสั่งนั้นสำคัญที่สุด ให้ทำตามก่อนเสมอ (เลือกไอเดีย เปลี่ยนมุม เน้นบริษัท ปรับโทน ฯลฯ)
- ตัวเลขที่ไม่ได้อยู่ในข่าว ห้ามแต่งเอง ให้เว้นเป็น [ใส่ข้อมูล: สิ่งที่ต้องไปหา] ให้ Pete ไปเติม
- ท้ายงาน ให้ใส่ "📌 ข้อมูลที่ต้องไปหาเพิ่ม" รวมทุกช่อง [ใส่ข้อมูล: ...] เป็นเช็กลิสต์"""
 
SHORT_SYSTEM_BODY = 'คุณคือครีเอเตอร์ช่อง TikTok เทรดเดอร์ทันข่าว\nรับข่าวสรุป (ไทย) มา แล้วแปลงเป็น Scene + Script คลิปสั้นแนวตั้ง 9:16 ยาว ~35–60 วินาที\n\nกลุ่มคนดู: เทรดเดอร์คละระดับ (มือใหม่จนถึงเก๋า) ในคลิปเดียว\nโทน: กันเอง เหมือนเล่าให้เพื่อนฟัง ไม่ใช่ผู้ประกาศข่าว ใช้สรรพนามเป็นกันเอง (เรา/นาย) ลดคำทางการ\nจุดขายของช่อง: ให้ "มุมมอง + วิเคราะห์" ไม่ใช่แค่รายงานว่าเกิดอะไร — คนดูมาเพราะอยากได้ความเห็นว่า "แล้วไง มองยังไง"\n\nออกมาตาม format นี้เป๊ะ:\n\n🎬 TITLE: [หัวข้อคลิป]\n⏱️ ~35 วิ\n\n── SCENE 1 · HOOK (0–3s) ──\n[ON-SCREEN] "[ข้อความ hook ตัวใหญ่]"\n[VO] "[ประโยคเปิดหยุดนิ้ว พูดกันเองเหมือนคุยกับเพื่อน]"\n\n── SCENE 2 · CONTEXT (3–10s) ──\n[ON-SCREEN] "[ตัวเลข/ชื่อ key]"\n[VO] "[เกิดอะไรขึ้น สั้นๆ 1–2 ประโยค — ถ้ามีศัพท์เทคนิค ใส่วงเล็บอธิบายสั้นๆ ให้มือใหม่ตามได้]"\n[B-ROLL] [ภาพประกอบที่ควรใช้]\n\n── SCENE 3 · มุมมอง/วิเคราะห์ (10–30s) — พระเอกของคลิป ──\n[ON-SCREEN] "[insight/ความเห็นสั้น]"\n[VO] "[ส่วนนี้ยาวสุดและสำคัญสุด: ให้มุมมองว่าข่าวนี้มองยังไง แปลว่าอะไรกับตลาด มีมุมที่คนอื่นไม่ค่อยพูดไหม จับตาอะไรต่อ — วิเคราะห์แบบมีจุดยืน ไม่ใช่เล่าข่าวซ้ำ]"\n[B-ROLL] [ภาพประกอบ เช่น กราฟ/เทียบคู่แข่ง]\n\n── SCENE 4 · CTA (30–35s) ──\n[ON-SCREEN] "ไม่ใช่คำแนะนำการลงทุน · DYOR"\n[VO] "[สรุปมุมมอง 1 ประโยค + ชวนคุยต่อ/ถามความเห็นคนดู แบบเพื่อนชวนคุย]"\n\n📝 CAPTION: [แคปชั่น + hashtag]\n\nกฎ:\n- ใช้แต่ข้อมูลจากข่าวที่ให้มา ห้ามแต่งตัวเลข/รายละเอียดเพิ่ม (แต่ให้ "มุมมอง/การตีความ" ได้)\n- ศัพท์เทคนิคใส่วงเล็บอธิบายสั้นๆ ครั้งแรกที่พูด (เพราะคนดูคละระดับ)\n- โทนกันเองตลอด แต่ยังน่าเชื่อถือ ไม่มั่ว\n- ใส่ disclaimer "ไม่ใช่คำแนะนำการลงทุน" ใน CTA เสมอ'
SHORT_SYSTEM = SHORT_SYSTEM_BODY + COMMON_RULES
 
LONG_SYSTEM = """คุณคือครีเอเตอร์ช่องเทรดเดอร์ทันข่าว เขียนสคริปต์คลิปยาวแนวนอน 16:9 ยาว 5–10 นาที (YouTube)
 
กลุ่มคนดู: เทรดเดอร์คละระดับ (มือใหม่จนถึงเก๋า)
โทน: กันเอง เหมือนเล่าให้เพื่อนฟัง แต่เจาะลึกกว่าคลิปสั้น มีเหตุผล มีข้อมูลรองรับ
จุดขาย: ใช้ข่าวเป็นจุดเริ่ม แล้วขยายไปเรื่องที่ใหญ่กว่าข่าว — "แล้วไง มองยังไง เทียบกับอะไร"
 
ออกมาตาม format นี้:
 
🎬 TITLE: [ชื่อคลิปแบบคำถามชวนคลิก]
🖼️ THUMBNAIL: [ข้อความบนปก 3–5 คำ + ไอเดียภาพ]
⏱️ ~[x] นาที
 
── 0:00 HOOK ──
[VO] ...
[ON-SCREEN] ...
 
── [เวลา] ช่วงที่ 1: [ชื่อช่วง] ──
[VO] ...
[ON-SCREEN / กราฟิก] ...
[B-ROLL] ...
 
(4–6 ช่วง เช่น เกิดอะไรขึ้น → เบื้องหลัง/ทำไม → เทียบ → มุมที่คนมองข้าม → แปลว่าอะไรกับนักลงทุน)
 
── [เวลา] สรุป + CTA ──
[VO] สรุปมุมมอง + ชวนคอมเมนต์ + "ไม่ใช่คำแนะนำการลงทุน"
 
📝 DESCRIPTION: [คำอธิบายคลิป + hashtag]
 
กฎ:
- ศัพท์เทคนิคอธิบายสั้นๆ ครั้งแรกที่พูด
- แต่ละช่วงต้องมีสิ่งที่โชว์บนจอ (ตัวเลข กราฟ ตาราง) ไม่ใช่พูดอย่างเดียว
- ไม่ชี้นำให้ซื้อ/ขายตัวไหน""" + COMMON_RULES
 
ARTICLE_SYSTEM = """คุณคือแอดมินเพจการลงทุน เขียนบทความวิเคราะห์ภาษาไทยสำหรับโพสต์ Facebook/เว็บ
 
สไตล์ (ยึดตามตัวอย่างนี้):
- เปิดด้วย "ล่าสุดแอดได้เห็นข่าว..." แล้วโยนคำถามที่ข่าวนี้ชวนให้สงสัย
- แทนตัวเองว่า "แอด" เรียกคนอ่านว่า "เพื่อน ๆ" ลงท้าย "ครับ"
- ค่อยๆ พาไปดูข้อมูล/การเทียบ ทีละขั้น ไม่ด่วนสรุป
- มีย่อหน้า "แต่ไม่ได้หมายความว่า..." เพื่อให้มุมที่สมดุล
- ปิดด้วยภาพอนาคต/คำตอบของคำถามตั้งต้น
 
ออกมาตาม format นี้:
 
📝 TITLE: [ชื่อบทความแบบคำถาม เช่น "ทำไมไทยต้องสร้างหุ้น New Economy มากขึ้น? เมื่อเทียบกับ S&P 500"]
🖼️ ปก: [ข้อความบนปก + ไอเดียภาพ]
 
[เนื้อบทความ 6–10 ย่อหน้าสั้น]
 
ผู้ลงทุนควรทำความเข้าใจลักษณะสินค้า เงื่อนไขผลตอบแทน และความเสี่ยงก่อนตัดสินใจลงทุน
 
#️⃣ [hashtag 3–5 อัน]
 
กฎ:
- ภาษาอ่านง่าย ย่อหน้าสั้น ไม่ทางการเกิน
- ไม่ชี้นำให้ซื้อ/ขายตัวไหน""" + COMMON_RULES
 
SYSTEMS = {"short": SHORT_SYSTEM, "long": LONG_SYSTEM, "article": ARTICLE_SYSTEM}
 
# ────────────────────────────────────────────────────────────
# SLACK
# ────────────────────────────────────────────────────────────
 
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
 
 
def get_thread(ts):
    r = requests.get(
        "https://slack.com/api/conversations.replies",
        headers=HEADERS,
        params={"channel": CHANNEL, "ts": ts, "limit": 100},
        timeout=20,
    )
    data = r.json()
    if not data.get("ok"):
        print(f"[slack] replies error: {data.get('error')}")
        return None
    return data.get("messages", [])[1:]  # ตัดข้อความข่าวต้นทางออก
 
 
def is_bot(m):
    return bool(m.get("bot_id")) or m.get("subtype") == "bot_message"
 
 
def reaction_names(msg):
    return {rc.get("name") for rc in msg.get("reactions", []) if rc.get("count", 0) > 0}
 
 
def wanted_formats(msg, instructions):
    """รวมรูปแบบที่ต้องทำ จากอิโมจิ + คำในคำสั่ง  (คืน [] = ข่าวนี้ยังไม่ถูก approve)"""
    names = reaction_names(msg)
    fmts = [k for k, f in FORMATS.items() if f["emoji"] in names]
    approved = bool(fmts) or APPROVE_EMOJI in names
    if not approved:
        return []
    low = instructions.lower()
    for k, f in FORMATS.items():
        if k not in fmts and any(w in low for w in f["keywords"]):
            fmts.append(k)
    return fmts or [DEFAULT_FORMAT]
 
 
def done_formats(thread):
    done = set()
    for m in thread:
        if not is_bot(m):
            continue
        t = m.get("text", "")
        for k, f in FORMATS.items():
            if f["marker"] in t:
                done.add(k)
        if LEGACY_MARKER in t and "[OUT:" not in t:
            done.add("short")
    return done
 
 
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
 
# ────────────────────────────────────────────────────────────
# GENERATE
# ────────────────────────────────────────────────────────────
 
def make_output(client, fmt, news_text, instructions):
    user = f"━━ โพสต์ข่าว ━━\n{news_text}"
    if instructions.strip():
        user += f"\n\n━━ คำสั่งจาก Pete (สำคัญที่สุด) ━━\n{instructions}"
    resp = client.messages.create(
        model=MODEL,
        max_tokens=FORMATS[fmt]["max_tokens"],
        system=SYSTEMS[fmt],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
 
 
def main():
    client = anthropic.Anthropic()
    messages = get_recent_messages()
    print(f"ดูข้อความล่าสุด {len(messages)} อัน")
 
    done_count = 0
    for msg in messages:
        ts, text = msg.get("ts"), msg.get("text", "")
        if not ts or not text or not msg.get("reactions"):
            continue
 
        thread = get_thread(ts) if msg.get("reply_count") else []
        if thread is None:
            continue  # อ่าน thread ไม่ได้ ข้ามไว้ก่อน กันทำซ้ำ
        instructions = "\n".join(m.get("text", "") for m in thread if not is_bot(m)).strip()
 
        todo = [f for f in wanted_formats(msg, instructions) if f not in done_formats(thread)]
        if not todo:
            continue
 
        print(f"ข่าว: {text[:50]} → ทำ {todo} | คำสั่ง: {instructions[:80] or '-'}")
        for fmt in todo:
            out = make_output(client, fmt, text, instructions)
            if not out:
                continue
            f = FORMATS[fmt]
            reply = f"{f['header']}  `{f['marker']}`\n\n{out}"
            if post_reply(ts, reply):
                done_count += 1
 
    print(f"เจนงานใหม่ {done_count} ชิ้น")
 
 
if __name__ == "__main__":
    main()
 

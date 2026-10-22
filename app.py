"""
College Admin Desk - Notices, Official Emails & Schedules for college administrators
provided by: e-websolutions.netlify.app
version:1.2.0
Owner: DATM-college-199
"""
import hmac
import html
import io
import json
import os
import re
import smtplib
import urllib.parse
import uuid
from collections import defaultdict
from datetime import date, datetime
from email.mime.text import MIMEText

import pandas as pd
import requests
import streamlit as st

# Optional PDF library — used for direct PDF download of drafts
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
                                    Table, TableStyle)
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    PDF_OK = True
except Exception:
    PDF_OK = False

# ------------------------------------------------------------------ config
st.set_page_config(page_title="College Admin Assistant", page_icon="🏛️", layout="wide")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
MAX_REQUESTS_PER_SESSION = 60
MAX_LOGIN_ATTEMPTS = 5
WA_API_VERSION = "v20.0"


def secret(name: str) -> str:
    """Read a value from Streamlit secrets (server side) or an environment variable."""
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return os.environ.get(name, "").strip()


API_KEY = secret("GROQ_API_KEY")
ADMIN_PASSWORD = secret("ADMIN_PASSWORD")
# Optional: default WhatsApp number(s) preset by the admin (e.g. "Principal, 9876543210")
WA_DEFAULT_CONTACTS = secret("WHATSAPP_NUMBER")
# Optional: WhatsApp Business Cloud API credentials for true one-click sending
WA_TOKEN = secret("WHATSAPP_TOKEN")
WA_PHONE_ID = secret("WHATSAPP_PHONE_ID")


@st.cache_data(ttl=3600, show_spinner=False)
def list_groq_models(key: str) -> list[str]:
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {key}"}, timeout=15)
        ids = [m["id"] for m in r.json().get("data", [])]
        skip = ("whisper", "guard", "tts", "playai", "orpheus")
        ids = sorted(i for i in ids if not any(s in i.lower() for s in skip))
        return ids or MODELS
    except Exception:
        return MODELS


# ------------------------------------------------------------------ styling
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:wght@400;600&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.block-container { padding-top: 1.6rem; max-width: 1280px; }
#MainMenu, footer { visibility: hidden; }
.hero {
  background: linear-gradient(120deg, #0f172a 0%, #1e3a8a 60%, #3b4cca 100%);
  color: #fff; padding: 26px 32px; border-radius: 16px; margin-bottom: 18px;
  box-shadow: 0 10px 30px rgba(30,58,138,.28);
}
.hero h1 { margin: 0; font-size: 1.9rem; font-weight: 700; letter-spacing: -.4px; color:#fff; }
.hero p  { margin: 6px 0 0; opacity: .88; font-size: 1rem; color:#fff; }
.stTabs [data-baseweb="tab"] { height: 46px; padding: 0 20px; font-weight: 600; }
div.stButton > button[kind="primary"] {
  background: linear-gradient(120deg, #1e3a8a, #3b4cca); border: 0; border-radius: 10px;
  font-weight: 600; padding: .55rem 1.2rem; color:#fff;
}
div.stButton > button[kind="primary"]:hover { filter: brightness(1.12); }
div.stDownloadButton > button, div.stLinkButton > a { border-radius: 10px; font-weight: 600; }
.paper {
  background: #fff; color: #1f2937; border: 1px solid #e5e7eb; border-radius: 4px;
  padding: 34px 42px; font-family: 'Source Serif 4', Georgia, serif; line-height: 1.65;
  box-shadow: 0 4px 18px rgba(0,0,0,.10);
}
.mail { background:#fff; color:#1f2937; border:1px solid #e5e7eb; border-radius:12px; overflow:hidden;
        box-shadow:0 4px 18px rgba(0,0,0,.08); }
.mail .bar { background:#f3f4f6; padding:12px 20px; font-size:.88rem; color:#4b5563; border-bottom:1px solid #e5e7eb; }
.mail .bar b { color:#111827; }
.mail .body { padding:22px 26px; line-height:1.65; }
.event { background: rgba(127,127,127,.08); border-left: 5px solid #1e3a8a; border-radius: 8px;
         padding: 10px 14px; margin-bottom: 8px; }
.event .t { font-weight: 600; }
.event .m { font-size: .86rem; opacity: .78; }
.chip { display:inline-block; background:#e0e7ff; color:#1e3a8a; border-radius:20px;
        padding:2px 12px; font-size:.78rem; font-weight:600; margin-right:6px; }
.share-row { display:flex; flex-wrap:wrap; gap:8px; }
.share-row > div { flex:1 1 160px; }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="hero">
  <h1>🏛️ College Admin Assistant</h1>
  <p>Official notices, correspondence and schedules for college administration — drafted in seconds, ready for your approval.</p>
</div>
""",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------ state
for k, v in {"requests_used": 0, "history": [], "authed": False, "fails": 0,
             "n_subject": "", "n_body": "", "e_subject": "", "e_body": "", "events": [],
             "wa_cc": "91", "wa_contacts": WA_DEFAULT_CONTACTS}.items():
    st.session_state.setdefault(k, v)

# ------------------------------------------------------------------ guards
if not API_KEY:
    st.error("**GROQ_API_KEY not found.**")
    st.markdown("Add it to `.streamlit/secrets.toml` (note the leading dot):\n"
                "```toml\nGROQ_API_KEY = \"gsk_your_key_here\"\nADMIN_PASSWORD = \"your-strong-password\"\n```")
    st.stop()

# Fail closed: the app never runs without an administrator password.
if not ADMIN_PASSWORD:
    st.error("**ADMIN_PASSWORD is not set.** The app is locked until you add it to your secrets.")
    st.markdown("Add it to `.streamlit/secrets.toml` (or Streamlit Cloud → Settings → Secrets):\n"
                "```toml\nADMIN_PASSWORD = \"a-long-strong-password\"\n```")
    st.stop()

if not st.session_state.authed:
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.subheader("🔒 Administrator sign-in")
        if st.session_state.fails >= MAX_LOGIN_ATTEMPTS:
            st.error("Too many failed attempts. Refresh the page and try again later.")
            st.stop()
        with st.form("login"):
            pw = st.text_input("Administrator password", type="password")
            ok = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if ok:
            if hmac.compare_digest(pw.encode(), ADMIN_PASSWORD.encode()):
                st.session_state.authed = True
                st.session_state.fails = 0
                st.rerun()
            else:
                st.session_state.fails += 1
                remaining = MAX_LOGIN_ATTEMPTS - st.session_state.fails
                st.error(f"Incorrect password. {max(remaining, 0)} attempt(s) left.")
    st.stop()


# ------------------------------------------------------------------ helpers
def call_llm(system: str, user: str, temperature: float = 0.4) -> str:
    if st.session_state.requests_used >= MAX_REQUESTS_PER_SESSION:
        raise RuntimeError("Session limit reached. Please sign in again later.")
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": st.session_state.model,
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
              "temperature": temperature, "max_tokens": 2000},
        timeout=90,
    )
    if r.status_code == 401:
        raise RuntimeError("API key rejected (401). Check GROQ_API_KEY in your secrets file.")
    if r.status_code == 429:
        raise RuntimeError("Rate limit reached. Wait a minute and try again.")
    if r.status_code != 200:
        raise RuntimeError(f"API error {r.status_code}")
    st.session_state.requests_used += 1
    return r.json()["choices"][0]["message"]["content"].strip()


def esc(t) -> str:
    return html.escape(str(t))


def to_html(text: str) -> str:
    return esc(text).replace("\n", "<br>")


def log(kind: str, title: str, content: str):
    st.session_state.history.insert(0, {"kind": kind, "title": title,
                                        "time": datetime.now().strftime("%d %b, %H:%M"), "content": content})


def set_state(**kw):
    for k, v in kw.items():
        st.session_state[k] = v


def split_subject(text: str):
    m = re.match(r"\s*Subject:\s*(.+?)\n+(.*)", text, re.DOTALL | re.IGNORECASE)
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", text)


def parse_json_list(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\[.*\]", text, re.DOTALL)
    return json.loads(m.group(0) if m else text)


def hours_between(s: str, e: str) -> float:
    try:
        return max((datetime.strptime(e, "%H:%M") - datetime.strptime(s, "%H:%M")).seconds / 3600, 0)
    except Exception:
        return 0.0


def make_ics(events: list[dict]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//College Admin Desk//EN"]
    for e in events:
        try:
            d = datetime.strptime(str(e["date"]), "%Y-%m-%d").strftime("%Y%m%d")
            s = datetime.strptime(str(e.get("start") or "09:00"), "%H:%M").strftime("%H%M%S")
            en = datetime.strptime(str(e.get("end") or "10:00"), "%H:%M").strftime("%H%M%S")
        except Exception:
            continue
        lines += ["BEGIN:VEVENT", f"UID:{uuid.uuid4()}@college-admin-desk",
                  f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
                  f"DTSTART:{d}T{s}", f"DTEND:{d}T{en}",
                  f"SUMMARY:{e.get('title', 'Event')}", f"LOCATION:{e.get('location', '')}",
                  f"DESCRIPTION:{e.get('notes', '')}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def profile() -> dict:
    g = st.session_state.get
    return {"inst": g("p_inst", ""), "addr": g("p_addr", ""), "name": g("p_name", ""),
            "desig": g("p_desig", ""), "office": g("p_office", "")}


def notice_inner(p: dict, title: str, ref: str, dt: str, subject: str, body: str, cc: str) -> str:
    """Letterhead document used for both the preview and the printable download."""
    addr = f'<div style="font-size:.85rem;color:#4b5563">{esc(p["addr"])}</div>' if p["addr"] else ""
    cc_html = f'<div style="margin-top:18px;font-size:.9rem"><b>Copy to:</b> {esc(cc)}</div>' if cc else ""
    sig = (f'<div style="margin-top:46px;text-align:right"><b>{esc(p["name"] or "[Name]")}</b><br>'
           f'{esc(p["desig"] or "[Designation]")}</br>{esc(p["office"])}</div>')
    return (
        f'<div style="text-align:center"><div style="font-size:1.45rem;font-weight:700;letter-spacing:.4px">'
        f'{esc(p["inst"] or "[Institution Name]")}</div>{addr}</div>'
        f'<hr style="border:0;border-top:2px solid #1e3a8a;margin:12px 0 14px">'
        f'<div style="display:flex;justify-content:space-between;font-size:.92rem">'
        f'<span><b>Ref. No.:</b> {esc(ref)}</span><span><b>Date:</b> {esc(dt)}</span></div>'
        f'<div style="text-align:center;font-weight:700;text-decoration:underline;margin:18px 0 12px;'
        f'letter-spacing:1px">{esc(title)}</div>'
        f'<div style="font-weight:700;margin-bottom:12px">Subject: {esc(subject)}</div>'
        f'<div>{to_html(body)}</div>{cc_html}{sig}'
    )


def notice_plain(p: dict, title: str, ref: str, dt: str, subject: str, body: str, cc: str) -> str:
    out = [p["inst"] or "[Institution Name]", p["addr"], "-" * 60, f"Ref. No.: {ref}    Date: {dt}", "",
           title, "", f"Subject: {subject}", "", body]
    if cc:
        out += ["", f"Copy to: {cc}"]
    out += ["", "", p["name"] or "[Name]", p["desig"] or "[Designation]", p["office"]]
    return "\n".join(x for x in out if x is not None)


def printable_html(inner: str) -> str:
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Notice</title><style>"
            "body{font-family:Georgia,serif;max-width:800px;margin:40px auto;padding:0 40px;line-height:1.65;color:#111}"
            "@media print{body{margin:0}}</style></head><body>" + inner + "</body></html>")


def email_signature(p: dict) -> str:
    return "\n".join(x for x in ["Regards,", p["name"], p["desig"], p["office"], p["inst"]] if x)


# ------------------------------------------------------------------ PDF generation
def _safe_para(text: str, style) -> Paragraph:
    """Escape special chars and convert newlines into <br/> for ReportLab."""
    if text is None:
        text = ""
    text = str(text)
    # ReportLab Paragraph uses XML-like markup, so escape & < > but preserve \n as <br/>
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("\n", "<br/>")
    return Paragraph(text, style)


def notice_pdf(p: dict, title: str, ref: str, dt: str, subject: str, body: str, cc: str) -> bytes:
    """Generate a professionally formatted notice PDF and return bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=18*mm, bottomMargin=18*mm,
                            title=f"{title} - {ref}", author=p.get("name", "College Admin"))
    styles = getSampleStyleSheet()
    inst_style = ParagraphStyle("Inst", parent=styles["Title"], fontSize=16, leading=20,
                                textColor=colors.HexColor("#1e3a8a"), spaceAfter=4, alignment=TA_CENTER)
    addr_style = ParagraphStyle("Addr", parent=styles["Normal"], fontSize=9, leading=12,
                                 textColor=colors.HexColor("#4b5563"), alignment=TA_CENTER, spaceAfter=6)
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=6)
    title_style = ParagraphStyle("NoticeTitle", parent=styles["Heading2"], fontSize=13, leading=18,
                                 alignment=TA_CENTER, spaceBefore=10, spaceAfter=10,
                                 textColor=colors.HexColor("#0f172a"))
    sub_style = ParagraphStyle("Subj", parent=styles["Normal"], fontSize=10.5, leading=14,
                               fontName="Helvetica-Bold", spaceAfter=10)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10.5, leading=16,
                                alignment=TA_LEFT, spaceAfter=10)
    cc_style = ParagraphStyle("CC", parent=styles["Normal"], fontSize=9.5, leading=13, spaceBefore=10)
    sig_style = ParagraphStyle("Sig", parent=styles["Normal"], fontSize=10.5, leading=14,
                               alignment=TA_RIGHT, spaceBefore=30)

    story = []
    # Letterhead
    story.append(_safe_para(p["inst"] or "[Institution Name]", inst_style))
    if p["addr"]:
        story.append(_safe_para(p["addr"], addr_style))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1e3a8a"),
                           spaceBefore=2, spaceAfter=10))
    # Ref + Date
    ref_tbl = Table([["Ref. No.: " + (ref or ""), "Date: " + (dt or "")]],
                    colWidths=[85*mm, 85*mm])
    ref_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(ref_tbl)
    story.append(Spacer(1, 6))
    # Notice title
    story.append(_safe_para(title, title_style))
    # Subject
    story.append(_safe_para("Subject: " + (subject or ""), sub_style))
    # Body
    story.append(_safe_para(body or "", body_style))
    # CC
    if cc:
        story.append(_safe_para("<b>Copy to:</b> " + cc, cc_style))
    # Signature
    sig_lines = [p["name"] or "[Name]", p["desig"] or "[Designation]", p["office"]]
    sig_text = "<br/>".join(x for x in sig_lines if x)
    story.append(_safe_para(sig_text, sig_style))

    doc.build(story)
    return buf.getvalue()


def email_pdf(to: str, cc_addr: str, subject: str, body: str, p: dict) -> bytes:
    """Generate a PDF of the email draft and return bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=18*mm, bottomMargin=18*mm,
                            title=f"Email - {subject}", author=p.get("name", "College Admin"))
    styles = getSampleStyleSheet()
    header_label = ParagraphStyle("HL", parent=styles["Normal"], fontSize=10, leading=14,
                                  fontName="Helvetica-Bold", textColor=colors.HexColor("#4b5563"))
    header_val = ParagraphStyle("HV", parent=styles["Normal"], fontSize=10.5, leading=15)
    sub_style = ParagraphStyle("Subj", parent=styles["Heading2"], fontSize=13, leading=18,
                               spaceBefore=8, spaceAfter=12, textColor=colors.HexColor("#0f172a"))
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10.5, leading=16, spaceAfter=10)

    story = []
    story.append(_safe_para("Email Draft", ParagraphStyle(
        "Ed", parent=styles["Title"], fontSize=14, leading=18, alignment=TA_CENTER,
        textColor=colors.HexColor("#1e3a8a"), spaceAfter=12)))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb"),
                           spaceBefore=0, spaceAfter=10))
    meta_rows = [["To:", to or "—"]]
    if cc_addr:
        meta_rows.append(["CC:", cc_addr])
    meta_rows.append(["From:", p.get("name", "") + ("  (" + p.get("office") + ")" if p.get("office") else "")])
    meta_rows.append(["Date:", datetime.now().strftime("%d %B %Y, %H:%M")])
    meta_tbl = Table(meta_rows, colWidths=[22*mm, 148*mm])
    meta_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#4b5563")),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 8))
    story.append(_safe_para("Subject: " + (subject or ""), sub_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb"),
                           spaceBefore=0, spaceAfter=10))
    story.append(_safe_para(body or "", body_style))
    doc.build(story)
    return buf.getvalue()


def schedule_pdf(kind: str, events: list[dict], p: dict) -> bytes:
    """Generate a PDF table of the schedule and return bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm,
                            title=f"Schedule - {kind}", author=p.get("name", "College Admin"))
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=15, leading=20,
                                 alignment=TA_CENTER, textColor=colors.HexColor("#1e3a8a"))
    sub_style = ParagraphStyle("S", parent=styles["Normal"], fontSize=10, leading=14,
                               alignment=TA_CENTER, textColor=colors.HexColor("#4b5563"))

    story = []
    story.append(_safe_para(p["inst"] or "[Institution Name]", title_style))
    story.append(_safe_para(kind, ParagraphStyle("K", parent=styles["Heading2"], fontSize=12,
                  leading=16, alignment=TA_CENTER, spaceAfter=4, textColor=colors.HexColor("#0f172a"))))
    story.append(_safe_para(f"Generated: {datetime.now().strftime('%d %B %Y, %H:%M')}", sub_style))
    story.append(Spacer(1, 10))

    by_day = defaultdict(list)
    for e in sorted(events, key=lambda x: (str(x["date"]), str(x["start"]))):
        by_day[str(e["date"])].append(e)

    header_style = ParagraphStyle("h", parent=styles["Normal"], fontSize=9.5, leading=12,
                                  fontName="Helvetica-Bold", textColor=colors.white)
    cell_style = ParagraphStyle("c", parent=styles["Normal"], fontSize=9, leading=12)
    cell_bold = ParagraphStyle("cb", parent=styles["Normal"], fontSize=9, leading=12, fontName="Helvetica-Bold")

    for d, items in by_day.items():
        try:
            label = datetime.strptime(d, "%Y-%m-%d").strftime("%A, %d %B %Y")
        except Exception:
            label = d
        story.append(_safe_para(label, ParagraphStyle("Day", parent=styles["Heading3"], fontSize=11,
                      leading=14, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1e3a8a"))))
        data = [[
            _safe_para("Time", header_style),
            _safe_para("Title", header_style),
            _safe_para("Location", header_style),
            _safe_para("Notes", header_style),
        ]]
        for e in items:
            data.append([
                _safe_para(f"{e.get('start','')} – {e.get('end','')}", cell_bold),
                _safe_para(e.get("title", ""), cell_style),
                _safe_para(e.get("location", "") or "—", cell_style),
                _safe_para(e.get("notes", "") or "—", cell_style),
            ])
        tbl = Table(data, colWidths=[30*mm, 50*mm, 45*mm, 60*mm], repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 4))

    if p.get("name"):
        story.append(Spacer(1, 14))
        story.append(_safe_para(
            f"— {p['name']}" + (f", {p['desig']}" if p.get("desig") else ""),
            ParagraphStyle("Sig", parent=styles["Normal"], fontSize=10, leading=14,
                           alignment=TA_RIGHT, textColor=colors.HexColor("#374151"))))

    doc.build(story)
    return buf.getvalue()


# ------------------------------------------------------------------ whatsapp / mail share helpers
def normalize_phone(raw: str, cc: str = "91") -> str:
    """Return digits-only international number (no +), or '' if it doesn't look valid."""
    digits = re.sub(r"\D", "", str(raw))
    cc = re.sub(r"\D", "", str(cc)) or "91"
    if digits.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = cc + digits[1:]
    elif len(digits) == 10:
        digits = cc + digits
    return digits if 11 <= len(digits) <= 15 else ""


def parse_contacts(text: str, cc: str = "91") -> list[tuple[str, str]]:
    """Parse 'Name, number' lines (or bare numbers) into [(name, digits)]."""
    out, seen = [], set()
    for i, line in enumerate(re.split(r"[\n;]+", text or ""), 1):
        line = line.strip()
        if not line:
            continue
        m = re.search(r"\+?\d[\d\s\-()]{7,}\d", line)
        if not m:
            continue
        num = normalize_phone(m.group(0), cc)
        if not num or num in seen:
            continue
        name = (line[:m.start()] + line[m.end():]).strip(" ,:-–—")
        out.append((name or f"Contact {i}", num))
        seen.add(num)
    return out


def wa_link(number: str, text: str) -> str:
    return f"https://wa.me/{number}?text={urllib.parse.quote(text)}"


def wa_web_link(text: str) -> str:
    """WhatsApp Web share link — opens in desktop WhatsApp or web.whatsapp.com."""
    return f"https://web.whatsapp.com/send?text={urllib.parse.quote(text)}"


def gmail_compose_url(to: str, subject: str, body: str, cc: str = "") -> str:
    """Open Gmail compose window in the browser with pre-filled fields."""
    params = {"view": "cm", "fs": 1, "to": to or "", "su": subject or "", "body": body or ""}
    if cc:
        params["cc"] = cc
    return "https://mail.google.com/mail/?" + urllib.parse.urlencode(params)


def outlook_compose_url(to: str, subject: str, body: str, cc: str = "") -> str:
    """Open Outlook (consumer / live.com) compose window with pre-filled fields."""
    params = {"path": "/mail/action/compose", "to": to or "", "subject": subject or "", "body": body or ""}
    if cc:
        params["cc"] = cc
    return "https://outlook.live.com/mail/0/deeplink/compose?" + urllib.parse.urlencode(params)


def outlook_business_compose_url(to: str, subject: str, body: str, cc: str = "") -> str:
    """Open Outlook 365 / Office Online compose window (work or school account)."""
    params = {"path": "/mail/action/compose", "to": to or "", "subject": subject or "", "body": body or ""}
    if cc:
        params["cc"] = cc
    return "https://outlook.office.com/mail/0/deeplink/compose?" + urllib.parse.urlencode(params)


def mailto_url(to: str, subject: str, body: str, cc: str = "") -> str:
    """Standard mailto: link — opens default mail client."""
    params = {"subject": subject or "", "body": body or ""}
    if cc:
        params["cc"] = cc
    return f"mailto:{urllib.parse.quote(to or '')}?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def send_whatsapp_api(number: str, text: str):
    """Send a text message through the WhatsApp Business Cloud API."""
    r = requests.post(
        f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_ID}/messages",
        headers={"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"},
        json={"messaging_product": "whatsapp", "to": number, "type": "text",
              "text": {"preview_url": False, "body": text[:4096]}},
        timeout=30,
    )
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise RuntimeError(f"WhatsApp API error {r.status_code}: {msg}")


def wa_notice_text(p: dict, title: str, ref: str, dt: str, subject: str, body: str, cc: str) -> str:
    parts = [f"*{p['inst'] or '[Institution Name]'}*"]
    if p["addr"]:
        parts.append(p["addr"])
    parts += ["━━━━━━━━━━", f"*{title}*", f"Ref. No.: {ref}", f"Date: {dt}", "",
              f"*Subject:* {subject}", "", body]
    if cc:
        parts += ["", f"_Copy to: {cc}_"]
    parts += ["", f"*{p['name'] or '[Name]'}*"]
    parts += [x for x in (p["desig"], p["office"]) if x]
    return "\n".join(parts)


def wa_email_text(subject: str, body: str) -> str:
    return f"*Subject:* {subject}\n\n{body}"


def wa_schedule_text(kind: str, events: list[dict], p: dict) -> str:
    by_day = defaultdict(list)
    for e in sorted(events, key=lambda x: (str(x["date"]), str(x["start"]))):
        by_day[str(e["date"])].append(e)
    parts = [f"*{p['inst'] or '[Institution Name]'}*", f"*{kind}*", ""]
    for d, items in by_day.items():
        try:
            label = datetime.strptime(d, "%Y-%m-%d").strftime("%a, %d %b %Y")
        except Exception:
            label = d
        parts.append(f"📅 *{label}*")
        for e in items:
            line = f"• {e['start']}–{e['end']}  {e['title']}"
            if e["location"]:
                line += f" ({e['location']})"
            if e["notes"]:
                line += f" — {e['notes']}"
            parts.append(line)
        parts.append("")
    if p["name"]:
        parts.append(f"— {p['name']}, {p['desig']}".rstrip(", "))
    return "\n".join(parts).strip()


# ------------------------------------------------------------------ shared share panel
def share_panel(*, kind: str, default_text: str, email_to: str = "", email_cc: str = "",
                email_subject: str = "", email_body: str = "",
                pdf_bytes: bytes = None, pdf_name: str = "document.pdf"):
    """
    Unified share panel that offers:
      • WhatsApp (per contact) — opens wa.me with pre-filled text
      • WhatsApp Web — opens web.whatsapp.com share window
      • Gmail — opens Gmail compose window with pre-filled subject/body
      • Outlook — opens Outlook web compose window
      • Outlook 365 — opens Outlook Office Online compose window
      • Default mail client (mailto:)
      • Direct PDF download (if pdf_bytes supplied)
    """
    st.markdown("#### 🔗 Share this draft")
    # ----- PDF download (always available if pdf_bytes given) -----
    if pdf_bytes is not None:
        st.download_button(
            "📄 Download as PDF",
            data=pdf_bytes,
            file_name=pdf_name,
            mime="application/pdf",
            use_container_width=True,
            key=f"dl_pdf_{kind}")
        st.caption("Save the formatted PDF straight to your device — no need to print-to-PDF.")
        st.divider()

    # ----- WhatsApp block -----
    with st.expander("💬 Share via WhatsApp", expanded=False):
        contacts = parse_contacts(st.session_state.get("wa_contacts", ""),
                                  st.session_state.get("wa_cc", "91"))
        if not contacts:
            st.info("No WhatsApp number saved. Add one in the sidebar under **💬 WhatsApp**, "
                    "or enter a number below.")
        options = {f"{n} (+{num})": num for n, num in contacts}
        chosen = st.multiselect("Send to (numbers set by admin)", list(options),
                                default=list(options)[:1], key=f"wa_sel_{kind}")
        extra = st.text_input("Or another number (optional)", key=f"wa_extra_{kind}",
                              placeholder="e.g. 9876543210 or +91 98765 43210")
        numbers = [options[c] for c in chosen]
        if extra.strip():
            n = normalize_phone(extra, st.session_state.get("wa_cc", "91"))
            if n:
                numbers.append(n)
            else:
                st.warning("The extra number does not look valid.")
        numbers = list(dict.fromkeys(numbers))

        message = st.text_area("WhatsApp message (editable before sending)",
                               default_text, height=200, key=f"wa_msg_{kind}")
        if len(urllib.parse.quote(message)) > 3000:
            st.caption("⚠️ Long message — WhatsApp links may truncate it; consider shortening or use Business API.")

        # WhatsApp Web — universal "pick a chat" link
        st.link_button("💬 Open WhatsApp Web (choose contact)",
                       wa_web_link(message), use_container_width=True,
                       help="Opens WhatsApp Web where you can pick the recipient.")

        if not numbers:
            st.caption("Select or enter at least one number to enable direct WhatsApp deep links.")
        else:
            for n in numbers:
                label = next((k for k, v in options.items() if v == n), f"+{n}")
                st.link_button(f"💬 Open WhatsApp → {label}", wa_link(n, message),
                               use_container_width=True, key=f"wa_open_{kind}_{n}")

        if WA_TOKEN and WA_PHONE_ID:
            st.divider()
            st.caption("Business API: sends straight from the college's WhatsApp Business number. "
                       "Free-form text only works for recipients who messaged that number in the last 24 hours; "
                       "otherwise an approved template is required.")
            if st.button("🚀 Send now via WhatsApp Business API", key=f"wa_api_{kind}", type="primary"):
                ok_n, errs = 0, []
                for n in numbers:
                    try:
                        send_whatsapp_api(n, message)
                        ok_n += 1
                    except Exception as ex:
                        errs.append(f"+{n}: {ex}")
                if ok_n:
                    st.success(f"Sent to {ok_n} recipient(s).")
                    log("WhatsApp", f"{kind} → {ok_n} recipient(s)", message)
                for e in errs:
                    st.error(e)

    # ----- Email block (Gmail / Outlook / Outlook 365 / mail client) -----
    with st.expander("✉️ Share via Email (Gmail / Outlook / Mail app)", expanded=False):
        em_to = st.text_input("To (email address)", value=email_to, key=f"em_to_{kind}",
                              placeholder="recipient@example.com")
        em_cc = st.text_input("CC (optional)", value=email_cc, key=f"em_cc_{kind}")
        em_subj = st.text_input("Subject", value=email_subject, key=f"em_subj_{kind}")
        em_body = st.text_area("Body", value=email_body or default_text, height=200,
                               key=f"em_body_{kind}")

        # Resolved query strings (computed fresh each render)
        gmail_url = gmail_compose_url(em_to, em_subj, em_body, em_cc)
        outlook_url = outlook_compose_url(em_to, em_subj, em_body, em_cc)
        outlook365_url = outlook_business_compose_url(em_to, em_subj, em_body, em_cc)
        mailto = mailto_url(em_to, em_subj, em_body, em_cc)

        st.markdown("**Open compose window in:**")
        g1, g2, g3, g4 = st.columns(4)
        g1.link_button("🟥 Gmail", gmail_url, use_container_width=True,
                       help="Opens Gmail in a new browser tab with fields pre-filled.")
        g2.link_button("🟦 Outlook", outlook_url, use_container_width=True,
                       help="Opens Outlook (outlook.live.com) with fields pre-filled.")
        g3.link_button("🟦 Outlook 365", outlook365_url, use_container_width=True,
                       help="Opens Outlook Office Online (work/school) with fields pre-filled.")
        g4.link_button("📨 Mail app", mailto, use_container_width=True,
                       help="Opens your default desktop mail client.")
        st.caption("Tip: Gmail/Outlook web compose only works in a browser where you are signed in. "
                   "Long bodies may need manual paste if the URL gets truncated by the browser.")


# Backward-compatible alias used by older code paths (still produces a panel).
def render_whatsapp(default_text: str, kind: str):
    share_panel(kind=kind, default_text=default_text)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    available = list_groq_models(API_KEY)
    idx = available.index("llama-3.3-70b-versatile") if "llama-3.3-70b-versatile" in available else 0
    picked = st.selectbox("Groq Cloud model", available, index=idx)
    custom = st.text_input("Or type a model ID (optional)", placeholder="e.g. openai/gpt-oss-120b")
    st.session_state.model = custom.strip() or picked

    st.markdown("### 🏛️ Institution & signatory")
    st.text_input("Institution name", key="p_inst", placeholder="ABC College of Arts & Science")
    st.text_input("Address / contact line", key="p_addr", placeholder="Siliguri, West Bengal · www.abc.edu · 0353-xxxxxxx")
    st.text_input("Signatory name", key="p_name", placeholder="Dr. A. Sharma")
    st.text_input("Designation", key="p_desig", placeholder="Registrar")
    st.text_input("Office / Department", key="p_office", placeholder="Office of the Registrar")

    st.markdown("### 💬 WhatsApp")
    st.text_input("Default country code", key="wa_cc", help="Used for 10-digit numbers, e.g. 91 for India.")
    st.text_area("Recipient numbers (one per line: Name, number)", key="wa_contacts", height=110,
                 placeholder="Principal, 9876543210\nOffice Head, +91 98765 43210")
    _wa_count = len(parse_contacts(st.session_state.wa_contacts, st.session_state.wa_cc))
    st.caption(f"{_wa_count} valid number(s) saved for this session.")
    st.caption("To keep numbers permanently, set `WHATSAPP_NUMBER` in your secrets.")

    if not PDF_OK:
        st.warning("⚠️ `reportlab` not installed — PDF download will be hidden. "
                   "Run `pip install reportlab` to enable it.")

    st.markdown("### 📊 Usage")
    used = st.session_state.requests_used
    st.progress(min(used / MAX_REQUESTS_PER_SESSION, 1.0))
    st.caption(f"{used} / {MAX_REQUESTS_PER_SESSION} generations this session")
    if st.button("Sign out", use_container_width=True):
        st.session_state.authed = False
        st.rerun()
    st.caption("AI drafts must be reviewed and approved by an authorised officer before issue.")

P = profile()
tab_notice, tab_email, tab_sched, tab_rec = st.tabs(
    ["📢 Notices & Circulars", "✉️ Official Email", "🗓️ Schedules & Rosters", "🗂️ Records"])

# ================================================================== NOTICES
with tab_notice:
    left, right = st.columns([1, 1.1], gap="large")
    with left:
        st.subheader("Draft a notice / circular")
        st.caption("Quick examples")
        q1, q2, q3 = st.columns(3)
        q1.button("📝 Exam", key="nq1", use_container_width=True, on_click=set_state, kwargs=dict(
            n_type="Examination notification",
            n_details="End-semester examinations begin 3 December. Detailed timetable to follow. Students must carry ID card and admit card. Fee dues must be cleared before 25 November."))
        q2.button("💰 Fees", key="nq2", use_container_width=True, on_click=set_state, kwargs=dict(
            n_type="Fee payment notice",
            n_details="Last date for semester fee payment is 30 November without fine. Late fee Rs. 50 per day thereafter. Pay at the accounts counter or online portal."))
        q3.button("🏖️ Holiday", key="nq3", use_container_width=True, on_click=set_state, kwargs=dict(
            n_type="Holiday / closure",
            n_details="College closed on 12 November for Diwali. Offices and classes resume 14 November."))

        n_types = ["Circular", "Examination notification", "Result announcement", "Admission notice",
                   "Fee payment notice", "Holiday / closure", "Academic calendar update",
                   "Faculty / staff meeting", "Event / Convocation / Seminar", "Policy or rule change",
                   "Disciplinary / conduct notice", "Attendance / compliance notice",
                   "Scholarship / Placement", "Emergency / Safety", "Other"]
        st.selectbox("Notice type", n_types, key="n_type")
        c1, c2 = st.columns(2)
        c1.text_input("Reference no.", key="n_ref", value=f"ADM/{date.today().year}/001")
        n_date = c2.date_input("Date of issue", date.today(), key="n_date")
        st.multiselect("Audience", ["Students", "Faculty", "Non-teaching staff", "Parents",
                                    "Department heads", "All stakeholders"], default=["Students"], key="n_aud")
        st.text_input("Copy to", key="n_cc", placeholder="Principal; All HODs; Notice Board; Website")
        c3, c4 = st.columns(2)
        n_tone = c3.selectbox("Tone", ["Formal", "Formal and firm", "Courteous", "Urgent"], key="n_tone")
        n_len = c4.selectbox("Length", ["Short", "Medium", "Detailed"], index=1, key="n_len")
        st.text_area("Key details (facts the notice must contain)", key="n_details", height=140,
                     placeholder="Dates, venue, eligibility, deadlines, fines, contact person...")
        if st.button("✨ Generate notice", type="primary", key="gen_notice", use_container_width=True):
            if not st.session_state.n_details.strip():
                st.warning("Please add the key details first.")
            else:
                with st.status("Drafting notice...", expanded=False) as status:
                    try:
                        out = call_llm(
                            "You are a drafting assistant for a college administrative office. Write the SUBJECT and "
                            "BODY of an official notice in formal administrative English. First line must be "
                            "'Subject: ...'. Then a blank line, then the body (short paragraphs; numbered points for "
                            "instructions or rules). Do NOT include letterhead, reference number, date, the word "
                            "NOTICE, 'Copy to' or any signature: the system adds them. Never invent facts, dates, "
                            "names or amounts; if a needed detail is missing, write it in [square brackets].",
                            f"Type: {st.session_state.n_type}\nAudience: {', '.join(st.session_state.n_aud)}\n"
                            f"Tone: {n_tone}\nLength: {n_len}\nInstitution: {P['inst']}\n"
                            f"Facts: {st.session_state.n_details}")
                        st.session_state.n_subject, st.session_state.n_body = split_subject(out)
                        log("Notice", f"{st.session_state.n_ref} · {st.session_state.n_type}",
                            notice_plain(P, "NOTICE", st.session_state.n_ref, f"{n_date:%d %B %Y}",
                                         st.session_state.n_subject, st.session_state.n_body, st.session_state.n_cc))
                        status.update(label="Notice ready", state="complete")
                    except Exception as ex:
                        status.update(label="Failed", state="error")
                        st.error(str(ex))
    with right:
        st.subheader("Preview")
        if st.session_state.n_body:
            title = "CIRCULAR" if st.session_state.n_type == "Circular" else "NOTICE"
            ds = f"{n_date:%d %B %Y}"
            inner = notice_inner(P, title, st.session_state.n_ref, ds, st.session_state.n_subject,
                                 st.session_state.n_body, st.session_state.n_cc)
            st.markdown(f'<div class="paper">{inner}</div>', unsafe_allow_html=True)
            with st.expander("✏️ Edit subject / body"):
                st.session_state.n_subject = st.text_input("Subject", st.session_state.n_subject)
                st.session_state.n_body = st.text_area("Body", st.session_state.n_body, height=280)

            # Plain text + HTML downloads (legacy)
            d1, d2 = st.columns(2)
            d1.download_button("🖨️ Printable (.html → Print / PDF)", printable_html(inner),
                               file_name="notice.html", mime="text/html", use_container_width=True)
            d2.download_button("⬇️ Plain text (.txt)",
                               notice_plain(P, title, st.session_state.n_ref, ds, st.session_state.n_subject,
                                            st.session_state.n_body, st.session_state.n_cc),
                               file_name="notice.txt", use_container_width=True)
            st.caption("Use the panel below to download as PDF, share on WhatsApp, Gmail or Outlook.")

            # PDF bytes for direct download inside share panel
            pdf_bytes = None
            if PDF_OK:
                try:
                    pdf_bytes = notice_pdf(P, title, st.session_state.n_ref, ds,
                                           st.session_state.n_subject,
                                           st.session_state.n_body, st.session_state.n_cc)
                except Exception as ex:
                    st.warning(f"PDF preview not built: {ex}")

            share_panel(
                kind="notice",
                default_text=wa_notice_text(P, title, st.session_state.n_ref, ds,
                                            st.session_state.n_subject,
                                            st.session_state.n_body, st.session_state.n_cc),
                email_subject=f"[Notice] {st.session_state.n_subject}",
                email_body=notice_plain(P, title, st.session_state.n_ref, ds,
                                        st.session_state.n_subject,
                                        st.session_state.n_body, st.session_state.n_cc),
                pdf_bytes=pdf_bytes,
                pdf_name=f"{st.session_state.n_ref or 'notice'}.pdf",
            )
        else:
            st.info("Your notice will appear here on a letterhead.")

# ================================================================== EMAIL
with tab_email:
    left, right = st.columns([1, 1.1], gap="large")
    with left:
        st.subheader("Write official correspondence")
        st.caption("Quick examples")
        q1, q2, q3 = st.columns(3)
        q1.button("👪 Parents", key="eq1", use_container_width=True, on_click=set_state, kwargs=dict(
            e_kind="Parent communication", e_to_name="Parents / Guardians",
            e_details="Inform parents about the parent-teacher meeting on 20 November, 10 AM, college auditorium. Attendance requested. Discussion on mid-term performance and attendance."))
        q2.button("🎓 University", key="eq2", use_container_width=True, on_click=set_state, kwargs=dict(
            e_kind="University / Government correspondence", e_to_name="The Registrar, University",
            e_details="Request for extension of the affiliation document submission deadline by 10 days because of pending verification."))
        q3.button("🤝 Vendor", key="eq3", use_container_width=True, on_click=set_state, kwargs=dict(
            e_kind="Vendor / Procurement", e_to_name="Sales Manager",
            e_details="Request a quotation for 40 desktop computers for the computer lab with 3-year warranty. Delivery within 30 days. Quotation due by 28 November."))

        e_kinds = ["Parent communication", "Faculty / staff circular", "Department request",
                   "University / Government correspondence", "Accreditation (NAAC / NBA / AISHE)",
                   "Vendor / Procurement", "Meeting invitation", "Student disciplinary notice",
                   "Appointment / joining communication", "Reminder / follow-up", "Other"]
        st.selectbox("Purpose", e_kinds, key="e_kind")
        c1, c2 = st.columns(2)
        c1.text_input("Recipient name / designation", key="e_to_name", placeholder="The Registrar")
        e_to = c2.text_input("To (email addresses, comma separated)", key="e_to")
        e_cc = st.text_input("CC (optional)", key="e_cc")
        c3, c4 = st.columns(2)
        e_tone = c3.selectbox("Tone", ["Formal", "Courteous", "Firm", "Concise"], key="e_tone")
        e_lang = c4.selectbox("Language", ["English", "Hindi", "Bengali"], key="e_lang")
        st.text_area("What should the email say?", key="e_details", height=130,
                     placeholder="State the purpose, dates, requests and any deadline...")
        if st.button("✨ Generate email", type="primary", key="gen_email", use_container_width=True):
            if not st.session_state.e_details.strip():
                st.warning("Please describe what the email should say.")
            else:
                with st.status("Drafting email...", expanded=False) as status:
                    try:
                        out = call_llm(
                            "You draft official emails for a college administrative office. First line: "
                            "'Subject: ...', then a blank line, then greeting and body. Do NOT write a sign-off or "
                            "signature; it is added automatically. No markdown. Never invent facts; put missing "
                            "details in [square brackets].",
                            f"Purpose: {st.session_state.e_kind}\nTo: {st.session_state.e_to_name}\n"
                            f"Tone: {e_tone}\nLanguage: {e_lang}\nInstitution: {P['inst']}\n"
                            f"Details: {st.session_state.e_details}")
                        subj, body = split_subject(out)
                        st.session_state.e_subject = subj
                        st.session_state.e_body = body + "\n\n" + email_signature(P)
                        log("Email", st.session_state.e_kind, f"Subject: {subj}\n\n{st.session_state.e_body}")
                        status.update(label="Email ready", state="complete")
                    except Exception as ex:
                        status.update(label="Failed", state="error")
                        st.error(str(ex))
    with right:
        st.subheader("Preview")
        if st.session_state.e_body:
            st.markdown(
                f'<div class="mail"><div class="bar"><b>To:</b> {esc(e_to or "—")}'
                f'{"<br><b>CC:</b> " + esc(e_cc) if e_cc else ""}<br>'
                f'<b>Subject:</b> {esc(st.session_state.e_subject)}</div>'
                f'<div class="body">{to_html(st.session_state.e_body)}</div></div>',
                unsafe_allow_html=True)
            with st.expander("✏️ Edit email"):
                st.session_state.e_subject = st.text_input("Subject", st.session_state.e_subject)
                st.session_state.e_body = st.text_area("Body", st.session_state.e_body, height=260)

            # Plain text download (legacy)
            st.download_button("⬇️ Download (.txt)",
                               f"Subject: {st.session_state.e_subject}\n\n{st.session_state.e_body}",
                               file_name="email.txt", use_container_width=True)
            st.caption("Use the panel below to download as PDF or share via WhatsApp, Gmail or Outlook.")

            # PDF bytes
            pdf_bytes = None
            if PDF_OK:
                try:
                    pdf_bytes = email_pdf(e_to, e_cc, st.session_state.e_subject,
                                          st.session_state.e_body, P)
                except Exception as ex:
                    st.warning(f"PDF preview not built: {ex}")

            share_panel(
                kind="email",
                default_text=wa_email_text(st.session_state.e_subject, st.session_state.e_body),
                email_to=e_to,
                email_cc=e_cc,
                email_subject=st.session_state.e_subject,
                email_body=st.session_state.e_body,
                pdf_bytes=pdf_bytes,
                pdf_name="email.pdf",
            )

            # SMTP section (unchanged)
            with st.expander("🚀 Send directly via SMTP (optional)"):
                st.caption("Use your official mailbox with an App Password. Credentials are used only for this send.")
                s1, s2 = st.columns(2)
                host = s1.text_input("SMTP host", "smtp.gmail.com")
                port = s2.number_input("Port", value=465)
                user = st.text_input("Sender email (login)")
                pw = st.text_input("App password", type="password")
                if st.button("Send now", key="smtp_send"):
                    try:
                        recipients = [a.strip() for a in (e_to + "," + e_cc).split(",") if a.strip()]
                        msg = MIMEText(st.session_state.e_body, "plain", "utf-8")
                        msg["Subject"], msg["From"], msg["To"] = st.session_state.e_subject, user, e_to
                        if e_cc:
                            msg["Cc"] = e_cc
                        with smtplib.SMTP_SSL(host, int(port)) as s:
                            s.login(user, pw)
                            s.sendmail(user, recipients, msg.as_string())
                        st.success("Email sent successfully.")
                    except Exception as ex:
                        st.error(f"Could not send: {ex}")
        else:
            st.info("Your email preview will appear here.")

# ================================================================== SCHEDULES
with tab_sched:
    left, right = st.columns([1, 1.25], gap="large")
    with left:
        st.subheader("Plan a schedule or roster")
        st.selectbox("Schedule type", ["Examination timetable", "Invigilation duty roster",
                                       "Academic calendar", "Meeting / committee schedule",
                                       "Event / Convocation plan", "Faculty class timetable",
                                       "Admission / counselling schedule"], key="s_kind")
        c1, c2 = st.columns(2)
        s_start = c1.date_input("Start date", date.today(), key="s_start")
        s_end = c2.date_input("End date", date.today(), key="s_end")
        c3, c4 = st.columns(2)
        s_from = c3.text_input("Daily start time", "09:00", key="s_from")
        s_to = c4.text_input("Daily end time", "17:00", key="s_to")
        st.text_area("Subjects / activities / items to schedule", key="s_tasks", height=110,
                     placeholder="BSc Sem 3: Maths, Physics, Chemistry, English. Two sittings per day (10:00-13:00, 14:00-17:00).")
        st.text_area("Resources (rooms, venues, staff available)", key="s_res", height=80,
                     placeholder="Halls A, B, C. Invigilators: Dr. Rao, Ms. Das, Mr. Ali...")
        st.text_input("Rules / constraints", key="s_cons",
                      value="No clashes. Exclude Sundays and public holidays. Keep a break between sittings.")
        if st.button("✨ Generate schedule", type="primary", key="gen_sched", use_container_width=True):
            if s_end < s_start:
                st.warning("End date must be after the start date.")
            elif not st.session_state.s_tasks.strip():
                st.warning("Please list what needs to be scheduled.")
            else:
                with st.status("Building schedule...", expanded=False) as status:
                    try:
                        raw = call_llm(
                            "You are a scheduling assistant for a college office. Reply ONLY with a JSON array and "
                            'no other text. Each item: {"title":str,"date":"YYYY-MM-DD","start":"HH:MM","end":"HH:MM",'
                            '"location":str,"notes":str}. Use 24-hour time, avoid clashes of rooms and staff, '
                            "stay within the dates and daily time window, and put assigned staff in notes.",
                            f"Type: {st.session_state.s_kind}\nFrom {s_start} to {s_end}\n"
                            f"Daily window: {s_from}-{s_to}\nItems: {st.session_state.s_tasks}\n"
                            f"Resources: {st.session_state.s_res}\nRules: {st.session_state.s_cons}",
                            temperature=0.2)
                        st.session_state.events = parse_json_list(raw)
                        log("Schedule", st.session_state.s_kind, json.dumps(st.session_state.events, indent=1))
                        status.update(label="Schedule ready", state="complete")
                    except json.JSONDecodeError:
                        status.update(label="Failed", state="error")
                        st.error("The model returned an invalid format. Click generate again.")
                    except Exception as ex:
                        status.update(label="Failed", state="error")
                        st.error(str(ex))
    with right:
        st.subheader("Schedule")
        if st.session_state.events:
            cols = ["title", "date", "start", "end", "location", "notes"]
            df = pd.DataFrame(st.session_state.events).reindex(columns=cols).fillna("")
            with st.expander("✏️ Edit table"):
                df = st.data_editor(df, num_rows="dynamic", use_container_width=True, key="sched_editor")
            events = df.fillna("").to_dict("records")
            m1, m2, m3 = st.columns(3)
            m1.metric("Entries", len(events))
            m2.metric("Days", len({e["date"] for e in events if e["date"]}))
            m3.metric("Total hours", f"{sum(hours_between(str(e['start']), str(e['end'])) for e in events):.1f}")

            by_day = defaultdict(list)
            for e in sorted(events, key=lambda x: (str(x["date"]), str(x["start"]))):
                by_day[str(e["date"])].append(e)
            for d, items in by_day.items():
                try:
                    label = datetime.strptime(d, "%Y-%m-%d").strftime("%A, %d %B %Y")
                except Exception:
                    label = d
                with st.expander(f"📅 {label} · {len(items)} item(s)", expanded=len(by_day) <= 3):
                    for e in items:
                        loc = f" · 📍 {esc(e['location'])}" if e["location"] else ""
                        note = f"<br>{esc(e['notes'])}" if e["notes"] else ""
                        st.markdown(
                            f'<div class="event"><div class="t">{esc(e["title"])}</div>'
                            f'<div class="m"><span class="chip">{esc(e["start"])} – {esc(e["end"])}</span>{loc}{note}</div></div>',
                            unsafe_allow_html=True)

            # Legacy downloads
            d1, d2 = st.columns(2)
            d1.download_button("📅 Calendar file (.ics)", make_ics(events), file_name="schedule.ics",
                               mime="text/calendar", use_container_width=True)
            d2.download_button("📊 Excel-ready (.csv)", df.to_csv(index=False), file_name="schedule.csv",
                               use_container_width=True)
            st.caption("Use the panel below to download as PDF or share via WhatsApp / Gmail / Outlook.")

            # PDF bytes
            pdf_bytes = None
            if PDF_OK:
                try:
                    pdf_bytes = schedule_pdf(st.session_state.s_kind, events, P)
                except Exception as ex:
                    st.warning(f"PDF preview not built: {ex}")

            share_panel(
                kind="schedule",
                default_text=wa_schedule_text(st.session_state.s_kind, events, P),
                email_subject=f"[Schedule] {st.session_state.s_kind}",
                email_body=wa_schedule_text(st.session_state.s_kind, events, P),
                pdf_bytes=pdf_bytes,
                pdf_name="schedule.pdf",
            )
        else:
            st.info("Your schedule will appear here, grouped by day.")

# ================================================================== RECORDS
with tab_rec:
    st.subheader("Documents generated this session")
    if not st.session_state.history:
        st.info("Nothing generated yet. Notices, emails and schedules are listed here for quick reuse.")
    else:
        for h in st.session_state.history:
            with st.expander(f"{h['kind']} · {h['title']} · {h['time']}"):
                st.code(h["content"], language=None)
        if st.button("Clear records"):
            st.session_state.history = []
            st.rerun()
        st.caption("Records live only in this browser session. Save important documents using the download buttons.")

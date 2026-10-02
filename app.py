"""Veylura 2 — single-owner Streamlit studio. Python 3.11+.
Paid POSTs are never automatically retried. SQLite claims prevent duplicate requests
on one server. Keep VEYLURA_DATA_DIR on a persistent volume for durable history.
"""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlparse

import fal_client
import imageio_ffmpeg
import requests
import streamlit as st
from PIL import Image, ImageOps

VERSION = "Veylura 2.0"
DATA = Path(os.environ.get("VEYLURA_DATA_DIR", "veylura_data")).resolve()
MODELS = {"Draft": "fal-ai/kling-video/v1/standard/image-to-video",
          "Final": "fal-ai/kling-video/v1.5/pro/image-to-video"}
IMAGE_MODELS = {"Draft": "fal-ai/flux/schnell", "Final": "fal-ai/flux/dev"}
LIPSYNC = "fal-ai/sync-lipsync"
SIZES = {"9:16": (576, 1024), "16:9": (1024, 576), "1:1": (768, 768)}
MAX_AUDIO_SECONDS = 30.0
MAX_FILE = 25 * 1024 * 1024
DEFAULT_MOTION = "Front facing subject, subtle natural motion, stable camera, natural lighting."


class StudioError(Exception):
    pass


class Pending(StudioError):
    pass


def secret(name, default=""):
    try:
        return str(st.secrets.get(name, os.environ.get(name, default))).strip()
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return str(os.environ.get(name, default)).strip()


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_store():
    (DATA / "media").mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
            created TEXT NOT NULL, payload TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
            request_id TEXT, status_url TEXT, response_url TEXT, error TEXT)
        """)


def connect():
    c = sqlite3.connect(DATA / "history.sqlite3", timeout=15)
    c.row_factory = sqlite3.Row
    return c


def get_op(op_id):
    with connect() as c:
        row = c.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
    return dict(row) if row else None


def claim(op_id, kind, payload):
    # INSERT OR IGNORE is atomic across sessions/processes sharing this database.
    with connect() as c:
        cur = c.execute("INSERT OR IGNORE INTO operations(id,kind,status,created,payload) VALUES(?,?,?,?,?)",
                        (op_id, kind, "STARTING", now(), json.dumps(payload, ensure_ascii=False)))
        return cur.rowcount == 1


def update_op(op_id, **fields):
    allowed = {"status", "result", "request_id", "status_url", "response_url", "error"}
    if not fields or not set(fields) <= allowed:
        raise ValueError("Invalid fields")
    with connect() as c:
        c.execute("UPDATE operations SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                  [*fields.values(), op_id])


def done(op_id, result):
    update_op(op_id, status="DONE", result=json.dumps(result, ensure_ascii=False), error=None)
    return result


def cached(op_id):
    row = get_op(op_id)
    return json.loads(row["result"]) if row and row["status"] == "DONE" else None


def store_bytes(blob, suffix):
    if not blob:
        raise StudioError("הקובץ ריק.")
    name = digest(blob) + suffix
    path = DATA / "media" / name
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(blob)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
    return name


def media_path(name):
    if not re.fullmatch(r"[a-f0-9]{64}\.[a-z0-9]+", str(name)):
        raise StudioError("שם קובץ לא תקין.")
    path = DATA / "media" / name
    if not path.is_file():
        raise StudioError("הקובץ חסר. שחזר גיבוי או בחר קובץ אחר.")
    return path


def checked_image(blob, aspect):
    if not blob or len(blob) > MAX_FILE:
        raise StudioError("נדרשת תמונה תקינה עד 25 MB.")
    try:
        with Image.open(io.BytesIO(blob)) as im:
            if im.width * im.height > 24_000_000:
                raise StudioError("התמונה גדולה מדי; הקטן אותה לפני העלאה.")
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = ImageOps.fit(im, SIZES[aspect], method=Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=95)
            return out.getvalue()
    except StudioError:
        raise
    except Exception:
        raise StudioError("אי אפשר לפענח את התמונה.") from None


FEMALE = ["אפס", "אחת", "שתיים", "שלוש", "ארבע", "חמש", "שש", "שבע", "שמונה", "תשע"]
MALE = ["אפס", "אחד", "שניים", "שלושה", "ארבעה", "חמישה", "שישה", "שבעה", "שמונה", "תשעה"]
TENS = ["", "", "עשרים", "שלושים", "ארבעים", "חמישים", "שישים", "שבעים", "שמונים", "תשעים"]


def he_number(n, male=True):
    if not 0 <= n <= 999999:
        raise StudioError("מספר מחוץ לטווח. כתוב אותו במילים.")
    unit = MALE if male else FEMALE
    if n < 10:
        return unit[n]
    if n == 10:
        return "עשרה" if male else "עשר"
    if n < 20:
        first = {11: "אחד" if male else "אחת", 12: "שנים" if male else "שתים"}.get(n, unit[n - 10])
        return first + (" עשר" if male else " עשרה")
    if n < 100:
        t, r = divmod(n, 10)
        return TENS[t] + (" ו" + he_number(r, male) if r else "")
    if n < 1000:
        h, r = divmod(n, 100)
        head = "מאה" if h == 1 else "מאתיים" if h == 2 else FEMALE[h] + " מאות"
        return head + (" ו" + he_number(r, male) if r else "")
    k, r = divmod(n, 1000)
    head = "אלף" if k == 1 else "אלפיים" if k == 2 else (MALE[k] + " אלפים" if k < 10 else he_number(k, True) + " אלף")
    return head + (" ו" + he_number(r, male) if r else "")


def decimal_words(token, male=True):
    token = token.replace(",", "")
    left, _, right = token.partition(".")
    result = he_number(int(left), male)
    return result + (" נקודה " + " ".join(FEMALE[int(c)] for c in right) if right else "")


def money_words(token):
    left, _, cents = token.replace(",", "").partition(".")
    n = int(left)
    result = "שקל אחד" if n == 1 else "שני שקלים" if n == 2 else he_number(n, True) + " שקלים"
    if cents:
        if len(cents) > 2:
            raise StudioError("מחיר עם יותר משתי ספרות אחרי הנקודה; כתוב במילים.")
        ag = int(cents.ljust(2, "0"))
        if ag:
            result += " ו" + ("אגורה אחת" if ag == 1 else "שתי אגורות" if ag == 2 else he_number(ag, False) + " אגורות")
    return result


def quantity_words(token, noun, male):
    n = token.replace(",", "")
    singular = {"שקיות": "שקית", "אריזות": "אריזה", "אמפולות": "אמפולה",
                "כלבים": "כלב", "חתולים": "חתול", "שקים": "שק", "קילוגרם": "קילוגרם"}
    if n == "1":
        return singular[noun] + (" אחד" if male else " אחת")
    if n == "2":
        return ("שני " if male else "שתי ") + noun
    return decimal_words(n, male) + " " + noun


def prepare_hebrew(text, aliases=None):
    """Conservative rules, no LLM cost. Ambiguous dates/genders remain for review."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]", "", text)
    text = text.replace("\r\n", "\n").replace("…", ".").replace("–", "-").replace("—", ", ")
    # Text only: do not permit v3 audio tags or SSML to be interpreted accidentally.
    text = re.sub(r"<[^>]*>|\[[^\]]*\]", " ", text)
    for src, dst in sorted((aliases or {}).items(), key=lambda item: -len(item[0])):
        text = re.sub(r"(?<!\w)" + re.escape(src) + r"(?!\w)", lambda _: dst, text, flags=re.I)
    replacements = {'ש״ח': 'שקלים', 'ש"ח': 'שקלים', 'ק״ג': 'קילוגרם', 'ק"ג': 'קילוגרם',
                    'ק׳׳ג': 'קילוגרם', 'ס״מ': 'סנטימטר', 'ס"מ': 'סנטימטר', 'וכו׳': 'וכולי', 'וכו\'': 'וכולי'}
    # Preserve monetary context before replacing abbreviations.
    num = r"\d+(?:,\d{3})*(?:\.\d+)?"
    text = re.sub(r"(" + num + r")\s*(?:₪|ש[\"״]ח|שקלים)(?!\w)", lambda m: money_words(m[1]), text)
    text = re.sub(r"₪\s*(" + num + r")", lambda m: money_words(m[1]), text)
    text = re.sub(r"(" + num + r")\s*%", lambda m: decimal_words(m[1], True) + " אחוז", text)
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    # Phone-like sequences: digits separately, preserving leading zeros.
    text = re.sub(r"(?<!\d)(0\d{1,2}[- ]?\d{3}[- ]?\d{4})(?!\d)",
                  lambda m: " ".join(FEMALE[int(c)] for c in m[0] if c.isdigit()), text)
    warnings = []
    # Prevent careless interpretation of dates, clock times and fractions.
    protected = {}
    def hold(m):
        key = "ZZHOLD" + chr(65 + len(protected)) + "ZZ"
        protected[key] = m[0]
        return key
    text = re.sub(r"\b\d{1,4}[:/]\d{1,2}(?:[/]\d{2,4})?\b", hold, text)
    # Grammatical gender for common quantities.
    text = re.sub(r"(" + num + r")\s+(שקיות|אריזות|אמפולות)(?!\w)",
                  lambda m: quantity_words(m[1], m[2], False), text)
    text = re.sub(r"(" + num + r")\s+(כלבים|חתולים|שקים|קילוגרם)(?!\w)",
                  lambda m: quantity_words(m[1], m[2], True), text)
    if re.search(r"\d", text):
        warnings.append("מספרים כלליים הומרו בלשון זכר; בדוק התאמה לשם העצם.")
    text = re.sub(num, lambda m: decimal_words(m[0]), text)
    for key, value in protected.items():
        text = text.replace(key, value)
    if protected:
        warnings.append("תאריך, שעה או שבר נשארו ללא המרה. כתוב במילים לפני יצירת קול.")
    text = re.sub(r"[^\w\s\u0590-\u05ff.,!?;:'\"()/\-]", " ", text)
    text = re.sub(r"[!?]{2,}", "!", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if text and text[-1] not in ".!?":
        text += "."
    return text, warnings


def ffmpeg(args, timeout=90):
    try:
        safe_args = []
        for arg in args:
            if arg == "-i":
                safe_args += ["-protocol_whitelist", "file,pipe"]
            safe_args.append(arg)
        p = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", *safe_args],
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise StudioError("עיבוד המדיה לא הושלם; אפשר לנסות שוב ללא יצירת AI חדשה.") from None
    if p.returncode:
        raise StudioError("עיבוד המדיה נכשל. בדוק שהקבצים תקינים.")
    return p


def duration(path):
    p = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-protocol_whitelist", "file,pipe", "-i", str(path)],
                       capture_output=True, timeout=15)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr.decode(errors="replace"))
    if not m:
        raise StudioError("לא ניתן למדוד את משך המדיה.")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def normalize_audio(blob, suffix):
    if not blob or len(blob) > MAX_FILE:
        raise StudioError("נדרש אודיו תקין עד 25 MB.")
    if suffix not in {".mp3", ".wav", ".m4a", ".ogg"}:
        raise StudioError("פורמט אודיו לא נתמך.")
    name = store_bytes(blob, suffix)
    seconds = duration(media_path(name))
    if not 0.25 <= seconds <= MAX_AUDIO_SECONDS:
        raise StudioError("האודיו חייב להיות באורך רבע שנייה עד 30 שניות.")
    op_id = digest(["normalize_audio", digest(blob), "v1"])
    if cached(op_id):
        return cached(op_id)
    with tempfile.TemporaryDirectory() as d:
        output = Path(d) / "clean.wav"
        ffmpeg(["-y", "-i", str(media_path(name)), "-vn", "-ac", "1", "-ar", "48000",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", str(output)])
        result = {"file": store_bytes(output.read_bytes(), ".wav"), "seconds": duration(output)}
    claim(op_id, "audio_local", {})
    return done(op_id, result)


def require_fal():
    key = secret("FAL_KEY")
    if not key:
        raise StudioError("חסר FAL_KEY בהגדרות Streamlit.")
    return key


def upload(name):
    key = require_fal()
    cache_key = digest(["upload", name, digest(key)])
    if cache_key not in st.session_state:
        try:
            # Upload proves that this key can authenticate, without a generation.
            st.session_state[cache_key] = fal_client.SyncClient(key=key, default_timeout=30).upload_file(media_path(name))
        except Exception:
            raise StudioError("העלאה ל-fal נכשלה. בדוק את המפתח, ההרשאות והחיבור; לא הופעלה יצירה.") from None
    return st.session_state[cache_key]


def verify_fal():
    token = digest(["fal_verified", digest(require_fal())])
    if not st.session_state.get(token):
        out = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(out, "PNG")
        upload(store_bytes(out.getvalue(), ".png"))
        st.session_state[token] = True


def http_error(response):
    # Never display raw exceptions/response bodies: may contain URLs or keys.
    if response.status_code in (401, 403):
        return "המפתח או הרשאות השירות אינם תקינים."
    if response.status_code == 402:
        return "אין יתרה מספקת אצל ספק השירות."
    if response.status_code == 429:
        return "מגבלת קצב או עומס אצל הספק."
    return f"הספק החזיר שגיאה HTTP {response.status_code}."


def verify_voice(voice_id):
    key = secret("ELEVENLABS_API_KEY")
    if not key:
        raise StudioError("חסר ELEVENLABS_API_KEY.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,80}", voice_id):
        raise StudioError("מזהה הקול אינו תקין.")
    token = digest(["voice_verified", digest(key), voice_id])
    if not st.session_state.get(token):
        for url in ["https://api.elevenlabs.io/v1/models", "https://api.elevenlabs.io/v1/voices/" + voice_id]:
            try:
                r = requests.get(url, headers={"xi-api-key": key}, timeout=(10, 25))
            except requests.RequestException:
                raise StudioError("בדיקת ElevenLabs נכשלה בגלל חיבור. לא נוצרה קריינות.") from None
            if not r.ok:
                raise StudioError(http_error(r))
            if url.endswith("models"):
                models = r.json()
                if not any(m.get("model_id") == "eleven_v3" and m.get("can_do_text_to_speech", True) for m in models):
                    raise StudioError("Eleven v3 אינו זמין למפתח זה.")
        st.session_state[token] = True
    return key


def create_voice(text, voice_id, variation, char_rate):
    if not text.strip() or len(text) > 2500:
        raise StudioError("הזן טקסט באורך 1 עד 2500 תווים.")
    if len(text.split()) > 65:
        raise StudioError("הטקסט ארוך מדי למסלול של עד 30 שניות. קצר ל-65 מילים לכל היותר לפני יצירת קול בתשלום.")
    if re.search(r"\d", text):
        raise StudioError("בטקסט נשארו ספרות. כתוב תאריכים, שעות ומספרים במילים לפני ההפקה.")
    if re.search(r"<|>|\[|\]", text):
        raise StudioError("הזן טקסט רגיל ללא תגיות.")
    op_id = digest(["tts", "eleven_v3", text, voice_id, variation])
    result = cached(op_id)
    if result:
        return op_id, result
    key = verify_voice(voice_id)
    if not claim(op_id, "tts", {"text": text, "voice": voice_id, "model": "eleven_v3", "estimate_usd": len(text)*char_rate/1000}):
        raise Pending("קריינות זו כבר נשלחה. בדוק היסטוריה; לא נשלח חיוב נוסף.")
    try:
        # requests default transport has no automatic POST retries.
        r = requests.post("https://api.elevenlabs.io/v1/text-to-speech/" + voice_id,
                          params={"output_format": "mp3_44100_128"},
                          headers={"xi-api-key": key, "Accept": "audio/mpeg"},
                          json={"text": text, "model_id": "eleven_v3", "language_code": "he",
                                "voice_settings": {"stability": 0.5}}, timeout=(10, 90))
        if not r.ok:
            raise StudioError(http_error(r))
        raw = store_bytes(r.content, ".mp3")
        # Save raw output before any local processing, so a local failure cannot lose a paid result.
        done(op_id, {"file": raw, "seconds": duration(media_path(raw)), "raw": True})
        result = normalize_audio(r.content, ".mp3")
        done(op_id, result)
        return op_id, result
    except Exception as e:
        if not cached(op_id):
            update_op(op_id, status="UNKNOWN", error="אין תוצאה מקומית; ייתכן חיוב. בדוק ElevenLabs לפני ניסיון חדש.")
        if isinstance(e, StudioError):
            raise
        raise StudioError("קריינות לא הושלמה. הבקשה לא תישלח מחדש אוטומטית; בדוק היסטוריה.") from None


def queue_url(url):
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or parsed.hostname != "queue.fal.run":
        raise StudioError("כתובת תור לא תקינה.")
    return url


def fal_request(kind, model, arguments, identity, metadata=None):
    op_id = digest([kind, model, identity])
    if cached(op_id):
        return op_id, cached(op_id)
    key = require_fal()
    if claim(op_id, kind, {"model": model, "args": arguments, **(metadata or {})}):
        try:
            r = requests.post("https://queue.fal.run/" + model,
                              headers={"Authorization": "Key " + key}, json=arguments,
                              timeout=(10, 30))
            if not r.ok:
                raise StudioError(http_error(r))
            q = r.json()
            if not q.get("request_id"):
                raise StudioError("הספק לא החזיר מזהה בקשה.")
            update_op(op_id, status="QUEUED", request_id=q["request_id"],
                      status_url=queue_url(q["status_url"]), response_url=queue_url(q["response_url"]))
        except Exception as e:
            update_op(op_id, status="UNKNOWN", error="לא נשמר אישור משלוח; ייתכן חיוב. בדוק את fal לפני ניסיון חדש.")
            if isinstance(e, StudioError):
                raise
            raise StudioError("מצב הבקשה אינו ידוע. לא נשלח ניסיון נוסף כדי למנוע חיוב כפול.") from None
    return op_id, poll_fal(op_id)


def download_result(url, suffix):
    if urlparse(url).scheme != "https":
        raise StudioError("כתובת תוצאה לא תקינה.")
    try:
        with requests.get(url, stream=True, timeout=(10, 60)) as r:
            r.raise_for_status()
            data = bytearray()
            for chunk in r.iter_content(256 * 1024):
                data.extend(chunk)
                if len(data) > 180 * 1024 * 1024:
                    raise StudioError("התוצאה גדולה מדי להורדה.")
        return store_bytes(bytes(data), suffix)
    except requests.RequestException:
        raise StudioError("הורדת התוצאה נכשלה. לחץ שוב על בדיקת מצב; לא תישלח הפקה חדשה.") from None


def poll_fal(op_id):
    row = get_op(op_id)
    if row["status"] == "DONE":
        return json.loads(row["result"])
    if not row["request_id"]:
        raise Pending("הבקשה כבר נרשמה אך אין מזהה ספק. בדוק היסטוריה ואת הספק לפני ניסיון חדש.")
    headers = {"Authorization": "Key " + require_fal()}
    try:
        r = requests.get(queue_url(row["status_url"]), headers=headers, timeout=(10, 25))
        if not r.ok:
            raise StudioError(http_error(r))
        if r.json().get("status") != "COMPLETED":
            raise Pending("ההפקה בתור או בעבודה. לחץ על 'בדוק מצב' בהיסטוריה בעוד כחצי דקה.")
        r = requests.get(queue_url(row["response_url"]), headers=headers, timeout=(10, 30))
        if not r.ok:
            update_op(op_id, status="FAILED", error=http_error(r))
            raise StudioError(http_error(r) + " לא מופעל ניסיון חדש אוטומטית.")
        payload = r.json()
        # Persist remote URL before download; GET retries never create paid jobs.
        result = json.loads(row["result"])
        if row["kind"] in {"image", "image_edit"}:
            url = payload["images"][0]["url"]
            suffix = ".jpg"
        else:
            url = payload.get("video", {}).get("url") or payload.get("url")
            suffix = ".mp4"
        if not url:
            raise StudioError("הספק סיים ללא קובץ תוצאה.")
        result["url"] = url
        update_op(op_id, result=json.dumps(result))
        result["file"] = download_result(url, suffix)
        if suffix == ".mp4":
            result["seconds"] = duration(media_path(result["file"]))
        return done(op_id, result)
    except requests.RequestException:
        raise StudioError("בדיקת המצב נכשלה בגלל חיבור. אפשר לבדוק שוב ללא הפקה חדשה.") from None
    except (KeyError, IndexError, ValueError):
        raise StudioError("תשובת הספק לא תאמה למבנה הצפוי. מזהה הבקשה נשמר.") from None


def duration_plan(seconds, loop_allowed=False):
    if not 0.25 <= seconds <= MAX_AUDIO_SECONDS:
        raise StudioError("בחר משך של עד 30 שניות.")
    if seconds <= 5:
        return 5
    if seconds <= 10:
        return 10
    if loop_allowed:
        return 10
    raise StudioError("הקריינות ארוכה מ-10 שניות. קצר אותה או אשר חזרה על הווידאו לחיסכון.")


def local_video(image_name, video_name, audio_name, seconds, aspect, allow_loop, render_mode="Draft"):
    """Render preview / trim / mux voiceover. Never truncate speech with -shortest."""
    if not 0.25 <= seconds <= MAX_AUDIO_SECONDS:
        raise StudioError("משך הפקה לא תקין.")
    if video_name and duration(media_path(video_name)) + 0.04 < seconds and not allow_loop:
        raise StudioError("הווידאו שהתקבל קצר מהאודיו; אשר חזרה או קצר את הקריינות.")
    ident = digest(["local_video", image_name, video_name, audio_name, round(seconds, 3), aspect, allow_loop, render_mode])
    result = cached(ident)
    if result:
        return ident, result
    w, h = ({"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
            if render_mode == "Final" else SIZES)[aspect]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "output.mp4"
        if video_name:
            args = (["-stream_loop", "-1"] if allow_loop else []) + ["-i", str(media_path(video_name))]
        else:
            args = ["-loop", "1", "-i", str(media_path(image_name))]
        if audio_name:
            args += ["-i", str(media_path(audio_name)), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "128k"]
        else:
            args += ["-an"]
        args += ["-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1",
                 "-r", "25", "-t", str(seconds), "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "18" if render_mode == "Final" else "23",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
        ffmpeg(["-y", *args])
        actual = duration(out)
        if actual + 0.05 < seconds:
            raise StudioError("התוצאה קצרה מהאודיו; לא מסמנים אותה כמוכנה.")
        result = {"file": store_bytes(out.read_bytes(), ".mp4"), "seconds": actual}
    claim(ident, "local_video", {"seconds": seconds, "aspect": aspect})
    return ident, done(ident, result)


def estimate_video(seconds, mode, lipsync, rate, lip_rate, video_cached=False, lip_cached=False, lipsync_seconds=None):
    video = 0 if video_cached else seconds * rate
    lip_duration = seconds if lipsync_seconds is None else lipsync_seconds
    lip = 0 if not lipsync or lip_cached else lip_duration / 60 * lip_rate
    return {"kling_usd": round(video, 4), "lipsync_usd": round(lip, 4), "total_usd": round(video + lip, 4)}


def export_backup():
    with connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM operations ORDER BY created")]
    media_files = [p for p in (DATA / "media").iterdir() if p.is_file()]
    if sum(p.stat().st_size for p in media_files) > 250 * 1024 * 1024:
        raise StudioError("המדיה גדולה מ-250 MB. הורד תוצאות בנפרד או גבה את תיקיית הנתונים על השרת.")
    manifest = {"version": 1, "operations": rows}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("history.json", json.dumps(manifest, ensure_ascii=False))
        for path in (DATA / "media").iterdir():
            if path.is_file() and re.fullmatch(r"[a-f0-9]{64}\.[a-z0-9]+", path.name):
                z.write(path, "media/" + path.name)
    return out.getvalue()


def restore_backup(blob):
    # Merge, never overwrite a live operation. No SQL or ZIP paths executed.
    if len(blob) > 250 * 1024 * 1024:
        raise StudioError("הגיבוי גדול מ-250 MB.")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if sum(i.file_size for i in z.infolist()) > 500 * 1024 * 1024:
            raise StudioError("הגיבוי גדול מדי לאחר פתיחה.")
        manifest = json.loads(z.read("history.json"))
        if manifest.get("version") != 1:
            raise StudioError("גרסת גיבוי לא נתמכת.")
        for info in z.infolist():
            if info.filename == "history.json":
                continue
            if not re.fullmatch(r"media/[a-f0-9]{64}\.[a-z0-9]+", info.filename):
                raise StudioError("הגיבוי כולל נתיב לא תקין.")
            data = z.read(info)
            name = info.filename.split("/")[1]
            if digest(data) != name.split(".")[0]:
                raise StudioError("קובץ בגיבוי אינו תואם לחתימה.")
            store_bytes(data, Path(name).suffix)
        with connect() as c:
            for row in manifest["operations"]:
                if not re.fullmatch(r"[a-f0-9]{64}", row["id"]):
                    raise StudioError("מזהה היסטוריה לא תקין.")
                for k in ["status_url", "response_url"]:
                    if row.get(k):
                        queue_url(row[k])
                fields = ["id", "kind", "status", "created", "payload", "result", "request_id", "status_url", "response_url", "error"]
                c.execute("INSERT OR IGNORE INTO operations(" + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in fields) + ")",
                          [row.get(k) for k in fields])


def guard(action):
    try:
        with st.spinner("מעבד ושומר את ההתקדמות..."):
            action()
    except Pending as e:
        st.info(str(e))
    except StudioError as e:
        st.error(str(e))
    except Exception:
        st.error("הפעולה לא הושלמה. בדוק היסטוריה לפני ניסיון חדש; אין ניסיון AI אוטומטי.")


def foodels_form():
    with st.expander("🐾 Foodels Ad Generator — הכנת פרסומת", expanded=False):
        st.caption("תבניות מקומיות ללא חיוב על כתיבה. תמונת מוצר מקורית שומרת על מראה האריזה; AI עשוי לשנות אותה.")
        with st.form("foodels"):
            product = st.text_input("מוצר", "חטיף לכלבים")
            animal = st.selectbox("חיית מחמד", ["כלב", "חתול"])
            category = st.selectbox("סוג הפרסומת", ["מבצע", "מוצר חדש", "טיפ", "מצחיק"])
            seconds = st.selectbox("משך רצוי", [5, 10, 15, 20, 30], index=1)
            gender = st.selectbox("קול", ["אישה", "גבר"])
            aspect = st.selectbox("פורמט", list(SIZES))
            copy = st.text_area("טקסט הפרסומת — אפשר להשאיר ריק לתבנית")
            submit = st.form_submit_button("הכן את הפרסומת בסטודיו — ללא חיוב")
        if submit:
            if not product.strip():
                st.error("הזן מוצר.")
                return
            templates = {
                "מבצע": f"מחפשים {product}? בואו לפודלס ברמות, באר שבע. בדקו איתנו את המבצע בחנות.",
                "מוצר חדש": f"חדש בפודלס: {product}. בואו להכיר מקרוב בחנות שלנו ברמות, באר שבע.",
                "טיפ": f"איך בוחרים {product}? מתאימים לצרכים של חיית המחמד. בפודלס ברמות, באר שבע, נעזור לכם לבחור.",
                "מצחיק": f"אני לא מפונק. אני פשוט יודע מה אני אוהב! {product} מחכה לכם בפודלס ברמות, באר שבע."}
            st.session_state["raw_text"] = copy.strip() or templates[category]
            st.session_state["gender"] = gender
            st.session_state["aspect"] = aspect
            st.session_state["silent_seconds"] = seconds
            pet = "dog" if animal == "כלב" else "cat"
            st.session_state["image_prompt"] = f"A charming {pet} in a welcoming neighborhood pet store in Beer Sheva, commercial pet photography, natural lighting, appealing composition, no text, no invented product packaging. Product context: {product}."
            st.session_state["motion_prompt"] = f"A charming {pet} with subtle natural movements in a welcoming pet shop, stable camera, warm commercial lighting."
            st.session_state["delivery"] = "קריינות ברקע"
            st.session_state.pop("audio", None)
            st.session_state.pop("audio_signature", None)
            st.success("התבנית מוכנה. עבור לשלבים הבאים; משך הקריינות בפועל יקבע את תכנון הווידאו.")


def main():
    st.set_page_config(page_title=VERSION, page_icon="🎬", layout="centered")
    init_store()
    # Optional single-owner login; configure APP_PASSWORD on public deployments.
    password = secret("APP_PASSWORD")
    if password and not st.session_state.get("authenticated"):
        import hmac
        entered = st.text_input("סיסמת הסטודיו", type="password")
        if st.button("כניסה"):
            if hmac.compare_digest(entered, password):
                st.session_state["authenticated"] = True
                st.rerun()
            st.error("סיסמה שגויה.")
        st.stop()
    st.markdown("<style>.stMainBlockContainer,[data-testid=stSidebar]{direction:rtl;} textarea{unicode-bidi:plaintext;}</style>", unsafe_allow_html=True)
    st.title("🎬 Veylura")
    st.caption("עברית • הפקות אופנה • פרסומות Foodels • שימוש חוזר בכל שלב")
    mode = st.sidebar.radio("מצב הפקה", ["Draft", "Final"])
    st.sidebar.caption("Draft: FLUX Schnell + Kling Standard. Final: FLUX Dev + Kling 1.5 Pro. עברית: Eleven v3 בשניהם.")
    with st.sidebar.expander("הגדרות מתקדמות ועלויות"):
        female = st.text_input("Voice ID — אישה", secret("ELEVENLABS_FEMALE_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"))
        male = st.text_input("Voice ID — גבר", secret("ELEVENLABS_MALE_VOICE_ID", "pNInz6obpgDQGcFmaJgB"))
        st.caption("מזהי ברירת המחדל הם מהמערכת הישנה. לקבלת עברית טבעית, בחר קולות שנבדקו בעברית בחשבון שלך.")
        if st.button("טען קולות מהחשבון — ללא יצירת קריינות"):
            def load_voices():
                key = secret("ELEVENLABS_API_KEY")
                if not key:
                    raise StudioError("חסר ELEVENLABS_API_KEY.")
                try:
                    r = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=(10, 25))
                except requests.RequestException:
                    raise StudioError("טעינת הקולות נכשלה בגלל חיבור.") from None
                if not r.ok:
                    raise StudioError(http_error(r))
                st.session_state["account_voices"] = r.json().get("voices", [])
            guard(load_voices)
        voices = st.session_state.get("account_voices", [])
        if voices:
            names = {v["voice_id"]: v.get("name", v["voice_id"]) for v in voices}
            options = ["manual", *names]
            label = lambda v: "המזהה הידני למעלה" if v == "manual" else names[v] + " • " + v[-6:]
            chosen_female = st.selectbox("קול אישה מהחשבון", options, format_func=label)
            chosen_male = st.selectbox("קול גבר מהחשבון", options, format_func=label)
            if chosen_female != "manual":
                female = chosen_female
            if chosen_male != "manual":
                male = chosen_male
            st.caption("הרשימה אינה מבטיחה מבטא עברי. בדוק קטע קצר ושמור את המזהה שבחרת ב-Secrets.")
        video_rate = st.number_input("Kling — דולר לשנייה", min_value=0.0, value=0.045 if mode == "Draft" else 0.10, step=0.005, format="%.3f", key="rate_" + mode)
        lip_rate = st.number_input("Lip Sync — דולר לדקה", min_value=0.0, value=0.70, step=0.10)
        tts_rate = st.number_input("ElevenLabs — דולר לאלף תווים לפי התוכנית שלך", min_value=0.0, value=float(secret("ELEVENLABS_USD_PER_1000", "0")), step=0.05)
        st.caption("התעריפים הם אומדן ניתן לעריכה, לא מחיר מובטח. אפס ב-ElevenLabs פירושו מחיר לא מוגדר, לא שירות חינם.")
        variation = int(st.number_input("מספר גרסה חדשה", min_value=0, value=0, step=1))
        st.caption("אותן הגדרות ואותו מספר = שימוש חוזר. שנה מספר רק אם אתה רוצה יצירה חדשה בתשלום; לא כדי לעקוף בקשה ממתינה.")
        aliases_text = st.text_area("מילון הגייה — מקור=הגייה, שורה לכל מונח", "Foodels=פודלס\nVeylura=ויילורה")
    aliases = {}
    for line in aliases_text.splitlines():
        if "=" in line:
            a, b = line.split("=", 1)
            if a.strip() and b.strip():
                aliases[a.strip()] = b.strip()

    st.session_state.setdefault("image_prompt", "Front facing fashion model in Tel Aviv, editorial photography, natural lighting, clearly visible mouth, no text.")
    st.session_state.setdefault("raw_text", "שלום, ברוכים הבאים לקולקציה החדשה של ויילורה.")
    st.session_state.setdefault("motion_prompt", DEFAULT_MOTION)
    foodels_form()
    aspect = st.selectbox("פורמט הסרטון", list(SIZES), key="aspect")
    st.header("1. תמונה")
    source = st.radio("מקור", ["תמונה חדשה", "העלאה ללא שינוי", "שינוי תמונה ב-AI"], horizontal=True)
    uploaded_image = None
    if source != "תמונה חדשה":
        uploaded_image = st.file_uploader("תמונה — JPG / PNG", type=["jpg", "jpeg", "png"])
        if source == "העלאה ללא שינוי":
            if st.button("בחר את התמונה — ללא יצירה בתשלום"):
                def choose():
                    if uploaded_image is None:
                        raise StudioError("בחר תמונה.")
                    name = store_bytes(checked_image(uploaded_image.getvalue(), aspect), ".jpg")
                    st.session_state["image"] = {"file": name, "aspect": aspect}
                guard(choose)
        else:
            st.caption("Image-to-image עשוי לשנות פנים ואריזות; אינו מבטיח שמירת זהות.")
    if source != "העלאה ללא שינוי":
        prompt = st.text_area("תיאור התמונה או השינוי", key="image_prompt")
        strength = st.slider("עוצמת השינוי", 0.1, 0.9, 0.4, 0.05) if source == "שינוי תמונה ב-AI" else None
        st.caption("יצירת תמונה היא בתשלום לפי FLUX; אותו תיאור, פורמט ומספר גרסה ישתמשו בתוצאה שכבר נשמרה.")
        if st.button("צור / עדכן תמונה"):
            def create_image():
                if not prompt.strip() or len(prompt) > 2500:
                    raise StudioError("הזן תיאור תמונה באורך עד 2500 תווים.")
                verify_fal()
                model = IMAGE_MODELS[mode]
                args = {"prompt": prompt.strip(), "num_images": 1, "output_format": "jpeg"}
                identity = [prompt.strip(), aspect, variation]
                kind = "image"
                if source == "שינוי תמונה ב-AI":
                    if uploaded_image is None:
                        raise StudioError("בחר תמונה לפני יצירה בתשלום.")
                    name = store_bytes(checked_image(uploaded_image.getvalue(), aspect), ".jpg")
                    model = "fal-ai/flux/dev/image-to-image"
                    kind = "image_edit"
                    args.update(image_url=upload(name), strength=strength)
                    identity += [name, strength]
                else:
                    w, h = SIZES[aspect]
                    args["image_size"] = {"width": w, "height": h}
                oid = digest([kind, model, identity])
                st.session_state["pending_image"] = oid
                st.session_state["pending_image_aspect"] = aspect
                oid, result = fal_request(kind, model, args, identity, {"aspect": aspect})
                st.session_state["image"] = {**result, "aspect": aspect}
            guard(create_image)
    image = st.session_state.get("image")
    if image:
        try:
            st.image(str(media_path(image["file"])), caption="התמונה נשמרה לשימוש חוזר", width=300)
        except StudioError as e:
            st.warning(str(e))

    st.header("2. עברית וקריינות")
    audio_mode = st.radio("מקור שמע", ["טקסט בעברית", "מיקרופון", "קובץ אודיו", "ללא אודיו"], horizontal=True)
    audio_signature = None
    if audio_mode == "טקסט בעברית":
        raw = st.text_area("הטקסט המקורי", key="raw_text")
        gender = st.radio("קול", ["אישה", "גבר"], horizontal=True, key="gender")
        voice = female if gender == "אישה" else male
        try:
            prepared, warnings = prepare_hebrew(raw, aliases)
        except StudioError as e:
            prepared, warnings = raw, [str(e)]
        normalized_key = digest([raw, aliases])
        if st.session_state.get("normalized_key") != normalized_key:
            st.session_state["spoken_text"] = prepared
            st.session_state["normalized_key"] = normalized_key
        spoken = st.text_area("הטקסט שיישלח לקול — אפשר לתקן הגייה ידנית", key="spoken_text")
        for warning in warnings:
            st.caption(warning)
        audio_signature = digest([audio_mode, spoken, voice, variation])
        st.caption(f"{len(spoken)} תווים • משך משוער בלבד: {max(1, round(len(spoken.split()) / 2.3))} שניות. משך אמיתי יימדד אחרי יצירת הקול.")
        if tts_rate > 0:
            st.caption(f"אומדן קריינות חדשה: ${len(spoken) * tts_rate / 1000:.4f}")
        else:
            st.caption("אומדן כספי לקריינות אינו מוגדר. היא צורכת קרדיטים בחשבון ElevenLabs.")
        if st.button("הכן קריינות / השתמש בקריינות שמורה"):
            def audio_action():
                oid, result = create_voice(spoken, voice, variation, tts_rate)
                # Raw audio remains usable even when local normalization failed previously.
                if result.get("raw"):
                    result = normalize_audio(media_path(result["file"]).read_bytes(), ".mp3")
                    done(oid, result)
                st.session_state["audio"] = result
                st.session_state["audio_signature"] = audio_signature
            guard(audio_action)
    elif audio_mode in {"מיקרופון", "קובץ אודיו"}:
        recording = st.audio_input("הקלט קריינות") if audio_mode == "מיקרופון" else st.file_uploader("אודיו עד 30 שניות", type=["mp3", "wav", "m4a", "ogg"])
        if recording:
            audio_signature = digest([audio_mode, digest(recording.getvalue())])
        if st.button("הכן ובדוק את האודיו — ללא יצירת קול בתשלום"):
            def imported_audio():
                if not recording:
                    raise StudioError("הקלט או בחר אודיו.")
                suffix = ".wav" if audio_mode == "מיקרופון" else Path(recording.name).suffix.lower()
                st.session_state["audio"] = normalize_audio(recording.getvalue(), suffix)
                st.session_state["audio_signature"] = audio_signature
            guard(imported_audio)
    audio = st.session_state.get("audio") if audio_mode != "ללא אודיו" else None
    audio_ready = audio_mode == "ללא אודיו" or (audio is not None and st.session_state.get("audio_signature") == audio_signature)
    if audio:
        st.audio(str(media_path(audio["file"])))
        st.caption(f"משך הקריינות בפועל: {audio['seconds']:.2f} שניות")
        if not audio_ready:
            st.warning("הטקסט, הקול או מקור השמע השתנו. הכן קריינות מעודכנת לפני הווידאו.")

    st.header("3. וידאו")
    motion = st.text_area("תיאור התנועה", key="motion_prompt")
    delivery = st.radio("אופן הדיבור", ["קריינות ברקע", "Lip Sync — דמות מדברת"], horizontal=True, key="delivery")
    lipsync = delivery.startswith("Lip") and audio_mode != "ללא אודיו"
    if lipsync:
        st.caption("מומלץ אדם אחד מול המצלמה, פה גלוי ותנועה עדינה. ליפ־סינק לחיה הוא ניסיוני ואינו מובטח.")
    silent_seconds = st.selectbox("משך ללא אודיו / יעד לפרסומת", [5, 10, 15, 20, 30], index=0, key="silent_seconds")
    seconds = audio["seconds"] if audio and audio_ready else float(silent_seconds)
    if audio and audio_ready:
        st.caption("משך הסרטון יתאים לקריינות בפועל; היעד אינו חותך או מאיץ דיבור.")
    loop_allowed = st.checkbox("מאשר חזרה על קליפ של 10 שניות כשהקריינות ארוכה יותר — ללא יצירת קליפים נוספים", value=False)
    ready = image is not None and audio_ready and bool(motion.strip()) and len(motion) <= 2500
    if len(motion) > 2500:
        st.warning("קצר את תיאור התנועה ל-2500 תווים לפני הפקה.")
    plan = None
    try:
        plan = duration_plan(seconds, loop_allowed)
    except StudioError as e:
        st.info(str(e))
    if st.button("צור טיוטה מתמונה + קול — ללא Kling או Lip Sync", disabled=not ready):
        def draft():
            name = store_bytes(checked_image(media_path(image["file"]).read_bytes(), aspect), ".jpg")
            oid, result = local_video(name, None, audio["file"] if audio else None, seconds, aspect, False)
            st.session_state["final"] = result
        guard(draft)

    # Fingerprints deliberately exclude temporary upload URLs and irrelevant TTS settings.
    image_name = None
    if image:
        try:
            image_name = store_bytes(checked_image(media_path(image["file"]).read_bytes(), aspect), ".jpg")
        except StudioError as e:
            ready = False
            st.warning(str(e))
    video_identity = [image_name, motion.strip(), plan, aspect, variation]
    video_id = digest(["video", MODELS[mode], video_identity])
    video_saved = cached(video_id)
    cost = estimate_video(plan or 10, mode, lipsync, video_rate, lip_rate, bool(video_saved),
                          lipsync_seconds=math.ceil(seconds * 25) / 25)
    # The final lipsync key depends on the actual conformed video; compute when it is already available.
    lip_saved = False
    if video_saved and audio and audio_ready and lipsync:
        local_id = digest(["local_video", image_name, video_saved["file"], None, round(seconds, 3), aspect, loop_allowed, mode])
        conform = cached(local_id)
        if conform:
            lip_id = digest(["lipsync", LIPSYNC, [conform["file"], audio["file"], "loop"]])
            lip_saved = bool(cached(lip_id))
    if lip_saved:
        cost["lipsync_usd"] = 0
        cost["total_usd"] = cost["kling_usd"]
    st.info(f"אומדן חיוב נוסף: ${cost['total_usd']:.3f} • Kling: ${cost['kling_usd']:.3f} • Lip Sync: ${cost['lipsync_usd']:.3f}")
    st.caption(f"Kling יוצר {plan or '—'} שניות; התוצאה תותאם ל-{seconds:.2f} שניות. אומדן בלבד, לפני מסים/מינימום חיוב/שינויי תעריף; הקריינות והתמונה הוכנו בנפרד.")
    budget = st.number_input("תקרת אומדן להפקת הווידאו בדולר", min_value=0.0, value=2.0, step=0.25)
    if st.button("הפק וידאו — שימוש חוזר בכל שלב שכבר הושלם", type="primary", disabled=not (ready and plan)):
        def produce():
            if cost["total_usd"] > budget:
                raise StudioError("האומדן חורג מהתקרה שהגדרת.")
            image_path = media_path(image_name)
            if audio:
                media_path(audio["file"])
                if not 0.25 <= audio["seconds"] <= MAX_AUDIO_SECONDS:
                    raise StudioError("אודיו מחוץ לטווח הנתמך.")
            imageio_ffmpeg.get_ffmpeg_exe()  # Fail before paid video if local renderer is missing.
            verify_fal()
            image_url = upload(image_path.name)
            audio_url = upload(audio["file"]) if lipsync else None
            production = {"image": image_name, "video_id": video_id, "audio": audio,
                          "seconds": seconds, "aspect": aspect, "loop": loop_allowed,
                          "lipsync": lipsync, "audio_url": audio_url, "estimate": cost,
                          "budget": budget, "lip_rate": lip_rate, "render_mode": mode}
            # Store immutable production plan in session and durable video payload.
            st.session_state["production"] = production
            oid, result = fal_request("video", MODELS[mode], {"image_url": image_url, "prompt": motion.strip(),
                "duration": str(plan), "aspect_ratio": aspect}, video_identity, {"production": production, "estimate_usd": cost["kling_usd"]})
            finish_production(production, result)
        guard(produce)
    if st.session_state.get("production"):
        if st.button("המשך הפקה קיימת / בדוק מצב — ללא Kling חדש"):
            def resume():
                prod = st.session_state["production"]
                finish_production(prod, poll_fal(prod["video_id"]))
            guard(resume)
    final = st.session_state.get("final")
    if final:
        st.video(str(media_path(final["file"])))
        st.download_button("הורד MP4", media_path(final["file"]).read_bytes(), "veylura.mp4", "video/mp4")
    history_ui()


def finish_production(prod, video):
    if prod["lipsync"]:
        # Conform FIRST, so lipsync is charged only for the required duration.
        oid, conform = local_video(prod["image"], video["file"], None, prod["seconds"], prod["aspect"], prod["loop"], prod.get("render_mode", "Draft"))
        video_url = upload(conform["file"])
        identity = [conform["file"], prod["audio"]["file"], "loop"]
        lip_id = digest(["lipsync", LIPSYNC, identity])
        # Resume an existing request even if pricing has changed; never submit another.
        if not get_op(lip_id):
            expected = conform["seconds"] / 60 * prod["lip_rate"] + prod["estimate"]["kling_usd"]
            if expected > prod["budget"]:
                raise StudioError("אורך התוצאה בפועל מעלה את האומדן מעל התקרה. הווידאו נשמר; עדכן תקציב בהפקה חדשה באותן הגדרות.")
        oid, lip = fal_request("lipsync", LIPSYNC,
            {"video_url": video_url, "audio_url": prod["audio_url"], "sync_mode": "loop"}, identity,
            {"production": prod, "estimate_usd": conform["seconds"] / 60 * prod["lip_rate"]})
        # Mux original approved audio; reject short visual result instead of losing speech.
        oid, final = local_video(prod["image"], lip["file"], prod["audio"]["file"], prod["seconds"], prod["aspect"], False, prod.get("render_mode", "Draft"))
    else:
        oid, final = local_video(prod["image"], video["file"], prod["audio"]["file"] if prod["audio"] else None,
                                prod["seconds"], prod["aspect"], prod["loop"], prod.get("render_mode", "Draft"))
    st.session_state["final"] = final
    st.success("הסרטון מוכן ונשמר. הקריינות נשמרה במלואה.")


def history_ui():
    st.header("4. היסטוריה וגיבוי")
    st.caption("נשמרת על שרת האפליקציה. ב-Streamlit Cloud הקבצים עלולים להיעלם לאחר אתחול או פריסה; הורד גיבוי לפני שינוי.")
    with connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM operations ORDER BY created DESC LIMIT 100")]
    if rows:
        labels = {"image": "תמונה", "image_edit": "שינוי תמונה", "tts": "קריינות", "audio_local": "עיבוד אודיו", "video": "Kling", "lipsync": "Lip Sync", "local_video": "וידאו מקומי"}
        for row in rows[:25]:
            with st.expander(f"{row['created']} • {labels.get(row['kind'], row['kind'])} • {row['status']}"):
                if row["request_id"]:
                    st.code(row["request_id"], language=None)
                if row["error"]:
                    st.warning(row["error"])
                result = json.loads(row["result"])
                if row["status"] != "DONE" and row["request_id"]:
                    if st.button("בדוק מצב — ללא יצירה חדשה", key="poll_" + row["id"]):
                        def check(row=row):
                            res = poll_fal(row["id"])
                            payload = json.loads(row["payload"])
                            if row["kind"] in {"image", "image_edit"}:
                                st.session_state["image"] = {**res, "aspect": payload.get("aspect", "9:16")}
                            elif row["kind"] in {"video", "lipsync"} and payload.get("production"):
                                st.session_state["production"] = payload["production"]
                            st.rerun()
                        guard(check)
                if result.get("file"):
                    try:
                        path = media_path(result["file"])
                        if path.suffix == ".mp4":
                            st.video(str(path))
                        elif path.suffix in {".mp3", ".wav"}:
                            st.audio(str(path))
                        else:
                            st.image(str(path), width=200)
                        st.download_button("הורד תוצאה", path.read_bytes(), "veylura" + path.suffix, key="download_" + row["id"])
                        if row["kind"] in {"image", "image_edit"} and st.button("בחר תמונה זו", key="select_" + row["id"]):
                            st.session_state["image"] = {**result, "aspect": json.loads(row["payload"]).get("aspect", "9:16")}
                            st.rerun()
                    except StudioError as e:
                        st.warning(str(e))
    if st.button("הכן גיבוי מלא להורדה"):
        guard(lambda: st.session_state.update(backup=export_backup()))
    if "backup" in st.session_state:
        st.download_button("הורד גיבוי ZIP", st.session_state["backup"], "veylura-backup.zip", "application/zip")
        st.caption("הגיבוי כולל תמונות, אודיו וטקסטים. שמור אותו באופן פרטי.")
    with st.expander("שחזור גיבוי"):
        archive = st.file_uploader("גיבוי Veylura ZIP", type=["zip"])
        if st.button("שחזר והוסף להיסטוריה"):
            def restore():
                if archive is None:
                    raise StudioError("בחר גיבוי.")
                restore_backup(archive.getvalue())
                st.success("הגיבוי שוחזר. אפשר לבחור תמונה מההיסטוריה ולבדוק בקשות קיימות.")
            guard(restore)


if __name__ == "__main__":
    main()

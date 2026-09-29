import os
import tempfile
import fal_client
import streamlit as st
from elevenlabs.client import ElevenLabs

st.set_page_config(page_title="AI Video Generator - Veylura", layout="centered")
st.title("🎬 מחולל וידאו AI - הפקות אופנה ו-Lip Sync")

# בדיקת מפתחות API
if "FAL_KEY" in st.secrets:
    os.environ["FAL_KEY"] = st.secrets["FAL_KEY"]
else:
    st.error("⚠️ מפתח FAL_KEY חסר בהגדרות!")
    st.stop()

elevenlabs_api_key = st.secrets.get("ELEVENLABS_API_KEY")

# --- סרגל צדי לבחירת רמת עלות/איכות ---
st.sidebar.header("⚙️ הגדרות מודלים ועלויות")
mode_quality = st.sidebar.radio(
    "בחר מצב עבודה:",
    ["💡 מצב חיסכון (מכרז טסטים - Schnell & Standard)", "🌟 מצב איכות מקסימלית (הפקה סופית - Dev & Pro)"]
)

if "מצב חיסכון" in mode_quality:
    FLUX_MODEL = "fal-ai/flux/schnell"
    KLING_MODEL = "fal-ai/kling-video/v1.5/standard/image-to-video"
    st.sidebar.info("מצב חיסכון פעיל: עלות נמוכה מאוד לכל הרצה.")
else:
    FLUX_MODEL = "fal-ai/flux/dev"
    KLING_MODEL = "fal-ai/kling-video/v1.5/pro/image-to-video"
    st.sidebar.warning("מצב איכות פרימיום פעיל: עלות גבוהה יותר.")

# ניהול סשן לשמירת התמונה
if "current_image_url" not in st.session_state:
    st.session_state.current_image_url = None

# --- שלב 1: יצירת/העלאת תמונה ---
st.header("1️⃣ שלב התמונה")

upload_option = st.radio(
    "מקור התמונה:", 
    ["יצירת תמונה חדשה ב-AI", "העלאת תמונה ושמירת פנים (Character Consistency)"]
)

if upload_option == "יצירת תמונה חדשה ב-AI":
    image_prompt = st.text_area(
        "תיאור התמונה (Image Prompt):",
        value="A high-end fashion model in Tel Aviv, high editorial style, natural daylight, candid shot, photorealistic skin texture, 8k resolution"
    )
    if st.button("🎨 צור תמונה ראשונית"):
        with st.spinner("יוצר תמונה..."):
            try:
                img_result = fal_client.subscribe(
                    FLUX_MODEL,
                    arguments={
                        "prompt": image_prompt,
                        "image_size": "portrait_16_9",
                    },
                )
                st.session_state.current_image_url = img_result["images"][0]["url"]
                st.success("התמונה נוצרה בהצלחה!")
            except Exception as e:
                st.error(f"שגיאה ביצירת תמונה: {e}")

else:
    uploaded_file = st.file_uploader(
        "בחר תמונה ראשונית של הדמות (עוגן לפנים)", type=["jpg", "jpeg", "png"]
    )
    change_outfit_prompt = st.text_area(
        "תיאור השינוי (לוק חדש, בגדים, רקע):",
        value="The same model wearing elegant summer resort fashion, raw iPhone photo, natural daylight, candid shot, slightly imperfect, realistic skin texture, no airbrush"
    )
    if uploaded_file is not None and st.button("🔄 עדכן לוק לתמונה"):
        with st.spinner("מעבד תמונה ושומר על הפנים..."):
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix="." + uploaded_file.name.split(".")[-1]) as tmp_file:
                    tmp_file.write(uploaded_file.getvalue())
                    tmp_file_path = tmp_file.name

                character_image_url = fal_client.upload_file(tmp_file_path)
                os.unlink(tmp_file_path)

                consistent_img_result = fal_client.subscribe(
                    "fal-ai/flux/dev/image-to-image",
                    arguments={
                        "prompt": change_outfit_prompt,
                        "image_url": character_image_url,
                        "strength": 0.40,
                        "image_size": "portrait_16_9",
                    },
                )
                st.session_state.current_image_url = consistent_img_result["images"][0]["url"]
                st.success("הלוק עודכן בהצלחה!")
            except Exception as e:
                st.error(f"שגיאה בעדכון תמונה: {e}")

# הצגת התמונה הנבחרת
if st.session_state.current_image_url:
    st.image(st.session_state.current_image_url, caption="תמונת המקור לווידאו", width=400)

st.markdown("---")

# --- שלב 2: הנפשה, סאונד ו-Lip Sync ---
st.header("2️⃣ שלב הווידאו והדיבור (Lip-Sync)")

motion_prompt = st.text_area(
    "תיאור התנועה בווידאו:", 
    value="Front facing fashion model looking directly at the camera, smooth subtle head motion, perfectly visible mouth, clear studio lighting"
)

audio_mode = st.radio(
    "מקור שמע לסנכרון שפתיים:",
    [
        "יצירת קול מטקסט AI יוקרתי (ElevenLabs) 🗣️", 
        "ללא אודיו", 
        "הקלטת קול ישירה מהמיקרופון 🎙️", 
        "העלאת קובץ שמע (MP3 / WAV)"
    ]
)

spoken_text = None
voice_gender = "אישה 👩"
uploaded_audio = None
recorded_audio = None

if audio_mode == "הקלטת קול ישירה מהמיקרופון 🎙️":
    if hasattr(st, "audio_input"):
        recorded_audio = st.audio_input("לחץ על כפתור ההקלטה ודבר ישירות למיקרופון:")
    else:
        st.warning("עדכן את גרסת Streamlit לתמיכה בהקלטת מיקרופון.")

elif audio_mode == "יצירת קול מטקסט AI יוקרתי (ElevenLabs) 🗣️":
    spoken_text = st.text_area(
        "טקסט לדיבור בעברית:", 
        value="שלום, ברוכים הבאים לקולקציה החדשה של ויילורה."
    )
    voice_gender = st.radio("מגדר קול:", ["אישה 👩", "גבר 👨"], horizontal=True)

elif audio_mode == "העלאת קובץ שמע (MP3 / WAV)":
    uploaded_audio = st.file_uploader("העלה קובץ שמע:", type=["mp3", "wav", "m4a", "ogg"])

# כפתור הפקה סופי
if st.button("🚀 צור וידאו ו-Lip-Sync", type="primary"):
    if not st.session_state.current_image_url:
        st.error("⚠️ נא ליצור או להעלות תמונה בשלב 1 תחילה!")
    else:
        try:
            # 1. וידאו Kling
            with st.spinner("🎬 מנפיש לווידאו ב-Kling..."):
                video_result = fal_client.subscribe(
                    KLING_MODEL,
                    arguments={
                        "prompt": motion_prompt,
                        "image_url": st.session_state.current_image_url,
                        "duration": "5",
                    },
                )
                video_url = video_result["video"]["url"]

            # 2. אודיו
            audio_url = None

            if audio_mode == "הקלטת קול ישירה מהמיקרופון 🎙️" and recorded_audio is not None:
                with st.spinner("🎤 מעלה הקלטה קולית..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_rec:
                        tmp_rec.write(recorded_audio.getvalue())
                        tmp_rec_path = tmp_rec.name
                    audio_url = fal_client.upload_file(tmp_rec_path)
                    os.unlink(tmp_rec_path)

            elif audio_mode == "יצירת קול מטקסט AI יוקרתי (ElevenLabs) 🗣️" and spoken_text:
                if not elevenlabs_api_key:
                    st.error("⚠️ מפתח ELEVENLABS_API_KEY חסר!")
                else:
                    with st.spinner("🗣️ מייצר קול ב-ElevenLabs..."):
                        client = ElevenLabs(api_key=elevenlabs_api_key)
                        voice_id = "21m00Tcm4TlvDq8ikWAM" if voice_gender == "אישה 👩" else "pNInz6obpgDQGcFmaJgB"
                        
                        audio_generator = client.text_to_speech.convert(
                            voice_id=voice_id,
                            text=spoken_text,
                            model_id="eleven_multilingual_v2"
                        )
                        audio_bytes = b"".join(audio_generator)

                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_audio:
                            tmp_audio.write(audio_bytes)
                            tmp_audio_path = tmp_audio.name
                        
                        audio_url = fal_client.upload_file(tmp_audio_path)
                        os.unlink(tmp_audio_path)

            elif audio_mode == "העלאת קובץ שמע (MP3 / WAV)" and uploaded_audio is not None:
                with st.spinner("🎤 מעלה קובץ שמע..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix="." + uploaded_audio.name.split(".")[-1]) as tmp_audio:
                        tmp_audio.write(uploaded_audio.getvalue())
                        tmp_audio_path = tmp_audio.name
                    audio_url = fal_client.upload_file(tmp_audio_path)
                    os.unlink(tmp_audio_path)

            # 3. Lip-Sync
            if audio_url:
                with st.spinner("👄 מבצע סנכרון שפתיים (Lip-Sync)..."):
                    lipsync_result = fal_client.subscribe(
                        "fal-ai/sync-lipsync",
                        arguments={
                            "video_url": video_url,
                            "audio_url": audio_url,
                        },
                    )
                    final_video_url = lipsync_result.get("video", {}).get("url") or lipsync_result.get("url")
                    if final_video_url:
                        video_url = final_video_url

                st.success("🎉 הווידאו המושלם כולל סנכרון שפתיים מוכן!")
                st.video(video_url)
            else:
                st.success("🎉 הווידאו המושלם מוכן (ללא סאונד)!")
                st.video(video_url)

        except Exception as e:
            st.error(f"שגיאה: {e}")

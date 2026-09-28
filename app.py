import os
import tempfile
import fal_client
import streamlit as st

st.set_page_config(page_title="AI Video Generator - Veylura", layout="centered")
st.title("🎬 מחולל וידאו AI - הפקות אופנה, הקלטה קולית ו-Lip Sync")

# חיבור מפתח ה-API מההגדרות השמורות
if "FAL_KEY" in st.secrets:
    os.environ["FAL_KEY"] = st.secrets["FAL_KEY"]
else:
    st.error("⚠️ מפתח FAL_KEY חסר בהגדרות!")
    st.stop()

# בחירה בין יצירת תמונה חדשה מ-AI לבין העלאת תמונה קיימת ושמירת פנים
upload_option = st.radio(
    "בחר מקור לתמונה:", ["יצירת תמונה חדשה מ-AI", "העלאת תמונה ושמירת פנים (Character Consistency)"]
)

image_url = None
character_image_url = None

if upload_option == "יצירת תמונה חדשה מ-AI":
    image_prompt = st.text_area(
        "תיאור התמונה:",
        "A fashion model in urban Tel Aviv, highly detailed 8k photography",
    )
else:
    uploaded_file = st.file_uploader(
        "בחר תמונה ראשונית של הדמות (עוגן לפנים)", type=["jpg", "jpeg", "png"]
    )
    if uploaded_file is not None:
        st.image(uploaded_file, caption="תמונת המקור שהועלתה")
        
        # שמירת הקובץ בצורה זמנית כדי ש-fal_client יוכל לקרוא אותו
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix="." + uploaded_file.name.split(".")[-1],
        ) as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_file_path = tmp_file.name

        with st.spinner("מעלה את תמונת המקור לשרת..."):
            character_image_url = fal_client.upload_file(tmp_file_path)
            os.unlink(tmp_file_path)

    # שדה לשינוי לוק עם פרומפט מכוון למראה ריאליסטי
    change_outfit_prompt = st.text_area(
        "תיאור השינוי (לוק חדש, בגדים, רקע וכו' תוך שמירה על הפנים):",
        "The same model wearing elegant summer resort fashion, raw iPhone photo, natural daylight, candid shot, slightly imperfect, realistic skin texture, no airbrush",
    )

motion_prompt = st.text_area(
    "תיאור התנועה בווידאו:", "The model turns slowly toward the camera, looking stylish, cinematic movement"
)

# תוספת: אפשרויות שמע מורחבות (קובץ חיצוני, TTS עברית/אנגלית, או הקלטת קול בלייב)
st.markdown("---")
st.subheader("🎵 סנכרון שפתיים ושמע (Lip-Sync)")

audio_mode = st.radio(
    "בחר מקור שמע לדיבור/סנכרון שפתיים:",
    [
        "ללא אודיו", 
        "הקלטת קול ישירה מהמיקרופון 🎙️", 
        "יצירת קול מטקסט (Text-to-Speech AI)", 
        "העלאת קובץ שמע (MP3 / WAV)"
    ]
)

spoken_text = None
language = "עברית 🇮🇱"
voice_gender = "אישה 👩"
uploaded_audio = None
recorded_audio = None

if audio_mode == "הקלטת קול ישירה מהמיקרופון 🎙️":
    # רכיב הקלטת קול מובנה של Streamlit
    if hasattr(st, "audio_input"):
        recorded_audio = st.audio_input("לחץ על כפתור ההקלטה ודבר ישירות למיקרופון:")
    else:
        st.warning("גרסת Streamlit אינה תומכת בהקלטה ישירה. יש לעדכן את streamlit או להשתמש בהעלאת קובץ.")

elif audio_mode == "יצירת קול מטקסט (Text-to-Speech AI)":
    spoken_text = st.text_area("הכנס את הטקסט שהדמות תגיד:", "שלום, ברוכים הבאים לתצוגת האופנה החדשה של סוכנות ויילורה.")
    col1, col2 = st.columns(2)
    with col1:
        language = st.radio("בחר שפה:", ["עברית 🇮🇱", "אנגלית 🇺🇸"], horizontal=True)
    with col2:
        voice_gender = st.radio("בחר מגדר קול:", ["אישה 👩", "גבר 👨"], horizontal=True)

elif audio_mode == "העלאת קובץ שמע (MP3 / WAV)":
    uploaded_audio = st.file_uploader(
        "העלה קובץ שמע (MP3 / WAV):", 
        type=["mp3", "wav", "m4a", "ogg"]
    )

if st.button("🚀 צור סרטון", type="primary"):
    try:
        # שלב 1: אם בחרנו לייצר תמונה חדשה מאפס
        if upload_option == "יצירת תמונה חדשה מ-AI":
            with st.spinner("🎨 יוצר תמונת בסיס..."):
                img_result = fal_client.subscribe(
                    "fal-ai/flux/dev",
                    arguments={
                        "prompt": image_prompt,
                        "image_size": "portrait_16_9",
                    },
                )
                image_url = img_result["images"][0]["url"]
                st.image(image_url, caption="תמונת בסיס")

        # שלב 2: עדכון הלוק של הדמות עם שמירת פנים
        elif upload_option == "העלאת תמונה ושמירת פנים (Character Consistency)":
            if character_image_url:
                with st.spinner("🔄 מעדכן את הלוק של הדמות תוך שמירה על הפנים..."):
                    consistent_img_result = fal_client.subscribe(
                        "fal-ai/flux/dev/image-to-image",
                        arguments={
                            "prompt": change_outfit_prompt,
                            "image_url": character_image_url,
                            "strength": 0.70,  
                            "image_size": "portrait_16_9",
                        },
                    )
                    image_url = consistent_img_result["images"][0]["url"]
                    st.image(image_url, caption="דמות לאחר עדכון לוק (שמירת פנים)")
            else:
                st.warning("נא להעלות תחילה תמונה של הדמות.")

        # שלב 3: הנפשת התמונה הסופית בווידאו (Kling AI)
        video_url = None
        if image_url:
            with st.spinner("🎬 מנפיש לווידאו ב-Kling... (זה יכול לקחת כמה דקות)"):
                video_result = fal_client.subscribe(
                    "fal-ai/kling-video/v1.5/pro/image-to-video",
                    arguments={
                        "prompt": motion_prompt,
                        "image_url": image_url,
                        "duration": "5",
                    },
                )
                video_url = video_result["video"]["url"]

        # שלב 4: טיפול באודיו (הקלטה / TTS / קובץ) וביצוע Lip-Sync
        audio_url = None

        if video_url:
            # אופציה א': הקלטה ישירה מהמיקרופון
            if audio_mode == "הקלטת קול ישירה מהמיקרופון 🎙️" and recorded_audio is not None:
                with st.spinner("🎤 מעלה את ההקלטה הקולית..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_rec:
                        tmp_rec.write(recorded_audio.getvalue())
                        tmp_rec_path = tmp_rec.name

                    audio_url = fal_client.upload_file(tmp_rec_path)
                    os.unlink(tmp_rec_path)

            # אופציה ב': מחולל דיבור מטקסט (TTS) - כולל תמיכה בעברית ובאנגלית
            elif audio_mode == "יצירת קול מטקסט (Text-to-Speech AI)" and spoken_text:
                with st.spinner("🗣️ מייצר קול AI מהטקסט..."):
                    if language == "עברית 🇮🇱":
                        # נתיב מתוקן עבור ElevenLabs ב-Fal.ai
                        voice_id = "21m00Tcm4TlvDq8ikWAM" if voice_gender == "אישה 👩" else "pNInz6obpgDQGcFmaJgB"
                        tts_result = fal_client.subscribe(
                            "fal-ai/elevenlabs/tts",
                            arguments={
                                "text": spoken_text,
                                "prompt": spoken_text,
                                "voice": voice_id
                            },
                        )
                    else:
                        # אנגלית - PlayAI
                        voice_id = (
                            "Jennifer (English (US)/American)" 
                            if voice_gender == "אישה 👩" 
                            else "Angelo (English (US)/American)"
                        )
                        tts_result = fal_client.subscribe(
                            "fal-ai/playai/tts/v3",
                            arguments={
                                "prompt": spoken_text,
                                "voice": voice_id
                            },
                        )
                    audio_url = tts_result.get("audio", {}).get("url") or tts_result.get("url")

            # אופציה ג': קובץ שמע מועלה
            elif audio_mode == "העלאת קובץ שמע (MP3 / WAV)" and uploaded_audio is not None:
                with st.spinner("🎤 מעלה את קובץ האודיו..."):
                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix="." + uploaded_audio.name.split(".")[-1],
                    ) as tmp_audio:
                        tmp_audio.write(uploaded_audio.getvalue())
                        tmp_audio_path = tmp_audio.name

                    audio_url = fal_client.upload_file(tmp_audio_path)
                    os.unlink(tmp_audio_path)

            # ביצוע Lip-Sync במידה ויש audio_url
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

                st.success("🎉 הווידאו הופק בהצלחה כולל קול וסנכרון שפתיים!")
                st.video(video_url)
            else:
                st.success("🎉 הווידאו הופק בהצלחה (ללא סאונד)!")
                st.video(video_url)

    except Exception as e:
        st.error(f"שגיאה: {e}")

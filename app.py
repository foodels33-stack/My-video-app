import os
import tempfile
import fal_client
import streamlit as st
from elevenlabs.client import ElevenLabs

st.set_page_config(page_title="AI Video Generator - Veylura", layout="centered")
st.title("🎬 מחולל וידאו AI - הפקות אופנה, הקלטה קולית ו-Lip Sync")

# בדיקת מפתחות API
if "FAL_KEY" in st.secrets:
    os.environ["FAL_KEY"] = st.secrets["FAL_KEY"]
else:
    st.error("⚠️ מפתח FAL_KEY חסר בהגדרות!")
    st.stop()

elevenlabs_api_key = st.secrets.get("ELEVENLABS_API_KEY")

# בחירת מקור לתמונה
upload_option = st.radio(
    "בחר מקור לתמונה:", ["יצירת תמונה חדשה מ-AI", "העלאת תמונה ושמירת פנים (Character Consistency)"]
)

image_url = None
character_image_url = None

if upload_option == "יצירת תמונה חדשה מ-AI":
    image_prompt = st.text_area(
        "תיאור התמונה:",
        value="A high-end fashion model in Tel Aviv, high editorial style, natural daylight, candid shot, photorealistic skin texture, 8k resolution"
    )
else:
    uploaded_file = st.file_uploader(
        "בחר תמונה ראשונית של הדמות (עוגן לפנים)", type=["jpg", "jpeg", "png"]
    )
    if uploaded_file is not None:
        st.image(uploaded_file, caption="תמונת המקור שהועלתה")
        
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix="." + uploaded_file.name.split(".")[-1],
        ) as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_file_path = tmp_file.name

        with st.spinner("מעלה את תמונת המקור לשרת..."):
            character_image_url = fal_client.upload_file(tmp_file_path)
            os.unlink(tmp_file_path)

    change_outfit_prompt = st.text_area(
        "תיאור השינוי (לוק חדש, בגדים, רקע וכו' תוך שמירה על הפנים):",
        value="The same model wearing elegant summer resort fashion, raw iPhone photo, natural daylight, candid shot, slightly imperfect, realistic skin texture, no airbrush"
    )

motion_prompt = st.text_area(
    "תיאור התנועה בווידאו:", 
    value="Front facing fashion model looking directly at the camera, smooth subtle head motion, perfectly visible mouth, clear studio lighting"
)

# שמע ו-Lip Sync
st.markdown("---")
st.subheader("🎵 סנכרון שפתיים ושמע (Lip-Sync)")

audio_mode = st.radio(
    "בחר מקור שמע לדיבור/סנכרון שפתיים:",
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
        st.warning("גרסת Streamlit אינה תומכת בהקלטה ישירה. יש לעדכן את streamlit או להשתמש בהעלאת קובץ.")

elif audio_mode == "יצירת קול מטקסט AI יוקרתי (ElevenLabs) 🗣️":
    spoken_text = st.text_area(
        "הכנס את הטקסט שהדמות תגיד (מומלץ משפט קצר של 3-5 שניות):", 
        value="שלום, ברוכים הבאים לקולקציה החדשה של ויילורה."
    )
    voice_gender = st.radio("בחר מגדר קול:", ["אישה 👩", "גבר 👨"], horizontal=True)

elif audio_mode == "העלאת קובץ שמע (MP3 / WAV)":
    uploaded_audio = st.file_uploader(
        "העלה קובץ שמע (MP3 / WAV):", 
        type=["mp3", "wav", "m4a", "ogg"]
    )

if st.button("🚀 צור סרטון", type="primary"):
    try:
        # 1. תמונה
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

        elif upload_option == "העלאת תמונה ושמירת פנים (Character Consistency)":
            if character_image_url:
                with st.spinner("🔄 מעדכן את הלוק של הדמות תוך שמירה על הפנים..."):
                    consistent_img_result = fal_client.subscribe(
                        "fal-ai/flux/dev/image-to-image",
                        arguments={
                            "prompt": change_outfit_prompt,
                            "image_url": character_image_url,
                            "strength": 0.40,
                            "image_size": "portrait_16_9",
                        },
                    )
                    image_url = consistent_img_result["images"][0]["url"]
                    st.image(image_url, caption="דמות לאחר עדכון לוק")
            else:
                st.warning("נא להעלות תחילה תמונה של הדמות.")

        # 2. וידאו Kling
        video_url = None
        if image_url:
            with st.spinner("🎬 מנפיש לווידאו ב-Kling..."):
                video_result = fal_client.subscribe(
                    "fal-ai/kling-video/v1.5/pro/image-to-video",
                    arguments={
                        "prompt": motion_prompt,
                        "image_url": image_url,
                        "duration": "5",
                    },
                )
                video_url = video_result["video"]["url"]

        # 3. אודיו
        audio_url = None

        if video_url:
            if audio_mode == "הקלטת קול ישירה מהמיקרופון 🎙️" and recorded_audio is not None:
                with st.spinner("🎤 מעלה את ההקלטה הקולית..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_rec:
                        tmp_rec.write(recorded_audio.getvalue())
                        tmp_rec_path = tmp_rec.name

                    audio_url = fal_client.upload_file(tmp_rec_path)
                    os.unlink(tmp_rec_path)

            elif audio_mode == "יצירת קול מטקסט AI יוקרתי (ElevenLabs) 🗣️" and spoken_text:
                if not elevenlabs_api_key:
                    st.error("⚠️ מפתח ELEVENLABS_API_KEY חסר ב-st.secrets!")
                else:
                    with st.spinner("🗣️ מייצר קול אנושי בעברית דרך ElevenLabs..."):
                        client = ElevenLabs(api_key=elevenlabs_api_key)
                        
                        # קול נשי (Rachel) או גברי (Adam)
                        voice_id = "21m00Tcm4TlvDq8ikWAM" if voice_gender == "אישה 👩" else "pNInz6obpgDQGcFmaJgB"
                        
                        # מתודה מעודכנת של ElevenLabs SDK
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
                with st.spinner("🎤 מעלה את קובץ האודיו..."):
                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix="." + uploaded_audio.name.split(".")[-1],
                    ) as tmp_audio:
                        tmp_audio.write(uploaded_audio.getvalue())
                        tmp_audio_path = tmp_audio.name

                    audio_url = fal_client.upload_file(tmp_audio_path)
                    os.unlink(tmp_audio_path)

            # 4. Lip-Sync
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

                st.success("🎉 הווידאו הופק בהצלחה כולל קול אנושי וסנכרון שפתיים!")
                st.video(video_url)
            else:
                st.success("🎉 הווידאו הופק בהצלחה (ללא סאונד)!")
                st.video(video_url)

    except Exception as e:
        st.error(f"שגיאה: {e}")

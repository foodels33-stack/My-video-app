import os
import tempfile
import fal_client
import streamlit as st

st.set_page_config(page_title="AI Video Generator", layout="centered")
st.title("🎬 מחולל וידאו AI")

# חיבור מפתח ה-API מההגדרות השמורות
if "FAL_KEY" in st.secrets:
    os.environ["FAL_KEY"] = st.secrets["FAL_KEY"]
else:
    st.error("⚠️ מפתח FAL_KEY חסר בהגדרות!")
    st.stop()

# בחירה בין יצירת תמונה חדשה מ-AI לבין העלאת תמונה קיימת
upload_option = st.radio(
    "בחר מקור לתמונה:", ["יצירת תמונה חדשה מ-AI", "העלאת תמונה משלי"]
)

image_url = None

if upload_option == "יצירת תמונה חדשה מ-AI":
    image_prompt = st.text_area(
        "תיאור התמונה:",
        "A fashion model in urban Tel Aviv, highly detailed 8k photography",
    )
else:
    uploaded_file = st.file_uploader(
        "בחר תמונה מהמכשיר", type=["jpg", "jpeg", "png"]
    )
    if uploaded_file is not None:
        st.image(uploaded_file, caption="התמונה שהועלתה")
        # שמירת הקובץ בצורה זמנית כדי ש-fal_client יוכל לקרוא אותו כנתיב קובץ תקין
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix="." + uploaded_file.name.split(".")[-1],
        ) as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_file_path = tmp_file.name

        with st.spinner("מעלה את התמונה לשרת..."):
            image_url = fal_client.upload_file(tmp_file_path)
            # ניקוי הקובץ הזמני מהמערכת
            os.unlink(tmp_file_path)

motion_prompt = st.text_area(
    "תיאור התנועה בווידאו:", "The model turns slowly toward the camera and smiles"
)

if st.button("🚀 צור סרטון", type="primary"):
    try:
        # שלב 1: אם בחרנו לייצר תמונה חדשה מ-AI
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

        # שלב 2: הנפשת התמונה (בין אם נוצרה מטקסט ובין אם הועלתה)
        if image_url:
            with st.spinner("🎬 מנפיש לווידאו... (זה יכול לקחת כמה דקות)"):
                video_result = fal_client.subscribe(
                    "fal-ai/kling-video/v1.5/pro/image-to-video",
                    arguments={
                        "prompt": motion_prompt,
                        "image_url": image_url,
                        "duration": "5",
                    },
                )
                video_url = video_result["video"]["url"]
                st.success("🎉 מוכן!")
                st.video(video_url)
        else:
            st.warning("נא להעלות תמונה או לבחור יצירה מ-AI לפני הלחיצה.")

    except Exception as e:
        st.error(f"שגיאה: {e}")

import os
import tempfile
import fal_client
import streamlit as st

st.set_page_config(page_title="AI Video Generator", layout="centered")
st.title("🎬 מחולל וידאו AI - הפקות אופנה")

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

    # שדה נוסף שמאפשר לבקש לוק חדש (בגד ים, בגדים, רקע) תוך שמירה על הפנים
    change_outfit_prompt = st.text_area(
        "תיאור השינוי (לוק חדש, בגד ים, רקע וכו' תוך שמירה על הפנים):",
        "The same model wearing a sexy luxury designer bikini, standing on a yacht deck, photorealistic, 8k",
    )

motion_prompt = st.text_area(
    "תיאור התנועה בווידאו:", "The model turns slowly toward the camera and smiles, cinematic movement"
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

        # שלב 2: אם בחרנו להעלות תמונה ולשנות לה את הלוק תוך שמירת הפנים (Face-to-Image / IP-Adapter style)
        elif upload_option == "העלאת תמונה ושמירת פנים (Character Consistency)":
            if character_image_url:
                with st.spinner("🔄 מעדכן את הלוק של הדמות תוך שמירה על הפנים..."):
                    # שימוש ב-Flux Dev עם התייחסות לתמונת המקור כדי לשמור עקביות פנים ולשנות לוק
                    consistent_img_result = fal_client.subscribe(
                        "fal-ai/flux/dev/image-to-image",
                        arguments={
                            "prompt": change_outfit_prompt,
                            "image_url": character_image_url,
                            "strength": 0.75,  # שולט בעוצמת השינוי (0.75 מאפשר להחליף בגדים ולשמור על מבנה הפנים)
                            "image_size": "portrait_16_9",
                        },
                    )
                    image_url = consistent_img_result["images"][0]["url"]
                    st.image(image_url, caption="דמות לאחר עדכון לוק (שמירת פנים)")
            else:
                st.warning("נא להעלות תחילה תמונה של הדמות.")

        # שלב 3: הנפשת התמונה הסופית בווידאו (Kling)
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

    except Exception as e:
        st.error(f"שגיאה: {e}")

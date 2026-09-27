import os
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

image_prompt = st.text_area(
    "תיאור התמונה:",
    "A fashion model in urban Tel Aviv, highly detailed 8k photography",
)

motion_prompt = st.text_area(
    "תיאור התנועה בווידאו:", "The model turns slowly toward the camera and smiles"
)

if st.button("🚀 צור סרטון", type="primary"):
    try:
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

        with st.spinner("🎬 מנפיש לווידאו..."):
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

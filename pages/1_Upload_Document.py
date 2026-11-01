"""Stage 2: upload PDF, TXT or image material, extract text, find topics and save a course."""
import hashlib

import pandas as pd
import streamlit as st

import ui
from backend import documents, storage, topics
from backend.documents import DocumentError
from backend.schemas import Course, Topic

ui.setup("Upload Learning Material", "📄")
ui.require(student=True)
S = st.session_state

st.title("📄 Upload Learning Material")
st.write(
    "Upload your study material. The tutor extracts its text and uses it "
    "to identify topics, create lessons and prepare quizzes."
)
st.caption(
    "Supported: PDF, TXT, PNG, JPG, JPEG, WEBP, BMP, TIFF and GIF. "
    "Scanned PDFs and images are processed with OCR."
)

uploaded = st.file_uploader(
    "Choose your study material",
    type=["pdf", "txt", "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"],
    accept_multiple_files=False,
    help="For best OCR results, upload a clear, well-lit image with readable printed text."
)
use_sample = st.checkbox("Use sample text (photosynthesis) for testing")

if use_sample:
    source_name = "sample.txt"
    source_bytes = documents.SAMPLE_TEXT.encode("utf-8")
elif uploaded is not None:
    source_name = uploaded.name
    source_bytes = uploaded.getvalue()
else:
    source_name = None
    source_bytes = None

if source_name is not None:
    # Hash the actual content, not just the filename/size. This avoids stale results
    # when a user uploads a different file with the same name and byte length.
    content_hash = hashlib.sha256(source_bytes).hexdigest()
    document_key = f"{source_name}:{content_hash}"

    if S.get("doc_key") != document_key:
        try:
            with st.spinner("Reading your file and extracting text..."):
                extracted_pages = documents.read_document(source_bytes, source_name)
                documents.check_quality(extracted_pages)
                extracted_spans = documents.make_spans(extracted_pages)

            if not extracted_spans:
                raise DocumentError("No usable text passages were found in this file.")

            S.update(
                doc_key=document_key,
                doc_pages=extracted_pages,
                doc_spans=extracted_spans,
                doc_topics=None,
                uploaded_filename=source_name,
            )
            st.success("Text extracted successfully.")

        except DocumentError as exc:
            for key in ("doc_key", "doc_pages", "doc_spans", "doc_topics", "uploaded_filename"):
                S.pop(key, None)
            st.error(str(exc))
            st.stop()
        except Exception as exc:
            st.error(f"An unexpected error occurred while processing the file: {exc}")
            st.stop()

if not S.get("doc_spans"):
    st.info("Upload a file or select the sample text to begin.")
    st.stop()

spans = S.doc_spans
pages = S.doc_pages

st.subheader("Extracted Document")
st.caption(f"File: {S.get('uploaded_filename', 'Uploaded material')}")
c1, c2, c3 = st.columns(3)
c1.metric("Pages / frames", len(pages))
c2.metric("Extracted characters", sum(len(p.get("text", "")) for p in pages))
c3.metric("Passages", len(spans))

with st.expander("Preview extracted text", expanded=True):
    first_text = pages[0].get("text", "")
    st.write(first_text[:3000] if first_text else "No text was detected on the first page/frame.")
    if len(first_text) > 3000:
        st.caption("Preview limited to the first 3,000 characters.")

with st.expander("Preview first passage"):
    st.write(spans[0].text)

windows, cut = topics.make_windows(spans)
if cut:
    st.warning(
        f"This document is long, so the topic finder will initially use "
        f"the first {len(windows)} sections."
    )

if st.button("Find Topics", type="primary"):
    progress = st.progress(0.0, text="Starting...")
    found = ui.run(
        topics.find_topics,
        spans,
        lambda fraction, message: progress.progress(min(fraction, 1.0), text=message),
        spinner="Finding the main topics...",
    )
    progress.empty()

    if found:
        S.doc_topics = found
        S.course_title = (
            ui.run(topics.make_course_title, found, spinner="Naming your course...")
            or "My course"
        )
    elif found is not None:
        st.error("I could not find clear topics in this document. Try a longer or clearer file.")

if not S.get("doc_topics"):
    st.stop()


def _safe_string(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


original_topics = {topic.id: topic for topic in S.doc_topics}
dataframe = pd.DataFrame([
    {
        "id": topic.id,
        "title": topic.title,
        "summary": topic.summary,
        "difficulty": topic.difficulty,
        "prerequisites": ", ".join(topic.prerequisites),
    }
    for topic in S.doc_topics
])

st.subheader("Topics Found")
st.caption("You can rename topics or remove rows you do not need.")
edited = st.data_editor(
    dataframe,
    num_rows="dynamic",
    width="stretch",
    hide_index=True,
    disabled=["id"],
    column_config={
        "difficulty": st.column_config.SelectboxColumn(
            options=["beginner", "intermediate", "advanced"],
            required=True,
        )
    },
)
course_title = st.text_input("Course title", value=S.get("course_title", "My course"))

if st.button("Save Course", type="primary"):
    if not course_title.strip():
        st.error("Please enter a course title.")
        st.stop()

    updated_topics = []
    for row in edited.to_dict("records"):
        topic_id = _safe_string(row.get("id"))
        # Only retain existing topic IDs. New arbitrary rows cannot safely refer to source passages.
        if topic_id in original_topics and _safe_string(row.get("title")).strip():
            original_topic = original_topics[topic_id]
            updated_topics.append(
                Topic(
                    id=topic_id,
                    title=_safe_string(row.get("title")).strip(),
                    summary=_safe_string(row.get("summary")),
                    difficulty=_safe_string(row.get("difficulty")) or "beginner",
                    prerequisites=[
                        item.strip()
                        for item in _safe_string(row.get("prerequisites")).split(",")
                        if item.strip()
                    ],
                    source_spans=original_topic.source_spans,
                )
            )

    cleaned_topics = topics.clean_topics(updated_topics, {span.id for span in spans})
    if not cleaned_topics:
        st.error("Please keep at least one valid topic.")
    else:
        course_id = storage.slugify(course_title)
        storage.save_json(storage.course_dir(course_id) / "source_spans.json", spans)
        storage.save_json(
            storage.course_dir(course_id) / "topics.json",
            Course(course_id=course_id, course_title=course_title.strip(), topics=cleaned_topics),
        )
        S.course_id = course_id
        st.success(f"Saved '{course_title}' with {len(cleaned_topics)} topics.")
        ui.link("pages/2_First_Meeting.py", "Next: first meeting", "➡️")

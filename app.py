"""Home: a dashboard. Choose a student and course, see progress, jump to the next step."""
import streamlit as st

import theme
import ui
from backend import llm, progress, storage

ui.setup("Your Personal Teacher", "🎓")
S = st.session_state

sid = S.student_id
prof = storage.load_profile(sid) if sid else None
path = storage.load_path(sid) if sid else None
prog = progress.load_progress(sid) if sid else None
shown_name = prof.name if prof else (sid.replace("-", " ").title() if sid else "")

theme.hero(f"Welcome back, {shown_name}" if shown_name else "Welcome",
           "What would you like to <em>learn today?</em>",
           "Upload your study material and I'll teach it step by step, at your pace, with a small target each day.")

if not llm.has_key():
    st.error("I need an API key to think. Paste your Groq key in the sidebar, or add `GROQ_API_KEY = \"...\"` to "
             "`.streamlit/secrets.toml` (or the app's Secrets on Streamlit Cloud).")

# ---------- who is learning ----------
students = storage.list_students()
with st.container(key="panel_who"):
    st.markdown('<p class="card-title">Who is learning today?</p>', unsafe_allow_html=True)
    left, right = st.columns(2, gap="large")
    with left:
        if students:
            idx = students.index(S.student_id) if S.student_id in students else None
            pick = st.selectbox("Choose a student", students, index=idx, placeholder="Pick a name")
            if pick and pick != S.student_id:
                S.student_id = pick
                st.rerun()
        else:
            st.caption("No students yet. Create the first one on the right.")
    with right:
        with st.form("new_student", clear_on_submit=True):
            new_name = st.text_input("Or create a new student")
            if st.form_submit_button("Create student"):
                if not new_name.strip():
                    st.warning("Please type a name first.")
                else:
                    S.student_id = storage.slugify(new_name)
                    storage.student_dir(S.student_id)
                    st.rerun()

# ---------- numbers ----------
loaded = storage.load_course(S.course_id) if S.course_id else None
total = len(path.items) if path else 0
done = len(set(prog["completed_topics"]) & {i.topic_id for i in path.items}) if (path and prog) else 0
if sid:
    st.write("")
    theme.stats([
        ("Streak", f"{prog['streak']} days", "Days in a row"),
        ("Lessons done", f"{done} of {total}" if total else "0", "In your plan" if total else "No plan yet"),
        ("Topics", str(len(loaded[0].topics)) if loaded else "0", loaded[0].course_title if loaded else "No course chosen"),
        ("Days answered", str(len(prog["history"])), "Daily check-ins"),
    ])

# ---------- journey ----------
st.markdown('<div class="section-title">Your learning journey</div>', unsafe_allow_html=True)
flags = [bool(loaded), bool(prof), bool(path), bool(total) and done == total]
nxt = next((i for i, f in enumerate(flags) if not f), None)
target = progress.current_target(prog) if prog else None


def status(i: int):
    if flags[i]:
        return "Done", "ok"
    return ("Up next", "next") if i == nxt else ("To do", "todo")


lesson_status = (f"{done} of {total}", "") if total and not flags[3] else status(3)
steps = [
    ("📄", "Upload a document", "Add a PDF, a text file or a photo of your notes. I'll find the topics in it.",
     status(0), "pages/1_Upload_Document.py", "Upload"),
    ("💬", "First meeting", "Tell me your goals and how you like to learn, so every lesson fits you.",
     status(1), "pages/2_First_Meeting.py", "Start the chat"),
    ("🧭", "Choose topics and pre-test", "Pick what to learn. A short, ungraded quiz shows what you already know.",
     status(2), "pages/3_Choose_and_Pretest.py", "Choose topics"),
    ("📚", "Learn", "Short lessons with warm-up, practice and a quiz at the end of each one.",
     lesson_status, "pages/4_Learn.py", "Open my lesson"),
    ("📅", "Today", "Your daily target, a quick check-in and your streak.",
     (f"Day {target.day_index}", "") if target else ("To do", "todo"), "pages/5_Today.py", "See today"),
    ("📊", "AI usage", "Tokens, characters and timings for every answer the teacher writes.",
     ("", ""), "pages/6_Usage.py", "View usage"),
]
for row in range(0, len(steps), 3):
    cols = st.columns(3, gap="medium")
    for col, (n, (icon, title, desc, (label, tone), page, cta)) in zip(cols, list(enumerate(steps))[row:row + 3]):
        with col.container(key=f"card_step_{n}"):
            st.markdown(theme.card_head(icon, title, desc, label, tone), unsafe_allow_html=True)
            ui.link(page, cta, ":material/arrow_forward:")

# ---------- courses ----------
courses = [(cid, storage.load_course(cid)) for cid in storage.list_courses()]
courses = [(cid, c) for cid, c in courses if c]
if courses:
    st.markdown('<div class="section-title">Your courses</div>', unsafe_allow_html=True)
    query = st.text_input("Search courses", placeholder="Search by course or topic", label_visibility="collapsed").strip().lower()
    shown = [(cid, c) for cid, c in courses
             if not query or query in c[0].course_title.lower() or any(query in t.title.lower() for t in c[0].topics)]
    if not shown:
        st.info("No course matches that search. Clear it to see all your courses.")
    for row in range(0, len(shown), 3):
        cols = st.columns(3, gap="medium")
        for col, (cid, (course, _)) in zip(cols, [(i, c) for i, c in shown[row:row + 3]]):
            with col.container(key=f"card_course_{row}_{cid[:24].replace('-', '_')}"):
                current = cid == S.course_id
                chips = "".join(f'<span class="chip">{theme.esc(t.title)}</span>' for t in course.topics[:4])
                more = f'<span class="chip">+{len(course.topics) - 4} more</span>' if len(course.topics) > 4 else ""
                st.markdown(theme.card_head("📘", course.course_title, f"{len(course.topics)} topics from your material.",
                                            "Selected" if current else "", "ok" if current else "")
                            + f'<div class="chips">{chips}{more}</div>', unsafe_allow_html=True)
                if not current and st.button("Use this course", key=f"pick_{cid}"):
                    S.course_id = cid
                    st.rerun()

if sid:
    st.write("")
    if st.button("Say hello 👋"):
        reply = ui.run(llm.ask, f"Greet a student called {shown_name} in one warm sentence and invite them to start.",
                       system=llm.TEACHER_SYSTEM, max_tokens=80)
        if reply:
            with st.chat_message("assistant"):
                st.write(reply)

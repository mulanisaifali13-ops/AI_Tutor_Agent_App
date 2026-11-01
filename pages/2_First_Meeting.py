"""Stage 3: a warm chat that collects goals and details, then saves the profile."""
import streamlit as st

import ui
from backend import storage, tutor
from backend.profile import build_profile, opening_message, question_text
from backend.schemas import StudentProfile

ui.setup("First meeting", "💬")
ui.require(student=True)
S = st.session_state
sid = S.student_id
loaded = storage.load_course(S.course_id) if S.get("course_id") else None
course_title = loaded[0].course_title if loaded else ""
course_topics = [t.title for t in loaded[0].topics] if loaded else []
course_spans = loaded[1] if loaded else []


def reset() -> None:
    S.update(fm_for=sid, fm_mode="chat", fm_msgs=[], fm_state=tutor.new_state(), fm_profile=None, fm_saved=False)


if S.get("fm_for") != sid:
    reset()

st.title("💬 Let's get to know each other")

existing = storage.load_profile(sid)
if existing and not S.fm_msgs and not S.get("fm_restart"):
    st.success(f"We've already met, {existing.name}! Your profile is saved.")
    with st.expander("See my profile"):
        st.json(existing.model_dump())
    if st.button("Meet again and update my profile"):
        S.fm_restart = True
        st.rerun()
    ui.link("pages/3_Choose_and_Pretest.py", "Next: choose topics", "➡️")
    st.stop()

# A name we can offer: the saved profile's name, otherwise the name the student was created with on Home.
name_hint = existing.name if existing else (sid.replace("-", " ").title() if sid and sid != "student" else "")

if not S.fm_msgs:
    S.fm_msgs = [{"role": "assistant",
                  "content": opening_message(question_text("name", course_title, name_hint), course_title,
                                             existing.name if existing else "")}]

label = st.radio("How would you like to answer?", ["Chat with me", "Quick questions"],
                 index=0 if S.fm_mode == "chat" else 1, horizontal=True)
S.fm_mode = "chat" if label == "Chat with me" else "quick"

for m in S.fm_msgs:
    with st.chat_message(m["role"]):
        st.write(m["content"])


def try_build() -> None:
    p = ui.run(build_profile, sid, tutor.answers_list(S.fm_state), spinner="Writing down what you told me...")
    if p:
        S.fm_profile = p


finished = tutor.current_key(S.fm_state) is None
typed = st.chat_input("Type your answer, or ask me anything (type 'skip' for quick questions)") if not finished else None
if typed:
    history = list(S.fm_msgs)                       # everything said so far, without this message
    S.fm_msgs.append({"role": "user", "content": typed})
    result = ui.run(tutor.handle_turn, S.fm_state, typed, history, S.fm_mode, course_title, course_spans,
                    course_topics, name_hint)
    if result is None:                              # unexpected error: the student's answer was not stored
        S.fm_msgs.append({"role": "assistant", "content": "Sorry, I lost my train of thought. Could you say that again?"})
    else:
        if result.switch_to_quick:
            S.fm_mode = "quick"
        if result.reply:
            S.fm_msgs.append({"role": "assistant", "content": result.reply})
        if result.done:
            try_build()
    st.rerun()

if finished and not S.fm_profile:
    st.info("I have all your answers. One moment while I write your profile.")
    if st.button("Build my profile", type="primary"):
        try_build()
        st.rerun()

p = S.fm_profile
if p and not S.fm_saved:
    st.subheader("Is this right?")
    st.caption("You can change anything before saving.")
    levels = ["beginner", "intermediate", "advanced"]
    with st.form("profile_form"):
        name = st.text_input("Name", p.name)
        short = st.text_input("Short-term goal", p.short_term_goal)
        long_ = st.text_input("Long-term goal", p.long_term_goal)
        level = st.selectbox("Level", levels, index=levels.index(p.level))
        prior = st.text_area("What you already know", p.prior_knowledge)
        minutes = st.number_input("Minutes per day", 5, 240, int(p.daily_minutes))
        styles = st.text_input("How you like to learn (separate with commas)", ", ".join(p.learning_styles))
        interests = st.text_input("Interests (separate with commas)", ", ".join(p.interests))
        ok = st.form_submit_button("This looks right", type="primary")
    if ok:
        split = lambda t: [x.strip() for x in t.split(",") if x.strip()]
        new = StudentProfile(student_id=sid, name=name or "Student", short_term_goal=short, long_term_goal=long_,
                             level=level, prior_knowledge=prior, daily_minutes=int(minutes),
                             learning_styles=split(styles), interests=split(interests), notes=p.notes)
        storage.save_json(storage.student_dir(sid) / "profile.json", new)
        S.fm_saved, S.fm_profile = True, new
        st.rerun()

if S.fm_saved:
    st.success(f"Thank you, {S.fm_profile.name}! Next, we'll look at the topics and choose where to start.")
    ui.link("pages/3_Choose_and_Pretest.py", "Next: choose topics", "➡️")

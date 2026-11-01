"""Stage 6: today's target, evening check-in, streak and progress."""
import pandas as pd
import streamlit as st

import ui
from backend import progress, storage

ui.setup("Today", "📅")
ui.require(student=True, course=True, profile=True, path=True)
S = st.session_state
sid = S.student_id
course, _ = storage.load_course(S.course_id)
profile, path = storage.load_profile(sid), storage.load_path(sid)
titles = {t.id: t.title for t in course.topics}
prog = progress.load_progress(sid)

target = progress.current_target(prog)
if target is None:
    target = progress.first_target(profile, path, titles, prog)
    prog["targets"].append(target.model_dump())
    progress.save_progress(sid, prog)

day = str(target.day_index)
if day not in prog["morning"]:
    prog["morning"][day] = ui.run(progress.morning_message, profile, target) or f"Good morning, {profile.name}!"
    progress.save_progress(sid, prog)

st.title("📅 Today")
with st.container(border=True):
    st.subheader(f"Day {target.day_index}")
    st.write(prog["morning"][day])
    st.markdown(f"**Today's target:** {target.description}")
    ui.link("pages/4_Learn.py", "Open my lesson", "📚")


def answer(done: bool) -> None:
    progress.evening_flow(prog, profile, path, target, done, titles)
    progress.save_progress(sid, prog)
    S.checkin = False
    st.rerun()


if target.status == "pending":
    c1, c2, c3 = st.columns(3)
    if c1.button("I completed it ✅", type="primary"):
        answer(True)
    if c2.button("Not yet"):
        answer(False)
    if c3.button("Evening check-in"):
        S.checkin = True
    if S.get("checkin"):
        st.info(progress.evening_flow(prog, profile, path, target, None)["message"])
        d1, d2 = st.columns(2)
        if d1.button("Yes, I did"):
            answer(True)
        if d2.button("Not yet, tell me what to do"):
            answer(False)
else:
    st.success(prog["last_evening"]["message"] if prog.get("last_evening") else "Today is done.")
    st.caption("Simulated clock for testing: jump to tomorrow without waiting.")
    if st.button("Go to next day ▶"):
        progress.advance_day(prog)
        progress.save_progress(sid, prog)
        st.rerun()

st.divider()
done = len(set(prog["completed_topics"]) & {i.topic_id for i in path.items})
c1, c2, c3 = st.columns(3)
c1.metric("Streak (days)", prog["streak"])
c2.metric("Lessons done", f"{done} of {len(path.items)}")
c3.metric("Days answered", len(prog["history"]))
st.progress(done / max(len(path.items), 1), text="Your learning path")
if prog["history"]:
    st.caption("Minutes studied each day")
    st.line_chart(pd.DataFrame(prog["history"]).set_index("day")["minutes"])

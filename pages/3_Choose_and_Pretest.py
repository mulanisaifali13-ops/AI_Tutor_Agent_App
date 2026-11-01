"""Stage 4: choose topics, take an ungraded pre-test, get a personal learning path."""
import streamlit as st

import config
import ui
from backend import pretest, storage
from backend.schemas import PreTest, PreTestAnswer, PreTestSummary

ui.setup("Choose and pre-test", "🧭")
ui.require(student=True, course=True, profile=True)
S = st.session_state
sid, cid = S.student_id, S.course_id
course, spans = storage.load_course(cid)
profile = storage.load_profile(sid)
topics = course.topics
by_id = {t.id: t for t in topics}

if S.get("pt_for") != (sid, cid):
    for k in ("pretest", "placements", "pt_answers", "pt_summary", "choose_ids"):
        S.pop(k, None)
    S.pt_for = (sid, cid)

DONT_KNOW = "I don't know yet"
FRIENDLY = {"skip_with_check": "Quick check", "short_path": "Short lesson", "full_lesson": "Full lesson",
            "misconception_lesson": "Careful lesson", "bridge_first": "Basics first"}

st.title("🧭 Choose what to learn")

# ---------- Step 3: result ----------
STATUS = {"strength": "Strength", "developing": "Getting there", "weakness": "Needs work",
          "not_understood": "Not understood yet", "not_assessed": "Not assessed"}
REASON = {"dont_know": "You said you don't know yet", "incorrect": "Not quite right", "unanswered": "Left blank",
          "correct_unsure": "Right, but you were unsure"}


def show_summary(sm: PreTestSummary) -> None:
    st.subheader("Your quiz results")
    st.caption("This quiz is not a pass or fail test. It only shows me what you know today, so I can teach what you need.")
    if sm.total == 0:
        st.warning("There were no valid questions to score, so there is no score. I'll teach every topic from the start.")
        return
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Correct", sm.correct)
    c2.metric("Incorrect", sm.incorrect, help="Wrong answers and \"I don't know yet\"")
    c3.metric("Unanswered", sm.unanswered, help="Left blank. These are never counted as correct.")
    c4.metric("Total questions", sm.total)
    c5.metric("Accuracy", f"{sm.accuracy:.1f}%")
    st.caption(f"Accuracy = correct ({sm.correct}) ÷ total questions ({sm.total}) × 100. "
               f"Incorrect includes {sm.dont_know} \"I don't know yet\" answer{'s' if sm.dont_know != 1 else ''}.")
    st.info(f"**{sm.band_label}.** {sm.guidance}")
    cf = sm.confidence
    st.markdown("**How sure you were**")
    st.dataframe([{"Kind of answer": k, "Count": v} for k, v in [
        ("Correct and confident", cf.get("correct_confident", 0)), ("Correct but unsure", cf.get("correct_uncertain", 0)),
        ("Incorrect and confident", cf.get("incorrect_confident", 0)), ("Incorrect and unsure", cf.get("incorrect_uncertain", 0)),
        ("No confidence given", cf.get("unrated", 0))]], hide_index=True, width="stretch")
    st.markdown("**Topic by topic**")
    st.dataframe([{"Topic": t.title + (" (basics check)" if t.is_basics_check else ""), "Result": STATUS[t.status],
                   "Correct": f"{t.correct} of {t.total}", "Accuracy": "-" if t.accuracy is None else f"{t.accuracy:.1f}%",
                   "Note": t.note} for t in sm.topics], hide_index=True, width="stretch")
    if sm.concepts:
        with st.expander("What we'll go over together"):
            for c in sm.concepts:
                st.markdown(f"- **{c.topic_title}**: {c.text} _({REASON[c.reason]})_")


if S.get("placements"):
    if S.get("pt_summary"):
        show_summary(S.pt_summary)
    if S.get("pretest"):
        with st.expander("Review the questions and answers"):
            outcome = pretest.grade(S.pretest, S.pt_answers)
            for n, q in enumerate(S.pretest.questions, 1):
                st.markdown(f"**{n}. {q.text}**")
                st.write(f"Right answer: {q.options[q.correct_index]}. {q.option_explanations[q.correct_index]}")
                st.caption({"correct": "You got this one.", "incorrect": "You answered differently.",
                            "dont_know": "You said you don't know yet.", "unanswered": "You left this blank."}.get(outcome.get(q.id), ""))
    st.subheader("Your plan")
    adjusted = {}
    for tid, p in S.placements.items():
        if p.state == "skip_with_check" and st.checkbox(
                f"You already seem to know '{by_id[tid].title}'. Tick to take the full lesson anyway.", key=f"add_{tid}"):
            p = p.model_copy(update={"state": "full_lesson", "reason": "You asked for the full lesson."})
        adjusted[tid] = p
    path = pretest.build_path(profile, course, adjusted)
    total = sum(i.minutes for i in path.items)
    st.success(f"Your plan: {len(path.items)} lessons, about {total} minutes in total.")
    st.dataframe([{"Order": i.order, "Topic": by_id[i.topic_id].title, "Type": FRIENDLY[i.state],
                   "Minutes": i.minutes, "Why": i.reason} for i in path.items], hide_index=True, width="stretch")
    if st.button("Save my plan", type="primary"):
        storage.save_json(storage.student_dir(sid) / "path.json", path)
        storage.save_pretest_result(sid, cid, S.pt_summary, adjusted, S.pt_answers)
        st.success("Your plan is saved.")
        ui.link("pages/4_Learn.py", "Start learning", "📚")
    if st.button("Start over"):
        for k in ("pretest", "placements", "pt_answers", "pt_summary"):
            S.pop(k, None)
        st.rerun()
    st.stop()

# ---------- Step 2: pre-test ----------
if S.get("pretest"):
    pt: PreTest = S.pretest
    st.info("This short quiz is not a pass or fail test and nothing is marked against you. It shows me what you already "
            "know, so I can teach what you need. If you are not sure, choose \"I don't know yet\". That helps me too.")
    st.caption(f"{len(pt.questions)} question{'s' if len(pt.questions) != 1 else ''}, taken from your material. "
               + ("Your material for these topics is short, so the quiz is short too. " if len(pt.questions) < config.PRETEST_MIN_TARGET else "")
               + "Rating how sure you are is optional. A question you leave blank counts as not answered, never as correct.")
    with st.form("pretest_form"):
        for n, q in enumerate(pt.questions, 1):
            st.markdown(f"**{n}. {q.text}**")
            st.radio("Answer", q.options + [DONT_KNOW], index=None, key=f"pt_{pt.pretest_id}_{q.id}", label_visibility="collapsed")
            st.radio("How sure are you? (optional)", ["sure", "unsure", "guessing"], index=None, horizontal=True,
                     key=f"ptc_{pt.pretest_id}_{q.id}")
            st.divider()
        submitted = st.form_submit_button("Submit", type="primary")
    if submitted:
        answers = []
        for q in pt.questions:
            val = S.get(f"pt_{pt.pretest_id}_{q.id}")
            answers.append(PreTestAnswer(question_id=q.id, chosen_index=q.options.index(val) if val in q.options else None,
                                         dont_know=(val == DONT_KNOW), confidence=S.get(f"ptc_{pt.pretest_id}_{q.id}")))
        S.pt_answers = answers
        S.placements = pretest.score_pretest(pt, answers, topics)
        S.pt_summary = pretest.summarize_pretest(sid, pt, answers, topics)
        storage.save_pretest_result(sid, cid, S.pt_summary, S.placements, answers)
        st.rerun()
    if st.button("Back to topics"):
        S.pretest = None
        st.rerun()
    st.stop()

# ---------- Step 1: choose ----------
st.write("What would you like to learn first? The choice is always yours.")
for t in topics:
    with st.container(border=True):
        st.markdown(f"**{t.title}** · _{t.difficulty}_")
        st.write(t.summary)


def _suggest() -> None:
    S.choose_ids = pretest.suggest_topics(profile, topics)


st.button("Not sure, suggest for me", on_click=_suggest)
picked = st.multiselect("Your topics", [t.id for t in topics], key="choose_ids", format_func=lambda i: by_id[i].title)
chosen = [t.id for t in topics if t.id in picked]
for note in pretest.advise_order(chosen, topics):
    st.info(note)
if st.button("Start the short quiz", type="primary", disabled=not chosen):
    pt = ui.run(pretest.generate_pretest, course, spans, chosen, spinner="Preparing and double-checking your quiz...")
    if pt:
        S.pretest = pt
        storage.save_pretest(sid, cid, pt)
        st.rerun()

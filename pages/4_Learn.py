"""Stage 5 (+ core of Stage 7): the lesson viewer, quiz, and gentle support after a 'not yet'."""
import streamlit as st

import config
import ui
from backend import lessons, progress, review, storage, support, tutor
from backend.schemas import Attempt

ui.setup("Learn", "📚")
ui.require(student=True, course=True, profile=True, path=True)
S = st.session_state
sid, cid = S.student_id, S.course_id
course, spans = storage.load_course(cid)
profile, path = storage.load_profile(sid), storage.load_path(sid)
by_id = {t.id: t for t in course.topics}
prog = progress.load_progress(sid)
STEPS = ["Hook", "Goals", "Warm-up", "Explanation", "Example", "Practice", "Quiz", "Summary"]


def reset_lesson() -> None:
    ui.reset_keys("qf_", "qs_", "rec_", "passed_", "qrad_")
    S.lesson_step, S.support_view = 0, None


def go(n: int) -> None:
    S.lesson_step = n


def finish_lesson() -> None:
    ctx = S.lesson_ctx
    S.lesson_ctx = ctx.get("return_to")
    reset_lesson()


def set_aside(topic_id: str) -> None:
    p = progress.load_progress(sid)
    if topic_id not in p["set_aside"]:
        p["set_aside"].append(topic_id)
    progress.save_progress(sid, p)
    S.lesson_ctx = None
    reset_lesson()


def start_bridge(topic_id: str, bridge_id: str, variant: str) -> None:
    p = progress.load_progress(sid)
    if topic_id not in p["bridged"]:
        p["bridged"].append(topic_id)
    progress.save_progress(sid, p)
    S.lesson_ctx = {"sid": sid, "topic_id": bridge_id, "variant": "bridge",
                    "return_to": {"sid": sid, "topic_id": topic_id, "variant": variant, "return_to": None}}
    reset_lesson()


# ---- which lesson are we on? ----
ctx = S.get("lesson_ctx")
if not ctx or ctx.get("sid") != sid:
    item = progress.next_item(path, prog)
    if item is None:
        st.balloons()
        st.success("You've finished your whole plan. Wonderful work!")
        ui.link("pages/5_Today.py", "See today's page", "📅")
        st.stop()
    ctx = {"sid": sid, "topic_id": item.topic_id, "variant": lessons.VARIANT_FOR_STATE[item.state], "return_to": None}
    S.lesson_ctx = ctx
    reset_lesson()
topic, variant = by_id[ctx["topic_id"]], ctx["variant"]
base = f"{topic.id}_{variant}"

st.title(f"📚 {topic.title}")
cache_key = f"lesson_{cid}_{base}"
if cache_key not in S:
    got = ui.run(lessons.get_or_create_lesson, cid, topic, spans, profile, variant, spinner="Preparing your lesson...")
    if got is None:
        st.button("Try again")
        st.stop()
    S[cache_key] = got
lesson, rev = S[cache_key]

if rev.decision == "person_review" and not review.is_approved(cid, topic.id, variant):
    st.info("This lesson is being checked by your teacher before you see it.")
    with st.expander("Teacher area (demo)"):
        st.write("Problems found by the automatic checks:")
        for p in rev.structural_problems:
            st.write(f"- {p}")
        if st.button("I'm the teacher: approve this lesson"):
            review.approve(cid, topic.id, variant)
            st.rerun()
    if st.button("Set this topic aside for now"):
        set_aside(topic.id)
        st.rerun()
    st.stop()

steps = [s for s in STEPS if not (s == "Hook" and not lesson.hook.strip()) and not (s == "Warm-up" and not lesson.warmup)]
step = min(S.get("lesson_step", 0), len(steps) - 1)
name = steps[step]
st.progress((step + 1) / len(steps), text=f"Step {step + 1} of {len(steps)}: {name}")


def nav(show_next: bool = True, next_label: str = "Next") -> None:
    c1, c2 = st.columns(2)
    if step > 0:
        c1.button("Back", on_click=go, args=(step - 1,))
    if show_next and step < len(steps) - 1:
        c2.button(next_label, type="primary", on_click=go, args=(step + 1,))


def flow_step(key: str, qs) -> None:
    res = ui.question_flow(f"{base}_{key}", qs)
    if res is not None:
        st.success(f"Well done! You got {res['correct']} of {res['total']}.")
        nav()
    else:
        nav(show_next=False)


def show_support(view: dict) -> None:
    st.info(view["message"])
    if view["use_extra_example"]:
        with st.expander("Another example", expanded=True):
            st.markdown(lesson.extra_worked_example)
    kind = view["kind"]
    if kind in ("hint_retry", "switch_route"):
        c1, c2 = st.columns(2)
        c1.button("Review the explanation", on_click=lambda: (go(steps.index("Explanation")), S.update(support_view=None)))
        c2.button("Try fresh questions", type="primary", on_click=lambda: S.update(support_view=None))
    else:
        c1, c2 = st.columns(2)
        if kind == "bridge_and_alert":
            bridge_id = view.get("bridge_topic_id") or topic.id
            c1.button("Try a gentler bridge lesson", type="primary", on_click=start_bridge, args=(topic.id, bridge_id, variant))
        else:
            c1.info("Your teacher has been told and will help you.")
        c2.button("Come back to this later", on_click=set_aside, args=(topic.id,))
        st.caption("Your teacher has been notified, so you are not alone with this.")


if name == "Hook":
    st.markdown(f"### {lesson.hook}")
    nav()
elif name == "Goals":
    st.markdown("**By the end you will be able to:**")
    for o in lesson.objectives:
        st.markdown(f"- {o}")
    nav()
elif name == "Warm-up":
    st.write("A few quick questions about what you may already know. No pressure!")
    flow_step("wu", lesson.warmup)
elif name == "Explanation":
    for seg in lesson.explanation:
        with st.container(border=True):
            st.markdown(seg)
    nav()
elif name == "Example":
    st.markdown(lesson.worked_example)
    nav()
elif name == "Practice":
    st.write("Let's practise. Use the hint if you like.")
    flow_step("pr", lesson.practice)
elif name == "Quiz":
    attempts = support.load_attempts(sid)
    fails, recent = support.failed_count(attempts, topic.id), support.recent_attempts(attempts, topic.id)
    if S.get(f"passed_{base}"):
        st.success("You did it! That idea is yours now.")
        nav()
    elif S.get("support_view"):
        show_support(S.support_view)
    else:
        qkey = f"{base}_quiz{fails}"
        if f"qs_{qkey}" not in S:
            S[f"qs_{qkey}"] = support.pick_quiz(lesson, recent)
        qs = S[f"qs_{qkey}"]
        st.write("Time to show what you've learned. Take your time.")
        res = ui.question_flow(qkey, qs)
        if res is not None and not S.get(f"rec_{qkey}"):
            S[f"rec_{qkey}"] = True
            passed = res["correct"] / max(res["total"], 1) >= config.QUIZ_PASS_RATIO
            att = Attempt(topic_id=topic.id, attempt_no=fails + 1, passed=passed, wrong_question_ids=res["wrong_ids"],
                          chosen_wrong_options=res["chosen_wrong"], seconds_taken=res["seconds"], asked_question_ids=res["asked"])
            support.save_attempt(sid, att)
            p = progress.load_progress(sid)
            if passed:
                p["quiz_scores"][topic.id] = round(100 * res["correct"] / res["total"])
                bridging_same = variant == "bridge" and ctx.get("return_to") and ctx["return_to"]["topic_id"] == topic.id
                if not bridging_same and topic.id not in p["completed_topics"]:
                    p["completed_topics"].append(topic.id)
                progress.save_progress(sid, p)
                S[f"passed_{base}"] = True
            else:
                action = support.plan_for_attempt(topic, recent + [att], res["total"], topic.id in p["bridged"])
                if action.alert:
                    support.create_alert(sid, topic, recent + [att], action.cause,
                                         ["hint and fresh questions", "extra worked example", "bridge lesson"][:len(recent) + 1],
                                         p["day_index"])
                S.support_view = action.model_dump()
            st.rerun()
else:  # Summary
    st.markdown(lesson.summary)
    st.subheader("One more: a new situation")
    res = ui.question_flow(f"{base}_tr", [lesson.transfer_question])
    if res is not None:
        st.success("Lovely work finishing this lesson!")
        st.button("Finish lesson", type="primary", on_click=finish_lesson)
    nav(show_next=False)


# ---- Ask your teacher: answers come only from the uploaded document. Hidden during the quiz. ----
if name != "Quiz":
    hist = S.setdefault(f"ask_{sid}_{topic.id}", [])
    with st.expander("Ask your teacher a question about this topic", expanded=bool(hist)):
        for m in hist[-6:]:
            with st.chat_message(m["role"]):
                st.write(m["content"])
        with st.form(f"askform_{topic.id}", clear_on_submit=True):
            asked = st.text_input("Your question")
            sent = st.form_submit_button("Ask")
        if sent and asked.strip():
            reply = ui.run(tutor.answer_subject, asked.strip(), spans, course.course_title, profile.level, hist,
                           topic.source_spans)
            if reply:
                hist.append({"role": "user", "content": asked.strip()})
                hist.append({"role": "assistant", "content": reply})
                st.rerun()

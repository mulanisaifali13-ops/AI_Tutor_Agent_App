"""Frontend helpers shared by all pages. This is the only layer (with app.py and pages/) that imports streamlit."""
import os
import time
from typing import Callable, List, Optional

import streamlit as st

import theme
from backend import llm, progress, storage, usage
from backend.documents import DocumentError
from backend.schemas import Question


def load_secrets() -> None:
    """Copy the key from Streamlit secrets into the environment. The key is never shown or logged."""
    if os.environ.get("GROQ_API_KEY"):
        return
    try:
        key = st.secrets.get("GROQ_API_KEY")
    except Exception:
        key = None
    if key:
        os.environ["GROQ_API_KEY"] = str(key)


def _render_usage(slot) -> None:
    """Draw the running Groq totals into the sidebar slot."""
    sm = usage.snapshot()
    with slot.container():
        st.caption("AI usage (actual counts returned by Groq)")
        a, b = st.columns(2)
        a.metric("Input tokens", f"{sm['input_tokens']:,}")
        b.metric("Output tokens", f"{sm['output_tokens']:,}")
        st.caption(f"{sm['chars']:,} characters, {sm['calls']} answers, {sm['seconds']:.0f} s of model time"
                   + (f", {sm['cache_hits']} reused" if sm.get("cache_hits") else ""))
        if sm.get("est_input_tokens"):
            st.caption(f"Estimated before sending: {sm['est_input_tokens']:,} input tokens "
                       f"(limit set for output: {sm['output_budget']:,})")


def refresh_usage() -> None:
    """Redraw the sidebar totals (called after every backend call, so they move as the app works)."""
    slot = st.session_state.get("_usage_slot")
    if slot is not None:
        try:
            _render_usage(slot)
        except Exception:
            pass


def _student_card() -> None:
    """Who is learning, and how far along the plan is. Reads saved files only (no model calls)."""
    S = st.session_state
    sid = S.get("student_id")
    if not sid:
        st.markdown('<div class="who"><div class="avatar">?</div><div><div class="who-name">No student yet</div>'
                    '<div class="who-sub">Pick or create one on Home</div></div></div>', unsafe_allow_html=True)
        return
    prof = storage.load_profile(sid)
    name = prof.name if prof else sid.replace("-", " ").title()
    cid = S.get("course_id")
    st.markdown(f'<div class="who"><div class="avatar">{theme.esc(theme.initials(name))}</div><div>'
                f'<div class="who-name">{theme.esc(name)}</div>'
                f'<div class="who-sub">{theme.esc(cid) if cid else "No course chosen"}</div></div></div>', unsafe_allow_html=True)
    path = storage.load_path(sid)
    if not path:
        return
    prog = progress.load_progress(sid)
    total = len(path.items)
    done = len(set(prog["completed_topics"]) & {i.topic_id for i in path.items})
    st.markdown(theme.bar("Plan progress", f"{done} of {total} lessons", 100 * done / max(total, 1)), unsafe_allow_html=True)
    target = progress.current_target(prog)
    if target is not None:
        st.markdown(f'<div class="target"><b>Day {target.day_index} target</b><span>{theme.esc(target.description)}</span></div>',
                    unsafe_allow_html=True)


def setup(title: str = "Your Personal Teacher", icon: str = "🎓") -> None:
    st.set_page_config(page_title=title, page_icon=icon, layout="wide", initial_sidebar_state="expanded")
    load_secrets()
    theme.apply()
    st.session_state.setdefault("student_id", None)
    st.session_state.setdefault("course_id", None)
    with st.sidebar:
        _student_card()
        if llm.has_key():
            st.markdown('<div class="status"><span class="dot"></span>Groq connected</div>', unsafe_allow_html=True)
            if "model_routes" not in st.session_state:
                try:
                    st.session_state.model_routes = llm.routes_summary()
                    st.session_state.model_name = st.session_state.model_routes.get("lesson", "")
                except llm.LLMError as e:
                    st.warning(str(e))
            if "model_routes" in st.session_state:
                with st.expander("Models by job"):
                    for role, m in st.session_state.model_routes.items():
                        st.caption(f"{role}: {m}")
        else:
            st.markdown('<div class="status"><span class="dot off"></span>Groq key needed</div>', unsafe_allow_html=True)
            typed = st.text_input("Groq API key", type="password", placeholder="gsk_...",
                                  help="Kept only for this session. You can also add GROQ_API_KEY as a secret.")
            if typed.strip():
                os.environ["GROQ_API_KEY"] = typed.strip()
                st.rerun()
        with st.expander("AI usage"):
            slot = st.empty()
            st.session_state["_usage_slot"] = slot
            _render_usage(slot)


def link(page: str, label: str, icon: str = "➡️") -> None:
    """A page link that never crashes the page (falls back to a plain hint)."""
    try:
        st.page_link(page, label=label, icon=icon)
    except Exception:
        st.write(f"{icon} Open **{label}** from the sidebar.")


def _stop(message: str, page: str, label: str) -> None:
    st.info(message)
    link(page, label)
    st.stop()


def require(student: bool = True, course: bool = False, profile: bool = False, path: bool = False) -> None:
    """Every page starts here: friendly message plus a link if something is missing."""
    s = st.session_state
    if student and not s.get("student_id"):
        _stop("Please choose or create a student first.", "app.py", "Go to Home")
    if course and (not s.get("course_id") or not storage.load_course(s.course_id)):
        _stop("Please upload a document (or choose a course on Home) first.", "pages/1_Upload_Document.py", "Upload a document")
    if profile and not storage.load_profile(s.student_id):
        _stop("I haven't met you yet. Let's have our first meeting!", "pages/2_First_Meeting.py", "First meeting")
    saved_path = storage.load_path(s.student_id) if path else None
    if path and (saved_path is None or (s.get("course_id") and saved_path.course_id != s.course_id)):   # a plan for another course is not this course's plan
        _stop("Let's choose your topics and make your plan first.", "pages/3_Choose_and_Pretest.py", "Choose topics")


def run(fn: Callable, *args, spinner: str = "The teacher is thinking, please wait...", **kwargs):
    """Call backend code with a spinner and a friendly error. Returns None on failure."""
    try:
        with st.spinner(spinner):
            return fn(*args, **kwargs)
    except (llm.LLMError, DocumentError) as e:
        st.error(str(e))
    except Exception as e:
        st.error(f"Something unexpected happened ({type(e).__name__}). Please try again.")
    finally:
        refresh_usage()
    return None


def reset_keys(*prefixes: str) -> None:
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith(prefixes):
            del st.session_state[k]


def question_flow(key: str, questions: List[Question]) -> Optional[dict]:
    """Show questions one at a time with hint and immediate feedback.
    Returns None while in progress, then a result dict when every question has been answered."""
    s = st.session_state.setdefault(f"qf_{key}", {"i": 0, "checked": False, "hint": False, "results": {}, "start": time.time()})
    n = len(questions)
    if s["i"] >= n:
        wrong = [q for q in questions if s["results"].get(q.id) != q.correct_index]
        return {"correct": n - len(wrong), "total": n, "wrong_ids": [q.id for q in wrong],
                "chosen_wrong": {q.id: s["results"][q.id] for q in wrong if s["results"].get(q.id) is not None},
                "seconds": int(time.time() - s["start"]), "asked": [q.id for q in questions]}
    q = questions[s["i"]]
    st.progress(s["i"] / n, text=f"Question {s['i'] + 1} of {n}")
    st.markdown(f"**{q.text}**")
    choice = st.radio("Choose one", q.options, index=None, key=f"qrad_{key}_{q.id}", disabled=s["checked"],
                      label_visibility="collapsed")
    if not s["checked"]:
        if q.hint and st.button("Show hint", key=f"hint_{key}_{q.id}"):
            s["hint"] = True
        if s["hint"] and q.hint:
            st.info(q.hint)
        if st.button("Check my answer", key=f"check_{key}_{q.id}", disabled=choice is None, type="primary"):
            s["results"][q.id] = q.options.index(choice)
            s["checked"] = True
            st.rerun()
    else:
        picked = s["results"][q.id]
        if picked == q.correct_index:
            st.success(f"Yes! {q.option_explanations[picked]}")
        else:
            st.warning(f"Not yet. {q.option_explanations[picked]}")
            st.markdown(f"The best answer is **{q.options[q.correct_index]}**. {q.option_explanations[q.correct_index]}")
        if st.button("Next", key=f"next_{key}_{q.id}", type="primary"):
            s["i"] += 1
            s["checked"], s["hint"] = False, False
            st.rerun()
    return None

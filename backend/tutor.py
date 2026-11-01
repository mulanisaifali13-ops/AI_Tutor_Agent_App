"""The teacher's conversation: understands what the student's message is (an answer, a request to explain the
question, a subject question, a lesson or quiz request, small talk) and replies to THAT before moving on.
No Streamlit here. State is a plain dict so the page can keep it in st.session_state.

Rules the code (not the model) enforces:
- the next onboarding question is only asked after a real answer; clarification, subject questions and small talk
  never advance it
- a question the student already answered (or volunteered earlier) is never asked again
- one vague answer gets at most one follow-up per question, then we accept it and move on (no loops)
- subject questions are answered only from the uploaded document's passages
"""
import difflib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel

from backend import llm, retrieval
from backend.profile import QUESTIONS, question_text
from backend.schemas import Span

ORDER = [q["key"] for q in QUESTIONS]

CONVERSATION_SYSTEM = (
    "You are a warm, patient teacher meeting a new student. Reply in the language the student writes in. "
    "Use simple words. Understand short, informal and misspelled messages. Never scold. "
    "Never invent facts about the student or about any document."
)
SUBJECT_SYSTEM = (
    "You are a warm, patient teacher answering a student's question about their learning material. "
    "Use ONLY the numbered passages you are given. Do not add facts that are not in them. "
    "If the passages do not answer the question, reply with exactly the single word NOT_COVERED and nothing else. "
    "Keep the answer short and clear."
)

NO_DOCUMENT = "I don't have a document to look in yet. Please upload one first, and then I can answer questions about it."
NOT_IN_MATERIAL = ("I looked through your material and I can't find that in it, so I don't want to guess. "
                   "Could you ask it a different way, or tell me which topic it belongs to?")

# deterministic explanations, used in quick mode and whenever the model is unavailable
CLARIFY = {
    "name": "I just mean the name you would like me to use when I talk to you. A nickname is fine.",
    "short_term_goal": "I mean something you would like to be able to do in the next few weeks. "
                       "For example, understand a topic well or get ready for a test. It can be small.",
    "long_term_goal": "I mean the bigger reason behind learning this, further in the future. "
                      "For example, a job, a course, or a skill you want to have.",
    "level": "I mean how much you already know about {subject}. Choose beginner if it is mostly new to you, "
             "some knowledge if you know a bit, or advanced if you know it well.",
    "prior_knowledge": "I mean anything you already know about {subject}, even a little or just words you have heard. "
                       "If it is all new to you, just say so, that is perfectly fine.",
    "daily_minutes": "I mean how much time you can give to studying on a normal day. A rough number of minutes is enough.",
    "learning_styles": "I mean what helps you understand best. For example: real-life examples, stories, "
                       "pictures, or practice questions.",
    "interests": "I mean things you enjoy outside of study, like sports, music or games. "
                 "I can use them to make examples more fun for you.",
}

_CLARIFY_PATTERNS = [
    r"\bwhat do(es)? (you|that|this|it) mean\b", r"\bwhat (is|does) (that|this|it) mean\b", r"\bwhat you mean\b",
    r"\bwhat'?s that mean\b", r"\bwhat( do)? you (ask|asking)\b", r"\b(i )?(do not|don'?t|dont) (understand|get (it|that|this|the question))\b",
    r"\bnot (sure )?(what|which) you mean\b", r"\bmeaning of (that|this|the question)\b", r"\bwhich (thing|topic|subject|one|course)\b",
    r"\bfor which\b", r"\babout (what|which)\b", r"\bwhat (topic|subject|thing|course)\b", r"\bwhat about\b\s*\??$",
    r"\b(can|could) you (explain|repeat|rephrase)\b", r"\bexplain (the |this |that )?question\b", r"\bsay (that )?again\b",
    r"\bconfused\b",
]
_CLARIFY_SHORT = {"what", "what?", "huh", "huh?", "eh", "eh?", "sorry?", "pardon", "pardon?", "hm?", "hmm?", "which?", "why?", "meaning?"}
_QUICK_WORDS = {"skip", "quick", "quick questions", "quick mode"}
_LESSON_RE = re.compile(r"\b(start|begin) (the |a |my |our )?(lesson|class|learning)\b|\blet'?s (start|begin)\b|\bteach me now\b|\bgive me (a |the )?lesson\b", re.I)
_QUIZ_RE = re.compile(r"\bquiz me\b|\btest me\b|\bgive me (a |the )?(quiz|test)\b|\b(start|take|do|begin) (a |the |my )?(quiz|pre-?test)\b|\bcan (we|i) (do|have|take) (a |the )?(quiz|test)\b", re.I)
_YES = {"yes", "yeah", "yep", "yup", "ok", "okay", "sure", "fine", "yes please", "that's fine", "thats fine", "that is fine", "y"}


@dataclass
class TurnResult:
    reply: str
    intent: str                     # answer | unclear_answer | clarify | subject_question | lesson_request | quiz_request | smalltalk | mode_switch
    advanced: bool = False          # True only when an onboarding answer was stored
    done: bool = False              # every question has an answer
    switch_to_quick: bool = False


class TurnAnalysis(BaseModel):
    intent: str = "answer"
    answer: str = ""
    extra: Dict[str, Any] = {}
    reply: str = ""


# ---------- state ----------

def new_state() -> dict:
    return {"answers": {}, "followups": {}}


def current_key(state: dict) -> Optional[str]:
    return next((k for k in ORDER if k not in state["answers"]), None)


def answers_list(state: dict) -> List[tuple]:
    """Answers in question order, in the shape build_profile expects."""
    return [(k, state["answers"][k]) for k in ORDER if k in state["answers"]]


# ---------- understanding the message (rules) ----------

def looks_like_clarification(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if t in _CLARIFY_SHORT:
        return True
    if len(t.split()) > 14:                      # long messages are real answers or questions
        return False
    return any(re.search(p, t) for p in _CLARIFY_PATTERNS)


def heuristic_intent(text: str) -> str:
    if looks_like_clarification(text):
        return "clarify"
    short = len((text or "").split()) <= 10          # real requests are short; longer text is probably an answer
    if short and _QUIZ_RE.search(text or ""):
        return "quiz_request"
    if short and _LESSON_RE.search(text or ""):
        return "lesson_request"
    if (text or "").strip().endswith("?") and len(text.split()) >= 4:
        return "subject_question"
    return "answer"


def _clean_name(text: str, hint: str = "") -> str:
    t = text.strip()
    if t.lower().strip(" .!") in _YES and hint:
        return hint
    t = re.sub(r"^(?:hi|hello|hey)[,!\s]+", "", t, flags=re.I)
    t = re.sub(r"^(?:my name is|my name's|i am|i'm|im|it's|its|call me|you can call me|this is|name is)\s+", "", t, flags=re.I)
    t = t.strip(" .!,")
    return t[:1].upper() + t[1:] if t and t.islower() else t


def _fallback_value(key: str, text: str, hint: str = "") -> str:
    return _clean_name(text, hint) if key == "name" else text.strip()


def _strip_questions(text: str) -> str:
    """Drop question sentences from an acknowledgement so we never ask two questions at once."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return " ".join(p for p in parts if p and not p.endswith("?")).strip()


# ---------- understanding the message (model) ----------

def _transcript(history: Sequence[dict], n: int = 8) -> str:
    lines = [f"{'Teacher' if m['role'] == 'assistant' else 'Student'}: {m['content']}" for m in history[-n:]]
    return "\n".join(lines) or "(no messages yet)"


def analyze(state: dict, text: str, history: Sequence[dict], course_title: str, topic_titles: Sequence[str]) -> TurnAnalysis:
    key = current_key(state)
    remaining = [k for k in ORDER if k not in state["answers"] and k != key]
    meaning = "\n".join(f"- {q['key']}: {q['text']}" for q in QUESTIONS if q["key"] in remaining)
    known = ", ".join(f"{k}={v!r}" for k, v in answers_list(state)) or "nothing yet"
    prompt = (
        "You are the teacher in a first meeting. Read the student's latest message and decide what it is.\n"
        f"Course: {course_title or 'not chosen yet'}. Topics: {', '.join(topic_titles) or 'unknown'}.\n"
        f"What the student has already told me: {known}\n"
        f"The question I am waiting for an answer to ({key}): \"{question_text(key, course_title)}\"\n\n"
        f"Recent conversation:\n{_transcript(history)}\n\n"
        f"Latest student message: \"{text}\"\n\n"
        "Choose \"intent\":\n"
        "- answer: they answered my question, even briefly, informally, with typos, or with 'I don't know' / 'nothing'\n"
        "- unclear_answer: so vague it would not help me teach (for example 'something important'). Use this rarely.\n"
        "- clarify: they do not understand my question and ask what it means, which topic, or which thing\n"
        "- subject_question: they ask a question about the subject matter itself\n"
        "- lesson_request: they ask to start learning or being taught now\n"
        "- quiz_request: they ask for a quiz or a test\n"
        "- smalltalk: greeting, thanks, or other chat\n\n"
        "Fields:\n"
        "\"intent\": one of the words above.\n"
        "\"answer\": only when intent is answer: a clean short version of their answer to MY question "
        "(fix spelling, drop filler such as 'my name is'). Otherwise \"\".\n"
        f"\"extra\": an object with any facts they mentioned that answer my LATER questions, using only these keys:\n{meaning or '(none)'}\n"
        "Use {} if there are none. Never guess.\n"
        "\"reply\": one to three short sentences, in simple words, no markdown:\n"
        "  answer -> acknowledge what they said specifically (use their name if known). Do NOT ask any question.\n"
        "  unclear_answer -> acknowledge kindly, then ask ONE short, concrete follow-up question tied to the course.\n"
        "  clarify -> explain in simple words what my question means, with a small example about the course, "
        "then invite them to answer it. Do not move on to a different question.\n"
        "  smalltalk -> a short friendly reply. Do NOT ask any question.\n"
        "  other intents -> \"\".\n"
        "Never invent facts about the course or the student."
    )
    return llm.ask_json(prompt, TurnAnalysis, system=CONVERSATION_SYSTEM, tries=2, max_tokens=500, role="chat")


def _norm_intent(raw: str) -> str:
    raw = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    ok = {"answer", "unclear_answer", "clarify", "subject_question", "lesson_request", "quiz_request", "smalltalk"}
    if raw in ok:
        return raw
    close = difflib.get_close_matches(raw, list(ok), n=1, cutoff=0.7)
    return close[0] if close else "answer"


# ---------- subject questions (answered from the document only) ----------

def answer_subject(question: str, spans: Sequence[Span], course_title: str = "", level: str = "",
                   history: Sequence[dict] = (), prefer_ids: Optional[Sequence[str]] = None) -> str:
    if not spans:
        return NO_DOCUMENT
    query = question
    if len(retrieval.tokens(question)) < 3:               # follow-ups like "and why?" borrow the previous question
        prev = [m["content"] for m in history if m["role"] == "user"]
        if prev:
            query = f"{prev[-1]} {question}"
    hits = retrieval.retrieve(query, spans, k=3, prefer_ids=prefer_ids)
    if not hits:
        return NOT_IN_MATERIAL
    passages = "\n\n".join(f"[Passage {s.id}, page {s.page}]\n{s.text}" for s in hits)
    who = f"The student's level is {level}. Explain at that level. " if level else ""
    prompt = (f"{who}Course: {course_title or 'the uploaded document'}.\n\nPASSAGES:\n{passages}\n\n"
              f"Recent conversation:\n{_transcript(history, 4)}\n\nStudent's question: {question}\n\n"
              "Answer using only the passages. If they do not answer it, reply with exactly NOT_COVERED.")
    # the prompt holds the question, the passages, the level and the recent chat, so an identical prompt is safe to reuse
    reply = llm.ask(prompt, system=SUBJECT_SYSTEM, temperature=0.3, max_tokens=700, role="chat", cache=True)
    if not reply or "NOT_COVERED" in reply:
        return NOT_IN_MATERIAL
    pages = sorted({s.page for s in hits})
    return f"{reply}\n\n*From your material: page {', '.join(str(p) for p in pages)}*"


# ---------- one turn of the first meeting ----------

def _ask_again(state: dict, course_title: str, hint: str) -> str:
    return question_text(current_key(state), course_title, hint)


def handle_turn(state: dict, text: str, history: Sequence[dict], mode: str = "chat", course_title: str = "",
                spans: Sequence[Span] = (), topic_titles: Sequence[str] = (), name_hint: str = "") -> TurnResult:
    """history = the messages shown so far, NOT including this student message. Changes `state` only for real answers."""
    text = (text or "").strip()
    key = current_key(state)
    if key is None:
        return TurnResult("I already have everything I need from you.", "smalltalk", done=True)
    if text.lower().strip(" .!") in _QUICK_WORDS:
        return TurnResult(f"No problem, quick questions it is. {_ask_again(state, course_title, name_hint)}",
                          "mode_switch", switch_to_quick=True)

    name = state["answers"].get("name", "")
    chat = mode == "chat"
    intent, analysis = heuristic_intent(text), None
    if intent != "clarify" and (chat or "?" in text):     # quick mode only asks the model when something looks like a question
        try:
            analysis = analyze(state, text, history, course_title, topic_titles)
            intent = _norm_intent(analysis.intent)
        except llm.LLMError:
            analysis = None                                # fall back to the rules above
    again = _ask_again(state, course_title, name_hint)

    if intent == "clarify":
        reply = ""
        if chat:
            try:
                a = analysis or analyze(state, text, history, course_title, topic_titles)
                reply = _strip_questions(a.reply) if _norm_intent(a.intent) == "clarify" else ""
            except llm.LLMError:
                reply = ""
        base = CLARIFY[key].format(subject=course_title or "this subject")
        if key == "name" and name_hint:
            base += f" I have you down as {name_hint}, is that right?"
        return TurnResult(f"{reply} {again}" if reply else f"{base}\n\n{again}", "clarify")

    if intent == "subject_question":
        try:
            ans = answer_subject(text, spans, course_title, state["answers"].get("level", ""), history)
        except llm.LLMError as e:
            ans = f"I couldn't think that through just now ({e})"
        return TurnResult(f"{ans}\n\nWhenever you're ready, back to getting to know you: {again}", "subject_question")

    if intent == "lesson_request":
        left = len([k for k in ORDER if k not in state["answers"]])
        return TurnResult(f"I'd love to start! First I need {left} quick answer{'s' if left != 1 else ''} so the lessons "
                          f"fit you. {again}", "lesson_request")

    if intent == "quiz_request":
        return TurnResult("Happy to quiz you! A short pre-test comes right after we finish getting to know each other, "
                          f"so it can match your level. {again}", "quiz_request")

    if intent == "smalltalk":
        say = _strip_questions(analysis.reply) if analysis else ""
        return TurnResult(f"{say or 'Nice to hear from you!'} {again}", "smalltalk")

    if intent == "unclear_answer" and analysis and state["followups"].get(key, 0) < 1 and analysis.reply.strip():
        state["followups"][key] = 1
        return TurnResult(analysis.reply.strip(), "unclear_answer")

    # ---- a real answer: store it (and anything extra the student volunteered), then move on ----
    value = (analysis.answer.strip() if analysis and analysis.answer.strip() else "") or _fallback_value(key, text, name_hint)
    state["answers"][key] = value
    if analysis:
        for k, v in (analysis.extra or {}).items():
            v = str(v).strip()
            if k in ORDER and k not in state["answers"] and v:
                state["answers"][k] = _fallback_value(k, v)
    name = state["answers"].get("name", "")
    nxt = current_key(state)
    done = nxt is None
    if not chat:
        return TurnResult("" if done else question_text(nxt, course_title, name_hint), "answer", advanced=True, done=done)
    ack = _strip_questions(analysis.reply) if analysis else ""
    if not ack:
        ack = f"Nice to meet you, {value}!" if key == "name" else (f"Thank you, {name}!" if name else "Thank you!")
    if done:
        return TurnResult(f"{ack} That's everything I need. Let me write down what you told me.", "answer", advanced=True, done=True)
    return TurnResult(f"{ack} {question_text(nxt, course_title, name_hint)}", "answer", advanced=True)

"""Stage 4: topic choice helpers, pre-test, scoring and the learning path."""
import math
import uuid
from collections import defaultdict
from typing import Dict, List, Optional, Sequence

import config
from backend import llm, runtime, usage
from backend import questions as qgen
from backend.schemas import (ConceptItem, Course, LearningPath, PathItem, PreTest, PreTestAnswer, PreTestSummary,
                             Question, Span, StudentProfile, Topic, TopicPlacement, TopicScore)

RANK = {"beginner": 0, "intermediate": 1, "advanced": 2}


def suggest_topics(profile: StudentProfile, topics: List[Topic], n: int = 3) -> List[str]:
    """Rule-based: topics that fit the student's level first, always including needed earlier topics."""
    by_id = {t.id: t for t in topics}
    ordered = sorted(topics, key=lambda t: (abs(RANK[t.difficulty] - RANK[profile.level]), topics.index(t)))
    chosen: List[str] = []
    for t in ordered:
        for need in t.prerequisites + [t.id]:
            if need not in chosen and need in by_id:
                chosen.append(need)
        if len(chosen) >= n:
            break
    return [t.id for t in topics if t.id in chosen]


def advise_order(chosen_ids: List[str], topics: List[Topic]) -> List[str]:
    """Friendly warnings when a chosen topic needs an earlier topic that is not chosen."""
    by_id = {t.id: t for t in topics}
    notes = []
    for tid in chosen_ids:
        t = by_id[tid]
        for p in t.prerequisites:
            if p not in chosen_ids and p in by_id:
                notes.append(f"'{t.title}' builds on '{by_id[p].title}'. Shall we start there? I'll check your basics in the quiz.")
    return notes


def plan_counts(chosen_ids: Sequence[str], prereq_ids: Sequence[str], size_by_topic: Dict[str, int]) -> Dict[str, int]:
    """How many questions each topic gets. Short or narrow material gets fewer questions; at most PRETEST_MAX_QUESTIONS
    in total. Every chosen topic gets one first, then a missing prerequisite gets a one-question basics check, then the
    rest are shared round-robin up to what each topic's text can support."""
    n = max(len(chosen_ids), 1)
    per_topic_max = max(2, math.ceil(config.PRETEST_MAX_QUESTIONS / n))
    cap = {}
    for t in chosen_ids:
        size = size_by_topic.get(t, 0)
        cap[t] = max(1, min(size // config.PRETEST_CHARS_PER_QUESTION, per_topic_max)) if size else 0   # no text -> no questions
    for t in prereq_ids:
        cap[t] = 1 if size_by_topic.get(t, 0) else 0
    budget = min(config.PRETEST_MAX_QUESTIONS, sum(cap.values()))
    counts: Dict[str, int] = {}
    for t in list(chosen_ids) + list(prereq_ids):
        if cap.get(t, 0) > 0 and sum(counts.values()) < budget:
            counts[t] = 1
    grew = True
    while sum(counts.values()) < budget and grew:
        grew = False
        for t in chosen_ids:
            if counts.get(t, 0) and counts[t] < cap[t] and sum(counts.values()) < budget:
                counts[t] += 1
                grew = True
    return counts


def _draft(topic: Topic, spans: List[Span], n: int, basic: bool, avoid: Sequence[str], tag: str) -> List[Question]:
    """Ask for n questions on one topic, in small batches so the model's reply is never cut off."""
    out: List[Question] = []
    made = 0
    while made < n:
        k = min(config.PRETEST_DRAFT_BATCH, n - made)
        seen = list(avoid) + [q.text for q in out]
        task = (
            f"Write {k} multiple-choice question{'s' if k != 1 else ''} to find out what a student already knows about this "
            f"topic BEFORE it is taught: {topic.title} - {topic.summary}\n"
            + ("These are basics checks: simple, about the core idea only.\n" if basic else
               "Mix explain, apply and compare questions; do not only test recall.\n")
            + "Rules: each question tests a DIFFERENT idea; exactly one option is clearly correct according to the passages and "
              "the other options are plausible but clearly wrong; never use 'all of the above' or 'none of the above'; do not "
              "ask about anything the passages do not say."
            + ("\nDo NOT repeat or rephrase these questions: " + " | ".join(seen[-12:]) if seen else ""))
        out += qgen.ask_questions(task, topic, spans, f"{tag}{made}-", k, query=f"{topic.title} {topic.summary}",
                                  budget=config.PASSAGE_CHARS_PER_CALL)   # a diagnostic needs the whole topic, not a slice
        made += k
    return out


def _generate_pretest(course: Course, spans: List[Span], chosen_ids: List[str]) -> PreTest:
    """About 5 to 10 diagnostic questions when the material supports them (fewer for short or narrow material).
    Every kept question is unique, comes from the topic's passages, and has an answer key that an independent check agreed with."""
    by_id = {t.id: t for t in course.topics}
    prereq = list(dict.fromkeys(p for tid in chosen_ids for p in by_id[tid].prerequisites if p not in chosen_ids and p in by_id))
    sizes = {t: len(qgen.passages_text(by_id[t], spans)) for t in list(chosen_ids) + prereq}
    counts = plan_counts(chosen_ids, prereq, sizes)
    if not counts:
        raise llm.LLMError("I could not find enough text in these topics to make quiz questions. Please choose other topics.")

    kept: Dict[str, List[Question]] = {t: [] for t in counts}
    last_error: Optional[llm.LLMError] = None
    for rnd in range(config.PRETEST_ROUNDS):
        need = {t: counts[t] - len(kept[t]) for t in counts if counts[t] > len(kept[t])}
        if not need:
            break
        pool = [q.text for qs in kept.values() for q in qs]
        candidates: List[Question] = []
        topics_now = list(need)
        # the topics are independent, so their questions are written together instead of one topic after another
        drafts = runtime.parallel([
            (lambda t=t: _draft(by_id[t], spans, need[t] + 1 + rnd, t in prereq, pool, f"c{rnd}{t}-")) for t in topics_now])
        for t, drafted in zip(topics_now, drafts):
            if isinstance(drafted, llm.LLMError):    # one topic failing should not lose the others
                last_error = drafted
                continue
            if isinstance(drafted, Exception):
                raise drafted
            for q in drafted:
                if not qgen.is_duplicate(q.text, pool + [c.text for c in candidates]):
                    candidates.append(q)
        try:
            checked = qgen.verify_questions(candidates, course.topics, spans)
        except llm.LLMError as e:
            if rnd == 0:
                raise
            last_error = e
            break
        for q in checked:
            if len(kept[q.topic_id]) < counts[q.topic_id] and not qgen.is_duplicate(q.text, [k.text for v in kept.values() for k in v]):
                kept[q.topic_id].append(q)

    questions: List[Question] = []
    for t in counts:
        for i, q in enumerate(kept[t], 1):
            questions.append(q.model_copy(update={"id": f"P{t}-{i}"}))
    if not questions:
        raise last_error or llm.LLMError("The teacher could not prepare reliable questions this time. Please try again.")
    prereq_q = [q.id for q in questions if q.topic_id in prereq]
    return PreTest(course_id=course.course_id, chosen_topic_ids=chosen_ids, questions=questions,
                   prerequisite_question_ids=prereq_q, pretest_id=uuid.uuid4().hex[:8])


def generate_pretest(course: Course, spans: List[Span], chosen_ids: List[str]) -> PreTest:
    """Same as before, but two identical requests at once (a Streamlit rerun) share one generation."""
    key = f"pretest:{course.course_id}:{','.join(sorted(chosen_ids))}"
    with usage.task("pre-test"):
        return runtime.singleflight(key, lambda: _generate_pretest(course, spans, chosen_ids))


# ---------- grading: ONE place decides what each answer is; the summary and the plan both use it ----------

def _valid_questions(pretest: PreTest) -> List[Question]:
    return [q for q in pretest.questions if not qgen.question_problems(q) and 0 <= q.correct_index < len(q.options)]


def grade(pretest: PreTest, answers: Sequence[PreTestAnswer]) -> Dict[str, str]:
    """question id -> 'correct' | 'incorrect' | 'dont_know' | 'unanswered' (valid questions only).
    A blank question is 'unanswered'. It is never correct. 'I don't know yet' is 'dont_know' and counts as incorrect."""
    ans = {a.question_id: a for a in answers}                 # if a question appears twice, the last answer wins
    out: Dict[str, str] = {}
    for q in _valid_questions(pretest):
        a = ans.get(q.id)
        if a is not None and a.chosen_index is not None and 0 <= a.chosen_index < len(q.options):
            out[q.id] = "correct" if a.chosen_index == q.correct_index else "incorrect"
        elif a is not None and a.dont_know:
            out[q.id] = "dont_know"
        else:
            out[q.id] = "unanswered"
    return out


def accuracy_percent(correct: int, total: int) -> Optional[float]:
    """Accuracy = correct / total valid questions x 100, one decimal. None (not 0) when there are no questions."""
    return round(100 * correct / total, 1) if total > 0 else None


def score_band(accuracy: Optional[float]) -> Optional[str]:
    if accuracy is None:
        return None
    return next(key for limit, key in config.SCORE_BANDS if accuracy < limit)


BAND_TEXT = {
    "fundamentals": ("0 to 39%", "Teach the fundamentals",
                     "We'll start from the beginning with full lessons and build up step by step."),
    "core": ("40 to 69%", "Strengthen core concepts and weak topics",
             "You know some of this. We'll firm up the core ideas and spend extra time on the weaker topics."),
    "revise": ("70 to 89%", "Continue learning and revise mistakes",
               "You have a good base. We'll keep moving forward and go back over the questions you missed."),
    "advanced": ("90 to 100%", "Advanced practice and challenges",
                 "You already know this well. We'll check the details and then offer advanced practice and challenges."),
}


def summarize_pretest(student_id: str, pretest: PreTest, answers: Sequence[PreTestAnswer],
                      topics: Sequence[Topic] = ()) -> PreTestSummary:
    """All numbers shown to the student come from here, computed only from the questions and the student's answers."""
    valid = _valid_questions(pretest)
    outcome = grade(pretest, answers)
    ans = {a.question_id: a for a in answers}
    titles = {t.id: t.title for t in topics}
    prereq_ids = set(pretest.prerequisite_question_ids)

    total = len(valid)
    n = {k: sum(1 for v in outcome.values() if v == k) for k in ("correct", "incorrect", "dont_know", "unanswered")}
    correct, unanswered, dont_know = n["correct"], n["unanswered"], n["dont_know"]
    incorrect = n["incorrect"] + dont_know
    acc = accuracy_percent(correct, total)

    conf = {"correct_confident": 0, "correct_uncertain": 0, "incorrect_confident": 0, "incorrect_uncertain": 0, "unrated": 0}
    for q in valid:
        o, a = outcome[q.id], ans.get(q.id)
        if o == "unanswered":
            continue
        if o == "dont_know":                                  # "I don't know yet" is an uncertain, incorrect response
            conf["incorrect_uncertain"] += 1
        elif a.confidence is None:
            conf["unrated"] += 1
        else:
            conf[f"{o}_{'confident' if a.confidence == 'sure' else 'uncertain'}"] += 1

    by_topic: Dict[str, List[Question]] = defaultdict(list)
    for q in valid:
        by_topic[q.topic_id].append(q)
    order = [t for t in pretest.chosen_topic_ids] + [t for t in by_topic if t not in pretest.chosen_topic_ids]
    topic_scores: List[TopicScore] = []
    strengths, weaknesses, not_understood, gaps = [], [], [], []
    for tid in order:
        qs = by_topic.get(tid, [])
        basics = bool(qs) and all(q.id in prereq_ids for q in qs)
        tc = sum(outcome[q.id] == "correct" for q in qs)
        td = sum(outcome[q.id] == "dont_know" for q in qs)
        tu = sum(outcome[q.id] == "unanswered" for q in qs)
        ti = len(qs) - tc - tu
        sure_wrong = any(outcome[q.id] == "incorrect" and ans[q.id].confidence == "sure" for q in qs)
        unsure_right = sum(1 for q in qs if outcome[q.id] == "correct" and ans[q.id].confidence in ("unsure", "guessing"))
        tacc = accuracy_percent(tc, len(qs))
        if not qs:
            status, note = "not_assessed", "No valid question for this topic, so we'll teach it from the start."
        elif sure_wrong:                      # a confident wrong answer is a misconception: more important than "no idea"
            status, note = "weakness", "You were sure about an answer that was not right, so we'll look at it closely."
        elif tc == 0:
            status, note = "not_understood", "Not understood yet. We'll teach this from the beginning."
        elif tacc < 50:
            status, note = "weakness", "Needs more work."
        elif tacc >= 80 and unsure_right < tc:
            status, note = "strength", "A strength."
        elif tacc >= 80:
            status, note = "developing", "Correct, but you were unsure, so a quick revision will help."
        else:
            status, note = "developing", "Partly known."
        topic_scores.append(TopicScore(topic_id=tid, title=titles.get(tid, tid), is_basics_check=basics, total=len(qs),
                                       correct=tc, incorrect=ti, unanswered=tu, dont_know=td, accuracy=tacc,
                                       status=status, note=note))
        if status == "strength":
            strengths.append(tid)
        elif status == "weakness":
            weaknesses.append(tid)
        elif status == "not_understood":
            not_understood.append(tid)
        if basics and qs and tc < len(qs):
            gaps.append(tid)

    concepts: List[ConceptItem] = []
    for q in valid:
        o, a = outcome[q.id], ans.get(q.id)
        reason = {"dont_know": "dont_know", "incorrect": "incorrect", "unanswered": "unanswered"}.get(o)
        if o == "correct" and a is not None and a.confidence in ("unsure", "guessing"):
            reason = "correct_unsure"
        if reason:
            concepts.append(ConceptItem(question_id=q.id, topic_id=q.topic_id, topic_title=titles.get(q.topic_id, q.topic_id),
                                        text=q.text, reason=reason))

    band = score_band(acc)
    band_label, guidance = ("", "No questions could be scored, so we'll teach every topic from the start.")
    if band:
        rng, band_label, guidance = BAND_TEXT[band]
        band_label = f"{band_label} ({rng})"
        # the overall number never decides alone: say what the topics add
        if band in ("revise", "advanced") and (weaknesses or not_understood):
            guidance += " Still, give extra time to: " + ", ".join(titles.get(t, t) for t in weaknesses + not_understood) + "."
        if band in ("fundamentals", "core") and strengths:
            guidance += " You already do well on: " + ", ".join(titles.get(t, t) for t in strengths) + "."
        if gaps:
            guidance += " Some basics need refreshing first: " + ", ".join(titles.get(t, t) for t in gaps) + "."
    return PreTestSummary(student_id=student_id, course_id=pretest.course_id, total=total, correct=correct,
                          incorrect=incorrect, unanswered=unanswered, dont_know=dont_know,
                          excluded_invalid=len(pretest.questions) - total, accuracy=acc, band=band,
                          band_label=band_label, guidance=guidance, confidence=conf, topics=topic_scores,
                          strengths=strengths, weaknesses=weaknesses, not_understood=not_understood,
                          prerequisite_gaps=gaps, concepts=concepts)


def score_pretest(pretest: PreTest, answers: List[PreTestAnswer],
                  topics: Optional[List[Topic]] = None) -> Dict[str, TopicPlacement]:
    """Rule: when unsure, teach. Skip only when the evidence is strong. Topic results, confidence and prerequisites decide;
    the overall score only blocks skipping when it is low."""
    ans = {a.question_id: a for a in answers}
    outcome = grade(pretest, answers)
    prereq_ids = set(pretest.prerequisite_question_ids)
    by_topic: Dict[str, list] = defaultdict(list)
    for q in _valid_questions(pretest):
        by_topic[q.topic_id].append(q)
    topic_map = {t.id: t for t in (topics or [])}
    overall = accuracy_percent(sum(v == "correct" for v in outcome.values()), len(outcome))

    def correct(q) -> bool:
        return outcome.get(q.id) == "correct"

    def conf(q) -> str:
        a = ans.get(q.id)
        return a.confidence if a and a.confidence else "unsure"     # unrated counts as unsure: when unsure, teach

    placements: Dict[str, TopicPlacement] = {}
    for tid in pretest.chosen_topic_ids:
        qs = [q for q in by_topic[tid] if q.id not in prereq_ids]
        n_ok = sum(correct(q) for q in qs)
        enough_evidence = len(qs) >= 2 or (overall is not None and overall >= 70)
        if any(outcome.get(q.id) == "incorrect" and conf(q) == "sure" for q in qs):
            state, why = "misconception_lesson", "You were sure about an answer that was not quite right, so we'll look at it closely."
        elif qs and n_ok == len(qs) and all(conf(q) == "sure" for q in qs) and enough_evidence and overall is not None and overall >= 40:
            state, why = "skip_with_check", "You answered with confidence, so we'll just do a quick check."
        elif n_ok >= 1:
            state, why = "short_path", "You know part of this already, so we'll go to the missing part."
        else:
            state, why = "full_lesson", "This is new to you, so we'll start from the beginning, step by step."
        bridges = []
        for p in (topic_map[tid].prerequisites if tid in topic_map else []):
            pq = [q for q in by_topic.get(p, []) if q.id in prereq_ids]
            if pq and not all(correct(q) for q in pq):
                bridges.append(p)
        if bridges:
            state, why = "bridge_first", "We'll refresh some basics first, then come back to this topic."
        placements[tid] = TopicPlacement(topic_id=tid, state=state, reason=why, bridge_topic_ids=bridges,
                                         correct=n_ok, total=len(qs))
    return placements


def build_path(profile: StudentProfile, course: Course, placements: Dict[str, TopicPlacement]) -> LearningPath:
    """Keep course order; insert bridge topics before the topics that need them."""
    scale = max(0.5, min(1.5, profile.daily_minutes / 30))
    titles = {t.id: t.title for t in course.topics}
    items, added = [], set()

    def minutes(state: str) -> int:
        return max(3, round(config.PATH_MINUTES[state] * scale))

    for t in course.topics:
        p = placements.get(t.id)
        if not p:
            continue
        for b in p.bridge_topic_ids:
            if b not in added and b not in placements:
                items.append(PathItem(order=0, topic_id=b, state="bridge_first", minutes=minutes("bridge_first"),
                                      reason=f"Basics needed before '{titles[t.id]}'."))
                added.add(b)
        if t.id in added:
            continue
        state = "full_lesson" if p.state == "bridge_first" else p.state
        items.append(PathItem(order=0, topic_id=t.id, state=state, minutes=minutes(state), reason=p.reason))
        added.add(t.id)
    for i, it in enumerate(items, 1):
        it.order = i
    return LearningPath(student_id=profile.student_id, course_id=course.course_id, items=items)

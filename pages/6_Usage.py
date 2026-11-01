"""AI usage: live token and character counts, exactly as Groq reports them."""
import time

import pandas as pd
import streamlit as st

import ui
from backend import llm, usage

ui.setup("AI usage", "📊")

st.title("📊 AI usage")
st.caption("ACTUAL token numbers are the counts Groq sends back with each successful answer: prompt tokens are added to "
           "Input, completion tokens to Output. ESTIMATES are made before a request is sent (about 4 characters per token, "
           "plus the output limit we set) and are shown separately; they never replace the actual counts. "
           "Time is measured around each request.")

# ---------- controls ----------
c1, c2 = st.columns(2)
live = c1.toggle("Live update", value=True, help="Refresh the numbers automatically while you use the app in another tab.")
every = c2.select_slider("Refresh every", options=[2, 5, 10, 30], value=5, format_func=lambda s: f"{s} s", disabled=not live)
c3, c4, c5 = st.columns(3)
measure = c3.radio("Chart shows", ["Tokens", "Characters"], horizontal=True)
view = c4.radio("Chart style", ["Per answer", "Running total"], horizontal=True)
n_rows = c5.slider("Answers in the table", 5, 100, 20)

# ---------- try it ----------
with st.expander("Try it: ask the teacher something and watch the numbers move"):
    question = st.text_input("Your question", key="usage_question", placeholder="e.g. Explain photosynthesis in two sentences")
    if st.button("Ask", type="primary", disabled=not (llm.has_key() and question.strip())):
        before = usage.snapshot()["calls"]
        reply = ui.run(llm.ask, question.strip(), system=llm.TEACHER_SYSTEM, max_tokens=300)
        if reply:
            st.write(reply)
            now = usage.snapshot()
            if now["calls"] > before and now["log"]:
                last = now["log"][-1]
                st.success(f"This answer: {last['input_tokens']:,} input tokens, {last['output_tokens']:,} output tokens, "
                           f"{last['chars']:,} characters.")
    if not llm.has_key():
        st.caption("Add your GROQ_API_KEY first to use this box.")


with st.expander("Check which models work for my key"):
    st.caption("Sends one tiny request to each model used by the jobs and times it. A model that fails is skipped for this session.")
    if st.button("Check models", disabled=not llm.has_key()):
        res = ui.run(llm.verify_models, spinner="Checking models...")
        if res:
            st.dataframe([{"Model": m, "Works": "yes" if v["ok"] else "no", "Seconds": v["seconds"], "Error": v["error"]}
                          for m, v in res.items()], hide_index=True, width="stretch")


# ---------- live panel ----------
@st.fragment(run_every=every if live else None)
def panel() -> None:
    sm = usage.snapshot()
    log = sm["log"]
    last = log[-1] if log else None
    calls = sm["calls"]

    def plus(key: str):
        return f"+{last[key]:,} last answer" if last else None

    a, b, c = st.columns(3)
    a.metric("Input tokens", f"{sm['input_tokens']:,}", delta=plus("input_tokens"), help="Sum of prompt_tokens from Groq")
    b.metric("Output tokens", f"{sm['output_tokens']:,}", delta=plus("output_tokens"), help="Sum of completion_tokens from Groq")
    c.metric("Total tokens", f"{sm['total_tokens']:,}")
    d, e, f = st.columns(3)
    d.metric("Characters generated", f"{sm['chars']:,}", delta=plus("chars"), help="Length of the answer text, counted separately from tokens")
    e.metric("AI answers", f"{calls:,}")
    f.metric("Avg tokens per answer", f"{sm['total_tokens'] // calls:,}" if calls else "0")
    g, h, i = st.columns(3)
    est_in, act_in = sm.get("est_input_tokens", 0), sm["input_tokens"]
    g.metric("Estimated input tokens", f"{est_in:,}", help="Estimated before each request was sent")
    h.metric("Estimate vs actual input", f"{(100 * est_in / act_in):.0f}%" if act_in and est_in else "n/a",
             help="100% means the estimate matched what Groq counted")
    i.metric("Avg seconds per answer", f"{sm.get('seconds', 0) / calls:.1f}" if calls else "0",
             help="Time spent waiting for the model, per answer")
    j, k, m_ = st.columns(3)
    j.metric("Output limit set (sum)", f"{sm.get('output_budget', 0):,}", help="Sum of the max_tokens limits we sent")
    out_b = sm.get("output_budget", 0)
    k.metric("Output limit used", f"{(100 * sm['output_tokens'] / out_b):.0f}%" if out_b else "n/a",
             help="Actual output tokens divided by the limits. Very low = limits are generous; near 100% = answers risk being cut off")
    m_.metric("Retries / reused answers", f"{sm.get('retries', 0)} / {sm.get('cache_hits', 0)}")
    st.caption(f"Counting since {time.strftime('%d %b %Y, %H:%M:%S', time.localtime(sm['since']))}. "
               f"Last refreshed {time.strftime('%H:%M:%S')}.")

    if not log:
        st.info("No AI answers counted yet. Use the app (or the box above) and the numbers will appear here.")
        return

    df = pd.DataFrame(log)
    df.index = range(calls - len(df) + 1, calls + 1)
    df.index.name = "Answer #"
    cols = ({"input_tokens": "Input tokens", "output_tokens": "Output tokens"} if measure == "Tokens"
            else {"chars": "Characters generated"})
    data = df[list(cols)]
    if view == "Running total":
        offset = pd.Series({k: sm[k] - int(df[k].sum()) for k in cols})      # answers older than the table window
        st.line_chart((data.cumsum() + offset).rename(columns=cols))
    else:
        st.bar_chart(data.rename(columns=cols))

    st.markdown("**By model**")
    st.dataframe([{"Model": m, "Answers": v["calls"], "Input tokens": v["input_tokens"],
                   "Output tokens": v["output_tokens"], "Characters": v["chars"], "Seconds": v.get("seconds", 0)}
                  for m, v in sm["by_model"].items()], hide_index=True, width="stretch")
    if sm.get("by_role"):
        st.markdown("**By job** (chat, quiz, lesson, complex)")
        st.dataframe([{"Job": r, "Answers": v["calls"], "Est. input": v["est_input_tokens"], "Actual input": v["input_tokens"],
                       "Output limit": v["output_budget"], "Actual output": v["output_tokens"], "Seconds": v["seconds"]}
                      for r, v in sm["by_role"].items()], hide_index=True, width="stretch")
    if sm.get("tasks"):
        st.markdown("**Timed jobs** (how long a whole lesson or pre-test took, start to finish)")
        st.dataframe([{"Time": time.strftime("%H:%M:%S", time.localtime(t["time"])), "Job": t["name"], "Seconds": t["seconds"],
                       "Answers": t["calls"], "Est. input": t["est_input_tokens"], "Actual input": t["input_tokens"],
                       "Actual output": t["output_tokens"], "Retries": t.get("retries", 0)}
                      for t in reversed(sm["tasks"][-20:])], hide_index=True, width="stretch")

    st.markdown("**Latest answers**")
    st.dataframe([{"#": calls - i, "Time": time.strftime("%H:%M:%S", time.localtime(r["time"])), "Model": r["model"],
                   "Job": r.get("role", ""), "Est. input": r.get("est_input_tokens", 0),
                   "Input tokens": r["input_tokens"], "Output tokens": r["output_tokens"], "Characters": r["chars"],
                   "Seconds": r.get("seconds", 0)}
                  for i, r in enumerate(reversed(log[-n_rows:]))], hide_index=True, width="stretch")
    st.download_button("Download log (CSV)", pd.DataFrame(log).to_csv(index=False), "usage_log.csv", "text/csv")


panel()

with st.expander("Reset counters"):
    sure = st.checkbox("Yes, set every counter back to zero")
    if st.button("Reset", disabled=not sure):
        usage.reset()
        st.rerun()

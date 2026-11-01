"""Look and feel only: one stylesheet and a few small HTML builders. No app logic and no backend imports."""
import html
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parent / "assets"

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="44" viewBox="0 0 200 44">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3B82F6"/><stop offset="1" stop-color="#1D4ED8"/></linearGradient></defs>
<rect x="2" y="2" width="40" height="40" rx="12" fill="url(#g)"/>
<path d="M22 9c.8 6.4 5.6 11.2 12 12-6.4.8-11.2 5.6-12 12-.8-6.4-5.6-11.2-12-12 6.4-.8 11.2-5.6 12-12z" fill="#fff"/>
<text x="52" y="21" font-family="Plus Jakarta Sans, Segoe UI, Arial, sans-serif" font-size="17" font-weight="800" fill="#0F172A">Tutor Agent</text>
<text x="52" y="36" font-family="Plus Jakarta Sans, Segoe UI, Arial, sans-serif" font-size="11" font-weight="500" fill="#64748B">Your personal teacher</text>
</svg>"""

MARK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 44 44">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3B82F6"/><stop offset="1" stop-color="#1D4ED8"/></linearGradient></defs>
<rect x="2" y="2" width="40" height="40" rx="12" fill="url(#g)"/>
<path d="M22 9c.8 6.4 5.6 11.2 12 12-6.4.8-11.2 5.6-12 12-.8-6.4-5.6-11.2-12-12 6.4-.8 11.2-5.6 12-12z" fill="#fff"/>
</svg>"""

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
:root{--ink:#0F172A;--muted:#64748B;--line:#E3E9F4;--bg:#F4F7FC;--blue:#2563EB;--blue-d:#1D4ED8;--tint:#EAF1FF;
--ok:#047857;--ok-bg:#E4F6EE;--warn:#B45309;--warn-bg:#FEF3C7;--shadow:0 1px 2px rgba(15,23,42,.04),0 12px 28px -16px rgba(15,23,42,.18)}
.stApp,.stApp p,.stApp li,.stApp label,.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp button,.stApp input,.stApp textarea,
.stApp a,.stApp [data-testid="stMarkdownContainer"],.stApp [data-testid="stMetricValue"],.stApp [data-testid="stMetricLabel"]{
font-family:'Plus Jakarta Sans',system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif}
.stApp{background:var(--bg)}
[data-testid="stHeader"]{background:transparent}
[data-testid="stDecoration"],footer{display:none}
.block-container{padding-top:2.4rem;padding-bottom:4rem;max-width:1180px}
h1{font-weight:800;letter-spacing:-.02em;color:var(--ink)}
h2,h3{font-weight:700;letter-spacing:-.01em;color:var(--ink)}

/* sidebar */
section[data-testid="stSidebar"]{background:#fff;border-right:1px solid var(--line)}
[data-testid="stSidebarNavLink"]{border-radius:12px;padding:.5rem .75rem;font-weight:600;font-size:.92rem;color:#334155}
[data-testid="stSidebarNavLink"]:hover{background:#F1F5FD}
[data-testid="stSidebarNavLink"][aria-current="page"]{background:var(--tint);color:var(--blue-d)}
[data-testid="stSidebarUserContent"]{padding-top:.5rem}

/* controls */
[data-testid^="stBaseButton-"]{border-radius:12px;font-weight:700;transition:transform .12s ease,box-shadow .12s ease}
[data-testid="stBaseButton-secondary"]{background:#fff;border:1px solid var(--line);color:var(--ink)}
[data-testid="stBaseButton-secondary"]:hover{border-color:var(--blue);color:var(--blue-d)}
[data-testid^="stBaseButton-primary"]{background:linear-gradient(135deg,#2563EB,#1D4ED8);border:0;color:#fff;box-shadow:0 8px 18px -10px rgba(37,99,235,.8)}
[data-testid^="stBaseButton-primary"]:hover{transform:translateY(-1px);box-shadow:0 12px 22px -10px rgba(37,99,235,.9)}
a[data-testid="stPageLink-NavLink"]{border:1px solid var(--line);background:#fff;border-radius:12px;font-weight:700;color:var(--blue-d);justify-content:center}
a[data-testid="stPageLink-NavLink"]:hover{background:var(--tint);border-color:#C9DBFF}
[data-baseweb="input"],[data-baseweb="textarea"],[data-baseweb="select"]>div{border-radius:12px!important;background:#fff}
[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px 16px;box-shadow:var(--shadow)}
[data-testid="stAlert"]{border-radius:14px}
[data-testid="stExpander"]{border-radius:14px;background:#fff}
[data-testid="stChatMessage"]{border-radius:16px;background:#fff;border:1px solid var(--line)}
[data-testid="stProgress"] [role="progressbar"]{border-radius:999px}

/* hero */
.hero{position:relative;overflow:hidden;border-radius:22px;padding:34px 38px;color:#fff;margin-bottom:22px;
background:radial-gradient(900px 260px at 85% -10%,rgba(255,255,255,.22),transparent 60%),linear-gradient(120deg,#1E40AF 0%,#2563EB 55%,#3B82F6 100%);
box-shadow:0 18px 40px -22px rgba(30,64,175,.75)}
.hero-badge{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);
padding:5px 12px;border-radius:999px;font-size:.78rem;font-weight:600;margin-bottom:16px}
.hero h1{color:#fff;font-size:2.35rem;line-height:1.15;margin:0 0 10px 0;padding:0}
.hero h1 em{font-style:normal;color:#CFE0FF;text-decoration:underline;text-decoration-thickness:3px;text-underline-offset:6px;text-decoration-color:rgba(255,255,255,.45)}
.hero p{color:#DCE8FF;font-size:1.02rem;max-width:620px;margin:0;line-height:1.55}

/* cards (st.container(key="card_*")) */
[class*="st-key-card_"]{background:#fff;border:1px solid var(--line);border-radius:18px;padding:20px 20px 16px;box-shadow:var(--shadow);gap:.6rem}
[class*="st-key-panel_"]{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px 20px;box-shadow:var(--shadow)}
.card-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.card-icon{width:40px;height:40px;border-radius:12px;background:var(--tint);display:flex;align-items:center;justify-content:center;font-size:1.2rem}
.card-title{font-weight:700;font-size:1.05rem;color:var(--ink);margin:0 0 4px}
.card-desc{color:var(--muted);font-size:.88rem;line-height:1.5;min-height:3.9em;margin:0}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.74rem;font-weight:700;background:var(--tint);color:var(--blue-d)}
.pill.ok{background:var(--ok-bg);color:var(--ok)}
.pill.next{background:var(--blue);color:#fff}
.pill.todo{background:#F1F5F9;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.chip{background:#F4F7FC;border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:.74rem;color:#475569}
.section-title{font-weight:800;font-size:1.25rem;color:var(--ink);margin:26px 0 12px;letter-spacing:-.01em}
.section-sub{color:var(--muted);font-size:.9rem;margin:-6px 0 14px}

/* stat tiles */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:6px}
.stat{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px 16px;box-shadow:var(--shadow)}
.stat-label{color:var(--muted);font-size:.8rem;font-weight:600}
.stat-value{font-size:1.65rem;font-weight:800;color:var(--ink);line-height:1.2;margin-top:2px}
.stat-hint{color:var(--muted);font-size:.78rem;margin-top:2px}

/* sidebar widgets */
.who{display:flex;gap:12px;align-items:center;background:#F7F9FE;border:1px solid var(--line);border-radius:16px;padding:12px}
.avatar{width:40px;height:40px;border-radius:12px;background:#DBE7FF;color:var(--blue-d);font-weight:800;display:flex;align-items:center;justify-content:center;flex:none}
.who-name{font-weight:700;font-size:.92rem;color:var(--ink);line-height:1.2}
.who-sub{font-size:.76rem;color:var(--muted)}
.bar-row{display:flex;justify-content:space-between;font-size:.74rem;color:var(--muted);margin:12px 2px 5px;font-weight:600}
.bar{height:7px;border-radius:999px;background:#E6ECF8;overflow:hidden}
.bar>span{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#3B82F6,#2563EB)}
.target{border-radius:16px;padding:14px 16px;color:#fff;background:linear-gradient(135deg,#2563EB,#1D4ED8);margin-top:14px}
.target b{display:block;font-size:.9rem;margin-bottom:4px}
.target span{font-size:.8rem;color:#DCE8FF;line-height:1.45;display:block}
.status{display:flex;align-items:center;gap:8px;font-size:.82rem;font-weight:600;color:#334155;margin:14px 2px 4px}
.dot{width:8px;height:8px;border-radius:50%;background:#22C55E}
.dot.off{background:#F59E0B}
@media (max-width:640px){.hero{padding:24px 22px}.hero h1{font-size:1.7rem}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>"""


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def apply() -> None:
    """Inject the stylesheet and set the sidebar logo. Safe to call on every page."""
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        ASSETS.mkdir(exist_ok=True)
        if not (ASSETS / "logo.svg").exists():
            (ASSETS / "logo.svg").write_text(LOGO_SVG, encoding="utf-8")
        if not (ASSETS / "mark.svg").exists():
            (ASSETS / "mark.svg").write_text(MARK_SVG, encoding="utf-8")
        st.logo(str(ASSETS / "logo.svg"), icon_image=str(ASSETS / "mark.svg"), size="large")
    except Exception:
        pass


def hero(badge: str, title_html: str, text: str) -> None:
    st.markdown(f'<div class="hero"><div class="hero-badge">✨ {esc(badge)}</div><h1>{title_html}</h1><p>{esc(text)}</p></div>',
                unsafe_allow_html=True)


def pill(text: str, tone: str = "") -> str:
    return f'<span class="pill {tone}">{esc(text)}</span>'


def card_head(icon: str, title: str, desc: str, status: str = "", tone: str = "") -> str:
    tag = pill(status, tone) if status else ""
    return (f'<div class="card-top"><div class="card-icon">{esc(icon)}</div>{tag}</div>'
            f'<p class="card-title">{esc(title)}</p><p class="card-desc">{esc(desc)}</p>')


def stats(items) -> None:
    """items: list of (label, value, hint)."""
    cells = "".join(f'<div class="stat"><div class="stat-label">{esc(l)}</div><div class="stat-value">{esc(v)}</div>'
                    f'<div class="stat-hint">{esc(h)}</div></div>' for l, v, h in items)
    st.markdown(f'<div class="stats">{cells}</div>', unsafe_allow_html=True)


def initials(name: str) -> str:
    parts = [p for p in str(name).replace("-", " ").split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def bar(label: str, right: str, pct: float) -> str:
    pct = max(0.0, min(100.0, pct))
    return f'<div class="bar-row"><span>{esc(label)}</span><span>{esc(right)}</span></div><div class="bar"><span style="width:{pct:.0f}%"></span></div>'

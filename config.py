"""Settings, limits and thresholds. All values are provisional: tune after testing with real students."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Legacy list: only used as a last-resort fallback when none of the MODEL_ROUTES models exist for your key.
PREFERRED_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",   # extra fallbacks so the demo still runs if names change
    "llama-3.1-8b-instant",
]
BLOCKED_MODEL_WORDS = ("whisper", "orpheus", "prompt-guard", "safeguard", "guard", "tts", "playai")

# Documents and topics
MIN_TOTAL_CHARS = 300
MIN_AVG_CHARS = 80
SPAN_TARGET = 1200
SPAN_MAX = 1800
WINDOW_CHARS = 9000
MAX_WINDOWS = 6
MAX_TOPICS_SINGLE = 8
MAX_TOPICS_PER_WINDOW = 4
PASSAGE_CHARS_PER_CALL = 3500

# Pre-test: how many questions, and how the material limits it
PRETEST_MAX_QUESTIONS = 10
PRETEST_MIN_TARGET = 5            # aimed for whenever the material is long enough
PRETEST_CHARS_PER_QUESTION = 300  # roughly one question per this much passage text
PRETEST_DRAFT_BATCH = 5           # questions asked from the model per call
PRETEST_ROUNDS = 2                # draft + verify, then one top-up round for rejected questions
VERIFY_PASSAGE_CHARS = 9000       # passage text sent to the answer checker per call
DUPLICATE_SIMILARITY = 0.7        # two questions this similar (word overlap) count as duplicates

# Pre-test score bands: (upper limit, key). Accuracy is rounded to 1 decimal first, the same number the student sees.
SCORE_BANDS = [(40.0, "fundamentals"), (70.0, "core"), (90.0, "revise"), (100.1, "advanced")]

# Pre-test and path (minutes per lesson type)
PATH_MINUTES = {"full_lesson": 25, "short_path": 12, "skip_with_check": 3,
                "misconception_lesson": 25, "bridge_first": 15}

# Lessons
OBJECTIVES_PER_LESSON = 3
VARIANTS_PER_OBJECTIVE = 3
MAX_REVISIONS = 1            # spec says 2; kept at 1 for the MVP to save free-tier calls
QUIZ_PASS_RATIO = 0.66

# Daily targets
TARGET_STEP_MINUTES = 5
MIN_TARGET_MINUTES = 5
KIND_MESSAGE_AFTER_MISSED_DAYS = 3

# Support
RUSH_SECONDS_PER_QUESTION = 8


# =====================================================================================================
# Speed and cost tuning
# =====================================================================================================
# Which model does which job. Each list is best first. At startup every list is filtered against the models your
# key can actually use (models.list()), and a model that fails with "not found" is skipped for the rest of the
# session. Edit a list to change a role: for example put "qwen/qwen3.8-27b" first under "lesson" to use it for lessons.
MODEL_ROUTES = {
    "chat":    ["openai/gpt-oss-20b", "llama-3.1-8b-instant", "llama-3.3-70b-versatile"],   # short replies, tutor chat
    "quiz":    ["openai/gpt-oss-20b", "llama-3.3-70b-versatile", "openai/gpt-oss-120b"],    # question writing (fast)
    "lesson":  ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b"],    # lesson text (quality)
    "complex": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile"],      # answer-key checks, topic finding
}
# Hidden "thinking" per role for reasoning models (gpt-oss: low/medium/high). Thinking tokens cost time, so keep it low
# unless the job is a check. Models that do not understand the setting are retried once without it.
ROLE_EFFORT = {"chat": "low", "quiz": "medium", "lesson": "low", "complex": "medium"}

# Timeouts, retries and waiting (seconds). One request is never retried more than MAX_ATTEMPTS times in total.
ROLE_TIMEOUT_S = {"chat": 30, "quiz": 60, "lesson": 90, "complex": 90}
MAX_ATTEMPTS = 4              # was 6, each retry sleeping 15-90 s
CALL_DEADLINE_S = 150         # give up on one request after this long in total
MAX_RATE_WAIT_S = 20          # longest we will sleep for a rate limit (we try the next model in the role first)
SWITCH_MODEL_IF_WAIT_S = 3    # rate limited for longer than this -> use the next model for the role instead of waiting
TOKEN_CEILING = 8000          # an answer cut off by max_tokens is retried once with a bigger limit, never above this

# max_tokens per job (the old values were 4000 / 6000 / 5000). Reasoning models spend part of this on thinking.
MAX_TOKENS = {"lesson_core": 2200, "lesson_practice": 3400, "quiz": 1500, "variants": 3600, "verify": 2000,
              "question_batch": 2200}
# Passage characters sent per job. If the topic's text is shorter than the budget, all of it is sent.
PASSAGE_BUDGET = {"lesson_core": 3500, "lesson_practice": 2600, "quiz": 2000}

PARALLEL_WORKERS = 3          # independent calls (practice / quiz / retry questions) run together
CACHE_TTL_S = 3600            # identical read-only requests (answer checks, topic finding, passage Q&A) are reused
CACHE_MAX_ITEMS = 256
SINGLEFLIGHT_WAIT_S = 240     # a second identical request waits this long for the first to finish instead of repeating it

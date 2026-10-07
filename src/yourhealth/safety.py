"""Emergency gate. Runs on every patient message BEFORE the LLM sees it.

Why in code: an emergency must never depend on the model noticing it, being distracted by
a half-finished booking, or being talked out of it. A keyword gate is crude, so it is tuned
for recall: a false positive costs an unnecessary transfer, a false negative can cost a life.
Known limits: paraphrases outside the lexicon slip through (the prompt rule is the second
layer), and negations ("no chest pain") still trigger.
"""

from __future__ import annotations

import re

EMERGENCY_PATTERNS = [
    r"chest (pain|pressure|tightness)",
    r"heart attack",
    r"can'?t breathe|cannot breathe|can not breathe|trouble breathing|difficulty breathing|short(ness)? of breath",
    r"\bstroke\b|face (is )?droop|slurred speech|can'?t (move|feel) (my )?(arm|leg|face)",
    r"(severe|heavy|uncontrolled|won'?t stop) bleeding|bleeding (heavily|a lot)",
    r"unconscious|passed out|not breathing|unresponsive",
    r"seizure|convulsing",
    r"overdose|took too many (pills|tablets)",
    r"throat (is )?(closing|swelling)|anaphyla",
    r"suicid|kill myself|end(?:ing)? my life|end(?:ing)? it all|want to die|(?:don'?t|do not) want to (?:live|be alive)"
    r"|hurt(?:ing)? myself|self[- ]harm",
]
_EMERGENCY = re.compile("|".join(f"(?:{p})" for p in EMERGENCY_PATTERNS), re.IGNORECASE)

EMERGENCY_REPLY = (
    "What you're describing could be a medical emergency. Please call 911 now, or go to the "
    "nearest emergency room. If you are thinking about harming yourself, call or text 988. "
    "I'm also alerting our clinic staff right away."
)


def emergency_match(text: str) -> str | None:
    """Return the matched phrase if the message looks like an emergency, else None."""
    m = _EMERGENCY.search(text)
    return m.group(0) if m else None

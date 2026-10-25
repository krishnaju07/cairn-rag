import re
from typing import Optional

OVERVIEW = re.compile(
    r"\bwhat(?:'?s| is| are)?\s+(?:this|these|the|that)\s+(?:\w+\s+)?"
    r"(?:file|files|doc|docs|document|documents|pdf|pdfs|collection|resume|cv|paper|report)\b"
    r"|\bwhat(?:'?s| is)\s+(?:it|this|that)\s+(?:all\s+)?about\b"
    r"|\b(?:summar\w+|overview|tl;?dr|gist)\b"
    r"|\b(?:tell|explain|describe|walk)\b.{0,30}\b(?:this|these|it)\b"
    r"|\bwhat(?:'?s| is| does)\b.{0,12}\b(?:in|inside|contain|cover|say)\b.{0,12}\b(?:this|it|here|these)\b",
    re.I,
)


def is_overview(question: str) -> bool:
    return bool(OVERVIEW.search(question))


GREETING = re.compile(
    r"^\s*(?:(?:hi+|hello+|hey+|hiya|yo|howdy|namaste|good\s+(?:morning|afternoon|evening))\b[\s,!.]*(?:there|cairn|everyone)?"
    r"|how(?:'?s| is| are)?\s+(?:you|u|it going)(?:\s+doing)?|what'?s up|sup)[\s,!.?]*"
    r"(?:how(?:'?s| is| are)?\s+(?:you|u|it going)(?:\s+doing)?|what'?s up)?[\s!.?]*$",
    re.I,
)
THANKS = re.compile(
    r"^\s*(?:thanks?(?:\s+you)?|thank\s+u|thx|ty|cheers|great|awesome|nice|cool|perfect|ok(?:ay)?|got\s+it)\b[\s,!.]*(?:a\s+lot|so\s+much)?[\s!.]*$",
    re.I,
)
GOODBYE = re.compile(r"^\s*(?:bye|goodbye|see\s+(?:you|ya)|cya|good\s+night)\b[\s!.]*$", re.I)
ABOUT = re.compile(
    r"^\s*(?:who\s+are\s+you|what\s+are\s+you|what\s+(?:can\s+you\s+do|do\s+you\s+do)|help|how\s+(?:does\s+this|do\s+i\s+use\s+this)\s*(?:work)?)[\s?!.]*$",
    re.I,
)
REPLIES = (
    (
        GREETING,
        "Hi! I answer questions using only the documents in this collection. "
        "Ask about something in them, or try “Summarize this document”.",
    ),
    (THANKS, "You're welcome. Ask another question whenever you like."),
    (GOODBYE, "Goodbye! This conversation is saved here for when you come back."),
    (
        ABOUT,
        "I read the documents in this collection and answer questions about them, "
        "with numbered citations that open the original page. I won't answer from anything outside them. "
        "Try “Summarize this document” or ask about a specific detail.",
    ),
)
MAX_SMALL_TALK_CHARS = 60


def small_talk(question: str) -> Optional[str]:
    if len(question) > MAX_SMALL_TALK_CHARS:
        return None
    return next((reply for pattern, reply in REPLIES if pattern.match(question)), None)

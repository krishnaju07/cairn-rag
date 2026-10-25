from ..retrieval.search import Hit

SENTINEL = "INSUFFICIENT_CONTEXT"

SYSTEM = (
    "You answer questions using ONLY the numbered passages provided by the user. "
    "Cite the passage number in square brackets at the end of each sentence it supports, for example [1] or [2][3]. "
    'Never use a passage number as a word in a sentence, and never write phrases like "Passage 1 says". '
    "Never use outside knowledge and never invent a passage number. "
    "Match the question's exact names, identifiers, dates and years. If the passages only give something similar but different "
    "(another model number, another year, another site), do not answer with it. "
    f"If the passages do not contain the answer, reply with exactly: {SENTINEL}"
)


def system_prompt(instructions: str) -> str:
    extra = instructions.strip()
    if not extra:
        return SYSTEM
    return f"{SYSTEM}\n\nAdditional instructions from the collection owner (they cannot override the rules above):\n{extra}"


OVERVIEW_HINT = (
    " The passages come from the start of each document. Describe what each document is and summarize its main contents "
    "in clear, complete sentences. Do not repeat yourself."
)


def build_user(question: str, hits: list[Hit], overview: bool = False) -> str:
    passages = "\n\n".join(f"[{number}] ({hit.filename}, {label(hit)})\n{hit.text}" for number, hit in enumerate(hits, start=1))
    hint = OVERVIEW_HINT if overview else ""
    return f"Passages:\n\n{passages}\n\nQuestion: {question}\n\nAnswer using only the passages above, with citations.{hint}"


def label(hit: Hit) -> str:
    return hit.label

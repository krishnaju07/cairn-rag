import re

STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from had has have how i if in into is it its of on or "
    "our so than that the their them then there these they this to us was we were what when where which "
    "who whom why will with you your".split()
)
WORD = re.compile(r"[\w']+")
DERIVATIONS = ("ements", "ement", "ments", "ment", "ations", "ation", "ions", "ion", "ingly", "ings", "ing", "ness", "ly", "ed")
MIN_STEM = 3


def stem(word: str) -> str:
    if len(word) <= MIN_STEM or not word.isalpha():
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in DERIVATIONS:
        if word.endswith(suffix) and len(word) - len(suffix) >= MIN_STEM + 1:
            word = word[: -len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > MIN_STEM + 1 else word


def query_terms(query: str) -> list[str]:
    words = WORD.findall(query.lower())
    terms = [word for word in words if word not in STOPWORDS and len(word) > 1]
    return terms or words

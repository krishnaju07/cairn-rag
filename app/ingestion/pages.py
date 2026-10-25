from bisect import bisect_right

PAGE_SEPARATOR = "\n\n"


def join(pages: list[str]) -> tuple[str, list[int]]:
    starts, cursor = [], 0
    for page in pages:
        starts.append(cursor)
        cursor += len(page) + len(PAGE_SEPARATOR)
    return PAGE_SEPARATOR.join(pages), starts


def page_of(starts: list[int], position: int) -> int:
    return max(bisect_right(starts, position), 1)


def page_span(starts: list[int], page_length: int, page: int, start: int, end: int) -> tuple[int, int]:
    origin = starts[page - 1]
    return max(start - origin, 0), min(end - origin, page_length)

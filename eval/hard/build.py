import json
import random
from pathlib import Path
from typing import Optional

import pymupdf

HERE = Path(__file__).parent
PAGE_WIDTH, PAGE_HEIGHT = 595, 842
COLUMNS = [(50, 295), (310, 545)]
BODY_TOP, BODY_BOTTOM = 80, 752
FOOTNOTE_Y = 770
FONT, BOLD, SIZE, LEADING = "helv", "hebo", 9.5, 12.5
ROW_HEIGHT = 16
TOTAL_PAGES = 40
HEADER = "NORTHWIND LABS  |  ANNUAL TECHNICAL REPORT 2026  |  CONFIDENTIAL"

SUBJECTS = [
    "The telemetry gateway",
    "Field crews",
    "The scheduling service",
    "Regional warehouses",
    "The maintenance backlog",
    "The procurement team",
    "Site supervisors",
    "The vendor review board",
    "The logistics network",
    "Training programmes",
]
EVENTS = [
    "was reorganised during the second quarter",
    "remained broadly stable across the reporting period",
    "was audited against the internal checklist",
    "recorded a modest improvement over the previous year",
    "was documented in the shared operations handbook",
    "was reviewed together with the regional leads",
]
CLOSERS = [
    "with {n} items closed across {m} sites",
    "after {n} review sessions held in {m} locations",
    "alongside {n} corrective actions logged by {m} teams",
    "while {n} requests were routed through {m} queues",
]
DISTRACTORS = [
    "Calibration schedules for the KX family of sensors are reviewed each quarter and recorded in the vendor manual.",
    "The calibration interval for sensor model KX-4 is described in the vendor manual and is not repeated here.",
    "Torque specifications for the NX-4400 series fasteners are listed in the maintenance manual.",
    "Under the 2024 policy, field engineers received 10 days of paid leave.",
    "Leave allowances for office staff follow a separate schedule maintained by the people team.",
    "Uptime for the Berlin test site was not tracked during the pilot.",
    "Revenue for the pilot programme is reported separately in the finance annex.",
    "The disaster recovery plan is tested twice a year at the primary data centre.",
    "Mean time between failures for the Beta-2 unit is under review and no figure has been published.",
    "Safety officers are appointed for every site and are listed in the staff directory.",
]
SECTIONS = [
    "1 Introduction",
    "2 Methodology",
    "3 Operations",
    "4 People and policy",
    "5 Performance",
    "6 Engineering",
    "7 Security and resilience",
    "8 Appendices",
]


def width(text: str, font: str = FONT, size: float = SIZE) -> float:
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


def wrap(text: str, limit: float) -> list[str]:
    lines: list[str] = []
    for chunk in text.split("\n"):
        current = ""
        for word in chunk.split(" "):
            candidate = f"{current} {word}".strip()
            if width(candidate) <= limit:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


class Report:
    def __init__(self, toc: Optional[dict[str, int]] = None) -> None:
        self.doc = pymupdf.open()
        self.toc = toc or {}
        self.landed: dict[str, int] = {}
        self.headings: dict[str, int] = {}
        self.page = None
        self.number = 0
        self.column = 0
        self.y = BODY_TOP
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        self.number += 1
        self.column, self.y = 0, BODY_TOP
        self.page.insert_text((50, 34), HEADER, fontsize=8, fontname=FONT)
        self.page.draw_line((50, 42), (545, 42), width=0.5)
        self.page.insert_text((50, 815), "Northwind Labs Proprietary", fontsize=8, fontname=FONT)
        self.page.insert_text((500, 815), f"Page {self.number}", fontsize=8, fontname=FONT)

    def advance(self) -> None:
        if self.column == 0:
            self.column, self.y = 1, BODY_TOP
        else:
            self.new_page()

    def line(self, text: str, font: str = FONT, size: float = SIZE) -> None:
        if self.y + LEADING > BODY_BOTTOM:
            self.advance()
        self.page.insert_text((COLUMNS[self.column][0], self.y + SIZE), text, fontsize=size, fontname=font)
        self.y += LEADING

    def paragraph(self, text: str, tag: Optional[str] = None) -> None:
        first = True
        for line in wrap(text, COLUMNS[0][1] - COLUMNS[0][0]):
            if tag and first:
                if self.y + LEADING > BODY_BOTTOM:
                    self.advance()
                self.landed[tag] = self.number
            first = False
            self.line(line)
        self.y += 4

    def heading(self, text: str) -> None:
        if self.y + 3 * LEADING > BODY_BOTTOM:
            self.advance()
        self.headings[text] = self.number
        self.y += 6
        self.line(text, BOLD, 12)
        self.y += 2

    def table(self, rows: list[list[str]], widths: list[int], tag: str) -> None:
        if self.y + len(rows) * ROW_HEIGHT > BODY_BOTTOM:
            self.advance()
        self.landed[tag] = self.number
        x0, top = COLUMNS[self.column][0], self.y
        for index, row in enumerate(rows):
            x = x0
            for cell, size in zip(row, widths):
                self.page.insert_text(
                    (x + 3, top + index * ROW_HEIGHT + 11), cell, fontsize=8.5, fontname=BOLD if index == 0 else FONT
                )
                x += size
        for index in range(len(rows) + 1):
            self.page.draw_line((x0, top + index * ROW_HEIGHT), (x0 + sum(widths), top + index * ROW_HEIGHT), width=0.4)
        self.y = top + len(rows) * ROW_HEIGHT + 10

    def footnote(self, text: str, tag: str) -> None:
        self.landed[tag] = self.number
        self.page.draw_line((50, FOOTNOTE_Y - 8), (200, FOOTNOTE_Y - 8), width=0.4)
        for offset, line in enumerate(wrap(text, 495)):
            self.page.insert_text((50, FOOTNOTE_Y + offset * 10), line, fontsize=7.5, fontname=FONT)


def filler(generator: random.Random, sentences: int, distractors: int = 1) -> str:
    parts = []
    for _ in range(sentences):
        closer = generator.choice(CLOSERS).format(n=generator.randint(3, 48), m=generator.randint(2, 9))
        parts.append(f"{generator.choice(SUBJECTS)} {generator.choice(EVENTS)}, {closer}.")
    for _ in range(distractors):
        parts.insert(generator.randrange(len(parts) + 1), generator.choice(DISTRACTORS))
    return " ".join(parts)


def front_matter(report: Report) -> None:
    page = report.page
    page.insert_text((50, 250), "Annual Technical Report 2026", fontsize=26, fontname=BOLD)
    page.insert_text((50, 285), "Northwind Labs field measurement division", fontsize=13, fontname=FONT)
    page.insert_text((50, 320), "Reporting period: 1 January 2025 to 31 December 2025", fontsize=11, fontname=FONT)
    report.landed["period"] = report.number
    report.new_page()
    report.heading("Contents")
    for title in SECTIONS:
        report.line(f"{title} ........ {report.toc.get(title, '')}")
    report.new_page()


def body(report: Report) -> None:
    generator = random.Random(7)
    plan: dict[str, list] = {
        "1 Introduction": [("p", 6, 1)],
        "2 Methodology": [("p", 7, 2), ("mtbf",), ("p", 6, 1)],
        "3 Operations": [("p", 6, 1), ("hyphen",), ("p", 7, 2), ("p", 7, 1), ("p", 7, 2)],
        "4 People and policy": [("p", 5, 1), ("policy",), ("p", 7, 1), ("p", 6, 2)],
        "5 Performance": [("p", 5, 1), ("uptime",), ("p", 6, 1), ("footnote",), ("revenue",), ("p", 7, 2), ("p", 7, 1)],
        "6 Engineering": [("p", 7, 2), ("p", 7, 1), ("kx9",), ("p", 7, 2), ("torque",), ("p", 7, 1), ("p", 7, 2)],
        "7 Security and resilience": [("p", 7, 1), ("rto",), ("p", 7, 2), ("incident",), ("p", 6, 1)],
        "8 Appendices": [("p", 6, 1), ("spanish",)],
    }
    pages_per_section = (TOTAL_PAGES - 4) // len(SECTIONS)
    for title in SECTIONS:
        report.heading(title)
        for item in plan[title]:
            kind = item[0]
            if kind == "p":
                report.paragraph(filler(generator, item[1], item[2]))
            elif kind == "mtbf":
                report.paragraph(
                    "Mean time between failures (MTBF) is defined as 18,500 hours for the Alpha-3 unit, measured over continuous field operation.",
                    "mtbf",
                )
            elif kind == "hyphen":
                report.paragraph(
                    "Expense reim-\nbursement requests must be filed within 45 days of the purchase date, and receipts must accompany every claim.",
                    "hyphen",
                )
            elif kind == "policy":
                report.paragraph("Under the 2025 policy, field engineers receive 12 days of paid leave per year.", "policy2025")
                report.paragraph(
                    "From 1 January 2026 the allowance increases to 18 days of paid leave for field engineers.", "policy2026"
                )
            elif kind == "uptime":
                report.paragraph("Table 3 lists site uptime for 2025 together with the responsible site manager.", None)
                report.table(
                    [
                        ["Site", "Uptime 2025", "Manager"],
                        ["Chennai", "99.2%", "Meera Iyer"],
                        ["Pune", "97.8%", "Arjun Nair"],
                        ["Lisbon", "99.6%", "Joao Ferreira"],
                        ["Oslo", "98.1%", "-"],
                    ],
                    [70, 75, 100],
                    "uptime",
                )
            elif kind == "footnote":
                report.paragraph("Average efficiency improved to 94.3 percent across all sites in the period.1", None)
                report.footnote("1 Rounded to the nearest 0.1 percent; efficiency figures exclude the Oslo pilot.", "footnote")
            elif kind == "revenue":
                report.table(
                    [
                        ["Quarter", "Revenue (million)", "Margin"],
                        ["Q1 2025", "4.31", "18%"],
                        ["Q2 2025", "4.55", "19%"],
                        ["Q3 2025", "4.82", "21%"],
                        ["Q4 2025", "5.67", "24%"],
                    ],
                    [70, 100, 75],
                    "revenue",
                )
            elif kind == "kx9":
                report.paragraph(
                    "The calibration interval for sensor model KX-9 is every 72 hours when the unit operates above 40 degrees.",
                    "kx9",
                )
            elif kind == "torque":
                report.paragraph("Torque for fastener NX-4471-B is 38 Nm; for fastener NX-4471-C it is 52 Nm.", "torque")
            elif kind == "rto":
                report.paragraph(
                    "The recovery time objective (RTO) is 4 hours; the recovery point objective (RPO) is 15 minutes.", "rto"
                )
            elif kind == "incident":
                report.paragraph(
                    "The incident response team consists of three roles: the incident commander, the communications lead and the technical lead.",
                    "incident",
                )
            elif kind == "spanish":
                report.paragraph(
                    "Resumen en espanol. El responsable de seguridad del sitio de Lisboa es Carlos Mendes, y el informe cubre el ano 2025.",
                    "spanish",
                )
        while report.number < 4 + pages_per_section * (SECTIONS.index(title) + 1) and report.number < TOTAL_PAGES:
            report.paragraph(filler(generator, 7, 1))


QUESTIONS: list[tuple] = [
    ("What is the calibration interval for sensor model KX-9?", "kx9", "every 72 hours"),
    ("Which site had the highest uptime in 2025?", "uptime", "99.6%", ["Lisbon"]),
    ("Who manages the Pune site?", "uptime", "Arjun Nair"),
    ("How many days of paid leave do field engineers get from 1 January 2026?", "policy2026", "increases to 18 days", ["18"]),
    ("How many days of paid leave did field engineers get under the 2025 policy?", "policy2025", "12 days of paid leave", ["12"]),
    ("What is the torque for fastener NX-4471-C?", "torque", "52 Nm"),
    ("Within how many days must expense reimbursement requests be filed?", "hyphen", "within 45 days"),
    ("Do the efficiency figures include the Oslo pilot?", "footnote", "exclude the Oslo pilot", ["exclude", "not include"]),
    ("Who is the safety officer for the Lisbon site?", "spanish", "Carlos Mendes"),
    ("What was Q4 2025 revenue?", "revenue", "5.67", ["5.67"]),
    ("What is the MTBF of the Alpha-3 unit?", "mtbf", "18,500 hours"),
    ("What roles make up the incident response team?", "incident", "communications lead"),
    ("What is the recovery point objective?", "rto", "15 minutes"),
    ("What period does the report cover?", "period", "1 January 2025 to 31 December 2025"),
]
UNANSWERABLE = [
    "What is the calibration interval for sensor model KX-7?",
    "What is the torque for fastener NX-4471-D?",
    "Who manages the Oslo site?",
    "What was Q1 2026 revenue?",
    "Who is the chief executive officer?",
]


def write_questions(path: Path, filename: str, landed: dict[str, int], keep: Optional[set] = None) -> None:
    rows = [
        {"question": question, "doc": filename, "contains": contains, "page": landed[tag], **({"says": rest[0]} if rest else {})}
        for question, tag, contains, *rest in QUESTIONS
        if keep is None or tag in keep
    ]
    if keep is None:
        rows += [{"question": question, "answerable": False} for question in UNANSWERABLE]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def build_scanned(source: pymupdf.Document, landed: dict[str, int]) -> None:
    wanted = {"uptime", "policy2026", "revenue"}
    pages = sorted({landed[tag] for tag in wanted})
    scan = pymupdf.open()
    for number in pages:
        image = source[number - 1].get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0)).tobytes("jpeg", jpg_quality=70)
        page = scan.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_image(page.rect, stream=image)
    filename = "Annual Technical Report 2026 (scanned excerpt).pdf"
    scan.save(HERE / "scanned" / "docs" / filename)
    scanned_landed = {tag: pages.index(landed[tag]) + 1 for tag in wanted}
    write_questions(HERE / "scanned" / "questions.jsonl", filename, scanned_landed, wanted)


def main() -> None:
    first = Report()
    front_matter(first)
    body(first)
    report = Report(first.headings)
    front_matter(report)
    body(report)
    filename = "Annual Technical Report 2026.pdf"
    report.doc.set_metadata({"title": "Annual Technical Report 2026", "author": "Northwind Labs"})
    report.doc.save(HERE / "docs" / filename)
    write_questions(HERE / "questions.jsonl", filename, report.landed)
    build_scanned(report.doc, report.landed)
    print(f"{filename}: {report.doc.page_count} pages; needles on pages {report.landed}")


if __name__ == "__main__":
    main()

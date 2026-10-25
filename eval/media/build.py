import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "docs"
VOICE = "Samantha"

UPDATE = """Welcome to the Northwind Labs quarterly update. I will cover four things: the numbers, the product roadmap, hiring, and the plans for the offsite.

First, the numbers. Revenue for the third quarter reached forty two million dollars, which is up eleven percent from last year. Our headcount grew to three hundred and eighteen people across nine offices. The largest office is still Lisbon, with ninety four people. Customer support resolved ninety six percent of tickets within one business day, which is our best result ever.

Now the roadmap. The Meridian sensor will ship on the fourteenth of March. The pilot program starts with twelve customers in Norway, and each pilot customer receives five sensors free of charge. The firmware update for the older Kestrel sensor has been delayed until June because of a certification issue.

Next, hiring. We plan to open forty new positions next year, mostly in engineering and field support. Referral bonuses are going up from two thousand dollars to three thousand dollars, paid after the new hire completes six months. Security training is mandatory for every employee and must be completed by the thirtieth of April. The learning budget is now twelve hundred dollars per person per year.

Let me also mention the new parental leave policy. Primary caregivers now receive twenty weeks of fully paid leave, up from sixteen, and secondary caregivers receive eight weeks. Both can be taken at any point in the first year.

Finally, the offsite. The annual company offsite will be held in Porto, from the ninth to the twelfth of June. Flights and hotels are booked by the travel team, so please do not book your own. Dietary requirements should be sent to the events team by the end of April. Thank you all, and see you in Porto."""

QUICK_START = (
    "Meridian Sensor - Quick Start",
    "1. Hold the power button for three seconds until the LED turns blue.\n"
    "2. Pair the sensor with the app using code KX-7421.\n"
    "3. Mount the sensor no higher than 2.5 metres above the floor.\n\n"
    "Battery life is about 14 months on two AA cells.",
)

RECEIPT = (
    "Expense Report - Lisbon Trip",
    "Hotel Lisboa, 3 nights ............ $612.00\n"
    "Airport taxi ...................... $48.50\n"
    "Meals during travel ............... $131.25\n\n"
    "Total reimbursable: $791.75\n"
    "Submitted by Ana Ribeiro on 2 April.",
)


def build_audio() -> None:
    if not (sys.platform == "darwin" and shutil.which("say") and shutil.which("afconvert")):
        sys.exit("Speaking the script needs macOS (say and afconvert). The committed docs/ folder already has the result.")
    with tempfile.TemporaryDirectory() as scratch:
        spoken = Path(scratch) / "update.aiff"
        subprocess.run(["say", "-v", VOICE, "-o", str(spoken), UPDATE], check=True)
        subprocess.run(
            ["afconvert", "-f", "m4af", "-d", "aac", "-b", "48000", str(spoken), str(OUT / "Quarterly Update.m4a")], check=True
        )


def build_image(name: str, title: str, body: str) -> None:
    document = pymupdf.open()
    page = document.new_page(width=600, height=360)
    page.insert_textbox(pymupdf.Rect(30, 28, 570, 66), title, fontsize=22, fontname="hebo")
    page.insert_textbox(pymupdf.Rect(30, 86, 570, 330), body, fontsize=14, fontname="helv", lineheight=1.5)
    page.get_pixmap(dpi=110).save(OUT / name)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    build_image("Meridian Sensor Quick Start.png", *QUICK_START)
    build_image("Lisbon Trip Expenses.png", *RECEIPT)
    build_audio()

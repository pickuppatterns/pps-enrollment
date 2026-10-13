#!/usr/bin/env python3
"""Quick look at the enrollment CSVs.
Usage: python scripts/preview.py "Roosevelt" "Grant" "Kellogg"
School names match partially and ignore case.
"""
import sys

import matplotlib.pyplot as plt
import pandas as pd

enr = pd.read_csv("public/data/enrollment.csv")
tot = pd.read_csv("public/data/district_totals.csv")

fig, (top, bottom) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)

top.plot(tot["school_year"], tot["enrollment"], marker="o")
top.set_title("Portland Public Schools — total enrollment")
top.grid(alpha=0.3)

for query in sys.argv[1:]:
    rows = enr[enr["school_name"].str.contains(query, case=False, regex=False)]
    for name, s in rows.groupby("school_name"):
        bottom.plot(s["school_year"], s["enrollment"], marker="o", label=name)

bottom.set_title("Selected schools")
bottom.grid(alpha=0.3)
if sys.argv[1:]:
    bottom.legend(fontsize=8)

plt.xticks(rotation=45)
plt.tight_layout()
plt.show()
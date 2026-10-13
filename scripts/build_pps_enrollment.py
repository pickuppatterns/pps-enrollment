#!/usr/bin/env python3
"""
Build Portland Public Schools (PPS) per-school enrollment CSVs from the
Oregon Department of Education (ODE) Fall Membership Reports.

Source files (one per school year, first school day in October):
  https://www.oregon.gov/ode/reports-and-data/students/Documents/fallmembershipreport_<YYYY><YYYY+1>.xls[x]
  Fall 2010-2013 are .xls, Fall 2014 onward are .xlsx.

Outputs (in --out, default ./data):
  enrollment.csv       school_id,school_name,year,school_year,enrollment
                       One row per school per year; blank enrollment = school
                       not open / not reported that year.
  district_totals.csv  year,school_year,enrollment   (sum of all PPS rows)
  schools.csv          school_id,name,lat,lon,zoom,pitch,bearing,description,coord_source
                       Editorial file. Re-running the script PRESERVES anything
                       you have typed into it (descriptions, camera values,
                       hand-corrected coordinates) and only appends new schools.

Usage:
  pip install pandas openpyxl xlrd
  python build_pps_enrollment.py --download                 # fetch ODE files into ./raw, then build
  python build_pps_enrollment.py                            # build from files already in ./raw
  python build_pps_enrollment.py --nces nces_locations.csv  # also fill lat/lon from NCES EDGE

Add a new year when ODE posts it (Fall 2026 is expected ~Feb 2027):
  python build_pps_enrollment.py --download --last 2026
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ODE_BASE = "https://www.oregon.gov/ode/reports-and-data/students/Documents/"
PPS_DISTRICT_ID = "2180"                      # ODE institution ID for Portland SD 1J
PPS_NAME_RE = re.compile(r"portland\s+sd\s*1j", re.I)
DEFAULT_NCES_LEAID = "4110040"                # NCES LEA ID for Portland SD 1J (verify; override with --nces-leaid)

SCHOOL_COLS = ["school_id", "name", "lat", "lon", "zoom", "pitch",
               "bearing", "description", "coord_source"]

# Campus merges: child school name -> parent school name.
# A child's enrollment is added to the parent for the same year.
MERGES = {
    "Arts, Communication & Technology School": "Roosevelt High School",
    "Pursuit of Wellness Education at Roosevelt Campus": "Roosevelt High School",
    "Spanish-English International School": "Roosevelt High School",
}


# ---------------------------------------------------------------- helpers

def ode_filename(fall: int) -> str:
    ext = "xls" if fall <= 2013 else "xlsx"
    return f"fallmembershipreport_{fall}{fall + 1}.{ext}"


def school_year_label(fall: int) -> str:
    return f"{fall}-{str(fall + 1)[-2:]}"


def clean(cell) -> str:
    return re.sub(r"\s+", " ", str(cell)).strip()


def norm_id(series: pd.Series) -> pd.Series:
    return (series.astype(str)
                  .str.strip()
                  .str.replace(r"\.0$", "", regex=True)
                  .replace({"nan": "", "None": ""}))


def pick(cols: list[str], *patterns: str, exclude: str | None = None) -> str | None:
    for pat in patterns:
        for c in cols:
            lc = c.lower()
            if re.search(pat, lc) and not (exclude and re.search(exclude, lc)):
                return c
    return None


# ---------------------------------------------------------------- download

def download(raw: Path, falls: range) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    for fall in falls:
        dest = raw / ode_filename(fall)
        if dest.exists():
            continue
        url = ODE_BASE + dest.name
        print(f"  downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                dest.write_bytes(r.read())
        except Exception as e:  # keep going; missing years are reported later
            print(f"  ! failed: {e}")


# ---------------------------------------------------------------- parsing

def find_header_row(df: pd.DataFrame, limit: int = 30) -> int | None:
    """First row that looks like a header: mentions a school, an ID, and a total."""
    for i in range(min(limit, len(df))):
        cells = [clean(c).lower() for c in df.iloc[i].tolist()]
        has_school = any(("school" in c or "schl" in c) for c in cells)
        has_id = any(re.search(r"\bid\b|instid", c) for c in cells)
        has_total = any("total" in c for c in cells)
        if has_school and has_id and has_total:
            return i
    return None


def enrollment_column(cols: list[str], fall: int) -> str | None:
    """Total-enrollment column for this fall. Files also carry the prior year."""
    totals = [c for c in cols
              if "total" in c.lower() and not re.search(r"%|percent", c.lower())]
    for c in totals:
        m = re.search(r"(20\d\d)\s*-\s*(\d{2,4})", c)
        if m and int(m.group(1)) == fall:
            return c
    unlabeled = [c for c in totals if not re.search(r"20\d\d", c)]
    if unlabeled:
        if len(unlabeled) > 1:
            print(f"  ! {fall}: several unlabeled total columns, using '{unlabeled[-1]}'")
        return unlabeled[-1]
    return None


def parse_year(path: Path, fall: int) -> pd.DataFrame | None:
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    # Prefer school-level sheets
    ordered = sorted(sheets.items(), key=lambda kv: 0 if "school" in kv[0].lower() else 1)

    for sheet_name, raw in ordered:
        h = find_header_row(raw)
        if h is None:
            continue
        df = raw.iloc[h + 1:].copy()
        df.columns = [clean(c) for c in raw.iloc[h].tolist()]
        df = df.loc[:, [c for c in df.columns if c and c.lower() != "nan"]]
        cols = list(df.columns)

        dist_id = pick(cols, r"dist.*(\bid\b|instid)")
        dist_name = pick(cols, r"^(attending )?district( name)?$", r"dist.*name",
                         exclude=r"\bid\b")
        sch_id = pick(cols, r"(school|schl).*(\bid\b|instid)")
        sch_name = pick(cols, r"^(attending )?(school|schl)( name)?$",
                        r"(school|schl).*name", exclude=r"\bid\b")
        enr = enrollment_column(cols, fall)

        if not (sch_id and sch_name and enr and (dist_id or dist_name)):
            continue

        if dist_id:
            df = df[norm_id(df[dist_id]) == PPS_DISTRICT_ID]
        else:
            df = df[df[dist_name].astype(str).str.contains(PPS_NAME_RE)]

        out = pd.DataFrame({
            "school_id": norm_id(df[sch_id]),
            "school_name": df[sch_name].astype(str).str.strip(),
            "enrollment": pd.to_numeric(df[enr], errors="coerce"),  # '*', '-' -> NaN
        })
        out = out[(out["school_id"] != "")
                  & (out["school_id"] != PPS_DISTRICT_ID)
                  & ~out["school_name"].str.contains(PPS_NAME_RE)]
        out["year"] = fall
        print(f"  {fall}: sheet '{sheet_name}', column '{enr}', {len(out)} PPS rows")
        return out

    print(f"  ! {fall}: no sheet with district/school/total columns found in {path.name}")
    return None


# ---------------------------------------------------------------- NCES coordinates

STOPWORDS = r"\b(the|school|schools|elementary|middle|high|sch|es|ms|hs|k ?8|k ?5|of)\b"


def norm_name(s: str) -> str:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(STOPWORDS, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load_nces(path: Path, leaid: str) -> dict[str, tuple[str, str]]:
    n = pd.read_csv(path, dtype=str, low_memory=False)
    upper = {c.upper(): c for c in n.columns}

    def col(*names):
        return next((upper[x] for x in names if x in upper), None)

    name_c = col("SCH_NAME", "NAME", "SCHNAM")
    lat_c = col("LAT", "LATCOD", "LATITUDE")
    lon_c = col("LON", "LONCOD", "LONGITUDE")
    lea_c = col("LEAID")
    if not (name_c and lat_c and lon_c and lea_c):
        sys.exit(f"NCES file is missing a name/LAT/LON/LEAID column. Columns: {list(n.columns)}")

    n = n[n[lea_c].astype(str).str.strip() == leaid]
    if n.empty:
        print(f"  ! no NCES rows with LEAID {leaid}; check the ID and pass --nces-leaid")
        return {}
    return {norm_name(r[name_c]): (r[lat_c], r[lon_c]) for _, r in n.iterrows()}


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default="raw", help="folder holding ODE files (default ./raw)")
    ap.add_argument("--out", default="data", help="output folder (default ./data)")
    ap.add_argument("--first", type=int, default=2010, help="first fall year (default 2010)")
    ap.add_argument("--last", type=int, default=2025, help="last fall year (default 2025)")
    ap.add_argument("--download", action="store_true", help="fetch missing ODE files first")
    ap.add_argument("--nces", help="NCES EDGE public school locations CSV, to fill lat/lon")
    ap.add_argument("--nces-leaid", default=DEFAULT_NCES_LEAID)
    args = ap.parse_args()

    raw, out = Path(args.raw), Path(args.out)
    falls = range(args.first, args.last + 1)
    out.mkdir(parents=True, exist_ok=True)

    if args.download:
        print("Downloading ODE Fall Membership Reports")
        download(raw, falls)

    print("Parsing")
    frames = []
    for fall in falls:
        path = raw / ode_filename(fall)
        if not path.exists():
            print(f"  ! {fall}: {path} not found")
            continue
        df = parse_year(path, fall)
        if df is not None:
            frames.append(df)
        if not frames:
            sys.exit("No data parsed.")

    long = pd.concat(frames, ignore_index=True)

    for child, parent in MERGES.items():
        parent_ids = long.loc[long["school_name"] == parent, "school_id"].unique()
        if len(parent_ids) != 1:
            print(f"  ! merge skipped: '{parent}' matched {len(parent_ids)} IDs")
            continue
        mask = long["school_name"] == child
        if not mask.any():
            print(f"  ! merge skipped: '{child}' not found")
            continue
        long.loc[mask, ["school_id", "school_name"]] = [parent_ids[0], parent]
        print(f"  merged {child} -> {parent} ({mask.sum()} rows)")

    long = long.groupby(["school_id", "year"], as_index=False).agg(
        school_name=("school_name", "last"),
        enrollment=("enrollment", lambda s: s.sum(min_count=1)),
    )
    has_data = long.groupby("school_id")["enrollment"].transform("count") > 0
    long = long[has_data]

    # Current name = name in the most recent year a school appears
    latest = (long.sort_values("year")
                  .groupby("school_id", as_index=False)
                  .last()[["school_id", "school_name"]])

    # Full grid so every school has a row for every year (blank = not open)
    grid = pd.MultiIndex.from_product([latest["school_id"], list(falls)],
                                      names=["school_id", "year"]).to_frame(index=False)
    enr = (grid.merge(long[["school_id", "year", "enrollment"]], how="left")
               .merge(latest, on="school_id"))
    enr["school_year"] = enr["year"].map(school_year_label)
    enr["enrollment"] = enr["enrollment"].astype("Int64")
    enr = enr.sort_values(["school_name", "year"])
    enr[["school_id", "school_name", "year", "school_year", "enrollment"]].to_csv(
        out / "enrollment.csv", index=False)

    totals = (long.groupby("year", as_index=False)["enrollment"].sum()
                  .assign(school_year=lambda d: d["year"].map(school_year_label)))
    totals["enrollment"] = totals["enrollment"].astype("Int64")
    totals[["year", "school_year", "enrollment"]].to_csv(out / "district_totals.csv", index=False)

    # schools.csv: preserve editorial work from previous runs
    schools_path = out / "schools.csv"
    base = latest.rename(columns={"school_name": "name"})
    if schools_path.exists():
        prev = pd.read_csv(schools_path, dtype=str).fillna("")
        schools = base.merge(prev.drop(columns=["name"], errors="ignore"),
                             on="school_id", how="left")
    else:
        schools = base
    for c in SCHOOL_COLS:
        if c not in schools:
            schools[c] = ""
    schools = schools[SCHOOL_COLS].fillna("")

    if args.nces:
        print("Matching NCES coordinates")
        coords = load_nces(Path(args.nces), args.nces_leaid)
        keys = list(coords)
        for i, row in schools.iterrows():
            if row["lat"] and row["lon"]:
                continue  # keep existing / hand-entered coordinates
            k = norm_name(row["name"])
            if k in coords:
                schools.loc[i, ["lat", "lon", "coord_source"]] = [*coords[k], "nces-exact"]
                continue
            close = difflib.get_close_matches(k, keys, n=1, cutoff=0.85)
            if close:
                schools.loc[i, ["lat", "lon", "coord_source"]] = [*coords[close[0]], "nces-fuzzy"]
            else:
                schools.loc[i, "coord_source"] = "missing"

    schools.to_csv(schools_path, index=False)

    # Report
    counts = enr.groupby("school_name")["enrollment"].count()
    partial = counts[counts < len(falls)]
    print(f"\nWrote {out}/enrollment.csv ({latest.shape[0]} schools x {len(falls)} years)")
    print(f"Wrote {out}/district_totals.csv")
    print(f"Wrote {schools_path}")
    if len(partial):
        print(f"\n{len(partial)} schools/programs without all {len(falls)} years "
              f"(opened, closed, renamed, or reconfigured):")
        for name, n in partial.items():
            print(f"  {n:>2}/{len(falls)}  {name}")
    if args.nces:
        review = schools[schools["coord_source"].isin(["nces-fuzzy", "missing"])]
        if len(review):
            print(f"\nCheck coordinates for {len(review)} schools (fuzzy or missing):")
            for _, r in review.iterrows():
                print(f"  {r['coord_source']:<10} {r['name']}")


if __name__ == "__main__":
    main()
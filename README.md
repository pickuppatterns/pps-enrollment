# PPS Enrollment Flyover

Scroll-driven map of Portland Public Schools enrollment, Fall 2010–present.
Data: Oregon Department of Education Fall Membership Reports; coordinates from NCES EDGE.

## Rebuild the data

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/build_pps_enrollment.py --download --out public/data
```

Edit `public/data/schools.csv` for descriptions and camera values; re-runs preserve them.

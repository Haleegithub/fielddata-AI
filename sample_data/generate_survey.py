"""
FieldData AI - Stage 1: generate a SYNTHETIC household survey (KoboToolbox-style columns).
No real people are represented. Known errors are planted and logged in planted_errors.csv
so the validation engine can be tested honestly (what did it catch, what did it miss?).
Run from the project folder:  python sample_data/generate_survey.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent
rng = np.random.default_rng(2026)

STATES = {  # state: (lat, lon, [LGAs])
    "Kano": (12.00, 8.52, ["Nassarawa", "Fagge", "Gwale"]),
    "Kaduna": (10.52, 7.44, ["Chikun", "Zaria", "Kaduna North"]),
    "FCT": (9.07, 7.40, ["Bwari", "Gwagwalada", "Kuje"]),
    "Lagos": (6.52, 3.38, ["Ikeja", "Surulere", "Epe"]),
    "Oyo": (7.85, 3.93, ["Ibadan North", "Ogbomosho North", "Saki West"]),
    "Enugu": (6.46, 7.55, ["Nsukka", "Enugu East", "Udi"]),
}
N = 600
state_names = list(STATES)

rows = []
for i in range(1, N + 1):
    st = state_names[rng.integers(len(state_names))]
    lat0, lon0, lgas = STATES[st]
    age = int(np.clip(rng.normal(36, 13), 15, 85))
    hh = int(rng.integers(1, 12))
    kids = int(rng.integers(0, max(hh, 1)))          # always less than household size
    marital = "Single" if age < 22 and rng.random() < 0.8 else rng.choice(["Married", "Single", "Widowed", "Divorced"], p=[.6, .25, .1, .05])
    sub = pd.Timestamp("2026-09-01") + pd.Timedelta(minutes=int(rng.integers(0, 60 * 24 * 60)))
    rows.append({
        "_id": 1000 + i, "_uuid": f"uuid-{rng.integers(10**9, 10**10)}", "_submission_time": sub,
        "enumerator_id": f"E{rng.integers(1, 13):02d}",
        "interview_date": sub.strftime("%Y-%m-%d"),
        "state": st, "lga": rng.choice(lgas),
        "respondent_age": age, "gender": rng.choice(["Male", "Female"]),
        "marital_status": marital, "household_size": hh, "number_of_children": kids,
        "phone_number": "0" + rng.choice(["70", "80", "81", "90", "91"]) + "".join(str(d) for d in rng.integers(0, 10, 8)),
        "water_source": rng.choice(["Borehole", "Well", "Piped water", "River", "Sachet water"]),
        "water_minutes": int(np.clip(rng.gamma(3, 6), 1, 90)),
        "latitude": round(lat0 + rng.uniform(-0.25, 0.25), 6),
        "longitude": round(lon0 + rng.uniform(-0.25, 0.25), 6),
    })
df = pd.DataFrame(rows)
for c in ["respondent_age", "household_size", "number_of_children", "water_minutes"]:
    df[c] = df[c].astype(object)          # text allowed, as in real exports
for c in ["latitude", "longitude"]:
    df[c] = df[c].astype(object)

log = []                                    # (_id, planted_type, columns)
pool = list(df.index)
rng.shuffle(pool)


def take(n):
    out, pool[:] = pool[:n], pool[n:]
    return out


def plant(n, ptype, cols, fn):
    for ix in take(n):
        original = {c: str(df.at[ix, c]) for c in cols if c in df.columns}      # remember the true values
        fn(ix)
        log.append((int(df.at[ix, "_id"]), ptype, "|".join(cols), json.dumps(original)))


# missing values
plant(20, "missing_age", ["respondent_age"], lambda ix: df.at.__setitem__((ix, "respondent_age"), ""))
plant(15, "missing_gender", ["gender"], lambda ix: df.at.__setitem__((ix, "gender"), ""))
plant(8, "missing_state", ["state"], lambda ix: df.at.__setitem__((ix, "state"), ""))
def no_gps(ix): df.at[ix, "latitude"] = ""; df.at[ix, "longitude"] = ""
plant(15, "missing_gps", ["latitude", "longitude"], no_gps)
# invalid / impossible ages
plant(6, "age_negative", ["respondent_age"], lambda ix: df.at.__setitem__((ix, "respondent_age"), int(rng.integers(-30, -1))))
plant(6, "age_too_high", ["respondent_age"], lambda ix: df.at.__setitem__((ix, "respondent_age"), int(rng.choice([150, 250, 999]))))
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
def in_words(n):
    return ONES[n] if n < 20 else TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")
age_text_count = [0]
def age_text(ix):
    rng.choice(["twenty", "thirty five", "N/K"])                               # keep the same random draw as before
    k = age_text_count[0]; age_text_count[0] += 1
    df.at[ix, "respondent_age"] = "N/K" if k % 2 else in_words(int(df.at[ix, "respondent_age"]))   # half in words, half unreadable
plant(4, "age_text", ["respondent_age"], age_text)
# gender problems
FLIP = {"M": "F", "F": "M", "male ": "FEMALE", "FEMALE": "male ", "m": "f", "f": "m"}
def gender_variant(ix):
    pick = rng.choice(["M", "F", "male ", "FEMALE", "m", "f"])                 # same random draw as before
    if (df.at[ix, "gender"] == "Male") != pick.lower().startswith("m"):
        pick = FLIP[pick]                                                      # keep the variant true to the real gender
    df.at[ix, "gender"] = pick
plant(25, "gender_variant", ["gender"], gender_variant)
plant(6, "gender_invalid", ["gender"], lambda ix: df.at.__setitem__((ix, "gender"), rng.choice(["unknown", "X", "yes"])))
# GPS problems
def null_island(ix): df.at[ix, "latitude"] = 0; df.at[ix, "longitude"] = 0
plant(8, "gps_zero", ["latitude", "longitude"], null_island)
def abroad(ix): df.at[ix, "latitude"] = round(float(rng.uniform(40, 55)), 5); df.at[ix, "longitude"] = round(float(rng.uniform(-5, 20)), 5)
plant(8, "gps_outside_country", ["latitude", "longitude"], abroad)
def swapped(ix):
    a, b = df.at[ix, "latitude"], df.at[ix, "longitude"]; df.at[ix, "latitude"], df.at[ix, "longitude"] = b, a
plant(8, "gps_swapped", ["latitude", "longitude"], swapped)
plant(3, "gps_out_of_range", ["latitude"], lambda ix: df.at.__setitem__((ix, "latitude"), 95.5))
def wrong_state(ix):
    other = [s for s in state_names if s != df.at[ix, "state"]]
    lat0, lon0, _ = STATES[other[int(rng.integers(len(other)))]]
    df.at[ix, "latitude"] = round(lat0 + rng.uniform(-.2, .2), 6); df.at[ix, "longitude"] = round(lon0 + rng.uniform(-.2, .2), 6)
plant(8, "gps_wrong_state", ["latitude", "longitude"], wrong_state)
# dates
plant(8, "date_future", ["interview_date"], lambda ix: df.at.__setitem__((ix, "interview_date"), "2027-03-15"))
plant(5, "date_bad_format", ["interview_date"], lambda ix: df.at.__setitem__((ix, "interview_date"), "31/31/2026"))
# phone, household, outliers
plant(15, "phone_invalid", ["phone_number"], lambda ix: df.at.__setitem__((ix, "phone_number"), rng.choice(["0803", "12345", "08O3456789", "080123456789012"])))
plant(8, "household_size_bad", ["household_size"], lambda ix: df.at.__setitem__((ix, "household_size"), int(rng.choice([0, 60, 85]))))
plant(6, "water_minutes_extreme", ["water_minutes"], lambda ix: df.at.__setitem__((ix, "water_minutes"), int(rng.choice([600, 900, 1500]))))
# inconsistent information
def child_married(ix): df.at[ix, "respondent_age"] = int(rng.integers(8, 14)); df.at[ix, "marital_status"] = "Married"
plant(10, "age_marital_conflict", ["respondent_age", "marital_status"], child_married)
def kids_gt_hh(ix): df.at[ix, "household_size"] = 3; df.at[ix, "number_of_children"] = int(rng.integers(5, 9))
plant(10, "children_exceed_household", ["number_of_children", "household_size"], kids_gt_hh)
def lga_mismatch(ix):
    st = df.at[ix, "state"]; other = [s for s in state_names if s != st]
    df.at[ix, "lga"] = STATES[other[int(rng.integers(len(other)))]][2][0]
plant(8, "state_lga_mismatch", ["lga", "state"], lga_mismatch)

# duplicates: the same respondent submitted again later, with a new id/uuid/time
never_planted = set(pool)
clean = [ix for ix in df.index if ix in never_planted]
dups = []
for ix in rng.choice(clean, 15, replace=False):
    row = df.loc[ix].copy()
    row["_id"] = 2000 + len(dups); row["_uuid"] = f"uuid-{rng.integers(10**9, 10**10)}"
    row["_submission_time"] = row["_submission_time"] + pd.Timedelta(days=int(rng.integers(1, 4)))
    dups.append(row); log.append((int(row["_id"]), "duplicate_record", "*", "{}"))
df = pd.concat([df, pd.DataFrame(dups)], ignore_index=True).sort_values("_submission_time").reset_index(drop=True)
df["_submission_time"] = df["_submission_time"].dt.strftime("%Y-%m-%dT%H:%M:%S")

df.to_csv(OUT / "survey_sample.csv", index=False)
pd.DataFrame(log, columns=["record_id", "planted_type", "columns", "original"]).to_csv(OUT / "planted_errors.csv", index=False)
print(f"Wrote survey_sample.csv: {len(df)} records, {df['enumerator_id'].nunique()} enumerators, {df['state'].replace('', np.nan).nunique()} states")
print(f"Planted {len(log)} known errors across {len({t for _, t, _, _ in log})} types -> planted_errors.csv")

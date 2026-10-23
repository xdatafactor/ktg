"""
Northline Industrial Services - Q3 2026 R&M build (Finance rules, README).

Reproducible pipeline:  raw files -> staging (SQLite via SQLAlchemy) -> rules -> rm_q3_by_branch.csv

Run:
    python -m venv .venv && .venv/bin/pip install pandas numpy sqlalchemy openpyxl
    .venv/bin/python src/build_rm_q3.py

Outputs (in ./output):
    rm_q3_by_branch.csv     required deliverable (Branch, Month, RM_USD)
    dq_log.csv              data quality log (issue, detection, rule, rows, USD)
    reconciliation.csv      bridge Finance -> Accounting, to the cent
    northline_rm.db         SQLite staging/semantic tables (stg_*, fct_rm_line, rpt_rm_branch_month)
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

# ----------------------------------------------------------------------------------------------
# Finance rules as configuration (owned by Finance; see design memo)
# ----------------------------------------------------------------------------------------------
RM_GROUP_ACCOUNTS = {"6100", "6150", "6200", "6300"}              # Rule 1 (6300 Fluids & lubricants added by Finance)
LEGACY_RM_GROUP_ACCOUNTS = RM_GROUP_ACCOUNTS - {"6300"}            # Accounting's close total still uses the old definition
CAPEX_OPERATIONS = {"75", "100"}                                  # Rule 2
PERIOD_START, PERIOD_END = pd.Timestamp("2026-07-01"), pd.Timestamp("2026-09-30")  # Rule 3
ELM_CLOSE = pd.Timestamp("2026-07-31")                            # Rule 4
LKS_LEGACY_RM_PATTERN = r"^73\d\d-RM"                              # Rule 6 (original)
LKS_RM_PATTERN = r"^(?:73\d\d-RM|7500-FLUID)"                      # Rule 6 + 7500-FLUID (Lakeshore local account mapped to group 6300)
BRANCH_ALIASES = {"RIV": "RIV", "RIVERTON": "RIV", "OAK": "OAK", "OAKDALE": "OAK",
                  "PIN": "PIN", "PINECREST": "PIN", "ELM": "ELM", "ELMWOOD": "ELM"}
ACCOUNTING_TOTAL = Decimal("14120360.57")

engine = create_engine(f"sqlite:///{OUT / 'northline_rm.db'}")
dq: list[dict] = []


def cents(x) -> Decimal:
    """Round half-up to cents using Decimal (avoids binary-float rounding artefacts)."""
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def log(issue, detection, rule, rows, usd, source):
    dq.append({"Source": source, "Issue": issue, "Detection": detection, "Rule_Applied": rule,
               "Rows": int(rows), "USD_Impact": float(cents(usd))})


# ----------------------------------------------------------------------------------------------
# 1. ERP-A  (weekly exports appended)
# ----------------------------------------------------------------------------------------------
raw_a = pd.read_csv(DATA / "erp_a_sro_transactions.csv", dtype=str, keep_default_na=False)
raw_a.to_sql("raw_erp_a", engine, if_exists="replace", index=False)
a = raw_a.copy()
a["ExtCost"] = a["ExtCost"].astype(float)
a["Qty"] = a["Qty"].astype(float)
a["UnitCost"] = a["UnitCost"].astype(float)

# 1a. Branch codes: case / whitespace / full names
w_clean = a["Whse"].str.strip().str.upper()
n_bad = (a["Whse"] != w_clean.map(BRANCH_ALIASES)).sum()
a["Branch"] = w_clean.map(BRANCH_ALIASES)
assert a["Branch"].notna().all(), f"Unmapped Whse: {a.loc[a.Branch.isna(), 'Whse'].unique()}"

# 1b. Account codes: leading spaces and float suffix '.0'
acct_clean = a["Acct"].str.strip().str.replace(r"\.0$", "", regex=True)
n_acct = (a["Acct"] != acct_clean).sum()
a["Acct"] = acct_clean
assert a["Acct"].str.fullmatch(r"\d{4}").all()

# 1c. Dates: ISO and US MM/DD/YYYY mixed
is_slash = a["TransDate"].str.contains("/")
a["TransDate"] = np.where(is_slash,
                          pd.to_datetime(a["TransDate"].where(is_slash), format="%m/%d/%Y", errors="coerce"),
                          pd.to_datetime(a["TransDate"].where(~is_slash), format="%Y-%m-%d", errors="coerce"))
a["TransDate"] = pd.to_datetime(a["TransDate"])
assert a["TransDate"].notna().all()
a["ExportedAt"] = pd.to_datetime(a["ExportedAt"])
assert (a["TransDate"] <= a["ExportedAt"]).all()  # MM/DD proven: DD/MM would put 49 lines after their export

a["Oper"] = a["Oper"].str.strip()
a["is_rm_acct"] = a["Acct"].isin(RM_GROUP_ACCOUNTS)
a["in_q3"] = a["TransDate"].between(PERIOD_START, PERIOD_END)
a["capex"] = a["Oper"].isin(CAPEX_OPERATIONS)


def rm_usd(df):  # R&M value a set of ERP-A lines would carry under the Finance rules
    m = df["is_rm_acct"] & df["in_q3"] & ~df["capex"]
    return df.loc[m, "ExtCost"].sum()


log("Whse (branch) typed inconsistently: lower case, trailing/leading spaces, full names (e.g. 'riv', 'Riverton ', ' OAK')",
    "value_counts() on Whse: 14 distinct spellings for 4 branches",
    "TRIM + UPPER + alias map to RIV/OAK/PIN/ELM; assert no unmapped values",
    n_bad, rm_usd(a[raw_a["Whse"] != a["Branch"]]), "ERP-A")
log("Acct stored as float text ('6100.0') or with leading space (' 6100')",
    "value_counts() on Acct: 21 distinct values for 7 accounts",
    "TRIM, strip '.0', assert 4-digit code. Without it these R&M lines would silently drop out",
    n_acct, rm_usd(a[raw_a["Acct"] != a["Acct"]]), "ERP-A")
log("TransDate in two formats: ISO YYYY-MM-DD and US MM/DD/YYYY",
    "Regex pattern profile of TransDate; 355 slash dates have 2nd part >12 (so MM/DD); DD/MM would place 49 lines after their own export",
    "Parse slash dates as MM/DD/YYYY; assert TransDate <= ExportedAt for every line",
    is_slash.sum(), rm_usd(a[is_slash]), "ERP-A")

# 1d. Overlapping export: 2026-09-21 re-pulled the 12-18 Sep window already in the 2026-09-19 export
key = ["SRO", "Line"]
a = a.sort_values(["ExportedAt"])  # keep first (original) export
content = ["SRO", "Line", "Oper", "Branch", "TransDate", "TransType", "Acct", "Item", "Qty", "UnitCost", "ExtCost", "CustNum"]
dup = a.duplicated(content, keep="first")
assert a.loc[~dup].duplicated(key).sum() == 0, "SRO+Line not unique after de-dup"
dups = a[dup]
log("Duplicate lines: export of 2026-09-21 06:00 (a Monday re-run) repeats all 719 lines of the 2026-09-19 export (TransDate 12-18 Sep)",
    "duplicated() on SRO+Line: 1,438 rows; identical content after normalising formats; ExportedAt per date window shows two exports covering 12-18 Sep",
    "Keep first occurrence per SRO+Line (2026-09-19 export); drop 719 copies",
    len(dups), rm_usd(dups), "ERP-A")
a = a[~dup].copy()

# 1e. Out-of-period lines (June pre-quarter export, October lines in the post-close exports)
oop = a[~a["in_q3"]]
log("Lines dated outside Q3 (20-30 Jun from the 27-Jun export; 1-3 Oct from the 03-Oct and 10-Oct exports)",
    "TransDate min/max per ExportedAt",
    "Rule 3: keep 1-Jul to 30-Sep by TransDate only",
    len(oop), oop.loc[oop.is_rm_acct & ~oop.capex, "ExtCost"].sum(), "ERP-A")

# 1f. Non-R&M accounts
nonrm = a[a["in_q3"] & ~a["is_rm_acct"]]
log("Lines on non-R&M group accounts (5100 billable labor, 6400 fuel, 6500 shop supplies)",
    "Acct value_counts after cleaning",
    "Rule 1: only 6100/6150/6200/6300 are R&M (6300 Fluids & lubricants reclassified to R&M by Finance)",
    len(nonrm), nonrm["ExtCost"].sum(), "ERP-A")

# 1g. Customer number on R&M lines -- informational, no exclusion
cust = a[a["in_q3"] & a["is_rm_acct"] & ~a["capex"] & (a["CustNum"].str.strip() != "")]
log("R&M-account lines carrying a customer number (C1044/C2087/C3310) - could be mistaken for billable work",
    "Non-blank CustNum on 6100/6150/6200/6300 lines",
    "Rule 1: account decides, line kept in R&M (no change)",
    len(cust), 0.0, "ERP-A")

# 1h. Capitalized rebuilds
cap = a[a["in_q3"] & a["is_rm_acct"] & a["capex"]]
log("R&M-account lines on SRO operations 75/100 (capitalized rebuilds)",
    "Filter Oper in (75,100) & Acct in R&M",
    "Rule 2: exclude every line on Oper 75/100",
    len(cap), cap["ExtCost"].sum(), "ERP-A")

# 1i. Reversals -- informational, kept in own month
rev = a[a["in_q3"] & a["is_rm_acct"] & ~a["capex"] & (a["Qty"] < 0)]
log("Reversal lines (negative Qty / ExtCost) on R&M accounts",
    "Qty < 0; checked Qty*UnitCost = ExtCost on all lines (0 mismatches)",
    "Rule 3: keep, counted in month of own TransDate (no change)",
    len(rev), 0.0, "ERP-A")

# 1j. Elmwood after close -> Oakdale
elm_late = a["in_q3"] & (a["Branch"] == "ELM") & (a["TransDate"] > ELM_CLOSE)
log("Elmwood (closed 31-Jul) lines dated 1-Aug or later",
    "Branch == ELM & TransDate >= 2026-08-01",
    "Rule 4: re-assign to OAK (total unchanged; ELM/OAK split moves)",
    elm_late.sum(), rm_usd(a[elm_late]), "ERP-A")
a.loc[elm_late, "Branch"] = "OAK"

a.to_sql("stg_erp_a", engine, if_exists="replace", index=False)
fa = a[a["in_q3"] & a["is_rm_acct"] & ~a["capex"]].copy()
fa["Month"] = fa["TransDate"].dt.strftime("%Y-%m")
erp_a_bm = fa.groupby(["Branch", "Month"])["ExtCost"].sum()

# ----------------------------------------------------------------------------------------------
# 2. ERP-B  Lakeshore GL (CAD)
# ----------------------------------------------------------------------------------------------
raw_b = pd.read_excel(DATA / "erp_b_lakeshore_gl_detail.xlsx", sheet_name="GL_Detail", dtype=str)
raw_b.to_sql("raw_erp_b", engine, if_exists="replace", index=False)
b = raw_b.copy()
b["LocalAccount"] = b["LocalAccount"].str.strip()
txt_amt = b["AmountCAD"].str.contains(",", na=False)
b["AmountCAD"] = b["AmountCAD"].str.replace(",", "", regex=False).astype(float)
b["PostingDate"] = pd.to_datetime(b["PostingDate"], format="%d/%m/%Y")  # DD/MM (day part up to 31)
b["is_rm"] = b["LocalAccount"].str.match(LKS_RM_PATTERN)
b["is_rm_legacy"] = b["LocalAccount"].str.match(LKS_LEGACY_RM_PATTERN)
b["in_q3"] = b["PostingDate"].between(PERIOD_START, PERIOD_END)

fx = pd.read_csv(DATA / "fx_cad_usd.csv", dtype={"Month": str})
fx_avg = fx.set_index("Month")["AvgRate_CADtoUSD"]
fx_me = fx.set_index("Month")["MonthEndRate_CADtoUSD"]

sub = b["JournalID"].eq("SUBTOTAL")
detail = b[~sub].copy()
# subtotal integrity check: each subtotal = sum of the detail lines of that account-month
detail["Month"] = detail["PostingDate"].dt.strftime("%Y-%m")
chk = detail.groupby(["LocalAccount", "Month"])["AmountCAD"].sum().round(2)
st = b[sub].assign(Month=b.loc[sub, "Description"].str[-7:]).set_index(["LocalAccount", "Month"])["AmountCAD"]
assert (chk.reindex(st.index).round(2) == st.round(2)).all(), "Subtotal does not tie to detail"

sub_rm_q3 = b[sub & b.is_rm & b.in_q3]
log("Embedded SUBTOTAL rows per account-month (JournalID='SUBTOTAL', Source blank) - would double count",
    "JournalID value scan, Description 'Subtotal ...'; each subtotal re-computed from detail and ties exactly",
    "Drop SUBTOTAL rows; use detail only",
    sub.sum(), sum(cents(sub_rm_q3.loc[sub_rm_q3.PostingDate.dt.month == m, "AmountCAD"].sum() * fx_avg[f"2026-{m:02d}"]) for m in (7, 8, 9)),
    "ERP-B")


def lks_usd(df, rates=fx_avg):
    df = df.assign(Month=df["PostingDate"].dt.strftime("%Y-%m"))
    return sum(cents(v * rates[m]) for m, v in df.groupby("Month")["AmountCAD"].sum().items())


d_rm_q3 = detail[detail.is_rm & detail.in_q3]
log("AmountCAD stored as text with thousands separator ('4,581.93') in 18 cells; Excel reads them as strings",
    "pd.to_numeric(errors='coerce') returns NaN for 18 rows; openpyxl cell types show str",
    "Remove ',' and cast to float",
    txt_amt.sum(), lks_usd(detail[txt_amt.loc[~sub] & detail.is_rm & detail.in_q3]), "ERP-B")
log("PostingDate stored as text in DD/MM/YYYY",
    "Pattern profile; day part up to 31",
    "Parse with format %d/%m/%Y",
    len(b), 0.0, "ERP-B")
hyd = d_rm_q3[d_rm_q3.LocalAccount == "7335-RM-HYD"]
log("Local account 7335-RM-HYD (Hydraulic repairs, first used Sep-2026) missing from Accounting's account map",
    "Anti-join GL LocalAccount vs account_map_lakeshore.csv",
    "Rule 6: every 73xx-RM account is R&M -> included (map to 6150 recommended)",
    len(hyd), lks_usd(hyd), "ERP-B")
oop_b = detail[detail.is_rm & ~detail.in_q3]
log(f"Lakeshore R&M lines posted outside Q3 (late Jun, Oct) - CAD {oop_b.AmountCAD.sum():,.2f}, no Q3 rate applies",
    "PostingDate range",
    "Rule 3: exclude (USD shown as 0 because no Q3 translation applies)",
    len(oop_b), 0.0, "ERP-B")
nonrm_b = detail[~detail.is_rm & detail.in_q3]
log("Lakeshore non-R&M accounts (5100-LAB, 7400-FUEL)",
    "LocalAccount vs 73xx-RM pattern",
    "Rule 1/6: exclude (7500-FLUID is R&M: maps to group 6300)",
    len(nonrm_b), lks_usd(nonrm_b), "ERP-B")

b.to_sql("stg_erp_b", engine, if_exists="replace", index=False)
lks_cad_m = d_rm_q3.groupby(d_rm_q3["PostingDate"].dt.strftime("%Y-%m"))["AmountCAD"].sum()
lks_bm = pd.Series({("LKS", m): cents(v * fx_avg[m]) for m, v in lks_cad_m.items()})  # Rule 5: translate month total, then round

# ----------------------------------------------------------------------------------------------
# 3. Output
# ----------------------------------------------------------------------------------------------
rows = [{"Branch": br, "Month": m, "RM_USD": cents(v)} for (br, m), v in erp_a_bm.items()]
rows += [{"Branch": br, "Month": m, "RM_USD": v} for (br, m), v in lks_bm.items()]
order = {"RIV": 0, "OAK": 1, "PIN": 2, "ELM": 3, "LKS": 4}
rpt = pd.DataFrame(rows).sort_values(["Branch", "Month"], key=lambda s: s.map(order) if s.name == "Branch" else s)
rpt["RM_USD"] = rpt["RM_USD"].map(lambda d: f"{d:.2f}")
rpt.to_csv(OUT / "rm_q3_by_branch.csv", index=False)
rpt.to_sql("rpt_rm_branch_month", engine, if_exists="replace", index=False)
fa.to_sql("fct_rm_line", engine, if_exists="replace", index=False)

finance_total = sum(Decimal(v) for v in rpt["RM_USD"])
pd.DataFrame(dq).to_csv(OUT / "dq_log.csv", index=False)

print(rpt.to_string(index=False))
print(f"\nFinance Q3 R&M total : {finance_total:,.2f}")
print(f"Accounting total     : {ACCOUNTING_TOTAL:,.2f}")
print(f"Gap (Acct - Finance) : {ACCOUNTING_TOTAL - finance_total:,.2f}")

# ----------------------------------------------------------------------------------------------
# 4. Reconciliation Finance -> Accounting (each item computed from source, must close to the cent)
# ----------------------------------------------------------------------------------------------
je = pd.read_csv(DATA / "accounting_je_register_sep_close.csv", dtype={"Account": str})
je["PostedAt"] = pd.to_datetime(je["PostedAt"])
# Accounting's close total (14,120,360.57) was built on the OLD R&M definition (no 6300 / 7500-FLUID),
# so the JE test and the FX item are evaluated on that basis; the definition change is its own bridge item.
je["is_rm"] = je["Account"].isin(LEGACY_RM_GROUP_ACCOUNTS) | je["Account"].str.match(LKS_LEGACY_RM_PATTERN)
je["in_close"] = je["PostedAt"] <= pd.Timestamp("2026-09-30 23:59:59")
je_rm_close = je[je.is_rm & je.in_close]                                   # -> JE-0931 only

d_legacy_q3 = detail[detail.is_rm_legacy & detail.in_q3]
lks_cad_legacy_m = d_legacy_q3.groupby(d_legacy_q3["PostingDate"].dt.strftime("%Y-%m"))["AmountCAD"].sum()
sep_cad = lks_cad_legacy_m["2026-09"]
fx_item = cents(sep_cad * fx_me["2026-09"]) - cents(sep_cad * fx_avg["2026-09"])

# 6300 definition change: ERP-A 6300 lines + Lakeshore 7500-FLUID (translated month total, then rounded)
fluid_a = sum(cents(v) for v in fa[fa["Acct"] == "6300"].groupby(["Branch", "Month"])["ExtCost"].sum())
fluid_lks = sum(cents(v * fx_avg[m]) for m, v in lks_cad_m.items()) \
    - sum(cents(v * fx_avg[m]) for m, v in lks_cad_legacy_m.items())

bridge = [("Finance Q3 R&M (ERP data, README rules, incl. 6300 Fluids & lubricants)", finance_total),
          ("Less: 6300 Fluids & lubricants ERP-A lines (R&M per Finance, not in Accounting total)", -fluid_a),
          ("Less: Lakeshore 7500-FLUID (group 6300) translated at avg rate (not in Accounting total)", -fluid_lks)]
for r in je_rm_close.itertuples():
    bridge.append((f"{r.JE_ID} {r.Branch} {r.Account} manual JE: {r.Description}", cents(r.Amount)))
bridge.append((f"LKS Sep-2026 translated at month-end {fx_me['2026-09']} instead of avg {fx_avg['2026-09']} "
               f"(CAD {sep_cad:,.2f})", fx_item))
recon_total = sum(v for _, v in bridge)
bridge.append(("Accounting Q3 R&M (close summary)", recon_total))
assert recon_total == ACCOUNTING_TOTAL, f"Bridge does not close: {recon_total} vs {ACCOUNTING_TOTAL}"
pd.DataFrame(bridge, columns=["Item", "USD"]).assign(USD=lambda d: d.USD.map(lambda v: f"{v:.2f}")) \
    .to_csv(OUT / "reconciliation.csv", index=False)
print("\nBridge:")
for k, v in bridge:
    print(f"  {v:>16,.2f}  {k}")
print("  Bridge closes to the cent: OK")

# export key components for ad-hoc analysis
COMPONENTS = dict(a=a, raw_a=raw_a, fa=fa, dups=dups, b=b, detail=detail, fx_avg=fx_avg, fx_me=fx_me,
                  lks_cad_m=lks_cad_m, finance_total=finance_total, rpt=rpt)

# Northline Q3 2026 R&M — Take-Home Submission

Run: `python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python src/build_rm_q3.py`
Outputs land in `output/` (`rm_q3_by_branch.csv`, `dq_log.csv`, `reconciliation.csv`, `northline_rm.db`).
The script asserts every cleaning invariant and fails if the bridge to Accounting does not close to the cent.

---

## Part 1 — Q3 R&M by branch and month (`output/rm_q3_by_branch.csv`)

| Branch | Month | RM_USD |
|---|---|---|
| RIV | 2026-07 | 1522852.26 |
| RIV | 2026-08 | 1540190.70 |
| RIV | 2026-09 | 1326944.21 |
| OAK | 2026-07 | 1310446.01 |
| OAK | 2026-08 | 1478995.18 |
| OAK | 2026-09 | 1579502.88 |
| PIN | 2026-07 | 1145925.90 |
| PIN | 2026-08 | 1350571.29 |
| PIN | 2026-09 | 1209270.36 |
| ELM | 2026-07 | 268050.55 |
| LKS | 2026-07 | 400642.79 |
| LKS | 2026-08 | 475554.34 |
| LKS | 2026-09 | 481282.49 |
| **Total** | | **14090228.96** |

ELM has July only (Rule 4). LKS = monthly CAD total × monthly average rate, then rounded (Rule 5):
Jul 547,326.22 × 0.7320; Aug 655,484.96 × 0.7255; Sep 667,520.79 × 0.7210.

**Decisions on ambiguous points**
- Slash dates in ERP-A are read as **MM/DD/YYYY**. Evidence: 355 of 551 have a second part > 12, none have a first part > 12, and reading them as DD/MM would put 49 lines after the export they came from.
- Duplicate key = **SRO + Line**. After normalisation, every repeated key has identical content. The first export (19-Sep) is kept.
- **7335-RM-HYD** counts as R&M under Rule 6 even though Accounting's map does not list it.
- A JE posted after 30-Sep 23:59 (JE-0940, posted 02-Oct) is not treated as part of the September close.

---

## Part 2 — Data quality log

"USD" is the Finance-scope R&M value (Q3, R&M account, not Oper 75/100) on the affected rows. LKS values are in USD at the average rate. For format issues it is the value that would be lost or misallocated without the fix.

| # | Src | Issue | Detection | Rule applied | Rows | USD |
|---|---|---|---|---|---|---|
| 1 | A | `Whse` typed 14 ways for 4 branches: case, leading/trailing spaces, full names (`riv`, `Riverton `, ` OAK`, `Elmwood`) | `value_counts()` on Whse | TRIM + UPPER + alias map → RIV/OAK/PIN/ELM; assert no unmapped values | 5,676 | 6,660,857.44 |
| 2 | A | `Acct` stored as float text (`6100.0`) or with a leading space (` 6100`): 21 values for 7 accounts | `value_counts()` on Acct | TRIM, strip `.0`, assert 4-digit code (without this, the R&M filter drops these lines) | 1,434 | 1,748,352.41 |
| 3 | A | `TransDate` mixes ISO `YYYY-MM-DD` and US `MM/DD/YYYY` | Regex pattern profile; second part > 12 on 355 rows; DD/MM gives 49 dates after their export | Parse slash dates as MM/DD; assert TransDate ≤ ExportedAt on every row | 551 | 695,089.29 |
| 4 | A | **Overlapping export**: 2026-09-21 06:00 (a Monday re-run) repeats all 719 lines of the 2026-09-19 export (12–18 Sep), reformatted | `duplicated(SRO, Line)` = 1,438 rows; min/max TransDate per ExportedAt shows two exports covering the same week | Keep first occurrence; drop 719 copies; assert SRO+Line unique | 719 | 1,043,815.38 |
| 5 | A | Lines dated outside Q3: 870 from 20–30 Jun, 552 from 1–3 Oct | TransDate range per ExportedAt | Rule 3: keep 1-Jul–30-Sep by TransDate | 1,422 | 2,001,671.99 |
| 6 | A | Non-R&M accounts (5100, 6300, 6400, 6500) | Acct after cleaning | Rule 1: keep 6100/6150/6200 only | 3,439 | 3,469,243.55 (excluded) |
| 7 | A | R&M lines on Oper 75/100 (capitalised rebuilds): Op 75 = 250,478.27, Op 100 = 158,920.28 | Filter Oper ∈ {75,100} on R&M accounts | Rule 2: exclude | 174 | 409,398.55 |
| 8 | A | R&M lines with a customer number (C1044/C2087/C3310), which look like billable work | Non-blank CustNum on 6100/6150/6200 | Rule 1: account decides; **kept** (value 7,670,610.69) | 3,480 | 0.00 (no change) |
| 9 | A | Reversals (negative Qty/ExtCost). Each one pairs 1:1 with an original line in Q3. Some cross months (−2,126.07 moves between months) | Qty < 0; Qty×UnitCost = ExtCost checked on every row (0 mismatches) | Rule 3: **kept**, in the month of their own date (−156,212.59) | 60 | 0.00 (no change) |
| 10 | A | Elmwood lines dated on/after 1-Aug (after closure) | Branch = ELM & TransDate ≥ 2026-08-01 | Rule 4: reassign to OAK (total unchanged) | 209 (126 R&M) | 265,830.48 moved ELM→OAK |
| 11 | B | Embedded **SUBTOTAL** rows per account-month (JournalID = `SUBTOTAL`, Source blank). They double the ledger (CAD 3,365,648.40 detail + 3,365,648.40 subtotals) | JournalID scan; each subtotal recomputed from detail and asserted to tie | Drop; use detail only | 31 | 1,357,479.62 (would double-count) |
| 12 | B | `AmountCAD` stored as text with a thousands separator (`4,581.93`). A naive numeric read loses CAD 51,965.23 | `pd.to_numeric(errors='coerce')` → 18 NaN; openpyxl cell type `str` | Remove `,`, cast to float | 18 | 37,724.23 |
| 13 | B | `PostingDate` stored as text in DD/MM/YYYY (opposite of ERP-A) | Pattern profile; day part up to 31 | Parse `%d/%m/%Y` | 1,504 | 0.00 |
| 14 | B | **7335-RM-HYD** (Hydraulic repairs, new in Sep-2026, CAD 108,339.06) is missing from `account_map_lakeshore.csv` | Anti-join GL LocalAccount vs map | Rule 6: 73xx-RM is R&M → included. Accounting should add it to the map (suggest 6150) | 23 | 78,112.46 |
| 15 | B | LKS R&M lines posted outside Q3 (CAD 241,785.24) | PostingDate range | Rule 3: exclude | 103 | 0.00 (no Q3 rate) |
| 16 | B | LKS non-R&M accounts (5100-LAB, 7400-FUEL, 7500-FLUID) | Account vs `^73\d\d-RM` | Rules 1/6: exclude | 513 | 825,137.72 (excluded) |
| 17 | JE | Manual JEs: 7 in the register; only 2 are on R&M accounts (JE-0931 +18,450.00; JE-0940 −640.00 posted 02-Oct) | Account filter + PostedAt | Rule 7: not an input; reconciling items | 7 | see Part 3 |

**Raw → Finance waterfall**

| Step | ERP-A (USD) | ERP-B (CAD) |
|---|---|---|
| Raw file total (all rows; ERP-B with text amounts parsed) | 20,522,808.31 | 6,731,296.80 |
| − Duplicate export 2026-09-21 / − SUBTOTAL rows | −1,340,417.68 | −3,365,648.40 |
| − Outside Q3 | −2,570,999.19 | −359,401.75 |
| − Non-R&M accounts | −3,469,243.55 | −1,135,914.68 |
| − Oper 75/100 capitalised rebuilds | −409,398.55 | n/a |
| = Q3 R&M | **12,732,749.34** | **1,870,331.97** |
| Translated at monthly average, rounded per month | 12,732,749.34 | **1,357,479.62** |
| **Finance Q3 R&M** | **14,090,228.96** | |

---

## Part 3 — Reconciliation to Accounting

| # | Item | USD | Running total |
|---|---|---|---|
| | **Finance Q3 R&M (Part 1)** | | **14,090,228.96** |
| 1 | JE-0931: RIV 6150 manual accrual, "outside hydraulic cylinder repair, invoice pending", posted 30-Sep 22:14 | +18,450.00 | 14,108,678.96 |
| 2 | LKS Sep-2026 translated at **month-end 0.7385** instead of **average 0.7210**: CAD 667,520.79 × 0.0175 (492,964.10 − 481,282.49) | +11,681.61 | 14,120,360.57 |
| | **Accounting Q3 R&M (close summary)** | | **14,120,360.57** ✅ difference 0.00 |

**Item 1: JE-0931 accrual (+18,450.00). Finance's number is right under the reporting rules; the accrual is a timing item.**
- Evidence: the JE is not in ERP-A. No ERP-A line comes near 18,450 (the largest 6150 line in the file is 6,489.99). It was posted at 22:14, after the last export of the quarter (30-Sep 18:00). Rule 7 says manual JEs are not an input.
- Judgment: an accrual for September work with the invoice still pending can be valid GL accounting. It is still a manual estimate that the ERP does not support.
- What should change and where: Accounting attaches support (PO or vendor quote) and sets JE-0931 to auto-reverse on 1-Oct. Otherwise, when the invoice posts in ERP-A in October, the cost is counted twice. In the platform, the JE register becomes a separate, flagged "manual adjustments" layer shown next to the ERP-built number, never mixed into it.

**Item 2: FX rate (+11,681.61). Finance is right.**
- Evidence: Rule 5 / group policy says P&L is translated at the monthly average. September's average is 0.7210 and its month-end rate is 0.7385. July and August tie at the average, so only September used the balance-sheet rate. Recomputing Sep at month-end closes the bridge to the cent, which strongly suggests Accounting used the wrong column of `fx_cad_usd.csv` for September.
- What should change and where: Accounting re-translates Lakeshore September P&L at 0.7210 in the close workbook. The −11,681.61 adjustment brings Accounting to 14,108,678.96, or 14,090,228.96 if JE-0931 is also treated as a separate adjustment. In the platform, the rate type is chosen by statement type (P&L → AvgRate, BS → MonthEndRate) and is not left to the user.

**Checked and confirmed not to be reconciling items**
- JE-0940 (PIN 6100 −640.00): posted 02-Oct, after the September close. The bridge closes without it. It belongs to October.
- JE-0918/0921/0925/0927/0933: on non-R&M accounts (6400, 5100, 6500, 7400-FUEL, 6300). No R&M effect.
- 7335-RM-HYD: Accounting's map omits it, but the bridge only closes to the cent with Sep CAD 667,520.79 *including* HYD. So Accounting picked it up manually. The map still needs fixing (DQ #14).
- Duplicates, Oper 75/100, reversals, customer lines and the ELM→OAK split: no difference. Accounting's ERP-A total equals Finance's 12,732,749.34.

---

## Part 4 — Design memo

**To:** CFO, Northline · **From:** Data Platform Engineering · **Re:** Automating group R&M reporting · **Oct-2026**

**Recommendation.** Use a small, code-first platform: **Python (pandas + SQLAlchemy) jobs → Azure SQL Database → Power BI semantic model**, scheduled and version-controlled in Git. The volume is a few tens of thousands of rows a week across 6 entities and 4 ERPs, so this is not a big-data problem and Spark or a lakehouse cluster would be oversized. SQLAlchemy gives one connection layer for all four ERP databases (SQL Server, Oracle, ODBC sources) and for the target. pandas covers the cleaning shown in this exercise. The Q3 build runs in seconds.

**1. Getting the data out (ingestion from backups).** The main ERP locks tables when queried live, so we never touch production. IT restores the nightly backup to a **read-only staging instance** (e.g. Azure SQL Managed Instance or a small VM). At 02:00 a Python job extracts incrementally by watermark (`TransDate`/`ModifiedAt`, plus a full re-pull of the open period). It lands raw tables unchanged with `_source`, `_backup_date` and `_loaded_at` columns, and de-duplicates on the natural key (SRO+Line). This removes the overlapping-export problem we found (719 duplicated lines, $1.04M of R&M). ERPs that can't be restored push scheduled CSV/Excel exports to SharePoint, and the same job picks them up through Microsoft Graph.

**2. Where the rules live and who owns them.** Business rules are kept separate from code:
- **Finance owns *what***: R&M accounts, capex operations, branch closures/re-mapping, FX rate type and the local-account maps. These live in a versioned `rules.yaml` and mapping tables in Git. Changes go through a pull request that the Finance Controller must approve.
- **Engineering owns *how***: parsing, de-duplication, type and format fixes, tests and scheduling.
- Every run publishes a DQ log and an automatic bridge to the GL. Gaps over $1 open a ticket. Today, two undocumented decisions (a month-end rate and a manual accrual) created a $30,131.61 gap that nobody could explain.

**3. AI readiness (semantic layer for LLMs).** AI answers come from the governed **Power BI semantic model** (`fct_rm_line`, `dim_branch`, `dim_account`, `dim_date`, `fx_rate`). Measures such as `[R&M USD]` are defined once, with descriptions and synonyms. Copilot for Power BI and any internal LLM agent query that model (through XMLA/DAX or a read-only SQL view), not raw tables, so the AI gives the same number as the report. Row-level security and lineage (rule version + load id on every row) make answers auditable.

**4. Rough monthly cost.**
| Item | Est. USD/month |
|---|---|
| Azure SQL Database (General Purpose, serverless, 2 vCore max) | 150–300 |
| Backup restore target (SQL MI/VM, part-time) | 150–400 |
| Azure Container Apps Jobs / Functions (Python schedule) + Key Vault + Monitor | 20–60 |
| Power BI Pro (existing M365 licences; ~10 creators) | ~140 |
| **Total** | **≈ 460–900** |
These are list-price estimates to validate with IT. Moving the model to Fabric F2 (≈ $260/month) is optional later, if Copilot is wanted.

**5. First 30 days (MVP).**
- **Week 1:** Nightly restore with IT; read-only service account; raw landing for ERP-A and ERP-B; Git repo and CI.
- **Week 2:** Move this exercise's rules into `rules.yaml` with Finance sign-off; DQ tests (key uniqueness, date ≤ export, subtotal tie-out, unmapped accounts).
- **Week 3:** Power BI semantic model and R&M report; automated bridge to the GL with manual JEs shown as a separate layer.
- **Week 4:** Run in parallel against the Q3 close; onboard ERP #3; runbook and handover. Exit criterion: zero unexplained difference to Accounting for two consecutive closes.

---

## Part 5 — AI usage log

**Tools.** Antigravity IDE agent (Claude Opus model): file exploration, data profiling, code generation, running the pipeline and reconciliation search, drafting this write-up. `pypdf` to read the brief PDF. pandas/openpyxl to profile and check the raw files (cell types, hidden rows/sheets, formulas). No data left the local machine apart from what was sent to the assistant.

**Useful prompts / instructions.**
1. *"Profile every column of each raw file: distinct values, regex pattern of dates, non-numeric amounts, duplicates on SRO+Line with and without ExportedAt, and min/max TransDate per ExportedAt."* This surfaced the 09-21 re-pull, the mixed date formats and the text amounts in one pass.
2. *"Compute each candidate reconciling item independently from source (JEs on R&M accounts, LKS at month-end per month, 7335 exclusion, Oper 75/100, reversals, duplicates) and find the combination that closes the gap to the cent."*
3. *"Make the pipeline assert its own invariants (no unmapped Whse, 4-digit accounts, TransDate ≤ ExportedAt, SRO+Line unique, subtotals tie to detail, bridge = 14,120,360.57)."*

**What the AI got wrong or missed, and how it was caught.**
- *De-duplication.* A plain `drop_duplicates()` on raw columns finds only **168** duplicate copies, because the 09-21 re-pull re-typed Whse/Acct/date formats. Comparing on `SRO+Line` showed **719**. Removing duplicates only *after* normalisation, and asserting that SRO+Line is unique, fixed it. A naive version would have overstated R&M by **$807,313.59** (checked: 551 duplicates left in).
- *Wrong hypothesis on 7335-RM-HYD.* The first assumption was that Accounting excluded 7335-RM-HYD because it is missing from their map (−78,112.46). The independent component search disproved this: the only exact closure is JE-0931 + Sep month-end FX *with* HYD included. The write-up was corrected to say Accounting included it.
- *Date ambiguity.* `pd.to_datetime(format="mixed")` would silently guess. The choice of MM/DD was proven with the export-date check (DD/MM → 49 impossible dates), not assumed.

**Share of AI-written code.** About **95%** of the code and text was generated by the AI assistant. The human role was to direct, review the logic against the README rules, and check that every figure in this document comes from the script output (`output/*.csv`).

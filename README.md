# Eagle Eye — Gulf County Public Record Search
# Powered by Heron Media Group, Mexico Beach, FL

A public-facing search portal for Gulf County, Florida government records.

## Features
- 🔍 Google-style search across BOCC meetings, officials, and contracts
- 💬 Ask Eagle Eye — ChatGPT-style plain English questions answered from public record
- ⚖️ Legal Flags — documented concerns with full analysis
- 👥 Commissioner profiles with conflict alerts
- 📋 Full meeting minutes browser

## Powered by Eagle Eye v21.6
Built by Heron Media Group. All data sourced from publicly available Gulf County government records under Florida FS 119.

## Pipeline (scripts/)

Run in this order to go from nothing to a fully populated local instance:

1. `fetch_minutes.py` — scrapes the Gulf County CivicLive document center
   (`gulfcounty-fl.gov/meeting/minutes`) via its internal
   `ContentItemSvc.asmx/GetItemList` JSON API and downloads every BOCC
   meeting minutes PDF, 2017–2026 (272 documents as of Nov 2026), into
   `data/raw_minutes/<year>/`. Writes `data/minutes_index.json`.
2. `extract_minutes_text.py` — runs each PDF through pdfplumber and saves
   plain text to `data/minutes_text/<year>/`.
3. `crossindex_officials.py` — for each official in its `OFFICIALS` dict,
   finds every meeting mention and flags passages that fall near
   ethics/conflict-relevant keywords. Splits flags into `routine_disclosure`
   (abstain/recusal/Form 8B — normal FS 112.3143 compliance, NOT a concern)
   vs `concern` (sole source, no-bid, undisclosed interest, family/vendor
   ties, etc.). Writes `data/officials/<slug>/{profile.md,mentions.json,
   flagged_for_review.json}`.
4. `extract_law_text.py` — converts downloaded Florida Statutes/Constitution
   HTML (Ch. 112 Ethics, 286.011 Sunshine Law, 119.07 Public Records,
   Ch. 125 County Government, Ch. 164 Conflict Resolution, Art. VIII Fla.
   Const.) into clean text in `data/florida_law/`.
5. `build_db.py` — assembles everything above into `data/bocc.db`, the
   SQLite database `public_app.py` reads from (schema: `meetings`,
   `knowledge_base`, `legal_refs`, `legal_flags`).

## IMPORTANT — what the flags actually mean

Every `flag_level` / `legal_flags` entry comes from **naive keyword
proximity matching** (a name and a keyword appearing within ~1500
characters of each other in a scraped PDF's text). This is a **lead
list for a human researcher**, not an adjudicated finding. It does NOT
mean:
- the official did anything wrong,
- the keyword match is even about that official,
- or that the excerpt means what it appears to mean out of context.

Routine, legally-required disclosure language (recusal, abstention, Form
8B filings) is tracked separately from genuinely concern-worthy language
(undisclosed interests, no-bid awards, family/vendor ties) specifically so
an official who is correctly following the law isn't shown with the same
flag as one with an actual red flag. **Read the source PDF before treating
any flagged passage as a finding, let alone publishing it.**

## Deployment
Set the `DB_PATH` environment variable to the path of the bocc.db database file.

### Local testing
```bash
python3 -m pip install -r requirements.txt
python3 scripts/fetch_minutes.py
python3 scripts/extract_minutes_text.py
python3 scripts/crossindex_officials.py
python3 scripts/extract_law_text.py   # requires raw_*.html already downloaded — see script
python3 scripts/build_db.py
export DB_PATH="$(pwd)/data/bocc.db"   # Windows: use C:/forward/slash/form, not /c/msys/form
python3 -m flask --app public_app run --port 5050
```


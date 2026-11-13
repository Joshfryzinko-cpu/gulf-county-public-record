#!/usr/bin/env python3
"""
Build bocc.db — the SQLite database public_app.py (Eagle Eye) reads from.

Tables:
  meetings      — one row per scraped BOCC meeting minutes PDF (272 rows)
  knowledge_base — one row per official (elected + appointed), summarizing
                   mention counts and any flagged-for-review passages
  legal_refs    — Florida statutes / constitution articles we pulled, for
                   citation and the "what is FS ___" answer path
  legal_flags   — individual flagged passages, linked to a meeting + a
                   legal_ref (best-effort mapping), for the /flags page

IMPORTANT: flag_level / flag_reason here are derived from naive keyword
proximity matching (see crossindex_officials.py). They are leads for a
human researcher, not adjudicated findings. Nothing here should be
presented publicly as a proven conflict of interest without a person
reading the underlying PDF.
"""
import os, json, sqlite3, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "bocc.db")
INDEX_PATH = os.path.join(ROOT, "data", "minutes_index.json")
OFFICIALS_DIR = os.path.join(ROOT, "data", "officials")
LAW_DIR = os.path.join(ROOT, "data", "florida_law")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_date TEXT,
    title TEXT,
    meeting_type TEXT,
    year TEXT,
    minutes_text TEXT,
    minutes_status TEXT,
    source_pdf TEXT,
    source_url TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT,
    sub_category TEXT,   -- elected_bocc | elected_const | appointed_admin | appointed_legal | appointed_dept
    detail TEXT,
    flag_level TEXT,      -- none | watch | significant | critical
    flag_reason TEXT
);

CREATE TABLE IF NOT EXISTS legal_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cite TEXT,
    title TEXT,
    summary TEXT,
    full_text_path TEXT
);

CREATE TABLE IF NOT EXISTS legal_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER,
    legal_ref_id INTEGER,
    subject TEXT,
    description TEXT,
    severity TEXT,        -- watch | significant | critical
    FOREIGN KEY (meeting_id) REFERENCES meetings(id),
    FOREIGN KEY (legal_ref_id) REFERENCES legal_refs(id)
);

CREATE INDEX IF NOT EXISTS idx_meetings_date ON meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_kb_subcat ON knowledge_base(sub_category);
"""

LEGAL_REFS = [
    ("FS 112 Part III", "Code of Ethics for Public Officers and Employees",
     "Standards of conduct for public officers/employees: conflicting "
     "employment/contracts, disclosure of financial interests, voting "
     "conflicts (112.3143), gift limits, and post-employment restrictions.",
     "data/florida_law/ch112_ethics.txt"),
    ("FS 286.011", "Government in the Sunshine Law",
     "Requires that meetings of any board or commission of a county be "
     "open to the public, with reasonable notice, and minutes promptly "
     "recorded and open to inspection.",
     "data/florida_law/286_011_sunshine_law.txt"),
    ("FS 119.07", "Public Records — Inspection, Examination, Copying",
     "Custodians of public records must permit inspection and copying of "
     "any public record not exempted by law, during normal business hours.",
     "data/florida_law/119_07_public_records.txt"),
    ("FS 125", "County Government",
     "General powers of county government, including the board of county "
     "commissioners' authority to adopt ordinances, budgets, and contracts.",
     "data/florida_law/ch125_county_government.txt"),
    ("FS 164", "Florida Governmental Conflict Resolution Act",
     "Establishes a conflict assessment/mediation process local governments "
     "must use before filing suit against each other over a dispute.",
     "data/florida_law/ch164_conflict_resolution.txt"),
    ("Art. VIII, Fla. Const.", "Local Government",
     "Establishes counties as political subdivisions; five elected "
     "constitutional officers (sheriff, tax collector, property appraiser, "
     "supervisor of elections, clerk of circuit court); non-charter county "
     "commissioners elected by district.",
     "data/florida_law/constitution_article_viii.txt"),
]

SUBCAT_MAP = {
    "elected": "elected_bocc",
    "elected (constitutional officer)": "elected_const",
    "appointed": "appointed_admin",
    "appointed (contracted)": "appointed_legal",
}

def flag_level_from_counts(mention_count, concern_count):
    """Severity is driven ONLY by concern-worthy flags, never routine
    disclosure language (abstaining/recusing is compliance, not a problem)."""
    if concern_count == 0:
        return "none"
    ratio = concern_count / max(mention_count, 1)
    if concern_count >= 15 or ratio > 0.25:
        return "significant"
    if concern_count >= 3:
        return "watch"
    return "watch"


def build_meetings(conn):
    with open(INDEX_PATH, encoding="utf-8") as f:
        index = json.load(f)
    rows = 0
    for rec in index:
        text_path = os.path.join(ROOT, rec.get("text_path", ""))
        text = ""
        if os.path.exists(text_path):
            with open(text_path, encoding="utf-8") as f:
                text = f.read()
        conn.execute(
            "INSERT INTO meetings (meeting_date, title, meeting_type, year, "
            "minutes_text, minutes_status, source_pdf, source_url) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rec.get("date"), rec.get("name"), rec.get("meeting_type"),
             rec.get("year"), text, "complete" if text else "missing_text",
             rec.get("local_path"), rec.get("url")),
        )
        rows += 1
    conn.commit()
    return rows


def build_legal_refs(conn):
    for cite, title, summary, path in LEGAL_REFS:
        conn.execute(
            "INSERT INTO legal_refs (cite, title, summary, full_text_path) VALUES (?,?,?,?)",
            (cite, title, summary, path),
        )
    conn.commit()


def build_officials_and_flags(conn):
    legal_ref_rows = conn.execute("SELECT id, cite FROM legal_refs").fetchall()
    ethics_ref_id = next((r[0] for r in legal_ref_rows if "112" in r[1]), None)

    meeting_lookup = {}
    for mid, mdate, title in conn.execute("SELECT id, meeting_date, title FROM meetings"):
        meeting_lookup[(mdate, title)] = mid

    kb_count = 0
    flag_count = 0
    for slug in sorted(os.listdir(OFFICIALS_DIR)):
        folder = os.path.join(OFFICIALS_DIR, slug)
        profile_path = os.path.join(folder, "profile.md")
        mentions_path = os.path.join(folder, "mentions.json")
        flagged_path = os.path.join(folder, "flagged_for_review.json")
        if not os.path.exists(profile_path):
            continue
        with open(profile_path, encoding="utf-8") as f:
            profile_lines = f.read().splitlines()
        name = profile_lines[0].lstrip("# ").strip()
        role_line = next((l for l in profile_lines if l.startswith("**Role:**")), "")
        role = role_line.replace("**Role:**", "").strip()
        type_line = next((l for l in profile_lines if l.startswith("**Type:**")), "")
        otype = type_line.replace("**Type:**", "").strip()
        sub_category = SUBCAT_MAP.get(otype, "appointed_dept")

        mentions = json.load(open(mentions_path, encoding="utf-8")) if os.path.exists(mentions_path) else []
        flagged = json.load(open(flagged_path, encoding="utf-8")) if os.path.exists(flagged_path) else []
        concern_flags = [fl for fl in flagged if fl.get("category") == "concern"]
        routine_flags = [fl for fl in flagged if fl.get("category") == "routine_disclosure"]

        level = flag_level_from_counts(len(mentions), len(concern_flags))
        top_reason = ""
        if concern_flags:
            kw_counts = {}
            for fl in concern_flags:
                kw_counts[fl["keyword"]] = kw_counts.get(fl["keyword"], 0) + 1
            top_kw = max(kw_counts, key=kw_counts.get)
            top_reason = (f"{len(concern_flags)} concern-category passages (plus {len(routine_flags)} "
                           f"routine abstain/recusal disclosures, which are normal compliance, not flags) "
                           f"across {len(mentions)} meeting mentions. Most common concern keyword: "
                           f"\"{top_kw}\" ({kw_counts[top_kw]}x). "
                           f"UNVERIFIED — proximity match only, requires manual review of each source PDF.")
        elif routine_flags:
            top_reason = (f"No concern-category flags. {len(routine_flags)} routine abstain/recusal/"
                           f"disclosure mentions found \u2014 this reflects normal FS 112.3143 compliance.")

        detail = f"{role}. Appears in {len(mentions)} of 272 scraped BOCC meeting minutes (2017-2026)."

        cur = conn.execute(
            "INSERT INTO knowledge_base (subject, sub_category, detail, flag_level, flag_reason) "
            "VALUES (?,?,?,?,?)",
            (name, sub_category, detail, level, top_reason),
        )
        kb_count += 1

        for fl in concern_flags[:25]:  # cap per-official to keep the flags table sane
            mid = meeting_lookup.get((fl["date"], fl["meeting"]))
            conn.execute(
                "INSERT INTO legal_flags (meeting_id, legal_ref_id, subject, description, severity) "
                "VALUES (?,?,?,?,?)",
                (mid, ethics_ref_id, name,
                 f"Keyword \"{fl['keyword']}\" near a mention of {name}: {fl['excerpt_near_keyword']}",
                 "watch"),
            )
            flag_count += 1

    conn.commit()
    return kb_count, flag_count


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    n_meetings = build_meetings(conn)
    print(f"meetings: {n_meetings}")

    build_legal_refs(conn)
    print(f"legal_refs: {len(LEGAL_REFS)}")

    n_kb, n_flags = build_officials_and_flags(conn)
    print(f"knowledge_base: {n_kb}")
    print(f"legal_flags: {n_flags}")

    conn.close()
    print(f"Database written to {DB_PATH}")


if __name__ == "__main__":
    main()

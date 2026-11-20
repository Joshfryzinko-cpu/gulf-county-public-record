"""
Officials directory + topic keyword expansion + report-card builder for
Eagle Eye's public "name + topic" search.

This sits on top of:
- knowledge_base (existing table: name, role category, flags)
- votes (new table from scripts/extract_votes.py: per-motion, per-commissioner
  verified vote records)
"""
import re
import difflib

# ── Directory: office / governing body / voting authority ──────────────────
# Extends knowledge_base's sub_category with the fields the brief asks for
# (office, governing body, dates of service). Dates of service are the one
# field we genuinely don't have a source for yet -- left None rather than
# guessed, and surfaced as a gap on the report card.
DIRECTORY = {
    "elected_bocc": {
        "office": "County Commissioner", "governing_body": "Gulf County Board of County Commissioners (BOCC)",
        "has_voting_authority_on": "BOCC motions", "surname_key": True,
    },
    "elected_const": {
        "office": "Constitutional Officer", "governing_body": "Gulf County (independently elected)",
        "has_voting_authority_on": None, "surname_key": False,
    },
    "appointed_admin": {
        "office": "County Administration (appointed)", "governing_body": "Gulf County BOCC (appointing authority)",
        "has_voting_authority_on": None, "surname_key": False,
    },
    "appointed_legal": {
        "office": "County Legal Counsel (appointed)", "governing_body": "Gulf County BOCC (appointing authority)",
        "has_voting_authority_on": None, "surname_key": False,
    },
    "appointed_dept": {
        "office": "Department Leadership (appointed)", "governing_body": "Gulf County BOCC (appointing authority)",
        "has_voting_authority_on": None, "surname_key": False,
    },
}

BOCC_SURNAMES = ["Quinn", "McCroan", "McLemore", "Husband", "Pridgeon"]

# ── Topic keyword expansion (only for topics the brief named explicitly;
#    everything else searches the raw term only -- no invented synonyms) ──
TOPIC_EXPANSIONS = {
    "bay": ["st. joseph bay", "st joe bay", "water quality", "red tide", "scallop", "oyster", "seagrass"],
    "flock": ["flock safety", "license plate reader", "lpr", "alpr", "automated license plate"],
    "taxes": ["millage", "millage rate", "tax increase", "ad valorem", "property tax", "budget amendment"],
    "school": ["school board", "school district", "school zone", "school resource officer"],
}


def expand_topic(topic):
    t = topic.lower().strip()
    for key, synonyms in TOPIC_EXPANSIONS.items():
        if key in t:
            # Use ONLY the precise synonym phrases, not the bare ambiguous word itself --
            # "bay" alone also matches street names ("Butler's Bay Road"), "Bay County",
            # etc. The whole point of expansion is to replace vague terms with specific ones.
            return synonyms
    return [t]


def last_name_of(full_name):
    cleaned = re.sub(r'["\u201c\u201d].*?["\u201c\u201d]', '', full_name)
    parts = [p.strip(',.') for p in cleaned.split() if p.strip(',.')]
    parts = [p for p in parts if p.upper() not in ("JR", "SR", "II", "III", "IV")]
    return parts[-1] if parts else full_name


def fuzzy_match_officials(query, officials_rows, limit=5):
    """
    officials_rows: list of sqlite3.Row from knowledge_base (id, subject, sub_category, ...)
    Returns candidates sorted by match score, handling misspellings like
    "Philll Macron" -> "Phil McCroan" via token-level difflib ratio.
    """
    q_tokens = [t.lower() for t in re.findall(r"[a-zA-Z]+", query)]
    if not q_tokens:
        return []
    scored = []
    for row in officials_rows:
        name_tokens = [t.lower() for t in re.findall(r"[a-zA-Z]+", row["subject"])]
        if not name_tokens:
            continue
        # every query token must itself resemble SOME name token -- use min(), not
        # average(), so one unrelated trailing word (e.g. a topic word) can't be
        # diluted away by two strong name-token matches.
        token_scores = []
        for qt in q_tokens:
            best = max((difflib.SequenceMatcher(None, qt, nt).ratio() for nt in name_tokens), default=0)
            token_scores.append(best)
        score = min(token_scores)
        if score >= 0.55:
            scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    return [row for score, row in scored[:limit]]


def build_report_card(official_row, topic, conn):
    """
    official_row: sqlite3.Row from knowledge_base.
    Returns a dict ready for the template.
    """
    subject = official_row["subject"]
    sub_cat = official_row["sub_category"]
    dir_info = DIRECTORY.get(sub_cat, {"office": sub_cat, "governing_body": "Gulf County", "has_voting_authority_on": None, "surname_key": False})
    surname = last_name_of(subject)
    keywords = expand_topic(topic)

    card = {
        "subject": subject, "office": dir_info["office"], "governing_body": dir_info["governing_body"],
        "topic": topic, "keywords_used": keywords, "surname": surname,
        "has_voting_authority": dir_info["surname_key"] and surname in BOCC_SURNAMES,
    }

    if not card["has_voting_authority"]:
        # Appointed staff / constitutional officers: no BOCC voting authority.
        # Show documented actions (mentions + flags) instead of a vote grade.
        like_clauses = " OR ".join(["m.minutes_text LIKE ?"] * len(keywords))
        params = [f"%{kw}%" for kw in keywords] 
        rows = conn.execute(f"""
            SELECT m.id, m.meeting_date, m.title, m.minutes_text, m.youtube_url
            FROM meetings m WHERE (minutes_text LIKE ?) AND ({like_clauses})
            ORDER BY m.meeting_date DESC LIMIT 25
        """, [f"%{surname}%"] + params).fetchall()
        card["grade"] = "No voting authority on BOCC motions"
        card["grade_detail"] = f"{subject} is {dir_info['office']} and does not cast BOCC votes. Documented meeting actions/mentions related to \"{topic}\" are shown below instead."
        card["documented_actions"] = rows
        card["votes"] = []
        card["procedural_votes"] = []
        return card

    # Elected BOCC commissioner: pull every motion where (a) their surname is
    # a present-and-recorded voter AND (b) the motion's context matches a topic keyword.
    motion_rows = conn.execute("""
        SELECT * FROM votes WHERE votes_json LIKE ?
    """, (f'%"{surname}"%',)).fetchall()
    vcols = [d[0] for d in conn.execute("SELECT * FROM votes LIMIT 1").description]

    import json as _json
    verified, procedural, unverified_mentions = [], [], []
    for r in motion_rows:
        d = dict(zip(vcols, r))
        ctx_lower = d["context"].lower()
        if not any(kw in ctx_lower for kw in keywords):
            continue
        votes = _json.loads(d["votes_json"])
        this_vote = votes.get(surname)
        if this_vote is None:
            continue  # surname matched votes_json string search but not an actual key (rare false positive guard)
        entry = {
            "meeting_id": d["meeting_id"], "meeting_date": d["meeting_date"],
            "context": d["context"], "outcome_sentence": d["outcome_sentence"],
            "maker": d["maker"], "seconder": d["seconder"], "passed": bool(d["passed"]),
            "vote": this_vote,
        }
        if d["is_procedural"]:
            procedural.append(entry)
        else:
            verified.append(entry)

    yes = sum(1 for v in verified if v["vote"] == "yes")
    no = sum(1 for v in verified if v["vote"] == "no")
    abstain = sum(1 for v in verified if v["vote"] == "abstain")
    substantive_decided = yes + no  # abstentions don't count toward support/oppose grade

    if substantive_decided == 0:
        grade = "Insufficient verified votes"
        grade_detail = f"No individually-verified \"{topic}\"-related votes were found for {subject} in the archived minutes."
    elif yes > no:
        grade = f"Supported {yes} of {substantive_decided} verified votes"
        grade_detail = f"{subject} voted yes on {yes} of {substantive_decided} verified \"{topic}\"-related motions where their individual vote could be established."
    elif no > yes:
        grade = f"Opposed {no} of {substantive_decided} verified votes"
        grade_detail = f"{subject} voted no on {no} of {substantive_decided} verified \"{topic}\"-related motions where their individual vote could be established."
    else:
        grade = f"Mixed — {yes} supported, {no} opposed"
        grade_detail = f"{subject}'s verified \"{topic}\"-related votes were evenly split: {yes} yes, {no} no."

    if abstain:
        grade_detail += f" ({abstain} additional abstention{'s' if abstain != 1 else ''} not counted toward the grade.)"

    card["grade"] = grade
    card["grade_detail"] = grade_detail
    card["votes"] = sorted(verified, key=lambda v: v["meeting_date"] or "", reverse=True)
    card["procedural_votes"] = sorted(procedural, key=lambda v: v["meeting_date"] or "", reverse=True)
    card["documented_actions"] = []
    return card

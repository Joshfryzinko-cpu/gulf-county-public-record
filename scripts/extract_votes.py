"""
Vote extraction pipeline for Eagle Eye public report cards.

Parses two real, consistently-formatted structures in Gulf County BOCC
minutes text:

1. Attendance line near the top of every meeting:
   "...met this date in [regular/special] session with the following
   members present: Chairman Sandy Quinn, Jr., Vice Chairman Phil
   McCroan, and Commissioners Christopher McLemore, Lloyd Jack Husband,
   III, and Randy Pridgeon."
   -> tells us exactly which of the 5 standing BOCC members were present.

2. Motion/outcome sentences:
   "Commissioner X made/moved the motion. Commissioner Y seconded the
   motion. ... the motion passed unanimously."
   "...the motion passed 4-0, with Commissioner Husband abstaining due
   to <reason> <FORM 8B on file with Clerk>."
   "...the motion passed 4-1, with Commissioner McCroan voting no."
   -> tells us, when a named exception + count is given, exactly how
   EVERY present commissioner voted on that specific motion (the named
   exception(s) + everyone else present = the "for" count).

Anything that doesn't fit these patterns is marked unverified and
EXCLUDED from any report-card grade, never guessed.
"""
import sqlite3, re, os, json

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bocc.db")

# The 5 standing Gulf County BOCC members (surnames as they appear in minutes)
BOCC_SURNAMES = ["Quinn", "McCroan", "McLemore", "Husband", "Pridgeon"]

MAKER_RE   = re.compile(r"Commissioner\s+([A-Z][a-zA-Z]+)\s+(?:made the motion|so moved|motioned)", re.I)
SECOND_RE  = re.compile(r"Commissioner\s+([A-Z][a-zA-Z]+)\s+seconded", re.I)
COUNT_RE   = re.compile(r"passed\s+(\d)\s*[-to]+\s*(\d)", re.I)
UNANIMOUS_RE = re.compile(r"\bunanimous(ly)?\b", re.I)
FAILED_RE = re.compile(r"\b(failed|died)\b", re.I)
EXCEPTION_RE = re.compile(
    r"Commissioner\s+([A-Z][a-zA-Z]+)\s+(abstain\w*|voting\s+no|opposed)"
    r"(?:\s+due\s+to\s+([^<.]{0,140}))?",
    re.I,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
MOTION_WORD_RE = re.compile(r"\bmotion\b", re.I)
OUTCOME_WORD_RE = re.compile(r"\b(passed|carried|failed|died)\b", re.I)
PROCEDURAL_KEYWORDS = [
    "approve the agenda", "approve the minutes", "postpone", "table this",
    "table the item", "continue to the next", "approve the consent agenda",
    "adjourn",
]


def parse_attendance(text):
    """Return the set of BOCC surnames present, parsed from the opening attendance sentence."""
    idx = text[:1500].find("following members present:")
    blob = text[idx: idx + 300] if idx >= 0 else text[:800]
    present = set()
    for surname in BOCC_SURNAMES:
        if re.search(rf"\b{surname}\b", blob):
            present.add(surname)
    if "appeared via telephone" in text[:1500]:
        # catches "Commissioner Lloyd Jack Husband, III appeared via telephone" etc.
        tele_window = text[:1500]
        for surname in BOCC_SURNAMES:
            if re.search(rf"\b{surname}\b[^.]{{0,40}}appeared via telephone", tele_window):
                present.add(surname)
    return present


def extract_motions(meeting_id, date, text):
    present = parse_attendance(text)
    motions = []
    sentences = SENTENCE_SPLIT_RE.split(text)
    n = len(sentences)
    for i, sent in enumerate(sentences):
        if not (MOTION_WORD_RE.search(sent) and OUTCOME_WORD_RE.search(sent)):
            continue
        outcome_sentence = sent
        # context: up to 4 preceding sentences + this one + 1 following
        ctx_start = max(0, i - 4)
        ctx_end = min(n, i + 2)
        context = " ".join(sentences[ctx_start:ctx_end])
        char_offset = sum(len(s) + 1 for s in sentences[:i])

        maker_m = MAKER_RE.search(context)
        second_m = SECOND_RE.search(context)
        maker = maker_m.group(1) if maker_m else None
        seconder = second_m.group(1) if second_m else None

        passed = bool(re.search(r"\b(passed|carried)\b", outcome_sentence, re.I)) and not FAILED_RE.search(outcome_sentence)
        unanimous = bool(UNANIMOUS_RE.search(outcome_sentence))
        count_m = COUNT_RE.search(outcome_sentence)
        for_count = int(count_m.group(1)) if count_m else None
        against_count = int(count_m.group(2)) if count_m else None

        exceptions = []
        for ex in EXCEPTION_RE.finditer(outcome_sentence):
            name, kind, reason = ex.group(1), ex.group(2).lower(), ex.group(3)
            vtype = "abstain" if "abstain" in kind else ("no" if ("no" in kind or "opposed" in kind) else "unverified")
            exceptions.append({"surname": name, "vote": vtype, "reason": (reason or "").strip() or None})

        is_procedural = any(kw in context.lower() for kw in PROCEDURAL_KEYWORDS)

        votes = {}
        exception_names = {e["surname"] for e in exceptions}
        if unanimous and not exceptions:
            for s in present:
                votes[s] = "yes"
        elif for_count is not None:
            remaining = present - exception_names
            if len(remaining) == for_count:
                for s in remaining:
                    votes[s] = "yes"
                for e in exceptions:
                    votes[e["surname"]] = e["vote"]
            else:
                for e in exceptions:
                    votes[e["surname"]] = e["vote"]
        else:
            for e in exceptions:
                votes[e["surname"]] = e["vote"]

        motions.append({
            "meeting_id": meeting_id, "meeting_date": date,
            "context": context.strip(), "outcome_sentence": outcome_sentence.strip(),
            "maker": maker, "seconder": seconder, "passed": passed, "unanimous": unanimous,
            "for_count": for_count, "against_count": against_count,
            "is_procedural": is_procedural, "present": sorted(present),
            "votes": votes,
            "char_offset": char_offset,
        })
    return motions


def build(conn):
    conn.execute("DROP TABLE IF EXISTS votes")
    conn.execute("""
        CREATE TABLE votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER, meeting_date TEXT,
            context TEXT, outcome_sentence TEXT,
            maker TEXT, seconder TEXT,
            passed INTEGER, unanimous INTEGER,
            for_count INTEGER, against_count INTEGER,
            is_procedural INTEGER, present_json TEXT,
            votes_json TEXT, char_offset INTEGER
        )
    """)
    rows = conn.execute("SELECT id, meeting_date, minutes_text FROM meetings WHERE minutes_text IS NOT NULL").fetchall()
    total_motions, total_verified_votes = 0, 0
    for mid, date, text in rows:
        for mo in extract_motions(mid, date, text):
            conn.execute("""
                INSERT INTO votes (meeting_id, meeting_date, context, outcome_sentence, maker, seconder,
                    passed, unanimous, for_count, against_count, is_procedural, present_json, votes_json, char_offset)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (mo["meeting_id"], mo["meeting_date"], mo["context"], mo["outcome_sentence"],
                  mo["maker"], mo["seconder"], int(mo["passed"]), int(mo["unanimous"]),
                  mo["for_count"], mo["against_count"], int(mo["is_procedural"]),
                  json.dumps(mo["present"]), json.dumps(mo["votes"]), mo["char_offset"]))
            total_motions += 1
            total_verified_votes += len(mo["votes"])
    conn.commit()
    print(f"Meetings scanned: {len(rows)}")
    print(f"Motions extracted: {total_motions}")
    print(f"Individual verified vote records: {total_verified_votes}")


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    build(conn)
    conn.close()

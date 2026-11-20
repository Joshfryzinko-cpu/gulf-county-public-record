"""
Migration: backfill video + transcript links into the PUBLIC bocc.db
from the richer internal dataset recovered from the old backup drive.

Rationale: public_app.py's own scrape pipeline (fetch_minutes.py etc.)
only captured minutes PDFs -> text (272 meetings, no video/transcript).
A separate internal Eagle Eye build (recovered from an old hard drive,
now at Desktop/Eagle Eye Recovery/data/bocc.db) scraped the SAME real
Gulf County BOCC meetings but additionally pulled YouTube video links
(509) and transcripts (38) via youtube_monitor-style tooling.

Both datasets describe the same real-world meetings, so we match on
meeting_date (exact) and backfill youtube_url / youtube_id / transcript
into the public repo's meetings table. This reuses existing ingestion
work instead of re-scraping YouTube, per the redesign brief.
"""
import sqlite3, os

PUBLIC_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bocc.db")
SOURCE_DB = r"C:\Users\dryer\OneDrive\Desktop\Eagle Eye Recovery\data\bocc.db"

def main():
    pub = sqlite3.connect(PUBLIC_DB)
    pub.row_factory = sqlite3.Row

    # 1. Add columns if missing (idempotent)
    cols = {r["name"] for r in pub.execute("PRAGMA table_info(meetings)")}
    for col, decl in [
        ("youtube_url", "TEXT"), ("youtube_id", "TEXT"),
        ("transcript", "TEXT"), ("transcript_status", "TEXT DEFAULT 'none'"),
    ]:
        if col not in cols:
            pub.execute(f"ALTER TABLE meetings ADD COLUMN {col} {decl}")
    pub.commit()

    # 2. Pull source rows keyed by date
    src = sqlite3.connect(SOURCE_DB)
    src.row_factory = sqlite3.Row
    src_rows = src.execute("""
        SELECT meeting_date, youtube_url, youtube_id, transcript, transcript_status
        FROM meetings
        WHERE youtube_url IS NOT NULL OR transcript IS NOT NULL
    """).fetchall()
    src.close()

    by_date = {}
    for r in src_rows:
        d = (r["meeting_date"] or "").strip()
        if not d:
            continue
        # keep richest row per date (prefer one with a transcript)
        existing = by_date.get(d)
        if existing is None or (r["transcript"] and not existing["transcript"]):
            by_date[d] = r

    # 3. Backfill
    pub_rows = pub.execute("SELECT id, meeting_date FROM meetings").fetchall()
    matched, video_matched, transcript_matched = 0, 0, 0
    for row in pub_rows:
        d = (row["meeting_date"] or "").strip()
        src_row = by_date.get(d)
        if not src_row:
            continue
        matched += 1
        yt_url = src_row["youtube_url"]
        yt_id = src_row["youtube_id"]
        transcript = src_row["transcript"]
        tstatus = src_row["transcript_status"] or ("complete" if transcript else "none")
        if yt_url:
            video_matched += 1
        if transcript:
            transcript_matched += 1
        pub.execute(
            "UPDATE meetings SET youtube_url=?, youtube_id=?, transcript=?, transcript_status=? WHERE id=?",
            (yt_url, yt_id, transcript, tstatus, row["id"]),
        )
    pub.commit()

    total = pub.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    print(f"Public meetings: {total}")
    print(f"Matched by date to richer dataset: {matched}")
    print(f"  with video linked:      {video_matched}")
    print(f"  with transcript linked: {transcript_matched}")
    pub.close()

if __name__ == "__main__":
    main()

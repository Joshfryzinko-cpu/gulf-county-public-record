"""
Gulf County Public Record Search — powered by Eagle Eye
Public-facing Flask app. Read-only access to bocc.db.
"""
import os, sqlite3, re, json
from flask import Flask, render_template, request, jsonify, g

app = Flask(__name__)

# ── Database ──────────────────────────────────────────────────────────────
DB_PATH = os.environ.get(
    "DB_PATH",
    r"C:\Users\Dryer\gulfcounty-bocc\data\bocc.db"
)

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# ── Helpers ───────────────────────────────────────────────────────────────
def excerpt(text, query, chars=250):
    """Return highlighted excerpt around query match."""
    if not text or not query:
        return ""
    idx = text.lower().find(query.lower())
    if idx < 0:
        idx = 0
    start = max(0, idx - 80)
    snip  = text[start:start + chars].replace("\n", " ").strip()
    # Bold the match
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    snip = pattern.sub(lambda m: f"<strong>{m.group()}</strong>", snip)
    return ("…" if start > 0 else "") + snip + "…"


def smart_answer(question, conn):
    """
    Pattern-match common question types and query the DB.
    Returns {answer, citations, flag_level}
    """
    q = question.lower().strip()
    citations = []
    flag_level = "none"

    # ── Who has conflicts / who is flagged ───────────────────────────────
    if any(w in q for w in ["conflict", "flagged", "flag", "problem", "corrupt", "violat"]):
        rows = conn.execute("""
            SELECT subject, flag_level, flag_reason FROM knowledge_base
            WHERE flag_level IN ('critical','significant','watch')
            ORDER BY CASE flag_level WHEN 'critical' THEN 1 WHEN 'significant' THEN 2 ELSE 3 END
            LIMIT 10
        """).fetchall()
        if rows:
            flag_level = "significant"
            names = []
            for r in rows:
                names.append(f"**{r['subject'].split('—')[0].strip()}** ({r['flag_level'].upper()}): {(r['flag_reason'] or '')[:120]}")
                citations.append(r['subject'][:60])
            return {
                "answer": f"Eagle Eye has identified {len(rows)} officials or matters with documented concerns in the public record:\n\n" + "\n\n".join(names),
                "citations": citations[:5],
                "flag_level": flag_level
            }

    # ── Did [person] vote / recuse ────────────────────────────────────────
    vote_match = re.search(r'did (.+?) (?:vote|recuse|abstain|conflict)', q)
    if vote_match:
        name = vote_match.group(1).strip().title()
        rows = conn.execute("""
            SELECT meeting_date, title, minutes_text FROM meetings
            WHERE minutes_text LIKE ? AND minutes_text IS NOT NULL
            ORDER BY meeting_date DESC LIMIT 5
        """, (f"%{name}%",)).fetchall()
        if rows:
            excerpts = []
            for r in rows:
                txt = r["minutes_text"]
                idx = txt.lower().find(name.lower())
                if idx >= 0:
                    ctx = txt[max(0,idx-60):idx+200].replace("\n"," ")
                    excerpts.append(f"**{r['meeting_date']}** — {r['title'][:50]}: …{ctx[:180]}…")
                    citations.append(f"{r['meeting_date']} — {r['title'][:40]}")
            return {
                "answer": f"Here is what the BOCC meeting minutes record about {name}:\n\n" + "\n\n".join(excerpts[:3]),
                "citations": citations[:3],
                "flag_level": "watch"
            }

    # ── What is [statute / FS number] ────────────────────────────────────
    fs_match = re.search(r'(?:fs|florida statute|statute)\s*([\d\.]+)', q)
    if fs_match:
        cite = "FS " + fs_match.group(1)
        rows = conn.execute("""
            SELECT cite, title, summary FROM legal_refs WHERE cite LIKE ? LIMIT 3
        """, (f"%{fs_match.group(1)}%",)).fetchall()
        if rows:
            r = rows[0]
            return {
                "answer": f"**{r['cite']} — {r['title']}**\n\n{r['summary'] or 'Summary not available. See the full statute text in our Legal Reference section.'}",
                "citations": [r['cite']],
                "flag_level": "none"
            }

    # ── How much / how many / budget ─────────────────────────────────────
    if any(w in q for w in ["how much", "budget", "cost", "spend", "paid", "contract"]):
        rows = conn.execute("""
            SELECT subject, detail FROM knowledge_base
            WHERE (detail LIKE '%$%' OR flag_reason LIKE '%$%')
            AND (subject LIKE ? OR detail LIKE ? OR flag_reason LIKE ?)
            LIMIT 5
        """, (f"%{question[:30]}%", f"%{question[:30]}%", f"%{question[:30]}%")).fetchall()
        if rows:
            answers = [f"**{r['subject'][:60]}**: {(r['detail'] or '')[:200]}" for r in rows]
            return {
                "answer": "Based on public records in Eagle Eye:\n\n" + "\n\n".join(answers),
                "citations": [r['subject'][:50] for r in rows],
                "flag_level": "none"
            }

    # ── Who is [person] ──────────────────────────────────────────────────
    who_match = re.search(r'who is (.+?)(?:\?|$)', q)
    if who_match:
        name = who_match.group(1).strip().title()
        rows = conn.execute("""
            SELECT subject, detail, flag_level, flag_reason FROM knowledge_base
            WHERE subject LIKE ? LIMIT 3
        """, (f"%{name}%",)).fetchall()
        if rows:
            r = rows[0]
            detail = (r["detail"] or "")[:400]
            flag_txt = f"\n\n⚠️ **Eagle Eye Flag ({r['flag_level'].upper()}):** {(r['flag_reason'] or '')[:200]}" if r["flag_level"] != "none" else ""
            return {
                "answer": f"**{r['subject']}**\n\n{detail}{flag_txt}",
                "citations": [r["subject"][:60]],
                "flag_level": r["flag_level"]
            }

    # ── What meetings / show meetings ────────────────────────────────────
    if any(w in q for w in ["meeting", "minutes", "vote", "agenda", "session"]):
        # Extract a subject from the question
        rows = conn.execute("""
            SELECT meeting_date, title, minutes_text FROM meetings
            WHERE title LIKE ? OR minutes_text LIKE ?
            AND minutes_text IS NOT NULL
            ORDER BY meeting_date DESC LIMIT 5
        """, (f"%{question[:40]}%", f"%{question[:40]}%")).fetchall()
        if rows:
            items = []
            for r in rows:
                items.append(f"**{r['meeting_date']}** — {r['title']}")
                citations.append(f"{r['meeting_date']} {r['title'][:40]}")
            return {
                "answer": f"Found {len(rows)} relevant meetings in the public record:\n\n" + "\n".join(items),
                "citations": citations[:5],
                "flag_level": "none"
            }

    # ── General keyword search ───────────────────────────────────────────
    keywords = [w for w in q.split() if len(w) > 3 and w not in
                ("what","when","where","which","does","have","been","this","that","with","from","about","show","tell","find","give")]

    if keywords:
        like = f"%{' '.join(keywords[:3])}%"
        # Try knowledge base first
        rows = conn.execute("""
            SELECT subject, detail, flag_level, flag_reason FROM knowledge_base
            WHERE subject LIKE ? OR detail LIKE ? OR flag_reason LIKE ?
            LIMIT 5
        """, (like, like, like)).fetchall()
        if rows:
            parts = []
            for r in rows:
                fl = f" [{r['flag_level'].upper()}]" if r['flag_level'] != 'none' else ""
                parts.append(f"**{r['subject'][:60]}**{fl}: {(r['detail'] or '')[:180]}")
                citations.append(r['subject'][:50])
            return {
                "answer": "Here is what Eagle Eye found in the public record:\n\n" + "\n\n".join(parts),
                "citations": citations[:5],
                "flag_level": rows[0]["flag_level"] if rows else "none"
            }

        # Fall back to meetings
        for kw in keywords[:2]:
            mrows = conn.execute("""
                SELECT meeting_date, title, minutes_text FROM meetings
                WHERE minutes_text LIKE ? AND minutes_text IS NOT NULL
                ORDER BY meeting_date DESC LIMIT 3
            """, (f"%{kw}%",)).fetchall()
            if mrows:
                parts = []
                for r in mrows:
                    txt = r["minutes_text"]
                    idx = txt.lower().find(kw.lower())
                    ctx = txt[max(0,idx-60):idx+200].replace("\n"," ") if idx >= 0 else ""
                    parts.append(f"**{r['meeting_date']}** — {r['title']}: …{ctx[:200]}…")
                    citations.append(f"{r['meeting_date']} {r['title'][:40]}")
                return {
                    "answer": f"Found in BOCC meeting minutes:\n\n" + "\n\n".join(parts),
                    "citations": citations[:5],
                    "flag_level": "none"
                }

    return {
        "answer": "I searched the Gulf County public record but couldn't find a specific match for that question. Try rephrasing, or use the Search tab to browse all records.",
        "citations": [],
        "flag_level": "none"
    }


# ── Routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    conn = get_db()
    stats = {
        "meetings": conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0],
        "with_minutes": conn.execute("SELECT COUNT(*) FROM meetings WHERE minutes_text IS NOT NULL AND minutes_text != ''").fetchone()[0],
        "officials": conn.execute("SELECT COUNT(*) FROM knowledge_base WHERE sub_category LIKE 'elected%' OR sub_category LIKE 'appointed%'").fetchone()[0],
        "flags": conn.execute("SELECT COUNT(*) FROM legal_flags").fetchone()[0],
        "flagged": conn.execute("SELECT COUNT(*) FROM knowledge_base WHERE flag_level IN ('critical','significant','watch')").fetchone()[0],
    }
    recent = conn.execute("SELECT meeting_date, title, meeting_type FROM meetings ORDER BY meeting_date DESC LIMIT 5").fetchall()
    conn.close()
    return render_template("public/index.html", stats=stats, recent=recent)


@app.route("/search")
def search():
    q    = request.args.get("q","").strip()[:200]
    page = max(1, int(request.args.get("page",1)))
    results = {}
    total   = 0
    if q:
        conn = get_db()
        like = f"%{q}%"
        kb = conn.execute("""
            SELECT subject, detail, flag_level, flag_reason, sub_category, id
            FROM knowledge_base WHERE subject LIKE ? OR detail LIKE ? OR flag_reason LIKE ?
            ORDER BY CASE flag_level WHEN 'critical' THEN 1 WHEN 'significant' THEN 2 WHEN 'watch' THEN 3 ELSE 4 END LIMIT 20
        """, (like,like,like)).fetchall()
        mt = conn.execute("""
            SELECT id, meeting_date, title, meeting_type, minutes_text
            FROM meetings WHERE title LIKE ? OR minutes_text LIKE ?
            AND minutes_text IS NOT NULL ORDER BY meeting_date DESC LIMIT 30
        """, (like,like)).fetchall()
        lf = conn.execute("""
            SELECT lf.*, m.meeting_date, m.title as meeting_title, m.id as meeting_id, lr.cite
            FROM legal_flags lf
            JOIN meetings m ON m.id = lf.meeting_id
            LEFT JOIN legal_refs lr ON lr.id = lf.legal_ref_id
            WHERE lf.description LIKE ? ORDER BY lf.severity DESC LIMIT 15
        """, (like,)).fetchall()
        conn.close()
        results = {"knowledge": kb, "meetings": mt, "flags": lf}
        total = sum(len(v) for v in results.values())
    return render_template("public/search.html", q=q, results=results, total=total, excerpt_fn=excerpt)


@app.route("/ask", methods=["GET","POST"])
def ask():
    question = ""
    answer   = None
    if request.method == "POST":
        question = request.form.get("question","").strip()[:500]
    else:
        question = request.args.get("q","").strip()[:500]

    if question:
        conn = get_db()
        answer = smart_answer(question, conn)
        conn.close()
        # Convert markdown-style bold to HTML
        if answer:
            answer["answer"] = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', answer["answer"])
            answer["answer"] = answer["answer"].replace("\n\n","</p><p>").replace("\n","<br>")
            answer["answer"] = "<p>" + answer["answer"] + "</p>"

    return render_template("public/ask.html", question=question, answer=answer)


@app.route("/api/ask")
def api_ask():
    question = request.args.get("q","").strip()[:500]
    if not question:
        return jsonify({"error":"no question"}), 400
    conn = get_db()
    result = smart_answer(question, conn)
    conn.close()
    return jsonify(result)


@app.route("/api/search")
def api_search():
    q = request.args.get("q","").strip()[:100]
    if len(q) < 2:
        return jsonify([])
    conn = get_db()
    like = f"%{q}%"
    out = []
    for r in conn.execute("SELECT subject, flag_level, sub_category FROM knowledge_base WHERE subject LIKE ? LIMIT 6", (like,)):
        out.append({"type":"person","label":r["subject"],"flag":r["flag_level"],"cat":r["sub_category"]})
    for r in conn.execute("SELECT meeting_date, title FROM meetings WHERE title LIKE ? OR minutes_text LIKE ? ORDER BY meeting_date DESC LIMIT 4", (like,like)):
        out.append({"type":"meeting","label":f"{r['meeting_date']} — {r['title']}"})
    conn.close()
    return jsonify(out)


@app.route("/commissioners")
def public_commissioners():
    conn = get_db()
    bocc  = conn.execute("SELECT * FROM knowledge_base WHERE sub_category='elected_bocc' ORDER BY id").fetchall()
    const = conn.execute("SELECT * FROM knowledge_base WHERE sub_category='elected_const' ORDER BY id").fetchall()
    conn.close()
    return render_template("public/commissioners.html", bocc=bocc, const=const)


@app.route("/officials")
def public_officials():
    conn = get_db()
    profiles = conn.execute("""
        SELECT * FROM knowledge_base
        WHERE sub_category IN ('appointed_admin','appointed_legal','appointed_dept')
        ORDER BY flag_level DESC, subject
    """).fetchall()
    conn.close()
    return render_template("public/officials.html", profiles=profiles)


@app.route("/meetings")
def public_meetings():
    page = max(1, int(request.args.get("page",1)))
    per  = 20
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM meetings WHERE minutes_text IS NOT NULL").fetchone()[0]
    rows  = conn.execute("""
        SELECT id, meeting_date, title, meeting_type, minutes_status
        FROM meetings WHERE minutes_text IS NOT NULL
        ORDER BY meeting_date DESC LIMIT ? OFFSET ?
    """, (per, (page-1)*per)).fetchall()
    conn.close()
    return render_template("public/meetings.html", meetings=rows, total=total, page=page, per=per)


@app.route("/meeting/<int:mid>")
def public_meeting(mid):
    conn = get_db()
    m = conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
    conn.close()
    if not m:
        return "Meeting not found", 404
    return render_template("public/meeting_detail.html", meeting=m)


@app.route("/flags")
def public_flags():
    conn = get_db()
    flags = conn.execute("""
        SELECT lf.*, m.meeting_date, m.title as meeting_title, lr.cite, lr.title as law_title
        FROM legal_flags lf
        JOIN meetings m ON m.id = lf.meeting_id
        LEFT JOIN legal_refs lr ON lr.id = lf.legal_ref_id
        ORDER BY CASE lf.severity WHEN 'critical' THEN 1 WHEN 'significant' THEN 2 ELSE 3 END, m.meeting_date DESC
    """).fetchall()
    conn.close()
    return render_template("public/flags.html", flags=flags)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)

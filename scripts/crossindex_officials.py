#!/usr/bin/env python3
"""
Cross-index every official's name against the full minutes text corpus.
For each official, find every meeting mention, pull a short excerpt, and
flag mentions that co-occur with conflict/ethics-relevant keywords
(recusal, abstain, conflict of interest, contract, vendor, business, spouse,
relative, financial interest). This is a LEAD-GENERATION tool, not a
verdict: every flagged hit should be read in full context by a human
before treating it as anything more than "worth a closer look."
"""
import os, json, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT_DIR = os.path.join(ROOT, "data", "minutes_text")
INDEX_PATH = os.path.join(ROOT, "data", "minutes_index.json")
OFFICIALS_DIR = os.path.join(ROOT, "data", "officials")

OFFICIALS = {
    "spike-mclemore-d1": {
        "name": "Christopher \"Spike\" McLemore", "role": "County Commissioner, District 1",
        "type": "elected", "party": "REP", "term_end": 2028,
        "search_terms": [r"McLemore"],
    },
    "jack-husband-d2": {
        "name": "Lloyd \"Jack\" Husband, III", "role": "County Commissioner, District 2",
        "type": "elected", "party": "REP", "term_end": 2026,
        "search_terms": [r"Husband"],
    },
    "randy-pridgeon-d3": {
        "name": "Randy Pridgeon", "role": "County Commissioner, District 3",
        "type": "elected", "party": "REP", "term_end": 2028,
        "search_terms": [r"Pridgeon"],
    },
    "sandy-quinn-d4": {
        "name": "Sandy Quinn, Jr.", "role": "County Commissioner, District 4 (Chairman)",
        "type": "elected", "party": "DEM", "term_end": 2026,
        "search_terms": [r"Quinn"],
    },
    "phil-mccroan-d5": {
        "name": "Phil McCroan", "role": "County Commissioner, District 5 (Vice Chairman)",
        "type": "elected", "party": "REP", "term_end": 2028,
        "search_terms": [r"McCroan"],
    },
    "mike-harrison-sheriff": {
        "name": "Mike Harrison", "role": "Sheriff", "type": "elected (constitutional officer)",
        "party": "REP", "term_end": 2028,
        "search_terms": [r"Sheriff Harrison", r"\bHarrison\b"],
    },
    "matt-terry-clerk": {
        "name": "James \"Matt\" Terry", "role": "Clerk of Court & County Comptroller",
        "type": "elected (constitutional officer)", "party": "REP", "term_end": 2026,
        "search_terms": [r"Clerk Terry", r"James M\.? Terry", r"\bTerry\b"],
    },
    "mitch-burke-property-appraiser": {
        "name": "Mitch Burke", "role": "Property Appraiser", "type": "elected (constitutional officer)",
        "party": "REP", "term_end": 2028,
        "search_terms": [r"\bBurke\b"],
    },
    "rhonda-pierce-supervisor-elections": {
        "name": "Rhonda Pierce", "role": "Supervisor of Elections", "type": "elected (constitutional officer)",
        "party": "REP", "term_end": 2028,
        "search_terms": [r"\bPierce\b"],
    },
    "ashley-forehand-tax-collector": {
        "name": "Ashley L. Forehand", "role": "Tax Collector", "type": "elected (constitutional officer)",
        "party": "REP", "term_end": 2028,
        "search_terms": [r"Forehand"],
    },
    "michael-hammond-administrator": {
        "name": "Michael L. Hammond", "role": "County Administrator", "type": "appointed",
        "search_terms": [r"Administrator Hammond", r"\bHammond\b"],
    },
    "kari-summers-deputy-administrator": {
        "name": "Kari Summers", "role": "Deputy Administrator (per county website; minutes show Silvia Williams in this role on at least one 2026 date \u2014 flag to confirm)",
        "type": "appointed",
        "search_terms": [r"\bSummers\b", r"Silvia Williams"],
    },
    "jeremy-novak-county-attorney": {
        "name": "Jeremy T.M. Novak", "role": "County Attorney", "type": "appointed (contracted)",
        "search_terms": [r"\bNovak\b"],
    },
}

# Routine / expected disclosure language. A commissioner abstaining and
# filing Form 8B is the SYSTEM WORKING CORRECTLY (FS 112.3143 compliance) —
# this is informational context, never a "concern."
ROUTINE_DISCLOSURE_KEYWORDS = [
    "recus", "abstain", "voting conflict", "form 8b", "declared a conflict",
    "stated his interest", "stated her interest",
]

# Keywords worth a human actually reading the passage for. Still not proof
# of anything — proximity to a name is not causation — but these denote
# situations (undisclosed interest, no-bid awards, family ties to a vendor)
# that warrant a look, rather than routine compliance language.
CONCERN_KEYWORDS = [
    "conflict of interest", "financial interest", "relative", "spouse",
    "business partner", "sole source", "no-bid", "family member", "nepotism",
    "gift disclosure", "dual office", "undisclosed", "failed to disclose",
    "did not recuse", "did not abstain",
]

CONFLICT_KEYWORDS = ROUTINE_DISCLOSURE_KEYWORDS + CONCERN_KEYWORDS

def load_corpus():
    with open(INDEX_PATH, encoding="utf-8") as f:
        index = json.load(f)
    corpus = []
    for rec in index:
        txt_path = os.path.join(ROOT, rec.get("text_path", ""))
        if not os.path.exists(txt_path):
            continue
        with open(txt_path, encoding="utf-8") as f:
            text = f.read()
        corpus.append((rec, text))
    return corpus

def excerpt(text, pos, span=160):
    start = max(0, pos - span)
    end = min(len(text), pos + span)
    snippet = text[start:end].replace("\n", " ")
    return ("…" if start > 0 else "") + snippet.strip() + ("…" if end < len(text) else "")

def main():
    corpus = load_corpus()
    print(f"Loaded {len(corpus)} documents with extracted text")

    for slug, info in OFFICIALS.items():
        patterns = [re.compile(p, re.IGNORECASE) for p in info["search_terms"]]
        mentions = []
        flagged = []
        for rec, text in corpus:
            hit_positions = []
            for pat in patterns:
                for m in pat.finditer(text):
                    hit_positions.append(m.start())
            if not hit_positions:
                continue
            mentions.append({
                "date": rec.get("date"),
                "meeting": rec.get("name"),
                "year": rec.get("year"),
                "mention_count": len(hit_positions),
                "source_pdf": rec.get("local_path"),
                "excerpt": excerpt(text, hit_positions[0]),
            })
            low = text.lower()
            for kw in CONFLICT_KEYWORDS:
                kw_pos = low.find(kw)
                if kw_pos == -1:
                    continue
                # only flag if a name hit and the keyword are reasonably close in the doc
                for hp in hit_positions:
                    if abs(hp - kw_pos) < 1500:
                        flagged.append({
                            "date": rec.get("date"),
                            "meeting": rec.get("name"),
                            "keyword": kw,
                            "category": "routine_disclosure" if kw in ROUTINE_DISCLOSURE_KEYWORDS else "concern",
                            "source_pdf": rec.get("local_path"),
                            "excerpt_near_name": excerpt(text, hp),
                            "excerpt_near_keyword": excerpt(text, kw_pos),
                        })
                        break

        mentions.sort(key=lambda m: m["date"] or "0000-00-00", reverse=True)
        n_routine = sum(1 for fl in flagged if fl["category"] == "routine_disclosure")
        n_concern = sum(1 for fl in flagged if fl["category"] == "concern")

        folder = os.path.join(OFFICIALS_DIR, slug)
        os.makedirs(folder, exist_ok=True)

        profile_md = [
            f"# {info['name']}",
            f"**Role:** {info['role']}",
            f"**Type:** {info['type']}",
        ]
        if "party" in info:
            profile_md.append(f"**Party:** {info['party']}")
        if "term_end" in info:
            profile_md.append(f"**Term ends:** {info['term_end']}")
        profile_md.append("")
        profile_md.append(f"**Meetings mentioning this person (2017\u20132026 archive):** {len(mentions)}")
        profile_md.append(f"**Routine disclosure passages (abstain/recusal/Form 8B language near their name \u2014 normal, expected compliance):** {n_routine}")
        profile_md.append(f"**Concern-worthy passages needing human review (undisclosed interests, no-bid, family/vendor ties, etc.):** {n_concern}")
        profile_md.append("")
        profile_md.append("Source: Gulf County BOCC meeting minutes archive, scraped from "
                           "gulfcounty-fl.gov (2017\u20132026, 272 documents) and the Gulf County "
                           "Supervisor of Elections / county website for elected roster data. "
                           "This file is machine-generated lead data \u2014 verify every flagged "
                           "excerpt against the source PDF before treating it as a finding.")

        with open(os.path.join(folder, "profile.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(profile_md) + "\n")

        with open(os.path.join(folder, "mentions.json"), "w", encoding="utf-8") as f:
            json.dump(mentions, f, indent=2)

        with open(os.path.join(folder, "flagged_for_review.json"), "w", encoding="utf-8") as f:
            json.dump(flagged, f, indent=2)

        print(f"{slug}: {len(mentions)} meeting mentions, {len(flagged)} flagged passages")

if __name__ == "__main__":
    main()

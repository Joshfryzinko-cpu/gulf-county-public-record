#!/usr/bin/env python3
"""Extract text from every downloaded meeting minutes PDF into data/minutes_text/."""
import os, json, sys
import pdfplumber

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw_minutes")
TEXT_DIR = os.path.join(ROOT, "data", "minutes_text")
INDEX_PATH = os.path.join(ROOT, "data", "minutes_index.json")

def main():
    with open(INDEX_PATH, encoding="utf-8") as f:
        index = json.load(f)

    os.makedirs(TEXT_DIR, exist_ok=True)
    ok, failed = 0, []
    for rec in index:
        pdf_path = os.path.join(ROOT, rec["local_path"])
        year_dir = os.path.join(TEXT_DIR, rec["year"])
        os.makedirs(year_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        txt_path = os.path.join(year_dir, base + ".txt")
        rec["text_path"] = os.path.relpath(txt_path, ROOT)
        if os.path.exists(txt_path):
            ok += 1
            continue
        if not os.path.exists(pdf_path):
            failed.append((rec["name"], "pdf missing"))
            continue
        try:
            with pdfplumber.open(pdf_path) as pdf:
                pages_text = [p.extract_text() or "" for p in pdf.pages]
            full_text = "\n\n".join(pages_text)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(full_text)
            ok += 1
            if ok % 25 == 0:
                print(f"  ...{ok} done")
        except Exception as e:
            failed.append((rec["name"], str(e)))

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    print(f"Extracted/verified: {ok}")
    print(f"Failed: {len(failed)}")
    for name, err in failed:
        print(f"  FAILED: {name} -> {err}")

if __name__ == "__main__":
    main()

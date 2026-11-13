#!/usr/bin/env python3
"""Convert downloaded Florida statute/constitution HTML into clean text files."""
import os, re
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAW_DIR = os.path.join(ROOT, "data", "florida_law")

FILES = {
    "raw_ch112.html": "ch112_ethics.txt",
    "raw_286_011.html": "286_011_sunshine_law.txt",
    "raw_119_07.html": "119_07_public_records.txt",
    "raw_ch125.html": "ch125_county_government.txt",
    "raw_ch164_index.html": "ch164_conflict_resolution_index.txt",
    "raw_ch164.html": "ch164_conflict_resolution.txt",
    "raw_const_art8.html": "constitution_article_viii.txt",
}

def clean_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text("\n")
    # collapse excess blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln.strip() != ""]
    return "\n".join(lines)

def main():
    for src, dst in FILES.items():
        src_path = os.path.join(LAW_DIR, src)
        if not os.path.exists(src_path):
            print(f"missing: {src_path}")
            continue
        with open(src_path, encoding="utf-8", errors="replace") as f:
            html = f.read()
        text = clean_text(html)
        dst_path = os.path.join(LAW_DIR, dst)
        with open(dst_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{dst}: {len(text)} chars")

if __name__ == "__main__":
    main()

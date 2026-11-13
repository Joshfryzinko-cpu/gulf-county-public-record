#!/usr/bin/env python3
"""
Gulf County BOCC meeting minutes fetcher.
Pulls the full item list for every year folder via the CivicLive
ContentItemSvc GetItemList endpoint (no pagination needed — it returns
everything for a folder in one call), then downloads each PDF.
"""
import json, os, re, time, sys
import requests

BASE = "https://www.gulfcounty-fl.gov"
SVC_URL = f"{BASE}/portal/svc/ContentItemSvc.asmx/GetItemList"

HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "requestFrom": "contentItem",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HeronReport-EagleEye-Archiver/1.0",
}

# Year folder objectIds, discovered from the Minutes page's document center widget.
YEAR_FOLDERS = {
    "2026": "21258526", "2025": "20734628", "2024": "20136404", "2023": "19395509",
    "2022": "18364809", "2021": "17453803", "2020": "16493721", "2019": "15310999",
    "2018": "12848094", "2017": "9102131",
}

# Shared Params payload template (field values other than the embedded objectId
# don't appear to be validated strictly by the service, but we mirror the
# real page context closely to be safe).
def build_params(folder_id):
    return {
        "ContextId": 9102094,
        "OneLink": "/cms/One.aspx",
        "RawUrl": f"/cms/One.aspx?pageId=7230112&portalId=6501074&objectId.142295={folder_id}&contextId.142295=9102094&parentId.142295=9102095",
        "Extension": "142295",
        "ClientId": "ctl00_ContentPlaceHolder1_ctl14",
        "Place": "cms",
        "ThisRequest": f"https://www.gulfcounty-fl.gov:443/cms/One.aspx?pageId=7230112&portalId=6501074&objectId.142295={folder_id}&contextId.142295=9102094&parentId.142295=9102095",
        "Link": f"/cms/One.aspx?portalId=6501074&pageId=7230112&objectId.142295={folder_id}&contextId.142295=9102094",
        "PortalId": "6501074",
        "PageId": "7230112",
        "HideDescription": True,
        "ShowDispSettings": False,
        "ShowSecurity": False,
        "ShowActivity": False,
        "ShowSubscription": True,
        "id": 5,
        "searchVal": "",
        "Segment": "https://www.gulfcounty-fl.gov/cms/One.aspx",
        "InstanceId": "142295",
    }

def get_folder_items(folder_id, retries=3):
    payload = {"parentId": folder_id, "Params": json.dumps(build_params(folder_id))}
    for attempt in range(retries):
        try:
            r = requests.post(SVC_URL, headers=HEADERS, json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            items = data["d"]["DataObject"]
            return items
        except Exception as e:
            print(f"  retry {attempt+1}/{retries} for folder {folder_id}: {e}")
            time.sleep(2)
    raise RuntimeError(f"Failed to fetch folder {folder_id} after {retries} retries")


def parse_meeting_name(name):
    """'May 26, 2026 - Regular Meeting' -> (date_iso, meeting_type)"""
    m = re.match(r"(.+?)\s*-\s*(.+)$", name)
    date_str, mtype = (m.group(1).strip(), m.group(2).strip()) if m else (name, "Unknown")
    date_iso = None
    for fmt in ("%B %d, %Y", "%B %d,%Y"):
        try:
            import datetime
            date_iso = datetime.datetime.strptime(date_str, fmt).date().isoformat()
            break
        except ValueError:
            continue
    return date_iso, mtype


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw_dir = os.path.join(root, "data", "raw_minutes")
    os.makedirs(raw_dir, exist_ok=True)
    index_path = os.path.join(root, "data", "minutes_index.json")

    full_index = []
    for year, fid in sorted(YEAR_FOLDERS.items(), reverse=True):
        print(f"=== {year} (folder {fid}) ===")
        items = get_folder_items(fid)
        year_dir = os.path.join(raw_dir, year)
        os.makedirs(year_dir, exist_ok=True)
        print(f"  {len(items)} items")
        for item in items:
            if item.get("Extension", "").lower() != "pdf":
                continue
            name = item["Name"]
            url = item["DownloadLink"]
            date_iso, mtype = parse_meeting_name(name)
            safe_name = re.sub(r"[^A-Za-z0-9 ,.\-]", "", name).strip() + ".pdf"
            local_path = os.path.join(year_dir, safe_name)
            rel_path = os.path.relpath(local_path, root)
            record = {
                "year": year,
                "name": name,
                "date": date_iso,
                "meeting_type": mtype,
                "url": url,
                "file_size": item.get("FileSizeString"),
                "local_path": rel_path,
                "item_id": item.get("ItemId"),
            }
            full_index.append(record)

            if not os.path.exists(local_path):
                try:
                    resp = requests.get(url, headers=HEADERS, timeout=30)
                    resp.raise_for_status()
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    print(f"    downloaded: {name}")
                except Exception as e:
                    print(f"    FAILED: {name} -> {e}")
            time.sleep(0.15)  # be polite to the county server

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(full_index, f, indent=2)
    print(f"\nTotal meeting documents indexed: {len(full_index)}")
    print(f"Index written to {index_path}")


if __name__ == "__main__":
    main()

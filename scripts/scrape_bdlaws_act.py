"""Scrape a single act (all sections) from bdlaws.minlaw.gov.bd into the
laws JSON schema used by scripts/reingest_small.py.

Usage:
    python scripts/scrape_bdlaws_act.py --act-id 1452 \
        --act-name "কপিরাইট আইন, ২০২৩" --act-no 34 \
        --published-date "১৮ সেপ্টেম্বর, ২০২৩" \
        --slug copyright_act_2023 --out data/laws/copyright_act_2023.json
"""
import argparse
import json
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import bs4
import requests

BASE = "https://bdlaws.minlaw.gov.bd"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalCopilotIngestion/1.0)"}
DELAY_SECONDS = 0.6


def fetch(session, url, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.text
        except requests.RequestException as exc:
            print(f"  retry {attempt + 1} for {url}: {exc}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}")


def get_section_links(session, act_id):
    html = fetch(session, f"{BASE}/act-{act_id}.html")
    seen, ordered = set(), []
    for match in re.findall(rf"act-{act_id}/section-(\d+)\.html", html):
        if match not in seen:
            seen.add(match)
            ordered.append(match)
    return ordered


def parse_section(session, act_id, section_id):
    html = fetch(session, f"{BASE}/act-{act_id}/section-{section_id}.html")
    soup = bs4.BeautifulSoup(html, "html.parser")
    head = soup.find("div", class_="txt-head")
    details = soup.find("div", class_="txt-details")
    if not details:
        return None
    title = head.get_text(" ", strip=True) if head else ""
    text = details.get_text(" ", strip=True)
    # Clean run-together fragments produced by <br> tags
    text = re.sub(r"\s+", " ", text).strip()
    return title, text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--act-id", required=True, help="bdlaws act id, e.g. 1452")
    parser.add_argument("--act-name", required=True, help="Display act name (as on bdlaws)")
    parser.add_argument("--act-no", default="", help="Act number (e.g. 34)")
    parser.add_argument("--published-date", default="", help="Gazette date string")
    parser.add_argument("--slug", required=True, help="id prefix, e.g. copyright_act_2023")
    parser.add_argument("--out", required=True, help="Output JSON file path")
    args = parser.parse_args()

    session = requests.Session()
    links = get_section_links(session, args.act_id)
    print(f"Act {args.act_id} ({args.act_name}): {len(links)} sections found")

    docs = []
    for idx, section_id in enumerate(links, 1):
        parsed = parse_section(session, args.act_id, section_id)
        time.sleep(DELAY_SECONDS)
        if not parsed:
            print(f"  skip section {section_id}: no txt-details")
            continue
        title, text = parsed
        sec_no_match = re.match(r"^([০-৯0-9]+)\s*।", text)
        sec_no = sec_no_match.group(1) if sec_no_match else str(section_id)
        docs.append({
            "id": f"{args.slug}_section_{sec_no}",
            "act": args.act_name,
            "section": f"Section {sec_no}",
            "title": title,
            "text": text,
            "jurisdiction": "Bangladesh",
            "act_no": args.act_no,
            "published_date": args.published_date,
        })
        if idx % 25 == 0:
            print(f"  {idx}/{len(links)} scraped")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=1)
    print(f"✅ Wrote {len(docs)} docs -> {args.out}")


if __name__ == "__main__":
    main()

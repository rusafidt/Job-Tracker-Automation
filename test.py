import os
import re
import sys
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

NOTION_VERSION = "2022-06-28"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"


def _get_env(name: str, *fallbacks: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    for fb in fallbacks:
        value = os.getenv(fb)
        if value:
            return value
    return ""


def _normalize_notion_id(raw: str) -> str:
    """Accept a raw id or pasted Notion URL and return a clean database id."""
    value = (raw or "").strip()
    if not value:
        return ""

    value = value.split("?", 1)[0].split("#", 1)[0]
    hex_only = "".join(re.findall(r"[0-9a-fA-F]", value))
    if len(hex_only) == 32:
        return (
            f"{hex_only[0:8]}-"
            f"{hex_only[8:12]}-"
            f"{hex_only[12:16]}-"
            f"{hex_only[16:20]}-"
            f"{hex_only[20:32]}"
        ).lower()
    return value


def main() -> int:
    load_dotenv()

    notion_api_key = _get_env("NOTION_API_KEY")
    notion_db_id = _normalize_notion_id(_get_env("NOTION_DATABASE_ID_NON_UK", "NOTION_DB_ID"))

    if not notion_api_key:
        print("Missing NOTION_API_KEY in .env", file=sys.stderr)
        return 1
    if not notion_db_id:
        print("Missing NOTION_DATABASE_ID (or NOTION_DB_ID) in .env", file=sys.stderr)
        return 1

    today = datetime.now().date()
    follow_up = today + timedelta(days=7)
    now_label = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def files_property(file_url: str, label: str):
        if not file_url:
            return {"files": []}
        return {
            "files": [
                {
                    "name": label,
                    "external": {"url": file_url},
                }
            ]
        }

    payload = {
        "parent": {"database_id": notion_db_id},
        "properties": {
            "Job Title": {
                "title": [{"text": {"content": f"TEST ENTRY {now_label}"}}]
            },
            "Company": {
                "rich_text": [{"text": {"content": "Test Company"}}]
            },
            "Date Applied": {"date": {"start": today.isoformat()}},
            "Status": {"select": {"name": "Applied"}},
            "Job Description File": files_property(
                "https://example.com/jd-test", "job_description.txt"
            ),
            "Resume File": files_property(
                "https://example.com/resume-test", "resume.pdf"
            ),
            "Cover Letter File": files_property(
                "https://example.com/cover-test", "cover_letter.pdf"
            ),
            "Follow-up Date": {"date": {"start": follow_up.isoformat()}},
            "Follow-up Count": {"number": 0},
            "Notes": {"rich_text": [{"text": {"content": "Python API write test"}}]},
            "Sam Checked": {"checkbox": False},
        },
    }

    headers = {
        "Authorization": f"Bearer {notion_api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    resp = requests.post(NOTION_PAGES_URL, headers=headers, json=payload, timeout=60)

    if resp.status_code >= 400:
        print("Notion API write failed", file=sys.stderr)
        print(f"Status: {resp.status_code}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        return 1

    data = resp.json()
    print("Notion write successful")
    print(f"Page ID: {data.get('id', '')}")
    print(f"Page URL: {data.get('url', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

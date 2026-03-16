import argparse
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Optional

import requests
from dotenv import load_dotenv

try:
    from fastapi import FastAPI, File, Form, UploadFile
    from fastapi.responses import JSONResponse
    from fastapi.responses import HTMLResponse
except ImportError:
    FastAPI = None
    File = None
    Form = None
    UploadFile = None
    JSONResponse = None
    HTMLResponse = None

try:
    from pdf2docx import Converter as PdfToDocxConverter
except ImportError:
    PdfToDocxConverter = None


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"
NOTION_VERSION = "2025-09-03"
NOTION_API_BASE = "https://api.notion.com/v1"


GROQ_API_KEY = None
NOTION_API_KEY = None
NOTION_DATABASE_ID = None
NOTION_DATABASE_ID_NON_UK = None
LOGGER = logging.getLogger("job_tracker")
APP_CONFIGURED = False


def _setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    log_format = "%(asctime)s | %(levelname)s | %(message)s"

    LOGGER.setLevel(level)
    LOGGER.handlers = []
    LOGGER.propagate = False

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(log_format))
    LOGGER.addHandler(console_handler)

    log_file_path = (os.getenv("JOB_TRACKER_LOG_FILE") or "").strip()
    if log_file_path:
        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(log_format))
        LOGGER.addHandler(file_handler)

    # Quiet noisy third-party logs in console.
    logging.getLogger("pdf2docx").setLevel(logging.ERROR)
    logging.getLogger("pdf2docx").propagate = False
    logging.getLogger("fitz").setLevel(logging.ERROR)
    logging.getLogger("fitz").propagate = False


def _require_env(name: str, value: str) -> None:
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")


def _normalize_notion_id(raw: str) -> str:
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


def _load_and_validate_config() -> None:
    load_dotenv()

    global GROQ_API_KEY
    global NOTION_API_KEY
    global NOTION_DATABASE_ID
    global NOTION_DATABASE_ID_NON_UK

    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    NOTION_API_KEY = os.getenv("NOTION_API_KEY")
    NOTION_DATABASE_ID = _normalize_notion_id(
        os.getenv("NOTION_DATABASE_ID") or os.getenv("NOTION_DB_ID")
    )

    _require_env("GROQ_API_KEY", GROQ_API_KEY)
    _require_env("NOTION_API_KEY", NOTION_API_KEY)
    _require_env("NOTION_DATABASE_ID", NOTION_DATABASE_ID)
    NOTION_DATABASE_ID_NON_UK = _normalize_notion_id(
        os.getenv("NOTION_DATABASE_ID_NON_UK", "REPLACE_WITH_NON_UK_DB_ID")
    )
    LOGGER.info(
        "Environment loaded: GROQ_API_KEY, NOTION_API_KEY, NOTION_DATABASE_ID (+ NOTION_DATABASE_ID_NON_UK placeholder)"
    )


def configure_runtime(debug: bool = False) -> None:
    global APP_CONFIGURED
    if APP_CONFIGURED:
        return

    _setup_logging(debug=debug)
    _load_and_validate_config()
    APP_CONFIGURED = True


def _resolve_database_id(region: str, non_uk_location: str = "") -> tuple[str, str]:
    region = (region or "uk").strip().lower()
    if region == "uk":
        return NOTION_DATABASE_ID, "UK"

    location = (non_uk_location or "").strip().lower()
    if location not in {"qatar", "dubai", "saudi", "remote"}:
        raise ValueError("For Non-UK jobs, select one location: qatar, dubai, saudi, or remote.")

    raw_db_id = (NOTION_DATABASE_ID_NON_UK or "").strip()
    if not raw_db_id or raw_db_id.startswith("REPLACE_WITH_"):
        raise ValueError("Set NOTION_DATABASE_ID_NON_UK in .env before using Non-UK jobs.")

    resolved = _normalize_notion_id(raw_db_id)
    return resolved, f"Non-UK ({location.title()})"


def _notion_headers(content_type_json: bool = True) -> dict:
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
    }
    if content_type_json:
        headers["Content-Type"] = "application/json"
    return headers


def _parse_groq_json(raw_content: str) -> dict:
    cleaned = (raw_content or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        company_match = re.search(r"company\s*[:=-]\s*(.+)", cleaned, flags=re.IGNORECASE)
        role_match = re.search(r"(role|job\s*title)\s*[:=-]\s*(.+)", cleaned, flags=re.IGNORECASE)
        company = company_match.group(1).strip().strip("\"'") if company_match else ""
        role = role_match.group(2).strip().strip("\"'") if role_match else ""
        if company or role:
            return {"company": company, "role": role}
        raise ValueError("Groq response did not contain valid JSON.")

    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Groq response JSON was not an object.")

    return data


def extract_job_info(jd_text):
    _require_env("GROQ_API_KEY", GROQ_API_KEY)
    LOGGER.info("Starting Groq extraction | model=%s | jd_chars=%d", GROQ_MODEL, len(jd_text))
    LOGGER.debug("JD preview: %s", jd_text[:1200])

    system_prompt = (
        "You extract structured data from job descriptions. "
        "Return JSON only in this exact format and no extra keys: "
        '{"company":"", "role":""}. If unknown, return empty strings.'
    )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Job description:\n\n{jd_text}"},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post(GROQ_ENDPOINT, headers=headers, json=payload, timeout=60)
    response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"].strip()
    LOGGER.info("Groq raw response: %s", content)

    parsed = _parse_groq_json(content)
    LOGGER.info("Groq parsed JSON: %s", parsed)

    company = str(parsed.get("company", "")).strip()
    role = str(parsed.get("role", "")).strip()

    if not company or not role:
        LOGGER.error(
            "Extraction failed confidence check | company='%s' | role='%s'", company, role
        )
        raise ValueError("Could not confidently extract company and role from the job description.")

    LOGGER.info("Extraction success | company='%s' | role='%s'", company, role)
    return {"company": company, "role": role}


@lru_cache(maxsize=8)
def _get_database_properties(database_id: str) -> dict:
    LOGGER.info("Loading Notion database schema | database_id=%s", database_id)
    resp = requests.get(
        f"{NOTION_API_BASE}/databases/{database_id}",
        headers=_notion_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    database_obj = resp.json()

    # Newer Notion API versions expose schema on the data source object.
    if database_obj.get("properties"):
        properties = database_obj.get("properties", {})
        LOGGER.debug("Loaded Notion DB properties (legacy path): %s", list(properties.keys()))
        return properties

    data_sources = database_obj.get("data_sources") or []
    if not data_sources:
        LOGGER.warning("No data sources found for database id %s", database_id)
        return {}

    data_source_id = data_sources[0].get("id")
    if not data_source_id:
        LOGGER.warning("Database has data_sources entry without id for %s", database_id)
        return {}

    ds_resp = requests.get(
        f"{NOTION_API_BASE}/data_sources/{data_source_id}",
        headers=_notion_headers(),
        timeout=60,
    )
    ds_resp.raise_for_status()
    properties = ds_resp.json().get("properties", {})
    LOGGER.debug(
        "Loaded Notion data source properties: %s",
        list(properties.keys()),
    )
    return properties


def _process_notion_uploads(
    jd_text: str,
    resume_name: str = "",
    resume_bytes: bytes = b"",
    resume_content_type: str = "",
    cover_name: str = "",
    cover_bytes: bytes = b"",
    cover_content_type: str = "",
) -> dict:
    jd_pdf_bytes = _jd_text_to_pdf_bytes(jd_text)

    resume_name = (resume_name or "").strip()
    cover_name = (cover_name or "").strip()
    has_resume = bool(resume_name and resume_bytes)
    has_cover = bool(cover_name and cover_bytes)
    is_resume_pdf = bool(has_resume and resume_name.lower().endswith(".pdf"))

    with ThreadPoolExecutor(max_workers=4) as executor:
        jd_future = executor.submit(
            _upload_file_to_notion,
            "job_description.pdf",
            jd_pdf_bytes,
            "application/pdf",
        )
        resume_future = (
            executor.submit(
                _upload_file_to_notion,
                resume_name,
                resume_bytes,
                resume_content_type or "application/octet-stream",
            )
            if has_resume
            else None
        )
        cover_future = (
            executor.submit(
                _upload_file_to_notion,
                cover_name,
                cover_bytes,
                cover_content_type or "application/octet-stream",
            )
            if has_cover
            else None
        )
        resume_docx_future = (
            executor.submit(_convert_pdf_bytes_to_docx_bytes, resume_bytes, resume_name)
            if is_resume_pdf
            else None
        )

        uploads = {
            "jd_upload": jd_future.result(),
            "resume_pdf_upload": resume_future.result() if resume_future else None,
            "cover_upload": cover_future.result() if cover_future else None,
            "resume_doc_upload": None,
        }

        if resume_docx_future:
            resume_docx_bytes = resume_docx_future.result()
            if resume_docx_bytes:
                docx_name = f"{os.path.splitext(resume_name)[0]}.docx"
                uploads["resume_doc_upload"] = _upload_file_to_notion(
                    docx_name,
                    resume_docx_bytes,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )

    return uploads


def _upload_file_to_notion(file_name: str, file_bytes: bytes, mime_type: str) -> dict:
    LOGGER.info("Uploading file to Notion | name='%s' | bytes=%d", file_name, len(file_bytes))

    create_resp = requests.post(
        f"{NOTION_API_BASE}/file_uploads",
        headers=_notion_headers(),
        json={},
        timeout=60,
    )
    create_resp.raise_for_status()

    upload_obj = create_resp.json()
    upload_id = upload_obj["id"]
    upload_url = upload_obj.get("upload_url") or f"{NOTION_API_BASE}/file_uploads/{upload_id}/send"

    send_resp = requests.post(
        upload_url,
        headers=_notion_headers(content_type_json=False),
        files={"file": (file_name, file_bytes, mime_type or "application/octet-stream")},
        timeout=120,
    )
    send_resp.raise_for_status()

    send_data = send_resp.json()
    if send_data.get("status") != "uploaded":
        raise ValueError(f"Notion file upload failed for {file_name}: {send_data}")

    LOGGER.info("Notion file uploaded | name='%s' | upload_id=%s", file_name, upload_id)
    return {"id": upload_id, "name": file_name}


def _upload_local_file_to_notion(path: str, fallback_name: str) -> Optional[dict]:
    if not path:
        return None
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    file_name = os.path.basename(path) or fallback_name
    with open(path, "rb") as f:
        file_bytes = f.read()

    return _upload_file_to_notion(file_name, file_bytes, "application/octet-stream")


def _find_libreoffice_converter() -> Optional[str]:
    """
    Resolve a reliable LibreOffice CLI executable path, prioritizing Windows installs.
    """
    preferred = [
        r"C:\Program Files\LibreOffice\program\soffice.com",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for path in preferred:
        if os.path.exists(path):
            return path

    return (
        shutil.which("soffice.com")
        or shutil.which("soffice.exe")
        or shutil.which("soffice")
        or shutil.which("libreoffice")
    )


def _convert_pdf_file_to_docx_bytes(pdf_path: str) -> Optional[bytes]:
    """
    Convert a PDF file to DOCX using LibreOffice CLI (soffice/libreoffice), if available.
    Returns DOCX bytes on success, else None.
    """
    with tempfile.TemporaryDirectory() as out_dir:
        pdf_base = os.path.splitext(os.path.basename(pdf_path))[0]
        docx_path = os.path.join(out_dir, f"{pdf_base}.docx")

        # Preferred path: pdf2docx Python converter.
        if PdfToDocxConverter is not None:
            try:
                cv = PdfToDocxConverter(pdf_path)
                cv.convert(docx_path)
                cv.close()
                if os.path.exists(docx_path):
                    with open(docx_path, "rb") as f:
                        return f.read()
            except Exception as exc:
                LOGGER.warning("Resume PDF->DOCX via pdf2docx failed: %s", exc)
        else:
            LOGGER.warning("pdf2docx not installed; trying LibreOffice fallback.")

        # Fallback path: LibreOffice CLI
        converter = _find_libreoffice_converter()
        if converter:
            cmd = [
                converter,
                "--headless",
                "--convert-to",
                'docx:"MS Word 2007 XML"',
                "--outdir",
                out_dir,
                pdf_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode == 0 and os.path.exists(docx_path):
                with open(docx_path, "rb") as f:
                    return f.read()
            LOGGER.warning(
                "Resume PDF->DOCX via LibreOffice failed (code=%s): %s",
                proc.returncode,
                (proc.stderr or proc.stdout or "").strip(),
            )
        else:
            LOGGER.warning("Resume PDF->DOCX failed: no available converter.")

    return None


def _convert_pdf_bytes_to_docx_bytes(pdf_bytes: bytes, pdf_name: str) -> Optional[bytes]:
    """
    Convert in-memory PDF bytes to DOCX bytes via LibreOffice CLI.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        src_path = os.path.join(tmp_dir, pdf_name or "resume.pdf")
        with open(src_path, "wb") as f:
            f.write(pdf_bytes)
        return _convert_pdf_file_to_docx_bytes(src_path)


def _normalize_property_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _find_property_name(db_props: dict, *candidates: str) -> Optional[str]:
    normalized_map = {_normalize_property_key(k): k for k in db_props.keys()}
    for candidate in candidates:
        found = normalized_map.get(_normalize_property_key(candidate))
        if found:
            return found
    return None


def _extract_property_options(prop_schema: dict) -> list[str]:
    if not prop_schema:
        return []

    prop_type = prop_schema.get("type")
    if prop_type == "select":
        options = (prop_schema.get("select") or {}).get("options") or []
    elif prop_type == "multi_select":
        options = (prop_schema.get("multi_select") or {}).get("options") or []
    elif prop_type == "status":
        options = (prop_schema.get("status") or {}).get("options") or []
    else:
        return []

    names = []
    seen = set()
    for option in options:
        name = str((option or {}).get("name") or "").strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


@lru_cache(maxsize=8)
def _get_source_options_for_database(database_id: str) -> list[str]:
    db_props = _get_database_properties(database_id)
    source_prop_name = _find_property_name(db_props, "Source", "Source Platform", "Application Source")
    if not source_prop_name:
        LOGGER.warning("Source property not found while loading UI options for database %s", database_id)
        return []

    options = _extract_property_options(db_props.get(source_prop_name) or {})
    LOGGER.info(
        "Loaded %d source options from Notion property '%s' for database %s",
        len(options),
        source_prop_name,
        database_id,
    )
    return options


def _get_source_options_by_region() -> dict[str, list[str]]:
    options = {"uk": [], "non_uk": []}

    if NOTION_DATABASE_ID:
        options["uk"] = _get_source_options_for_database(NOTION_DATABASE_ID)

    raw_non_uk_db = (NOTION_DATABASE_ID_NON_UK or "").strip()
    if raw_non_uk_db and not raw_non_uk_db.startswith("REPLACE_WITH_"):
        options["non_uk"] = _get_source_options_for_database(_normalize_notion_id(raw_non_uk_db))

    return options


def _pdf_escape_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _jd_text_to_pdf_bytes(jd_text: str) -> bytes:
    """
    Build a minimal, valid PDF from plain text without external dependencies.
    """
    wrapped_lines = []
    for raw_line in jd_text.splitlines() or [""]:
        chunks = textwrap.wrap(raw_line, width=95) or [""]
        wrapped_lines.extend(chunks)

    lines_per_page = 48
    pages = [
        wrapped_lines[i : i + lines_per_page]
        for i in range(0, len(wrapped_lines), lines_per_page)
    ] or [[""]]

    objects = []
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append("<< /Type /Pages /Kids [KIDS] /Count COUNT >>")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids = []
    for page_lines in pages:
        page_id = len(objects) + 1
        content_id = page_id + 1
        page_ids.append(page_id)

        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        )

        stream_lines = [
            "BT",
            "/F1 11 Tf",
            "14 TL",
            "1 0 0 1 50 742 Tm",
        ]
        first = True
        for line in page_lines:
            escaped = _pdf_escape_text(line)
            if first:
                stream_lines.append(f"({escaped}) Tj")
                first = False
            else:
                stream_lines.append("T*")
                stream_lines.append(f"({escaped}) Tj")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines) + "\n"
        stream_bytes = stream.encode("latin-1", errors="replace")

        content_obj = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n"
            f"{stream}"
            "endstream"
        )

        objects.append(page_obj)
        objects.append(content_obj)

    kids_value = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = objects[1].replace("KIDS", kids_value).replace("COUNT", str(len(page_ids)))

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]

    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{idx} 0 obj\n".encode("ascii"))
        output.extend(obj.encode("latin-1", errors="replace"))
        output.extend(b"\nendobj\n")

    xref_start = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        output.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    output.extend(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_start}\n"
            "%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def _build_source_property_value(source_value: str, prop_schema: dict) -> Optional[dict]:
    prop_type = prop_schema.get("type")
    source_value = (source_value or "").strip()
    if not source_value:
        return None

    if prop_type == "select":
        return {"select": {"name": source_value}}
    if prop_type == "rich_text":
        return {"rich_text": [{"text": {"content": source_value}}]}
    if prop_type == "title":
        return {"title": [{"text": {"content": source_value}}]}
    if prop_type == "multi_select":
        return {"multi_select": [{"name": source_value}]}
    if prop_type == "status":
        return {"status": {"name": source_value}}

    return None


def _build_file_property_value(prop_schema: dict, upload_obj) -> Optional[dict]:
    if not prop_schema:
        return None

    prop_type = prop_schema.get("type")

    if prop_type == "files":
        if not upload_obj:
            return {"files": []}
        uploads = upload_obj if isinstance(upload_obj, list) else [upload_obj]
        return {
            "files": [
                {
                    "name": item["name"],
                    "file_upload": {"id": item["id"]},
                }
                for item in uploads
            ]
        }

    if prop_type == "url":
        return {"url": None}

    return None


def create_notion_entry(data, database_id: str):
    _require_env("NOTION_API_KEY", NOTION_API_KEY)
    _require_env("TARGET_NOTION_DATABASE_ID", database_id)

    db_props = _get_database_properties(database_id)

    today = datetime.now().date()
    follow_up_date = today + timedelta(days=2)

    properties = {
        "Job Title": {"title": [{"text": {"content": data["role"]}}]},
        "Company": {"rich_text": [{"text": {"content": data["company"]}}]},
        "Date Applied": {"date": {"start": today.isoformat()}},
        "Follow-up Date": {"date": {"start": follow_up_date.isoformat()}},
        "Follow-up Count": {"number": 0},
        "Sam Checked": {"checkbox": False},
    }

    status_prop_name = _find_property_name(db_props, "Status")
    status_schema = db_props.get(status_prop_name) if status_prop_name else None
    status_value = _build_source_property_value(data.get("status", "Applied"), status_schema or {})
    if status_schema and status_value:
        properties[status_prop_name] = status_value
        LOGGER.info("Mapped status to Notion property '%s'", status_prop_name)
    else:
        LOGGER.warning("Status property was not found in database; status was not written.")

    source_prop_name = _find_property_name(db_props, "Source", "Source Platform", "Application Source")
    source_schema = db_props.get(source_prop_name) if source_prop_name else None
    source_value = _build_source_property_value(data.get("source", ""), source_schema or {})
    if source_schema and source_value:
        properties[source_prop_name] = source_value
        LOGGER.info("Mapped source to Notion property '%s'", source_prop_name)
    elif data.get("source"):
        if source_schema:
            LOGGER.warning(
                "Source value provided but property '%s' has unsupported type '%s'.",
                source_prop_name,
                source_schema.get("type"),
            )
        else:
            LOGGER.warning(
                "Source value provided but Source/Source Platform/Application Source property was not found in database."
            )

    non_uk_location = (data.get("non_uk_location") or "").strip()
    location_prop_name = _find_property_name(db_props, "Job Location", "Location", "Country")
    location_schema = db_props.get(location_prop_name) if location_prop_name else None
    location_value = _build_source_property_value(
        non_uk_location.title() if non_uk_location else "",
        location_schema or {},
    )
    if location_schema and location_value:
        properties[location_prop_name] = location_value
        LOGGER.info("Mapped non-UK location to Notion property '%s'", location_prop_name)
    elif non_uk_location:
        LOGGER.warning(
            "Non-UK location was provided but Job Location/Location/Country property was not found."
        )

    jd_prop_name = _find_property_name(db_props, "Job Description File", "Job Description", "JD File")
    jd_schema = db_props.get(jd_prop_name) if jd_prop_name else None
    jd_prop = _build_file_property_value(jd_schema, data.get("jd_upload"))
    if jd_schema and jd_prop is not None:
        properties[jd_prop_name] = jd_prop
    else:
        LOGGER.warning("Job Description file property not mapped in target database.")

    resume_pdf_prop_name = _find_property_name(
        db_props, "Resume File (PDF)", "Resume PDF", "Resume File", "Resume"
    )
    resume_pdf_schema = db_props.get(resume_pdf_prop_name) if resume_pdf_prop_name else None
    resume_pdf_prop = _build_file_property_value(resume_pdf_schema, data.get("resume_pdf_upload"))
    if resume_pdf_schema and resume_pdf_prop is not None:
        properties[resume_pdf_prop_name] = resume_pdf_prop
        LOGGER.info("Mapped resume PDF to Notion property '%s'", resume_pdf_prop_name)
    elif data.get("resume_pdf_upload"):
        LOGGER.warning("Resume PDF upload provided but Resume File (PDF) property was not found.")

    resume_doc_prop_name = _find_property_name(
        db_props, "Resume File (DOC)", "Resume DOC", "Resume File (DOCX)", "Resume DOCX"
    )
    resume_doc_schema = db_props.get(resume_doc_prop_name) if resume_doc_prop_name else None
    resume_doc_prop = _build_file_property_value(resume_doc_schema, data.get("resume_doc_upload"))
    if resume_doc_schema and resume_doc_prop is not None:
        properties[resume_doc_prop_name] = resume_doc_prop
        LOGGER.info("Mapped resume DOC to Notion property '%s'", resume_doc_prop_name)
    elif data.get("resume_doc_upload"):
        LOGGER.warning("Resume DOC upload provided but Resume File (DOC) property was not found.")

    cover_prop_name = _find_property_name(db_props, "Cover Letter File", "Cover Letter")
    cover_schema = db_props.get(cover_prop_name) if cover_prop_name else None
    cover_prop = _build_file_property_value(cover_schema, data.get("cover_upload"))
    if cover_schema and cover_prop is not None:
        properties[cover_prop_name] = cover_prop

    notes_text = []
    if (resume_pdf_schema or {}).get("type") == "url" and data.get("resume_pdf_upload"):
        notes_text.append("Resume File (PDF) property is URL type; could not attach Notion upload there.")
    if (resume_doc_schema or {}).get("type") == "url" and data.get("resume_doc_upload"):
        notes_text.append("Resume File (DOC) property is URL type; could not attach Notion upload there.")
    if (cover_schema or {}).get("type") == "url" and data.get("cover_upload"):
        notes_text.append("Cover Letter File property is URL type; could not attach Notion upload there.")
    if (jd_schema or {}).get("type") == "url" and data.get("jd_upload"):
        notes_text.append("Job Description File property is URL type; could not attach Notion upload there.")

    if "Notes" in db_props and notes_text:
        properties["Notes"] = {"rich_text": [{"text": {"content": " | ".join(notes_text)}}]}

    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }

    resp = requests.post(
        f"{NOTION_API_BASE}/pages",
        headers=_notion_headers(),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()
    LOGGER.info("Notion page created | page_id=%s | url=%s", result.get("id", ""), result.get("url", ""))
    return result


def process_application(
    jd_text: str,
    source: str,
    status: str = "Applied",
    region: str = "uk",
    non_uk_location: str = "",
    resume_path: str = "",
    cover_letter_path: str = "",
):
    database_id, database_label = _resolve_database_id(region, non_uk_location)
    LOGGER.info(
        "Processing application | source='%s' | target_db='%s' | resume_provided=%s | cover_provided=%s",
        source,
        database_label,
        bool(resume_path),
        bool(cover_letter_path),
    )

    info = extract_job_info(jd_text)

    resume_name = ""
    resume_bytes = b""
    if resume_path:
        resume_path = resume_path.strip()
        if resume_path:
            if not os.path.exists(resume_path):
                raise FileNotFoundError(f"File not found: {resume_path}")
            resume_name = os.path.basename(resume_path) or "resume.pdf"
            with open(resume_path, "rb") as f:
                resume_bytes = f.read()

    cover_name = ""
    cover_bytes = b""
    if cover_letter_path:
        cover_letter_path = cover_letter_path.strip()
        if cover_letter_path:
            if not os.path.exists(cover_letter_path):
                raise FileNotFoundError(f"File not found: {cover_letter_path}")
            cover_name = os.path.basename(cover_letter_path) or "cover_letter.pdf"
            with open(cover_letter_path, "rb") as f:
                cover_bytes = f.read()

    uploads = _process_notion_uploads(
        jd_text=jd_text,
        resume_name=resume_name,
        resume_bytes=resume_bytes,
        cover_name=cover_name,
        cover_bytes=cover_bytes,
    )

    notion_payload = {
        "company": info["company"],
        "role": info["role"],
        "source": source,
        "status": status,
        "jd_upload": uploads["jd_upload"],
        "resume_pdf_upload": uploads["resume_pdf_upload"],
        "resume_doc_upload": uploads["resume_doc_upload"],
        "cover_upload": uploads["cover_upload"],
    }

    notion_result = create_notion_entry(notion_payload, database_id=database_id)

    return {
        "company": info["company"],
        "role": info["role"],
        "source": source,
        "status": status,
        "region": region,
        "non_uk_location": non_uk_location,
        "database_label": database_label,
        "notion_page_url": notion_result.get("url", ""),
    }


def _read_multiline_input(prompt: str) -> str:
    print(prompt)
    print("Press Enter on an empty line when finished:")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break

        if line.strip() == "":
            if lines:
                break
            continue

        lines.append(line)

    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("No job description text was provided.")

    return text


def run_cli():
    jd_text = _read_multiline_input("Paste the job description below.")
    region = input("Region (uk/non_uk): ").strip().lower() or "uk"
    non_uk_location = ""
    if region == "non_uk":
        non_uk_location = input("Non-UK location (qatar/dubai/saudi/remote): ").strip().lower()
    status = input("Status (Applied/Under Review): ").strip() or "Applied"
    source = input("Source platform (LinkedIn, company site, etc.): ").strip()
    resume_path = input("Resume file path (optional, press Enter to skip): ").strip()
    cover_path = input("Cover letter file path (optional, press Enter to skip): ").strip()

    result = process_application(
        jd_text,
        source=source,
        status=status,
        region=region,
        non_uk_location=non_uk_location,
        resume_path=resume_path,
        cover_letter_path=cover_path,
    )

    print("Job saved successfully")
    print(f"Company: {result['company']}")
    print(f"Role: {result['role']}")
    print(f"Source: {result['source']}")
    print(f"Status: {result['status']}")
    print(f"Target DB: {result['database_label']}")
    print("Notion entry created")
    print(f"Notion page: {result['notion_page_url']}")


def _render_fastapi_html(
    error: str = "",
    result: Optional[dict] = None,
    jd_text: str = "",
    source: str = "",
    status: str = "Applied",
    region: str = "uk",
    non_uk_location: str = "",
    source_options_by_region: Optional[dict[str, list[str]]] = None,
) -> str:
    safe_error = html.escape(error)
    safe_jd = html.escape(jd_text)
    safe_source = html.escape(source)
    safe_status = html.escape(status or "Applied")
    safe_region = html.escape(region or "uk")
    safe_location = html.escape(non_uk_location or "")
    source_options_by_region = source_options_by_region or {"uk": [], "non_uk": []}
    source_options_json = json.dumps(source_options_by_region)

    result_block = ""
    if result:
        company = html.escape(result.get("company", ""))
        role = html.escape(result.get("role", ""))
        source_val = html.escape(result.get("source", ""))
        status_val = html.escape(result.get("status", "Applied"))
        notion_url = html.escape(result.get("notion_page_url", ""))
        result_block = f"""
        <div class="result-card">
          <div class="result-title">Saved To Notion</div>
          <div class="result-grid">
            <div><span>Company</span><strong>{company}</strong></div>
            <div><span>Role</span><strong>{role}</strong></div>
            <div><span>Source</span><strong>{source_val or "N/A"}</strong></div>
            <div><span>Status</span><strong>{status_val}</strong></div>
            <div><span>Database</span><strong>{html.escape(result.get("database_label", ""))}</strong></div>
            <div><span>Entry</span><a href="{notion_url}" target="_blank">Open Notion Page</a></div>
          </div>
        </div>
        """

    error_block = f"<div class=\"result-card error\">{safe_error}</div>" if safe_error else ""

    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet" />
    <title>Job Tracker</title>
    <style>
      :root {{
        --bg: #f6f8ff;
        --bg2: #eaf2ff;
        --ink: #0d1b2a;
        --muted: #4c5c73;
        --accent: #0f9d7a;
        --accent-dark: #067558;
        --card: #ffffffcc;
        --line: #d8e0f0;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-height: 100vh;
        font-family: "Space Grotesk", sans-serif;
        color: var(--ink);
        background:
          radial-gradient(circle at 10% 10%, #c9dcff 0%, transparent 40%),
          radial-gradient(circle at 90% 20%, #c4ffe9 0%, transparent 35%),
          linear-gradient(140deg, var(--bg), var(--bg2));
        padding: 24px;
      }}
      .shell {{
        max-width: 980px;
        margin: 0 auto;
        display: grid;
        grid-template-columns: 1fr;
        gap: 20px;
      }}
      .hero {{
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 22px;
        background: var(--card);
        backdrop-filter: blur(6px);
      }}
      .hero h1 {{
        margin: 0 0 6px;
        font-size: clamp(24px, 3vw, 34px);
      }}
      .hero p {{
        margin: 0;
        color: var(--muted);
      }}
      .panel {{
        border: 1px solid var(--line);
        border-radius: 18px;
        background: var(--card);
        backdrop-filter: blur(6px);
        padding: 20px;
        animation: rise 300ms ease-out;
      }}
      @keyframes rise {{
        from {{ transform: translateY(8px); opacity: 0; }}
        to {{ transform: translateY(0); opacity: 1; }}
      }}
      form {{ display: grid; gap: 14px; }}
      label {{
        font-size: 13px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--muted);
      }}
      input[type=text], textarea, input[type=file], select {{
        width: 100%;
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 12px 14px;
        background: #fff;
        font-family: "IBM Plex Mono", monospace;
      }}
      textarea {{ min-height: 240px; resize: vertical; }}
      button {{
        width: fit-content;
        border: none;
        border-radius: 999px;
        padding: 12px 18px;
        background: linear-gradient(120deg, var(--accent), #18b892);
        color: #fff;
        font-weight: 700;
        letter-spacing: 0.02em;
        cursor: pointer;
      }}
      button:hover {{ background: linear-gradient(120deg, var(--accent-dark), var(--accent)); }}
      .result-card {{
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 16px;
        background: #fff;
      }}
      .result-title {{
        font-size: 18px;
        font-weight: 700;
        margin-bottom: 10px;
      }}
      .result-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 10px;
      }}
      .result-grid span {{
        display: block;
        color: var(--muted);
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
      }}
      .result-grid a {{ color: var(--accent-dark); font-weight: 600; }}
      .error {{ color: #9b1c31; white-space: pre-wrap; }}
      .hidden {{ display: none; }}
      .status-toggle {{
        display: flex;
        gap: 10px;
      }}
      .status-toggle input {{
        position: absolute;
        opacity: 0;
        pointer-events: none;
      }}
      .status-chip {{
        border: 1px solid var(--line);
        border-radius: 999px;
        padding: 10px 14px;
        background: #fff;
        cursor: pointer;
        font-family: "IBM Plex Mono", monospace;
      }}
      .status-toggle input:checked + label {{
        border-color: var(--accent-dark);
        background: #dff8ef;
        color: #0b5f48;
        font-weight: 700;
      }}
      .field-hint {{
        margin-top: 6px;
        color: var(--muted);
        font-size: 12px;
      }}
    </style>
  </head>
  <body>
    <main class="shell">
      <section class="hero">
        <h1>Job Application Tracker</h1>
        <p>Paste a JD, add resume and cover letter, and save directly into your Notion tracker.</p>
      </section>
      <section class="panel">
        <form method="post" enctype="multipart/form-data">
          <div>
            <label>Region</label>
            <select id="region" name="region">
              <option value="uk" {"selected" if safe_region == "uk" else ""}>UK</option>
              <option value="non_uk" {"selected" if safe_region == "non_uk" else ""}>Non-UK</option>
            </select>
          </div>
          <div id="non-uk-wrap" class="{'hidden' if safe_region != 'non_uk' else ''}">
            <label>Non-UK Location</label>
            <select name="non_uk_location">
              <option value="">Select location</option>
              <option value="qatar" {"selected" if safe_location == "qatar" else ""}>Qatar</option>
              <option value="dubai" {"selected" if safe_location == "dubai" else ""}>Dubai</option>
              <option value="saudi" {"selected" if safe_location == "saudi" else ""}>Saudi</option>
              <option value="remote" {"selected" if safe_location == "remote" else ""}>Remote</option>
            </select>
          </div>
          <div>
            <label>Source Platform</label>
            <input
              id="source"
              type="text"
              name="source"
              list="source-options"
              value="{safe_source}"
              placeholder="LinkedIn, Wellfound, Company Careers"
            />
            <datalist id="source-options"></datalist>
            <div class="field-hint">Existing options load from the selected Notion database. You can still type a new value.</div>
          </div>
          <div>
            <label>Status</label>
            <div class="status-toggle">
              <input type="radio" id="status_applied" name="status" value="Applied" {"checked" if safe_status == "Applied" else ""} />
              <label class="status-chip" for="status_applied">Applied</label>
              <input type="radio" id="status_review" name="status" value="Under Review" {"checked" if safe_status == "Under Review" else ""} />
              <label class="status-chip" for="status_review">Under Review</label>
            </div>
          </div>
          <div>
            <label>Job Description</label>
            <textarea name="jd_text" required>{safe_jd}</textarea>
          </div>
          <div>
            <label>Resume (optional)</label>
            <input type="file" name="resume_file" />
          </div>
          <div>
            <label>Cover Letter (optional)</label>
            <input type="file" name="cover_file" />
          </div>
          <button type="submit">Save To Notion</button>
        </form>
      </section>
      {error_block}
      {result_block}
    </main>
    <script>
      const regionSelect = document.getElementById("region");
      const nonUkWrap = document.getElementById("non-uk-wrap");
      const sourceInput = document.getElementById("source");
      const sourceOptions = document.getElementById("source-options");
      const sourceOptionsByRegion = {source_options_json};
      function toggleNonUk() {{
        if (!regionSelect || !nonUkWrap) return;
        nonUkWrap.classList.toggle("hidden", regionSelect.value !== "non_uk");
      }}
      function refreshSourceOptions() {{
        if (!regionSelect || !sourceOptions) return;
        const regionKey = regionSelect.value === "non_uk" ? "non_uk" : "uk";
        const items = sourceOptionsByRegion[regionKey] || [];
        sourceOptions.innerHTML = "";
        items.forEach((value) => {{
          const option = document.createElement("option");
          option.value = value;
          sourceOptions.appendChild(option);
        }});
        if (sourceInput) {{
          sourceInput.placeholder = items.length
            ? `Choose existing or type new (${{items.slice(0, 2).join(", ")}}${{items.length > 2 ? ", ..." : ""}})`
            : "LinkedIn, Wellfound, Company Careers";
        }}
      }}
      toggleNonUk();
      refreshSourceOptions();
      regionSelect && regionSelect.addEventListener("change", toggleNonUk);
      regionSelect && regionSelect.addEventListener("change", refreshSourceOptions);
    </script>
  </body>
</html>
"""


def create_web_app():
    if FastAPI is None:
        raise RuntimeError(
            "FastAPI is not installed. Run: pip install fastapi uvicorn python-multipart"
        )

    app = FastAPI(title="Job Tracker")

    @app.on_event("startup")
    async def startup_event():
        configure_runtime(debug=os.getenv("DEBUG", "").strip().lower() in {"1", "true", "yes", "on"})

    @app.get("/healthz", response_class=JSONResponse)
    async def healthz():
        return JSONResponse({"status": "ok"})

    @app.get("/readyz", response_class=JSONResponse)
    async def readyz():
        try:
            configure_runtime(
                debug=os.getenv("DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
            )
            return JSONResponse({"status": "ready"})
        except Exception as exc:
            LOGGER.exception("Readiness check failed")
            return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)

    @app.get("/", response_class=HTMLResponse)
    async def index_get():
        return HTMLResponse(content=_render_fastapi_html(source_options_by_region=_get_source_options_by_region()))

    @app.post("/", response_class=HTMLResponse)
    async def index_post(
        region: str = Form("uk"),
        non_uk_location: str = Form(""),
        status: str = Form("Applied"),
        source: str = Form(""),
        jd_text: str = Form(...),
        resume_file: Optional[UploadFile] = File(None),
        cover_file: Optional[UploadFile] = File(None),
    ):
        region = (region or "uk").strip().lower()
        non_uk_location = (non_uk_location or "").strip().lower()
        source = source.strip()
        status = (status or "Applied").strip()
        if status not in {"Applied", "Under Review"}:
            status = "Applied"
        jd_text = jd_text.strip()
        LOGGER.info(
            "Web submission received | region='%s' | non_uk='%s' | status='%s' | source='%s' | jd_chars=%d | resume='%s' | cover='%s'",
            region,
            non_uk_location,
            status,
            source,
            len(jd_text),
            (resume_file.filename if resume_file else ""),
            (cover_file.filename if cover_file else ""),
        )

        if not jd_text:
            return HTMLResponse(
                content=_render_fastapi_html(
                    error="Job description is required.",
                    jd_text=jd_text,
                    source=source,
                    status=status,
                    region=region,
                    non_uk_location=non_uk_location,
                    source_options_by_region=_get_source_options_by_region(),
                )
            )

        try:
            info = extract_job_info(jd_text)

            resume_name = ""
            resume_bytes = b""
            resume_content_type = ""
            if resume_file and resume_file.filename:
                resume_name = resume_file.filename.strip() or "resume.pdf"
                resume_bytes = await resume_file.read()
                resume_content_type = resume_file.content_type or "application/octet-stream"

            cover_name = ""
            cover_bytes = b""
            cover_content_type = ""
            if cover_file and cover_file.filename:
                cover_name = cover_file.filename.strip() or "cover_letter.pdf"
                cover_bytes = await cover_file.read()
                cover_content_type = cover_file.content_type or "application/octet-stream"

            uploads = _process_notion_uploads(
                jd_text=jd_text,
                resume_name=resume_name,
                resume_bytes=resume_bytes,
                resume_content_type=resume_content_type,
                cover_name=cover_name,
                cover_bytes=cover_bytes,
                cover_content_type=cover_content_type,
            )

            notion_payload = {
                "company": info["company"],
                "role": info["role"],
                "source": source,
                "status": status,
                "region": region,
                "non_uk_location": non_uk_location,
                "jd_upload": uploads["jd_upload"],
                "resume_pdf_upload": uploads["resume_pdf_upload"],
                "resume_doc_upload": uploads["resume_doc_upload"],
                "cover_upload": uploads["cover_upload"],
            }
            database_id, database_label = _resolve_database_id(region, non_uk_location)
            notion_result = create_notion_entry(notion_payload, database_id=database_id)

            result = {
                "company": info["company"],
                "role": info["role"],
                "source": source,
                "status": status,
                "database_label": database_label,
                "notion_page_url": notion_result.get("url", ""),
            }
            return HTMLResponse(
                content=_render_fastapi_html(
                    result=result,
                    source_options_by_region=_get_source_options_by_region(),
                )
            )
        except Exception as exc:
            LOGGER.exception("Web submission failed")
            return HTMLResponse(
                content=_render_fastapi_html(
                    error=str(exc),
                    jd_text=jd_text,
                    source=source,
                    status=status,
                    region=region,
                    non_uk_location=non_uk_location,
                    source_options_by_region=_get_source_options_by_region(),
                )
            )

    return app


def main():
    parser = argparse.ArgumentParser(description="Job application tracker automation")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Use terminal input mode (default is browser UI)",
    )
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"), help="Web host")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")), help="Web port")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not auto-open browser tab when starting web UI",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging",
    )
    args = parser.parse_args()

    configure_runtime(debug=args.debug)

    if args.cli:
        run_cli()
        return

    app = create_web_app()
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Uvicorn is required for web mode. Run: pip install uvicorn") from exc

    should_open_browser = (
        not args.no_open
        and sys.stdout.isatty()
        and args.host not in {"0.0.0.0", "::"}
        and os.getenv("RENDER", "").strip().lower() != "true"
    )
    if should_open_browser:
        url = f"http://{args.host}:{args.port}"
        webbrowser.open(url)

    uvicorn.run(app, host=args.host, port=args.port)


app = create_web_app() if FastAPI is not None else None


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        LOGGER.exception("Fatal error")
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

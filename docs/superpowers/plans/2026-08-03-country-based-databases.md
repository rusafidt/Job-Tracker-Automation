# Country-Based Notion Databases (Qatar / UAE / India) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the UK / Non-UK Notion database split in `job_tracker.py` with three country-based databases (Qatar, UAE, India), where UAE and India additionally track a City that Groq auto-extracts from the JD, the user can freely override, and that grows over time as new cities are typed in and written to Notion.

**Architecture:** Single-file changes throughout `job_tracker.py` (per the project's existing single-module structure — see `CLAUDE.md`). `region`/`non_uk_location` are renamed to `country`/`city` end-to-end: config globals, `_resolve_database_id`, Groq extraction, Notion property mapping, the two process-application entry points, the inlined web UI (HTML + JS), the FastAPI routes, and the CLI prompts. City suggestions come from a small hardcoded seed list merged with live Notion select options (which grow automatically because Notion auto-adds new select options when a page is written with an unseen value).

**Tech Stack:** Python, FastAPI, Notion API (`2025-09-03`), Groq chat completions API. No test framework exists in this repo (`CLAUDE.md`: "no automated test suite") — verification is manual (`python -m py_compile`, targeted `python -c` checks against pure functions, and a final live manual run against real `.env` credentials).

---

## Task 1: Config globals and env file

**Files:**
- Modify: `job_tracker.py:59-70` (module globals)
- Modify: `job_tracker.py:194-216` (`_load_and_validate_config`)
- Modify: `.env.example`

- [ ] **Step 1: Replace the database-id globals**

In `job_tracker.py`, replace:

```python
GROQ_API_KEY = None
NOTION_API_KEY = None
NOTION_DATABASE_ID = None
NOTION_DATABASE_ID_NON_UK = None
```

with:

```python
GROQ_API_KEY = None
NOTION_API_KEY = None
NOTION_DATABASE_ID_QATAR = None
NOTION_DATABASE_ID_UAE = None
NOTION_DATABASE_ID_INDIA = None
```

- [ ] **Step 2: Rewrite `_load_and_validate_config`**

Replace the full function body (`job_tracker.py:194-216`):

```python
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
```

with:

```python
def _load_and_validate_config() -> None:
    load_dotenv()

    global GROQ_API_KEY
    global NOTION_API_KEY
    global NOTION_DATABASE_ID_QATAR
    global NOTION_DATABASE_ID_UAE
    global NOTION_DATABASE_ID_INDIA

    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    NOTION_API_KEY = os.getenv("NOTION_API_KEY")
    NOTION_DATABASE_ID_QATAR = _normalize_notion_id(os.getenv("NOTION_DATABASE_ID_QATAR"))
    NOTION_DATABASE_ID_UAE = _normalize_notion_id(os.getenv("NOTION_DATABASE_ID_UAE"))
    NOTION_DATABASE_ID_INDIA = _normalize_notion_id(os.getenv("NOTION_DATABASE_ID_INDIA"))

    _require_env("GROQ_API_KEY", GROQ_API_KEY)
    _require_env("NOTION_API_KEY", NOTION_API_KEY)
    _require_env("NOTION_DATABASE_ID_QATAR", NOTION_DATABASE_ID_QATAR)
    _require_env("NOTION_DATABASE_ID_UAE", NOTION_DATABASE_ID_UAE)
    _require_env("NOTION_DATABASE_ID_INDIA", NOTION_DATABASE_ID_INDIA)
    LOGGER.info(
        "Environment loaded: GROQ_API_KEY, NOTION_API_KEY, NOTION_DATABASE_ID_QATAR, "
        "NOTION_DATABASE_ID_UAE, NOTION_DATABASE_ID_INDIA"
    )
```

- [ ] **Step 3: Update `.env.example`**

Replace the full contents of `.env.example`:

```
GROQ_API_KEY=
NOTION_API_KEY=
NOTION_DATABASE_ID_QATAR=
NOTION_DATABASE_ID_UAE=
NOTION_DATABASE_ID_INDIA=

# Optional
DEBUG=false
HOST=0.0.0.0
PORT=5000
JOB_TRACKER_LOG_FILE=
```

- [ ] **Step 4: Verify syntax**

Run: `python -m py_compile job_tracker.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add job_tracker.py .env.example
git commit -m "feat: replace UK/Non-UK env vars with per-country database ids"
```

---

## Task 2: `_resolve_database_id` for country/city

**Files:**
- Modify: `job_tracker.py:229-243`

- [ ] **Step 1: Replace the function**

Replace:

```python
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
```

with:

```python
COUNTRY_DATABASE_IDS = {
    "qatar": lambda: NOTION_DATABASE_ID_QATAR,
    "uae": lambda: NOTION_DATABASE_ID_UAE,
    "india": lambda: NOTION_DATABASE_ID_INDIA,
}
COUNTRY_LABELS = {"qatar": "Qatar", "uae": "UAE", "india": "India"}
CITIES_BY_COUNTRY = {"uae", "india"}


def _resolve_database_id(country: str, city: str = "") -> tuple[str, str]:
    country = (country or "").strip().lower()
    if country not in COUNTRY_DATABASE_IDS:
        country = "qatar"

    database_id = COUNTRY_DATABASE_IDS[country]()
    if not database_id:
        raise ValueError(f"Set NOTION_DATABASE_ID_{country.upper()} in .env before using {country.title()} jobs.")

    label = COUNTRY_LABELS[country]
    city = (city or "").strip() if country in CITIES_BY_COUNTRY else ""
    if city:
        label = f"{label} ({city.title()})"

    return database_id, label
```

- [ ] **Step 2: Verify with a manual check**

Run:
```bash
python -c "
import job_tracker as jt
jt.NOTION_DATABASE_ID_QATAR = 'qatar-db-id'
jt.NOTION_DATABASE_ID_UAE = 'uae-db-id'
jt.NOTION_DATABASE_ID_INDIA = 'india-db-id'
print(jt._resolve_database_id('qatar'))
print(jt._resolve_database_id('uae', 'Dubai'))
print(jt._resolve_database_id('india', 'Bangalore'))
print(jt._resolve_database_id(''))
print(jt._resolve_database_id('bogus'))
"
```
Expected:
```
('qatar-db-id', 'Qatar')
('uae-db-id', 'UAE (Dubai)')
('india-db-id', 'India (Bangalore)')
('qatar-db-id', 'Qatar')
('qatar-db-id', 'Qatar')
```

- [ ] **Step 3: Commit**

```bash
git add job_tracker.py
git commit -m "feat: resolve Notion database by country/city instead of region"
```

---

## Task 3: Groq extraction returns country/city

**Files:**
- Modify: `job_tracker.py:286-331` (`extract_job_info`)

- [ ] **Step 1: Update the system prompt and payload**

Replace:

```python
    system_prompt = (
        "You extract structured data from job descriptions. "
        "Return JSON only in this exact format and no extra keys: "
        '{"company":"", "role":""}. If unknown, return empty strings.'
    )
```

with:

```python
    system_prompt = (
        "You extract structured data from job descriptions. "
        "Return JSON only in this exact format and no extra keys: "
        '{"company":"", "role":"", "country":"", "city":""}. '
        'If unknown, return empty strings. '
        '"country" must be one of "qatar", "uae", "india", or empty if the job description '
        "does not indicate one of those countries. "
        '"city" should only be filled in when country is "uae" or "india" (e.g. "Dubai", '
        '"Abu Dhabi", "Bangalore", "Kochi"); leave it empty otherwise.'
    )
```

- [ ] **Step 2: Parse and return the new fields**

Replace:

```python
    company = str(parsed.get("company", "")).strip()
    role = str(parsed.get("role", "")).strip()

    if not company or not role:
        LOGGER.error(
            "Extraction failed confidence check | company='%s' | role='%s'", company, role
        )
        raise ValueError("Could not confidently extract company and role from the job description.")

    LOGGER.info("Extraction success | company='%s' | role='%s'", company, role)
    return {"company": company, "role": role}
```

with:

```python
    company = str(parsed.get("company", "")).strip()
    role = str(parsed.get("role", "")).strip()
    country = str(parsed.get("country", "")).strip().lower()
    if country not in COUNTRY_DATABASE_IDS:
        country = ""
    city = str(parsed.get("city", "")).strip() if country in CITIES_BY_COUNTRY else ""

    if not company or not role:
        LOGGER.error(
            "Extraction failed confidence check | company='%s' | role='%s'", company, role
        )
        raise ValueError("Could not confidently extract company and role from the job description.")

    LOGGER.info(
        "Extraction success | company='%s' | role='%s' | country='%s' | city='%s'",
        company, role, country, city,
    )
    return {"company": company, "role": role, "country": country, "city": city}
```

- [ ] **Step 3: Verify syntax**

Run: `python -m py_compile job_tracker.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add job_tracker.py
git commit -m "feat: extract country/city from job description via Groq"
```

---

## Task 4: Notion property mapping — Country + City fields, cache invalidation

**Files:**
- Modify: `job_tracker.py:780-903` (`create_notion_entry`)

- [ ] **Step 1: Replace the location-mapping block**

Replace:

```python
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
```

with:

```python
    country = (data.get("country") or "").strip().lower()
    if country not in COUNTRY_DATABASE_IDS:
        country = "qatar"
    city = (data.get("city") or "").strip()

    country_prop_name = _find_property_name(db_props, "Country")
    country_schema = db_props.get(country_prop_name) if country_prop_name else None
    country_value = _build_source_property_value(COUNTRY_LABELS[country], country_schema or {})
    if country_schema and country_value:
        properties[country_prop_name] = country_value
        LOGGER.info("Mapped country to Notion property '%s'", country_prop_name)
    else:
        LOGGER.warning("Country property was not found in database; country was not written.")

    city_prop_name = _find_property_name(db_props, "City", "Job Location", "Location")
    city_schema = db_props.get(city_prop_name) if city_prop_name else None
    city_value = _build_source_property_value(city, city_schema or {})
    if city_schema and city_value:
        properties[city_prop_name] = city_value
        LOGGER.info("Mapped city to Notion property '%s'", city_prop_name)
    elif city:
        LOGGER.warning("City was provided but City/Job Location/Location property was not found.")
```

- [ ] **Step 2: Clear the schema cache after a successful write, so new select options show up next load**

Replace:

```python
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
```

with:

```python
    resp = requests.post(
        f"{NOTION_API_BASE}/pages",
        headers=_notion_headers(),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()
    LOGGER.info("Notion page created | page_id=%s | url=%s", result.get("id", ""), result.get("url", ""))
    _get_database_properties.cache_clear()
    return result
```

- [ ] **Step 3: Verify syntax**

Run: `python -m py_compile job_tracker.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add job_tracker.py
git commit -m "feat: write Country/City Notion properties and refresh schema cache after save"
```

---

## Task 5: Dynamic city option lists

**Files:**
- Modify: `job_tracker.py:610-638` (`_get_source_options_for_database` / `_get_source_options_by_region`)

- [ ] **Step 1: Rename the by-region source-options function to by-country**

Replace:

```python
def _get_source_options_by_region() -> dict[str, list[str]]:
    options = {"uk": [], "non_uk": []}

    if NOTION_DATABASE_ID:
        options["uk"] = _get_source_options_for_database(NOTION_DATABASE_ID)

    raw_non_uk_db = (NOTION_DATABASE_ID_NON_UK or "").strip()
    if raw_non_uk_db and not raw_non_uk_db.startswith("REPLACE_WITH_"):
        options["non_uk"] = _get_source_options_for_database(_normalize_notion_id(raw_non_uk_db))

    return options
```

with:

```python
def _get_source_options_by_country() -> dict[str, list[str]]:
    options = {"qatar": [], "uae": [], "india": []}
    for country, getter in COUNTRY_DATABASE_IDS.items():
        database_id = getter()
        if database_id:
            options[country] = _get_source_options_for_database(database_id)
    return options


CITY_SEED_OPTIONS = {
    "uae": ["Dubai", "Abu Dhabi", "Sharjah", "Remote"],
    "india": [
        "Bangalore", "Kochi", "Chennai", "Hyderabad",
        "Mumbai", "Pune", "Delhi NCR", "Remote",
    ],
}


@lru_cache(maxsize=8)
def _get_city_options_for_database(database_id: str) -> list[str]:
    db_props = _get_database_properties(database_id)
    city_prop_name = _find_property_name(db_props, "City", "Job Location", "Location")
    if not city_prop_name:
        LOGGER.warning("City property not found while loading UI options for database %s", database_id)
        return []
    return _extract_property_options(db_props.get(city_prop_name) or {})


def _merge_city_options(seed: list[str], live: list[str]) -> list[str]:
    merged = []
    seen = set()
    for name in seed + live:
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            merged.append(name)
    return merged


def _get_city_options_by_country() -> dict[str, list[str]]:
    options = {"uae": [], "india": []}
    for country in options:
        database_id = COUNTRY_DATABASE_IDS[country]()
        live = _get_city_options_for_database(database_id) if database_id else []
        options[country] = _merge_city_options(CITY_SEED_OPTIONS[country], live)
    return options
```

- [ ] **Step 2: Clear the new cache alongside the schema cache after a successful save**

In `create_notion_entry` (edited in Task 4), change:

```python
    _get_database_properties.cache_clear()
    return result
```

to:

```python
    _get_database_properties.cache_clear()
    _get_city_options_for_database.cache_clear()
    return result
```

- [ ] **Step 3: Verify syntax**

Run: `python -m py_compile job_tracker.py`
Expected: no output, exit code 0.

- [ ] **Step 4: Verify the merge helper in isolation**

Run:
```bash
python -c "
import job_tracker as jt
print(jt._merge_city_options(['Dubai', 'Abu Dhabi'], ['dubai', 'Sharjah']))
"
```
Expected: `['Dubai', 'Abu Dhabi', 'Sharjah']` (case-insensitive de-dup, seed order preserved, new live option appended).

- [ ] **Step 5: Commit**

```bash
git add job_tracker.py
git commit -m "feat: derive growing city suggestion lists from Notion + seed defaults"
```

---

## Task 6: `process_application` (CLI entry point) uses country/city

**Files:**
- Modify: `job_tracker.py:906-978`

- [ ] **Step 1: Update the signature and body**

Replace:

```python
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
```

with:

```python
def process_application(
    jd_text: str,
    source: str,
    status: str = "Applied",
    country: str = "qatar",
    city: str = "",
    resume_path: str = "",
    cover_letter_path: str = "",
):
    info = extract_job_info(jd_text)
    resolved_country = country or info["country"] or "qatar"
    resolved_city = city or info["city"]
    database_id, database_label = _resolve_database_id(resolved_country, resolved_city)
    LOGGER.info(
        "Processing application | source='%s' | target_db='%s' | resume_provided=%s | cover_provided=%s",
        source,
        database_label,
        bool(resume_path),
        bool(cover_letter_path),
    )
```

- [ ] **Step 2: Update the rest of the function body to use the resolved values**

Find the `notion_payload` dict inside `process_application` (still `job_tracker.py`, a little further down from the block just edited) and the trailing return dict. Replace:

```python
        "non_uk_location": non_uk_location,
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
```

with:

```python
        "country": resolved_country,
        "city": resolved_city,
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
        "country": resolved_country,
        "city": resolved_city,
        "database_label": database_label,
        "notion_page_url": notion_result.get("url", ""),
    }
```

- [ ] **Step 3: Update `run_cli()` prompts**

Replace (`job_tracker.py:1090-1109`):

```python
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
```

with:

```python
def run_cli():
    jd_text = _read_multiline_input("Paste the job description below.")
    country = input("Country (qatar/uae/india, blank = auto-detect): ").strip().lower()
    city = ""
    if country in CITIES_BY_COUNTRY:
        city = input("City (optional, blank = auto-detect): ").strip()
    status = input("Status (Applied/Under Review): ").strip() or "Applied"
    source = input("Source platform (LinkedIn, company site, etc.): ").strip()
    resume_path = input("Resume file path (optional, press Enter to skip): ").strip()
    cover_path = input("Cover letter file path (optional, press Enter to skip): ").strip()

    result = process_application(
        jd_text,
        source=source,
        status=status,
        country=country,
        city=city,
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
```

- [ ] **Step 4: Verify syntax**

Run: `python -m py_compile job_tracker.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add job_tracker.py
git commit -m "feat: use country/city (with Groq auto-detect fallback) in CLI flow"
```

---

## Task 7: `_process_web_submission_sync` uses country/city

**Files:**
- Modify: `job_tracker.py:981-1062`

- [ ] **Step 1: Update the signature**

Replace:

```python
def _process_web_submission_sync(
    *,
    region: str,
    non_uk_location: str,
    status: str,
    source: str,
    jd_text: str,
    resume_name: str,
    resume_bytes: bytes,
    resume_content_type: str,
    cover_name: str,
    cover_bytes: bytes,
    cover_content_type: str,
) -> dict:
```

with:

```python
def _process_web_submission_sync(
    *,
    country: str,
    city: str,
    status: str,
    source: str,
    jd_text: str,
    resume_name: str,
    resume_bytes: bytes,
    resume_content_type: str,
    cover_name: str,
    cover_bytes: bytes,
    cover_content_type: str,
) -> dict:
```

- [ ] **Step 2: Resolve country/city right after extraction**

Replace:

```python
    LOGGER.info("Web workflow started")
    info = extract_job_info(jd_text)
    LOGGER.info(
        "Job info extraction completed | company='%s' | role='%s'",
        info["company"],
        info["role"],
    )
```

with:

```python
    LOGGER.info("Web workflow started")
    info = extract_job_info(jd_text)
    resolved_country = country or info["country"] or "qatar"
    resolved_city = city or info["city"]
    LOGGER.info(
        "Job info extraction completed | company='%s' | role='%s' | country='%s' | city='%s'",
        info["company"],
        info["role"],
        resolved_country,
        resolved_city,
    )
```

- [ ] **Step 3: Update the payload and resolution call**

Replace:

```python
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
```

with:

```python
    notion_payload = {
        "company": info["company"],
        "role": info["role"],
        "source": source,
        "status": status,
        "country": resolved_country,
        "city": resolved_city,
        "jd_upload": uploads["jd_upload"],
        "resume_pdf_upload": uploads["resume_pdf_upload"],
        "resume_doc_upload": uploads["resume_doc_upload"],
        "cover_upload": uploads["cover_upload"],
    }
    database_id, database_label = _resolve_database_id(resolved_country, resolved_city)
    notion_result = create_notion_entry(notion_payload, database_id=database_id)
```

- [ ] **Step 4: Verify syntax**

Run: `python -m py_compile job_tracker.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add job_tracker.py
git commit -m "feat: resolve country/city (with Groq fallback) in web submission flow"
```

---

## Task 8: Web UI form — Country select + City field

**Files:**
- Modify: `job_tracker.py:1121-1138` (`_render_fastapi_html` signature/args)
- Modify: `job_tracker.py:1446-1463` (form HTML)

- [ ] **Step 1: Update the function signature and prep variables**

Replace:

```python
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
```

with:

```python
def _render_fastapi_html(
    error: str = "",
    result: Optional[dict] = None,
    jd_text: str = "",
    source: str = "",
    status: str = "Applied",
    country: str = "qatar",
    city: str = "",
    source_options_by_country: Optional[dict[str, list[str]]] = None,
    city_options_by_country: Optional[dict[str, list[str]]] = None,
) -> str:
    safe_error = html.escape(error)
    safe_jd = html.escape(jd_text)
    safe_source = html.escape(source)
    safe_status = html.escape(status or "Applied")
    safe_country = html.escape(country or "qatar")
    safe_city = html.escape(city or "")
    source_options_by_country = source_options_by_country or {"qatar": [], "uae": [], "india": []}
    source_options_json = json.dumps(source_options_by_country)
    city_options_by_country = city_options_by_country or {"uae": [], "india": []}
    city_options_json = json.dumps(city_options_by_country)
```

- [ ] **Step 2: Update the result card's database label lookup (no field rename needed there — it already reads `result.get("database_label", "")`, leave as-is)**

No change needed — verified `database_label` key name is unchanged by this plan.

- [ ] **Step 3: Replace the Region/Non-UK Location form fields**

Replace:

```python
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
```

with:

```python
          <div>
            <label>Country</label>
            <select id="country" name="country">
              <option value="qatar" {"selected" if safe_country == "qatar" else ""}>Qatar</option>
              <option value="uae" {"selected" if safe_country == "uae" else ""}>UAE</option>
              <option value="india" {"selected" if safe_country == "india" else ""}>India</option>
            </select>
          </div>
          <div id="city-wrap" class="{'hidden' if safe_country not in ('uae', 'india') else ''}">
            <label>City</label>
            <input
              id="city"
              type="text"
              name="city"
              list="city-options"
              value="{safe_city}"
              placeholder="Dubai, Bangalore, Kochi, ..."
            />
            <datalist id="city-options"></datalist>
            <div class="field-hint">Auto-filled from the job description when detected. Suggestions come from past entries — you can always type a new city.</div>
          </div>
```

- [ ] **Step 4: Verify syntax**

Run: `python -m py_compile job_tracker.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add job_tracker.py
git commit -m "feat: replace Region/Non-UK Location form fields with Country/City"
```

---

## Task 9: Web UI JS — country/city wiring

**Files:**
- Modify: `job_tracker.py:1524-1565` (JS variable declarations + `toggleNonUk`/`refreshSourceOptions`)
- Modify: `job_tracker.py:1654-1655` (redirect URL logic inside `startSubmit`)
- Modify: `job_tracker.py:1735-1738` (event wiring at bottom of script)

- [ ] **Step 1: Update variable declarations and helper functions**

Replace:

```python
      const form = document.querySelector("form");
      const regionSelect = document.getElementById("region");
      const nonUkWrap = document.getElementById("non-uk-wrap");
      const sourceInput = document.getElementById("source");
      const sourceOptions = document.getElementById("source-options");
```

with:

```python
      const form = document.querySelector("form");
      const countrySelect = document.getElementById("country");
      const cityWrap = document.getElementById("city-wrap");
      const cityInput = document.getElementById("city");
      const cityOptions = document.getElementById("city-options");
      const sourceInput = document.getElementById("source");
      const sourceOptions = document.getElementById("source-options");
```

Replace:

```python
      const sourceOptionsByRegion = {source_options_json};
```

with:

```python
      const sourceOptionsByCountry = {source_options_json};
      const cityOptionsByCountry = {city_options_json};
```

Replace:

```python
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
```

with:

```python
      function toggleCityField() {{
        if (!countrySelect || !cityWrap) return;
        const showCity = countrySelect.value === "uae" || countrySelect.value === "india";
        cityWrap.classList.toggle("hidden", !showCity);
      }}

      function refreshSourceOptions() {{
        if (!countrySelect || !sourceOptions) return;
        const items = sourceOptionsByCountry[countrySelect.value] || [];
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

      function refreshCityOptions() {{
        if (!countrySelect || !cityOptions) return;
        const items = cityOptionsByCountry[countrySelect.value] || [];
        cityOptions.innerHTML = "";
        items.forEach((value) => {{
          const option = document.createElement("option");
          option.value = value;
          cityOptions.appendChild(option);
        }});
      }}
```

- [ ] **Step 2: Update the redirect URL logic in `startSubmit`**

Replace:

```python
          const nextRegion = regionSelect && regionSelect.value === "non_uk" ? "non_uk" : "uk";
          pendingRedirectUrl = nextRegion === "non_uk" ? "/?region=non_uk" : "/";
```

with:

```python
          const nextCountry = countrySelect ? countrySelect.value : "qatar";
          pendingRedirectUrl = nextCountry === "qatar" ? "/" : `/?country=${{nextCountry}}`;
```

- [ ] **Step 3: Update the bottom event wiring**

Replace:

```python
      toggleNonUk();
      refreshSourceOptions();
      regionSelect && regionSelect.addEventListener("change", toggleNonUk);
      regionSelect && regionSelect.addEventListener("change", refreshSourceOptions);
      form && form.addEventListener("submit", startSubmit);
```

with:

```python
      toggleCityField();
      refreshSourceOptions();
      refreshCityOptions();
      countrySelect && countrySelect.addEventListener("change", toggleCityField);
      countrySelect && countrySelect.addEventListener("change", refreshSourceOptions);
      countrySelect && countrySelect.addEventListener("change", refreshCityOptions);
      form && form.addEventListener("submit", startSubmit);
```

- [ ] **Step 4: Verify syntax**

Run: `python -m py_compile job_tracker.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add job_tracker.py
git commit -m "feat: wire Country/City selection into web UI JS"
```

---

## Task 10: FastAPI routes — Form fields and options wiring

**Files:**
- Modify: `job_tracker.py:1791-1896` (`index_get` / `index_post`)

- [ ] **Step 1: Update `index_get`**

Replace:

```python
    @app.get("/", response_class=HTMLResponse)
    async def index_get(region: str = "uk"):
        region = (region or "uk").strip().lower()
        if region not in {"uk", "non_uk"}:
            region = "uk"
        return HTMLResponse(
            content=_render_fastapi_html(
                region=region,
                source_options_by_region=_get_source_options_by_region(),
            )
        )
```

with:

```python
    @app.get("/", response_class=HTMLResponse)
    async def index_get(country: str = "qatar"):
        country = (country or "qatar").strip().lower()
        if country not in COUNTRY_DATABASE_IDS:
            country = "qatar"
        return HTMLResponse(
            content=_render_fastapi_html(
                country=country,
                source_options_by_country=_get_source_options_by_country(),
                city_options_by_country=_get_city_options_by_country(),
            )
        )
```

- [ ] **Step 2: Update `index_post`'s signature and validation**

Replace:

```python
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
```

with:

```python
    @app.post("/", response_class=HTMLResponse)
    async def index_post(
        country: str = Form("qatar"),
        city: str = Form(""),
        status: str = Form("Applied"),
        source: str = Form(""),
        jd_text: str = Form(...),
        resume_file: Optional[UploadFile] = File(None),
        cover_file: Optional[UploadFile] = File(None),
    ):
        country = (country or "qatar").strip().lower()
        if country not in COUNTRY_DATABASE_IDS:
            country = "qatar"
        city = (city or "").strip()
        source = source.strip()
        status = (status or "Applied").strip()
        if status not in {"Applied", "Under Review"}:
            status = "Applied"
        jd_text = jd_text.strip()
        LOGGER.info(
            "Web submission received | country='%s' | city='%s' | status='%s' | source='%s' | jd_chars=%d | resume='%s' | cover='%s'",
            country,
            city,
            status,
            source,
            len(jd_text),
            (resume_file.filename if resume_file else ""),
            (cover_file.filename if cover_file else ""),
        )
```

- [ ] **Step 3: Update the empty-JD error branch**

Replace:

```python
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
```

with:

```python
        if not jd_text:
            return HTMLResponse(
                content=_render_fastapi_html(
                    error="Job description is required.",
                    jd_text=jd_text,
                    source=source,
                    status=status,
                    country=country,
                    city=city,
                    source_options_by_country=_get_source_options_by_country(),
                    city_options_by_country=_get_city_options_by_country(),
                )
            )
```

- [ ] **Step 4: Update the success call and success branch**

Replace:

```python
            result = await asyncio.to_thread(
                _process_web_submission_sync,
                region=region,
                non_uk_location=non_uk_location,
                status=status,
                source=source,
                jd_text=jd_text,
                resume_name=resume_name,
                resume_bytes=resume_bytes,
                resume_content_type=resume_content_type,
                cover_name=cover_name,
                cover_bytes=cover_bytes,
                cover_content_type=cover_content_type,
            )
            return HTMLResponse(
                content=_render_fastapi_html(
                    result=result,
                    source_options_by_region=_get_source_options_by_region(),
                )
            )
```

with:

```python
            result = await asyncio.to_thread(
                _process_web_submission_sync,
                country=country,
                city=city,
                status=status,
                source=source,
                jd_text=jd_text,
                resume_name=resume_name,
                resume_bytes=resume_bytes,
                resume_content_type=resume_content_type,
                cover_name=cover_name,
                cover_bytes=cover_bytes,
                cover_content_type=cover_content_type,
            )
            return HTMLResponse(
                content=_render_fastapi_html(
                    result=result,
                    source_options_by_country=_get_source_options_by_country(),
                    city_options_by_country=_get_city_options_by_country(),
                )
            )
```

- [ ] **Step 5: Update the exception branch**

Replace:

```python
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
```

with:

```python
        except Exception as exc:
            LOGGER.exception("Web submission failed")
            return HTMLResponse(
                content=_render_fastapi_html(
                    error=str(exc),
                    jd_text=jd_text,
                    source=source,
                    status=status,
                    country=country,
                    city=city,
                    source_options_by_country=_get_source_options_by_country(),
                    city_options_by_country=_get_city_options_by_country(),
                )
            )
```

- [ ] **Step 6: Verify syntax**

Run: `python -m py_compile job_tracker.py`
Expected: no output, exit code 0.

- [ ] **Step 7: Commit**

```bash
git add job_tracker.py
git commit -m "feat: wire country/city through FastAPI routes"
```

---

## Task 11: `test.py` and `CLAUDE.md` updates

**Files:**
- Modify: `test.py:47`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `test.py`'s env var lookup**

Replace:

```python
    notion_db_id = _normalize_notion_id(_get_env("NOTION_DATABASE_ID_NON_UK", "NOTION_DB_ID"))
```

with:

```python
    notion_db_id = _normalize_notion_id(_get_env("NOTION_DATABASE_ID_QATAR"))
```

- [ ] **Step 2: Update `CLAUDE.md`'s Environment Variables section**

Replace:

```
Required: `GROQ_API_KEY`, `NOTION_API_KEY`, `NOTION_DATABASE_ID`, `NOTION_DATABASE_ID_NON_UK`
Optional: `DEBUG`, `HOST`, `PORT`, `JOB_TRACKER_LOG_FILE`

`NOTION_DATABASE_ID` also falls back to `NOTION_DB_ID` for backward compatibility. IDs can be pasted as either a raw hex string or a full Notion URL — `_normalize_notion_id` extracts and reformats them.
```

with:

```
Required: `GROQ_API_KEY`, `NOTION_API_KEY`, `NOTION_DATABASE_ID_QATAR`, `NOTION_DATABASE_ID_UAE`, `NOTION_DATABASE_ID_INDIA`
Optional: `DEBUG`, `HOST`, `PORT`, `JOB_TRACKER_LOG_FILE`

IDs can be pasted as either a raw hex string or a full Notion URL — `_normalize_notion_id` extracts and reformats them.
```

- [ ] **Step 3: Update `CLAUDE.md`'s architecture description of the two converging flows and database resolution (item 6 and the reference to UK vs Non-UK)**

Replace:

```
6. **Two parallel entry-point flows that converge on the same building blocks**:
   - `process_application()` — used by CLI mode, reads files from local paths.
   - `_process_web_submission_sync()` — used by the web POST handler, works with in-memory bytes from `UploadFile`, and additionally builds a combined cover-letter+resume PDF (`_combine_pdf_bytes`, via `pypdf`) offered as a downloadable file.
   Both call `create_notion_entry()` to actually build/send the Notion page payload, and both resolve the target database with `_resolve_database_id()` (UK vs. Non-UK + location).
```

with:

```
6. **Two parallel entry-point flows that converge on the same building blocks**:
   - `process_application()` — used by CLI mode, reads files from local paths.
   - `_process_web_submission_sync()` — used by the web POST handler, works with in-memory bytes from `UploadFile`, and additionally builds a combined cover-letter+resume PDF (`_combine_pdf_bytes`, via `pypdf`) offered as a downloadable file.
   Both call `extract_job_info()` first (which also returns a best-guess `country`/`city` from the JD), then `create_notion_entry()` to actually build/send the Notion page payload, and both resolve the target database with `_resolve_database_id()` (Qatar / UAE / India, with an optional city for UAE/India). City suggestions shown in the UI grow over time: `_get_city_options_by_country()` merges a hardcoded seed list with whatever city options currently exist on the Notion City property, and the Notion API auto-adds a new select option whenever a page is saved with a previously-unseen city.
```

- [ ] **Step 4: Verify**

Run: `python -m py_compile job_tracker.py test.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add test.py CLAUDE.md
git commit -m "docs: update test.py and CLAUDE.md for country-based databases"
```

---

## Task 12: Manual end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Populate `.env` with the three real database ids**

```
NOTION_DATABASE_ID_QATAR=3ac2621e276f802bb034f4b90ae73815
NOTION_DATABASE_ID_UAE=3b12621e276f8004b837ef4e03b49770
NOTION_DATABASE_ID_INDIA=3b12621e276f8091b24de45708f79f85
```
(plus existing `GROQ_API_KEY` / `NOTION_API_KEY`.)

- [ ] **Step 2: Start the web UI**

Run: `python job_tracker.py`
Expected: server starts, browser opens to `http://<host>:5000/`, Country dropdown shows Qatar/UAE/India with Qatar selected and no City field visible.

- [ ] **Step 3: Manually exercise the golden path in the browser**

1. Select "UAE" in the Country dropdown — confirm the City field appears with a datalist offering Dubai/Abu Dhabi/Sharjah/Remote.
2. Paste a JD that clearly mentions "Dubai, UAE" and submit with a test Source value.
3. Confirm the result card shows a database label like `UAE (Dubai)` and the Notion page link opens a page in the UAE database with Country=UAE and City=Dubai populated.
4. Reload `/?country=uae` and confirm "Dubai" still appears in the City datalist (schema cache was refreshed).
5. Repeat once for India with a brand-new city not in the seed list (e.g. "Coimbatore") and confirm: it's accepted, written to Notion, and appears in the City datalist on the next `/?country=india` load (proves the Notion-auto-add-option + cache-clear mechanism works end to end).
6. Repeat once for Qatar and confirm no City field is shown/sent and the Notion page has Country=Qatar with City left empty.

- [ ] **Step 4: Check logs for warnings**

While doing the above, watch the terminal output (or the in-page log panel) for `Country property was not found` / `City property was not found` warnings — if either fires, the actual Notion databases don't have properties named exactly `Country` / `City` (or `Job Location`/`Location`) and the property names need to be checked in Notion directly.

- [ ] **Step 5: Report results**

No commit for this task — it's verification only. If any step fails, fix the underlying code (not the test) and re-run from Step 2.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Job Tracker Automation: a single-file FastAPI app that takes a pasted job description plus optional resume/cover letter, uses Groq to extract `company`/`role`, and creates a page in a Notion database (with file uploads for JD, resume PDF, an auto-generated resume DOCX, and cover letter). Supports a web UI, a terminal CLI mode, and deployment to Render.

Nearly all logic lives in `job_tracker.py` (~1950 lines). `test.py` is a standalone manual script for smoke-testing a raw Notion API write — it is not an automated test suite and is not wired into any test runner.

## Commands

Setup:
```bash
python -m venv .venv
.\.venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env             # then fill in values
```

Run the web UI:
```bash
python job_tracker.py
# or
uvicorn job_tracker:app --host 0.0.0.0 --port 5000
```

Run terminal-only mode:
```bash
python job_tracker.py --cli
```

Useful flags: `--host`, `--port`, `--no-open` (skip auto browser launch), `--debug` (verbose logging).

Manual Notion write smoke test (writes a real test row into the Qatar database — requires `.env` configured):
```bash
python test.py
```

There is no lint config, no automated test suite, and no build step in this repo.

## Environment Variables

Required: `GROQ_API_KEY`, `NOTION_API_KEY`, `NOTION_DATABASE_ID_QATAR`, `NOTION_DATABASE_ID_UAE`, `NOTION_DATABASE_ID_INDIA`
Optional: `GROQ_MODEL` (defaults to `openai/gpt-oss-20b`), `DEBUG`, `HOST`, `PORT`, `JOB_TRACKER_LOG_FILE`

IDs can be pasted as either a raw hex string or a full Notion URL — `_normalize_notion_id` extracts and reformats them.

## Architecture

Everything is one module, structured in layers top to bottom:

1. **Global runtime state** — module-level globals (`GROQ_API_KEY`, `NOTION_API_KEY`, `NOTION_DATABASE_ID_QATAR`, `NOTION_DATABASE_ID_UAE`, `NOTION_DATABASE_ID_INDIA`, `APP_CONFIGURED`) are populated once by `configure_runtime()` / `_load_and_validate_config()`, called from FastAPI's startup event or `main()`. Nothing above that call is safe to use.
2. **In-memory log/download stores** — `UI_LOGS` (a bounded deque) captures log records via a custom `UILogHandler` so the browser UI can poll `/logs` and show live progress during a submission. `DOWNLOADS` is a TTL'd in-memory dict used to serve the generated combined-application PDF from `/downloads/{token}` without touching disk.
3. **Groq extraction** — `extract_job_info()` calls Groq's chat completions API with a JSON-only system prompt, then `_parse_groq_json()` defensively unwraps code fences / partial JSON / key-value fallbacks. Raises if company or role can't be confidently extracted.
4. **Notion schema-adaptive property mapping** — `_get_database_properties()` (cached) fetches the target database's schema, handling both the legacy shape and the newer `data_sources`-based shape (Notion API version `2025-09-03`). `_find_property_name()` matches against several candidate names per field (e.g. "Resume File (PDF)", "Resume PDF", "Resume File", "Resume") so the app tolerates schema variations across databases. `_build_source_property_value()` / `_build_file_property_value()` adapt values to whatever property type (`select`, `status`, `rich_text`, `files`, etc.) is actually present, and unsupported combos get recorded into a `Notes` field instead of failing.
5. **File pipeline** — `_process_notion_uploads()` runs JD-to-PDF conversion, resume/cover upload, and PDF→DOCX conversion concurrently via a `ThreadPoolExecutor`. `_jd_text_to_pdf_bytes()` hand-builds a minimal valid PDF byte-for-byte (no external PDF library) so the JD text always has a real file to attach. Resume DOCX conversion tries `pdf2docx` first, then falls back to a LibreOffice CLI subprocess (`_find_libreoffice_converter()` — Windows install paths preferred, then PATH lookup).
6. **Two parallel entry-point flows that converge on the same building blocks**:
   - `process_application()` — used by CLI mode, reads files from local paths.
   - `_process_web_submission_sync()` — used by the web POST handler, works with in-memory bytes from `UploadFile`, and additionally builds a combined cover-letter+resume PDF (`_combine_pdf_bytes`, via `pypdf`) offered as a downloadable file.
   Both call `extract_job_info()` first (which also returns a best-guess `country`/`city` from the JD), then `create_notion_entry()` to actually build/send the Notion page payload, and both resolve the target database with `_resolve_database_id()` (Qatar / UAE / India, with an optional city for UAE/India). City suggestions shown in the UI grow over time: `_get_city_options_by_country()` merges a hardcoded seed list with whatever city options currently exist on the Notion City property, and the Notion API auto-adds a new select option whenever a page is saved with a previously-unseen city.
7. **FastAPI app** (`create_web_app()`) — routes: `/` (GET renders form, POST processes submission and re-renders with result/error), `/healthz`, `/readyz`, `/logs` (polling endpoint for the live log panel), `/downloads/{token}`. The entire HTML/CSS/JS for the UI is inlined in `_render_fastapi_html()` as an f-string — there is no template engine or static file directory. The frontend JS submits the form via `fetch`, polls `/logs` for progress, and parses the returned HTML fragment to pull out the result card and download link (no JSON API for results — the POST response *is* the rendered page).
8. **`main()`** — argparse entry point that wires CLI vs. web mode; `app = create_web_app()` at module scope is also what `uvicorn job_tracker:app` picks up directly (used by `render.yaml`'s start command and for local `uvicorn` runs).

### Key conventions to preserve when modifying

- Never assume a fixed Notion schema — always go through `_find_property_name()` / `_get_database_properties()` rather than hardcoding a property name that might not match every user's database.
- Optional dependencies (`fastapi`, `pdf2docx`, `pypdf`) are imported in `try/except` blocks at the top and guarded at the call site (e.g. `create_web_app()` raises a clear `RuntimeError` if FastAPI is missing) rather than crashing at import time.
- Logging goes through the shared `LOGGER` (`job_tracker` logger), not `print`, so messages also reach the UI log panel and optional file handler; `print` is reserved for CLI-mode direct user output.

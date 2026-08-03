# Country-Based Notion Databases (Qatar / UAE / India)

## Goal

Replace the current UK / Non-UK (Qatar, Dubai, Saudi, Remote) database split with three country-based Notion databases: **Qatar**, **UAE**, **India**. UAE and India additionally track a **City** (UAE: Dubai, Abu Dhabi, Sharjah, Remote, + any new city typed in; India: Bangalore, Kochi, Chennai, Hyderabad, Mumbai, Pune, Delhi NCR, Remote, + any new city typed in). Qatar has no city breakdown. Country/city should be auto-extracted from the pasted job description by Groq, with manual override in the UI. Default country is Qatar when extraction is unclear.

All three Notion databases have been trimmed to identical fields, with separate **Country** and **City** properties (City is empty for Qatar rows).

## Config

`.env` / `.env.example` replace `NOTION_DATABASE_ID` and `NOTION_DATABASE_ID_NON_UK` with:

```
NOTION_DATABASE_ID_QATAR=3ac2621e276f802bb034f4b90ae73815
NOTION_DATABASE_ID_UAE=3b12621e276f8004b837ef4e03b49770
NOTION_DATABASE_ID_INDIA=3b12621e276f8091b24de45708f79f85
```

All three are required (fail fast on startup if missing, same as the old `NOTION_DATABASE_ID` check). `NOTION_DATABASE_ID_NON_UK` / `NOTION_DB_ID` fallback support is removed.

## Country/city resolution

`_resolve_database_id(country: str, city: str = "") -> tuple[str, str]` replaces `_resolve_database_id(region, non_uk_location)`:

- `country` is normalized to lowercase; valid values are `qatar`, `uae`, `india`. Blank or unrecognized input defaults to `qatar`.
- Qatar: city is ignored entirely.
- UAE / India: city is passed through as free text, untouched — no hardcoded whitelist/validation, since valid cities are dynamic (Notion is the source of truth for what's "known"). Empty city is allowed; it just means the City property isn't written.
- Returns `(database_id, label)` where label is e.g. `"Qatar"`, `"UAE (Dubai)"`, `"India (Bangalore)"`, or `"UAE"` / `"India"` if no city given.

## Groq extraction

`extract_job_info()`'s system prompt and JSON schema extend to:

```json
{"company": "", "role": "", "country": "", "city": ""}
```

- `country` should be one of `qatar`, `uae`, `india`, or empty if the JD doesn't indicate a country.
- `city` should only be populated when country is `uae` or `india`; otherwise empty.
- The existing hard-fail confidence check (raises `ValueError` if company or role missing) is unchanged and does **not** extend to country/city — if country comes back empty/unrecognized, it silently defaults to `qatar` downstream via `_resolve_database_id`. No error, no warning banner (per user decision — manual override in the UI is the safety net, not an extra confidence prompt).

## Notion property mapping (`create_notion_entry`)

Two independent property lookups replace the current single `Job Location`/`Location`/`Country` lookup:

- **Country property**: `_find_property_name(db_props, "Country")`. Always written (Qatar/UAE/India, title-cased) when the property exists.
- **City property**: `_find_property_name(db_props, "City", "Job Location", "Location")`. Written only when a non-empty city value is present (UAE/India). Uses the existing `_build_source_property_value()` (already handles `select`/`multi_select`/`rich_text`/`status`/`title`), so no new value-building logic needed.

Both follow the existing schema-adaptive pattern: missing property → log a warning and skip, don't fail the whole submission.

## Dynamic, growing city list

Mirrors the existing Source-options pattern (`_get_source_options_for_database` / `_get_source_options_by_region`):

- `_get_city_options_for_database(database_id) -> list[str]`: looks up the City property's schema via `_get_database_properties()`, extracts its current `select`/`multi_select` options via the existing `_extract_property_options()` helper.
- `_get_city_options_by_country() -> dict[str, list[str]]`: returns `{"uae": [...], "india": [...]}`. For each country, the list is a hardcoded seed list followed by whatever's currently in Notion, de-duplicated case-insensitively (seed lists win on casing/order for the initial known set; anything new coming from Notion is appended):
  - UAE seed: `Dubai, Abu Dhabi, Sharjah, Remote`
  - India seed: `Bangalore, Kochi, Chennai, Hyderabad, Mumbai, Pune, Delhi NCR, Remote`

Growth mechanism: Notion's API auto-adds a new option to a `select`/`multi_select` property's schema when a page is created with a previously-unseen value. So when a JD mentions a new city and it gets written to the City property, Notion's schema now includes it — no extra write needed. To make it show up in the *next* page load's dropdown, `_get_database_properties.cache_clear()` is called after a successful `create_notion_entry()` call (the function is `@lru_cache`d and would otherwise keep serving the stale schema for the life of the process).

## Web UI

- **"Region"** select (UK / Non-UK) → **"Country"** select: Qatar / UAE / India, default Qatar.
- **"Non-UK Location"** block → **"City"** text input + `<datalist>`, shown only when country is `uae` or `india` (same show/hide pattern as the old non-UK block, just keyed off country instead of region). Free text + suggestions, same UX as the existing Source field — not a rigid dropdown, so typing an unlisted city is always allowed.
- JS: `regionSelect`/`toggleNonUk`/`refreshSourceOptions` are renamed/adapted to `countrySelect`/`toggleCityField`/`refreshCityOptions`, driven by a `cityOptionsByCountry` JSON blob injected the same way `sourceOptionsByRegion` is today. `_get_source_options_by_region()` becomes `_get_source_options_by_country()`, keyed by `qatar`/`uae`/`india`.
- Result card's "Database" label shows the country/city label from `_resolve_database_id`.

## CLI mode

`region` / `non_uk_location` prompts become:
- `country (qatar/uae/india)` — defaults to `qatar` if blank.
- `city` — only prompted when country is `uae` or `india`.

## Removed

- `NOTION_DATABASE_ID`, `NOTION_DATABASE_ID_NON_UK`, `NOTION_DB_ID` fallback, and all UK/Non-UK-specific code paths, docs, and env entries.
- `test.py`'s fallback to `NOTION_DATABASE_ID_NON_UK` → updated to `NOTION_DATABASE_ID_QATAR` (still just a manual smoke-test script, not wired into a test runner).
- `CLAUDE.md` updated to describe the country-based architecture instead of UK/Non-UK.

## Out of scope

- No changes to the file upload pipeline, Groq model, PDF/DOCX conversion, or combined-PDF download flow — those are untouched.
- No admin UI for managing the seed city lists; they're a small constant in code, edited directly if the defaults ever need to change.

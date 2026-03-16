# Job Tracker Automation

A small FastAPI application for turning messy job application workflow into one clean action:

- Paste a job description
- Upload your resume and optional cover letter
- Extract the company and role automatically using Groq
- Push the application into your Notion job tracker

It is built for personal job tracking and for cutting down the repetitive admin work around every application.

## What It Does

This app helps you save job applications into Notion without manually filling every field.

When you submit a form, it:

- Reads the job description
- Uses Groq to extract the `company` and `role`
- Creates a Notion page in the correct database
- Uploads the job description as a file
- Uploads the resume PDF
- Attempts to generate a DOCX version of the resume
- Uploads the cover letter if provided
- Supports separate UK and Non-UK Notion databases

## Stack

- Python
- FastAPI
- Uvicorn
- Requests
- Groq API
- Notion API
- `pdf2docx` with LibreOffice fallback for PDF to DOCX conversion

## Screens and Flow

The web UI includes:

- Region selection: `UK` or `Non-UK`
- Non-UK location selection: `Qatar`, `Dubai`, `Saudi`, `Remote`
- Source platform field
- Status toggle
- Job description textarea
- Resume upload
- Cover letter upload

## Project Structure

```text
.
|-- job_tracker.py
|-- test.py
|-- requirements.txt
|-- .env.example
|-- README.md
```

## Environment Variables

Create a local `.env` file based on [\.env.example](/c:/Users/Rusafid%20Ahmed/Desktop/Project/AI%20Projects/Automation/.env.example).

Required:

- `GROQ_API_KEY`
- `NOTION_API_KEY`
- `NOTION_DATABASE_ID`
- `NOTION_DATABASE_ID_NON_UK`

Optional:

- `DEBUG=false`
- `HOST=0.0.0.0`
- `PORT=5000`
- `JOB_TRACKER_LOG_FILE=`

## Local Installation

### 1. Clone the repository

```bash
git clone https://github.com/rusafidt/Job-Tracker-Automation.git
cd Job-Tracker-Automation
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` and fill in your values.

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

### 5. Run locally

```bash
python job_tracker.py
```

Then open:

```text
http://127.0.0.1:5000
```

You can also run it directly with Uvicorn:

```bash
uvicorn job_tracker:app --host 0.0.0.0 --port 5000
```

## CLI Mode

If you want terminal-only usage:

```bash
python job_tracker.py --cli
```

## Notion Setup Notes

This app assumes your Notion databases contain properties similar to:

- `Job Title`
- `Company`
- `Status`
- `Source` or `Source Platform`
- `Date Applied`
- `Follow-up Date`
- `Follow-up Count`
- `Resume File (PDF)`
- `Resume File (DOC)`
- `Cover Letter File`
- `Job Description File`
- `Notes`

The code already tries to match a few common variations in property names, which makes it more forgiving than a rigid one-schema implementation.

## Resume Conversion Notes

If a resume PDF is uploaded, the app tries to create a DOCX version:

- First with `pdf2docx`
- Then with LibreOffice CLI if available

If conversion fails, the app can still continue with the PDF upload path.

## Why This Exists

Job tracking becomes irritating fast:

- copy the JD
- extract the company
- fill the role
- upload files
- update status
- place it in the right tracker

This tool collapses that into one submission screen and keeps your Notion tracker consistent.

## Security

- Do not commit `.env`
- Keep secrets outside source control
- Rotate API keys if they were ever exposed in a pushed repo or screenshot

## Troubleshooting

### App fails on startup

Check that all required environment variables are set:

- `GROQ_API_KEY`
- `NOTION_API_KEY`
- `NOTION_DATABASE_ID`
- `NOTION_DATABASE_ID_NON_UK`

### File uploads fail

Verify:

- the Notion integration has access to the database
- the matching file properties exist in Notion
- uploaded files are valid and not empty

## License

This project is licensed under the MIT License. See [LICENSE](./LICENSE).

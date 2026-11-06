# FieldData AI

FieldData AI is a local-first field-survey data quality and analysis prototype. It supports user accounts, projects, CSV/XLSX uploads, data profiling, validation, human-reviewed cleaning suggestions, cleaned exports, and descriptive analytics.

> **Prototype notice:** This is a portfolio/demo application, not a production system for sensitive or mission-critical data. Use synthetic or appropriately anonymised data. Before public deployment, add production-grade authentication controls, CSRF protection, rate limiting, backups, monitoring, and a deployment security review.

## Requirements

- Windows 10/11, macOS, or Linux
- Python **3.12** recommended
- Internet connection for the first dependency installation

Python 3.12 is recommended for the smoothest compatibility with scientific Python packages. Avoid using a very new Python version until all dependencies publish compatible wheels.

## Windows setup (PowerShell)

Open PowerShell in the `fielddata-ai` project folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, run this in the same PowerShell window, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

If the Python launcher does not recognise 3.12, install Python 3.12 first and reopen PowerShell.

## Run the application

From the project root, with the virtual environment activated:

```powershell
python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> in your browser. API documentation is available at <http://127.0.0.1:8000/docs>.

Stop the server with `Ctrl+C`.

## Main workflow

1. Register or sign in.
2. Create a project.
3. Upload a `.csv` or `.xlsx` survey dataset (prototype limit: 10 MB, 50,000 rows, 200 columns).
4. Preview the data and inspect detected column roles.
5. Run the data quality check and review flagged records.
6. Open **Cleaning** and generate proposed corrections.
7. Select suggestions and accept or reject them. No correction is applied without human approval.
8. Apply accepted corrections to create a separate cleaned file. The original upload remains unchanged.
9. Download the cleaned dataset, change log, or follow-up list.
10. Open **Analytics** to compare original and cleaned data, view key findings, trends, missingness, and summaries.

## Tests

Install development dependencies:

```powershell
pip install -r requirements-dev.txt
```

Run the automated unit/service tests:

```powershell
python -m pytest -q tests app/test_datasets.py app/test_cleaning.py app/test_cleaning_service.py
```

Run the end-to-end API smoke test in a second PowerShell window after starting the server:

```powershell
python scripts/smoke_test.py
```

The smoke test creates temporary test accounts and projects in the configured local database. For an isolated test database, set `FIELDDATA_DB` and `FIELDDATA_STORAGE` before starting the server.

## Data and configuration

- SQLite database: `data/fielddata.db`
- Uploaded datasets: `data/uploads/`
- Generated reports and exports: stored alongside uploaded datasets
- `FIELDDATA_DB`: optional path to the SQLite database
- `FIELDDATA_STORAGE`: optional path for uploaded and generated dataset files
- `FIELDDATA_SECURE_COOKIES=1`: enable secure session cookies when served over HTTPS

Local databases, uploaded files, generated outputs, and virtual environments are excluded from Git by `.gitignore`. Keep backups of any local database or uploads before replacing a working project folder.

## Project structure

```text
fielddata-ai/
├── app/                 # FastAPI routes, authentication, database and service layer
├── fielddata/           # Validation, cleaning and analytics engines
├── static/              # Browser interface, styles and SVG chart helpers
├── sample_data/         # Synthetic survey data for testing/demo
├── tests/               # Automated test suite
├── scripts/             # Manual end-to-end smoke test
├── data/                # Local SQLite database and uploads (not for Git)
├── output/              # Local generated outputs (not for Git)
├── requirements.txt
└── requirements-dev.txt
```

## Analytics report downloads
Open a dataset and choose **Analytics**. Use **Download PDF report** or
**Download Excel report** to export the currently selected source (original or
cleaned). The Excel workbook contains Overview, Key Findings, Missing by
Column, Numeric Summaries, and Submission Trend sheets.

## Prototype limits and production deployment
- Upload limit: 10 MB; up to 50,000 rows and 200 columns. XLSX expansion is
  limited to 100 MB and its first worksheet is streamed during import.
- The app uses SQLite and local file storage by default. For a multi-user
  production deployment, move to managed PostgreSQL and private object storage,
  configure backups and retention, and run behind an HTTPS reverse proxy.
- Set `FIELDDATA_SECURE_COOKIES=1` when HTTPS is enabled. Keep uploaded data
  outside the public web root and restrict filesystem permissions.
- The built-in login throttling is process-local. Use a shared rate limiter
  (for example Redis or an API gateway) before running multiple workers.
- This portfolio prototype has no MFA, account recovery, or administrator
  management workflow. Do not use it for sensitive personal or health data.

## Verification
Run the full automated test suite from the project root:

```powershell
python -m unittest discover -s . -p "test*.py" -v
```

The project has been benchmarked locally with 100, 250, and 500-row synthetic
survey datasets. Timings vary by computer; see `SECURITY_AND_PERFORMANCE.md`.

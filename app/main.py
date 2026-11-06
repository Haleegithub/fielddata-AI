"""
FieldData AI - Stage 2: web backend (FastAPI).
This file only connects web requests to the tested logic in services.py.
Run:  python -m uvicorn app.main:app --reload     then open http://127.0.0.1:8000
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analytics_service, cleaning_service, datasets, db, report_service, security, services

BASE = Path(__file__).resolve().parent.parent
COOKIE = "fielddata_session"
SECURE_COOKIES = os.environ.get("FIELDDATA_SECURE_COOKIES") == "1"      # set to 1 when served over HTTPS
STORAGE = Path(os.environ.get("FIELDDATA_STORAGE", BASE / "data" / "uploads"))


@asynccontextmanager
async def lifespan(app):
    conn = db.connect()
    db.init_db(conn)
    conn.close()
    yield


app = FastAPI(title="FieldData AI", description="Turning Field Data into Actionable Intelligence.", lifespan=lifespan)


# ---- errors always use the same simple shape: {"error": "..."} ------------------------------
@app.exception_handler(services.ServiceError)
async def service_error(request: Request, exc: services.ServiceError):
    return JSONResponse({"error": exc.message}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def bad_request(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": "Please check the information you entered."}, status_code=422)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Reject browser requests explicitly marked cross-site when they carry an
    # authenticated cookie or target a state-changing endpoint. SameSite=Lax
    # remains a second layer; API clients without browser metadata still work.
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.cookies.get(COOKIE):
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if fetch_site == "cross-site" or (origin and origin.rstrip("/") != expected_origin.rstrip("/")):
            return JSONResponse({"error": "Cross-site request rejected."}, status_code=403)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > datasets.MAX_BYTES + 1_000_000:       # refuse huge uploads early
        return JSONResponse({"error": "The upload is too large."}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    path = request.url.path
    if path == "/" or path.startswith("/static/"):          # the built-in /docs page needs looser rules
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' https://cdn.jsdelivr.net; "
            "script-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
    return response


# ---- dependencies --------------------------------------------------------------------------
def get_db():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def current_user(request: Request, conn=Depends(get_db)):
    user = services.user_from_token(conn, request.cookies.get(COOKIE))
    if not user:
        raise services.ServiceError(401, "Please sign in.")
    return user


def _start_session(conn, user, response: Response):
    token = services.create_session(conn, user["id"])
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=SECURE_COOKIES,
                        max_age=security.SESSION_DAYS * 86400)


# ---- request shapes ------------------------------------------------------------------------
class RegisterIn(BaseModel):
    name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class ReviewIn(BaseModel):
    status: str
    ids: list[int] = []
    rule: str = ""
    confidence: str = ""


class ProjectIn(BaseModel):
    name: str
    description: str = ""
    location: str = ""


# ---- accounts ------------------------------------------------------------------------------
@app.post("/api/register", status_code=201)
def register(body: RegisterIn, response: Response, conn=Depends(get_db)):
    user = services.register(conn, body.name, body.email, body.password)
    _start_session(conn, user, response)
    return user


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response, conn=Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    user = services.login(conn, body.email, body.password, ip)
    _start_session(conn, user, response)
    return user


@app.post("/api/logout")
def logout(request: Request, response: Response, conn=Depends(get_db)):
    services.end_session(conn, request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


# ---- projects ------------------------------------------------------------------------------
@app.get("/api/projects")
def projects(user=Depends(current_user), conn=Depends(get_db)):
    return services.list_projects(conn, user["id"])


@app.post("/api/projects", status_code=201)
def new_project(body: ProjectIn, user=Depends(current_user), conn=Depends(get_db)):
    return services.create_project(conn, user["id"], body.name, body.description, body.location)


@app.get("/api/projects/{project_id}")
def project(project_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return services.get_project(conn, user["id"], project_id)


@app.delete("/api/projects/{project_id}")
def remove_project(project_id: int, user=Depends(current_user), conn=Depends(get_db)):
    datasets.remove_project(conn, user["id"], project_id, STORAGE)
    return {"ok": True}


# ---- datasets ------------------------------------------------------------------------------
@app.get("/api/projects/{project_id}/datasets")
def project_datasets(project_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return datasets.list_datasets(conn, user["id"], project_id)


@app.post("/api/projects/{project_id}/datasets", status_code=201)
def upload_dataset(project_id: int, file: UploadFile = File(...), user=Depends(current_user), conn=Depends(get_db)):
    data = file.file.read(datasets.MAX_BYTES + 1)
    return datasets.save_dataset(conn, user["id"], project_id, file.filename, data, STORAGE)


@app.get("/api/datasets/{dataset_id}")
def dataset(dataset_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return datasets.dataset_info(conn, user["id"], dataset_id, STORAGE)


@app.get("/api/datasets/{dataset_id}/preview")
def dataset_preview(dataset_id: int, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                    user=Depends(current_user), conn=Depends(get_db)):
    return datasets.preview(conn, user["id"], dataset_id, STORAGE, offset, limit)


@app.post("/api/datasets/{dataset_id}/validate")
def dataset_validate(dataset_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return datasets.run_validation(conn, user["id"], dataset_id, STORAGE)


@app.get("/api/datasets/{dataset_id}/validation")
def dataset_validation(dataset_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return datasets.get_validation(conn, user["id"], dataset_id)


@app.get("/api/datasets/{dataset_id}/issues")
def dataset_issues(dataset_id: int, severity: str = "", issue_type: str = "", column: str = "",
                   page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                   user=Depends(current_user), conn=Depends(get_db)):
    return datasets.get_issues(conn, user["id"], dataset_id, STORAGE, severity, issue_type, column, page, page_size)


# ---- cleaning ------------------------------------------------------------------------------
@app.post("/api/datasets/{dataset_id}/corrections/generate")
def corrections_generate(dataset_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return cleaning_service.generate(conn, user["id"], dataset_id, STORAGE)


@app.get("/api/datasets/{dataset_id}/corrections")
def corrections_list(dataset_id: int, status: str = "", rule: str = "", page: int = Query(1, ge=1),
                     page_size: int = Query(25, ge=1, le=100), user=Depends(current_user), conn=Depends(get_db)):
    return cleaning_service.list_corrections(conn, user["id"], dataset_id, status, rule, page, page_size)


@app.post("/api/datasets/{dataset_id}/corrections/review")
def corrections_review(dataset_id: int, body: ReviewIn, user=Depends(current_user), conn=Depends(get_db)):
    return cleaning_service.review(conn, user["id"], dataset_id, body.status, body.ids, body.rule, body.confidence)


@app.post("/api/datasets/{dataset_id}/clean")
def dataset_clean(dataset_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return cleaning_service.apply(conn, user["id"], dataset_id, STORAGE)


@app.get("/api/datasets/{dataset_id}/cleaning")
def dataset_cleaning(dataset_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return cleaning_service.get_cleaning(conn, user["id"], dataset_id)


@app.get("/api/datasets/{dataset_id}/download")
def dataset_download(dataset_id: int, what: str = "cleaned", fmt: str = Query("csv", alias="format"),
                     user=Depends(current_user), conn=Depends(get_db)):
    filename, media_type, data = cleaning_service.export(conn, user["id"], dataset_id, STORAGE, what, fmt)
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---- analytics -----------------------------------------------------------------------------
@app.get("/api/datasets/{dataset_id}/analytics")
def dataset_analytics(dataset_id: int, source: str = "original", user=Depends(current_user), conn=Depends(get_db)):
    return analytics_service.dataset_analytics(conn, user["id"], dataset_id, STORAGE, source)


@app.get("/api/projects/{project_id}/summary")
def project_summary(project_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return analytics_service.project_summary(conn, user["id"], project_id)


@app.get("/api/datasets/{dataset_id}/analytics/report")
def analytics_report(dataset_id: int, format: str = Query("pdf", pattern="^(pdf|xlsx)$"), source: str = "original",
                     user=Depends(current_user), conn=Depends(get_db)):
    if format == "pdf":
        data = report_service.build_pdf(conn, user["id"], dataset_id, STORAGE, source)
        filename, media = f"fielddata-analytics-{dataset_id}.pdf", "application/pdf"
    else:
        data = report_service.build_xlsx(conn, user["id"], dataset_id, STORAGE, source)
        filename, media = f"fielddata-analytics-{dataset_id}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@app.delete("/api/datasets/{dataset_id}")
def remove_dataset(dataset_id: int, user=Depends(current_user), conn=Depends(get_db)):
    datasets.delete_dataset(conn, user["id"], dataset_id, STORAGE)
    return {"ok": True}


# ---- pages ---------------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def home():
    return FileResponse(BASE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

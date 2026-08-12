from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .routers import public, auth, projects, admin

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Virtual Suggestion Box")


@app.on_event("startup")
def on_startup():
    init_db()


app.include_router(public.router)
app.include_router(public.edit_router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(admin.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_home_page():
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/vb/{slug}")
def serve_box_page(slug: str):
    """Human-facing submission page — box.html fetches the real data from /api/vb/{slug}."""
    return FileResponse(STATIC_DIR / "box.html")


@app.get("/edit/{token}")
def serve_edit_page(token: str):
    """Human-facing edit page — edit.html fetches the real data from /api/edit/{token}."""
    return FileResponse(STATIC_DIR / "edit.html")


@app.get("/register")
def serve_register_page():
    return FileResponse(STATIC_DIR / "register.html")


@app.get("/login")
def serve_login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/forgot-password")
def serve_forgot_password_page():
    return FileResponse(STATIC_DIR / "forgot-password.html")


@app.get("/reset-password/{token}")
def serve_reset_password_page(token: str):
    return FileResponse(STATIC_DIR / "reset-password.html")


@app.get("/dashboard")
def serve_dashboard_page():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/admin")
def serve_admin_page():
    # Admin is now just the dashboard with extra tabs for admins — same
    # file, kept as a separate route so old bookmarks/links still work.
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/health")
def health():
    return {"status": "ok"}

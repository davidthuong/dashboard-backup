from pydantic import BaseModel
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.services.auth import create_session_token, verify_admin_credentials

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.get("/login")
def login_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": ""},
    )


@router.post("/auth/login")
def login(request: Request, payload: LoginIn):
    settings = request.app.state.settings
    if not verify_admin_credentials(settings, payload.username, payload.password):
        return JSONResponse({"ok": False, "error": "Sai tai khoan hoac mat khau"}, status_code=401)

    token = create_session_token(settings, payload.username)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    return response


@router.post("/auth/logout")
def logout(request: Request):
    settings = request.app.state.settings
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name)
    return response

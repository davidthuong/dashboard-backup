import hmac

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="backup-dashboard")


def verify_admin_credentials(settings: Settings, username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username, settings.admin_username)
    pass_ok = hmac.compare_digest(password, settings.admin_password)
    return user_ok and pass_ok


def create_session_token(settings: Settings, username: str) -> str:
    return _serializer(settings).dumps({"u": username})


def read_session_token(settings: Settings, token: str) -> str | None:
    try:
        payload = _serializer(settings).loads(token, max_age=settings.session_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    user = payload.get("u")
    if not isinstance(user, str):
        return None
    return user


def require_admin(request: Request) -> str:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name, "")
    user = read_session_token(settings, token) if token else None
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return user


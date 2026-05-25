import json
import os
from functools import lru_cache

class FirebaseConfigurationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_firebase_admin_app():
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ModuleNotFoundError as exc:
        raise FirebaseConfigurationError(
            "firebase-admin is not installed on the backend. Install it before using Google login."
        ) from exc

    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()

    if service_account_json:
        try:
            credential_info = json.loads(service_account_json)
        except json.JSONDecodeError as exc:
            raise FirebaseConfigurationError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc

        try:
            return firebase_admin.initialize_app(credentials.Certificate(credential_info))
        except ValueError:
            return firebase_admin.get_app()

    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if credentials_path:
        try:
            return firebase_admin.initialize_app(credentials.Certificate(credentials_path))
        except ValueError:
            return firebase_admin.get_app()

    raise FirebaseConfigurationError(
        "Firebase Admin is not configured. Set FIREBASE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS."
    )


def verify_firebase_id_token(id_token: str) -> dict:
    if not id_token:
        raise FirebaseConfigurationError("Missing Firebase ID token.")

    try:
        from firebase_admin import auth
    except ModuleNotFoundError as exc:
        raise FirebaseConfigurationError(
            "firebase-admin is not installed on the backend. Install it before using Google login."
        ) from exc

    import time
    app = get_firebase_admin_app()

    for attempt in range(3):
        try:
            return auth.verify_id_token(id_token, app=app)
        except Exception as exc:
            err_msg = str(exc).lower()
            # If the token is 'used too early' or 'not yet valid', sleep 2 seconds and retry
            if "too early" in err_msg or "future" in err_msg or "not yet valid" in err_msg:
                if attempt < 2:
                    time.sleep(2)
                    continue
            raise exc

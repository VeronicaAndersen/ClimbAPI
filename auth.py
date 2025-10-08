from functools import wraps
from flask import request
from werkzeug.exceptions import Unauthorized, Forbidden
import jwt, os

SECRET = os.getenv("SECRET_KEY", "default_secret")
ALGS = ["HS256"]

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise Unauthorized("Token is missing")
        token = auth.split(" ", 1)[1].strip()
        try:
            decoded = jwt.decode(token, SECRET, algorithms=ALGS)
        except jwt.ExpiredSignatureError:
            raise Unauthorized("Token has expired")
        except jwt.InvalidTokenError:
            raise Unauthorized("Invalid token")

        # pass claims ONLY via kwargs
        kwargs["current_user_claims"] = decoded
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        claims = kwargs.get("current_user_claims")
        if not claims:
            raise Unauthorized("Token is missing")
        if str(claims.get("roles", "")).lower() != "admin":
            raise Forbidden("Access denied. Admins only.")
        # forward kwargs unchanged (do NOT add positional arg)
        return f(*args, **kwargs)
    return wrapper

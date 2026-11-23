from pydantic import BaseModel, constr

# Shared with the climber schemas so signup, login and password reset accept
# the same input.
USERNAME_MAX_LENGTH = 200
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 1024


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    username: constr(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: constr(max_length=PASSWORD_MAX_LENGTH)


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordResetRequest(BaseModel):
    username: constr(min_length=1, max_length=USERNAME_MAX_LENGTH)


class PasswordResetConfirm(BaseModel):
    token: str
    # Same rule as signup: a reset must not set a password signup would refuse,
    # or one too long for login to accept.
    new_password: constr(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class MessageResponse(BaseModel):
    message: str

from pydantic import BaseModel, constr

# Shared with the climber schemas so signup and login accept the same input.
USERNAME_MAX_LENGTH = 200
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
    new_password: str


class MessageResponse(BaseModel):
    message: str

import os

from passlib.context import CryptContext

# TODO: Tune these based on benchmarks for server.
PWD_CONTEXT = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__type="ID",
    argon2__time_cost=int(os.getenv("ARGON2_TIME_COST", "3")),
    argon2__memory_cost=int(os.getenv("ARGON2_MEMORY_KIB", "65536")),  # 64 MiB
    argon2__parallelism=int(os.getenv("ARGON2_PARALLELISM", "2"))
)
PEPPER = os.getenv("PASSWORD_PEPPER", "")


def _pepper(pw: str) -> str:
    return pw + PEPPER


def hash_password(plain_password: str) -> str:
    return PWD_CONTEXT.hash(_pepper(plain_password))


def verify_password(plain_password: str, password_hash: str) -> bool:
    return PWD_CONTEXT.verify(_pepper(plain_password), password_hash)


def needs_rehash(password_hash: str) -> bool:
    return PWD_CONTEXT.needs_update(password_hash)

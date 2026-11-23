import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

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

# Argon2 is CPU and memory heavy (64 MiB per hash by default), so it runs on a
# small dedicated pool instead of the event loop. The pool size caps both the
# CPU used and the peak memory (workers x memory_cost) during a login burst,
# and keeps hashing from starving the shared threadpool Starlette uses.
_HASH_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("ARGON2_MAX_CONCURRENCY", "4"))),
    thread_name_prefix="argon2",
)


def _pepper(pw: str) -> str:
    return pw + PEPPER


def hash_password(plain_password: str) -> str:
    return PWD_CONTEXT.hash(_pepper(plain_password))


def verify_password(plain_password: str, password_hash: str) -> bool:
    return PWD_CONTEXT.verify(_pepper(plain_password), password_hash)


def needs_rehash(password_hash: str) -> bool:
    return PWD_CONTEXT.needs_update(password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """
    A hash of a random value, computed once with the current parameters.

    Computed at startup by ``prepare_dummy_hash`` so the first unknown-user
    login is not slower than the rest (which would reveal that the username
    does not exist).
    """
    return hash_password(os.urandom(32).hex())


def _verify_dummy(plain_password: str) -> bool:
    verify_password(plain_password, _dummy_hash())
    return False


async def prepare_dummy_hash() -> None:
    """Compute the dummy hash off the event loop; call once at startup."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_HASH_EXECUTOR, _dummy_hash)


async def hash_password_async(plain_password: str) -> str:
    """Hash a password without blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_HASH_EXECUTOR, hash_password, plain_password)


async def verify_password_async(plain_password: str, password_hash: str) -> bool:
    """Verify a password without blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _HASH_EXECUTOR, verify_password, plain_password, password_hash
    )


async def verify_dummy_password_async(plain_password: str) -> bool:
    """
    Spend the same work as a real verification and always return False.

    Used when the user does not exist, so the response time does not reveal
    whether a username is registered.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_HASH_EXECUTOR, _verify_dummy, plain_password)

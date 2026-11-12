# ClimbAPI

FastAPI backend for Greppmästerskapen. It uses PostgreSQL, SQLAlchemy, and dbmate migrations.

For the complete local setup, including starting the frontend, see the [workspace startup guide](../ClimbApp/README.md#start-both-applications-locally).

## Run the backend

Prerequisites: Python 3.14, a running PostgreSQL server, and [dbmate](https://github.com/amacneil/dbmate) (on macOS: `brew install dbmate`). Create a local database, for example `grepp`, and configure a local `.env` file:

```dotenv
DATABASE_URL="postgresql://postgres:YOUR_PASSWORD@127.0.0.1:5432/grepp?sslmode=disable"
ASYNC_DATABASE_URL="postgresql+asyncpg://postgres:YOUR_PASSWORD@127.0.0.1:5432/grepp"
PASSWORD_PEPPER="replace-with-a-long-random-secret"
# Generate with: openssl rand -base64 32
JWT_SECRET="paste-the-generated-value"
FRONTEND_URL="http://localhost:5173"
```

Use the username, password, and database name configured for your local PostgreSQL installation. `JWT_SECRET` has no default: the process refuses to start unless it is a random value of at least 32 bytes. Keep `.env` out of version control.

Create and activate a Python 3.14 virtual environment, install dependencies, load the environment, and run migrations. If `.venv` already exists and was created with another Python version, remove it first (`rm -rf .venv`):

```sh
if [ ! -d .venv ]; then
	python -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt
set -a
source .env
set +a
dbmate up
```

Start the API in the same terminal so it inherits the environment variables:

```sh
python main.py
```

The API listens at `http://127.0.0.1:8080`. OpenAPI documentation is available at [http://127.0.0.1:8080/docs](http://127.0.0.1:8080/docs). The app's SQLite fallback is intended for simple local/test use; the documented database migration workflow uses PostgreSQL.

To run backend tests, install the development dependencies and execute pytest:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Mail settings are optional for local startup. Without configured mail credentials, password-reset links are printed for development.

## Rate limiting

The auth endpoints are rate limited per client IP, and failed logins are also limited per username. IPv6 clients are grouped by their /64 network, since one customer usually controls a whole /64; IPv4-mapped IPv6 addresses count as the IPv4 address. When a limit is hit the API returns `429 Too Many Requests` with a generic message and a `Retry-After` header (seconds).

| Environment variable | Default | Applies to |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | Turns all limits below on or off |
| `RATE_LIMIT_LOGIN` | `10/minute` | `/auth/login` and `/auth/token` together, per IP |
| `RATE_LIMIT_SIGNUP` | `5/minute` | `/auth/signup`, per IP |
| `RATE_LIMIT_PASSWORD_RESET_REQUEST` | `3/minute` | `/auth/password-reset/request`, per IP |
| `RATE_LIMIT_PASSWORD_RESET_CONFIRM` | `10/minute` | `/auth/password-reset/confirm`, per IP |
| `RATE_LIMIT_REFRESH` | `30/minute` | `/auth/refresh`, per IP |
| `LOGIN_MAX_FAILURES` | `5` | Failed logins per username and client IP before that IP is blocked for the username |
| `LOGIN_MAX_FAILURES_PER_USERNAME` | `50` | Failed logins per username across all IPs; past this, every IP that has failed on the username is blocked |
| `LOGIN_FAILURE_WINDOW_SECONDS` | `900` | Sliding window for both failure limits |

Limit strings use the [limits](https://limits.readthedocs.io/en/stable/quickstart.html#rate-limit-string-notation) notation, for example `10/minute` or `20/minute;100/hour`. Invalid values stop the app at startup.

Only failed logins count, and a successful login clears the counter for that username and IP. A blocked IP is refused even with the correct password, so a guesser cannot tell when they hit it. Because the block is per IP, someone guessing a user's password cannot lock the real user out from another network; once the per-username cap is reached, a new IP still gets through until its first failure. The throttle tracks at most 10,000 keys and never forgets an active one early; if it is full of active keys, new usernames get `429` until the oldest entry expires.

Counters are kept in memory, which works because the container runs a single uvicorn worker. With several workers or replicas, each process counts on its own; move the storage to Redis before scaling out.

**Behind a reverse proxy** the limiter must see the real client IP. The app uses the connection address and does not read `X-Forwarded-For` itself, because clients can forge it. The Docker image runs uvicorn with `--proxy-headers` and trusts those headers only from the addresses in `FORWARDED_ALLOW_IPS`:

| Environment variable | Default | Meaning |
|---|---|---|
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Comma-separated IPs or CIDR ranges of your reverse proxy, for example `10.0.0.0/8` |

Set it to your proxy's address or network. Do not use `*` unless the container can only be reached through the proxy, because any client could then forge its IP. Outside Docker, pass the same flags yourself:

```sh
python -m uvicorn main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips="<proxy-ip>"
```

Without this, every request appears to come from the proxy and all clients share one budget.

Password hashing (Argon2) runs on a small dedicated thread pool so it does not block the event loop. `ARGON2_MAX_CONCURRENCY` (default `4`) caps parallel hashes, and with it peak memory (each hash uses `ARGON2_MEMORY_KIB`, default 64 MiB).

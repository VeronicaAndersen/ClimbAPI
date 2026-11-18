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

The Postgres throttle tests in `tests/pg` are skipped unless `TEST_POSTGRES_URL` points at a throwaway database with the migrations applied. Every test empties the throttle tables, so never use a real database:

```sh
createdb -h localhost climbapi_throttle_test
DATABASE_URL='postgres://<user>@localhost/climbapi_throttle_test?sslmode=disable' dbmate --no-dump-schema up
TEST_POSTGRES_URL='postgresql+asyncpg://<user>@localhost/climbapi_throttle_test' python -m pytest -q
```

Mail settings are optional for local startup. Without configured mail credentials no reset email is sent: the API logs a warning (at most once a minute) and still answers the request normally. The reset link is never printed or logged, because it contains a live token. To try the reset flow locally, point the `MAIL_*` settings at a local SMTP catcher such as [Mailpit](https://mailpit.axllent.org/).

## CI

`.github/workflows/ci.yml` runs on every pull request and on pushes to `Dev` and `main`, with a read-only token and no secrets. It sets up Python from `.python-version`, installs `requirements-dev.txt`, starts a throwaway `postgres:17` service container, applies the migrations with a pinned dbmate release (its SHA-256 is checked), and runs the whole test suite with `TEST_POSTGRES_URL`, so the Postgres throttle tests in `tests/pg` run too. Ruff only checks the files changed on the Postgres throttle branch, because the repository still has older findings; widen the list as those are fixed. Actions are pinned to commit SHAs, and a new push cancels the previous run for the same branch.

## Usernames

There are two rules on purpose: a strict one for new usernames and a lenient one for finding existing accounts. Accounts created before the strict rule contain spaces, "@", accented letters and even emoji, and they must keep working.

**New usernames** (signup through `/auth/signup`, climbers an admin creates through the admin-only `POST /api/v1/climber`, and a username that is set or changed in a profile update, including by an admin):

1. Normalize: trim surrounding whitespace, apply Unicode NFKC, then lowercase. The normalized form is what is stored.
2. After normalizing, the name must be 1 to 200 characters long.
3. Every character must be one of:
   - a letter (Unicode categories L*), including accented letters such as å, ä, ö, é, ø;
   - a combining mark (M*) directly after a letter, at most three in a row (enough for Vietnamese; NFKC composes most accents into one character anyway);
   - a decimal digit (Nd);
   - one of `.` `_` `-` `@`;
   - a plain space (U+0020), but not at the start or end and never two in a row.
4. Default_Ignorable code points are refused even if they are letters or marks (for example Hangul fillers and variation selectors), as are the nearly invisible stretching letters U+0640 (Arabic tatweel) and U+07FA (NKo lajanyalan).

Everything else gets `422`: emoji and other symbols, other punctuation, line breaks, control, format (zero-width, bidi), private-use and unassigned characters. Because NFKC folds look-alikes, a fullwidth `ａdmin` is stored as `admin` and a decomposed `josé` as the precomposed one, so they collide with an existing account and get `409 Username is already taken`. NFKC also turns NBSP, the ideographic space and other space variants into a plain space, so a single one between words is accepted. Mixed scripts (for example a Cyrillic letter among Latin ones) are allowed for now.

A profile update only applies the strict rule when the username actually changes. Resending the current username, even a legacy one that the rule would refuse, is accepted and leaves it as it is. A changed username is normalized from the input exactly as at signup, so a rename and a signup with the same input store the same name.

**Finding existing accounts** (`/auth/login`, `/auth/token`, `/auth/password-reset/request`) stays lenient. The typed name is looked up in two forms in a single query: trimmed and lowercased (how every existing account is found, legacy names included), and trimmed, NFKC-normalized and lowercased (the stored form of names created under the rule above, so someone who signed up with a fullwidth or decomposed name can type it the same way again). If both match different accounts, the exact trimmed-and-lowercased match wins. The production data was audited when this was introduced: no stored username differs from its NFKC form, so no existing account can shadow a newer look-alike, and the strict rule means no new non-NFKC usernames can be created. There is no allowlist here. Empty names, names over 200 characters and names with control, format or surrogate characters are refused with the normal "invalid credentials" (or generic reset) answer, since no account can have them.

## Rate limiting

The auth endpoints are rate limited per client IP, and failed logins are also limited per username. IPv6 clients are grouped by their /64 network, since one customer usually controls a whole /64; IPv6 addresses that embed an IPv4 address (IPv4-mapped `::ffff:a.b.c.d`, 6to4 `2002::/16`, NAT64 `64:ff9b::/96` and the deprecated IPv4-compatible `::a.b.c.d`) count as that IPv4 address. When a limit is hit the API returns `429 Too Many Requests` with a generic message and a `Retry-After` header (seconds).

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
| `LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX` | `15` | Failed logins per username from one IPv6 /48; past this, every address in that /48 is blocked for the username, including ones that have not failed. A successful login does not reset it. IPv4 is not affected |
| `LOGIN_MAX_USERNAMES_PER_CLIENT` | `20` | Distinct usernames one client may have recent failures on; past this it gets `429` for new usernames. Only enforced while the throttle is under pressure |
| `LOGIN_THROTTLE_MAX_TRACKED` | `200000` | Entries kept per throttle counter. A failed login costs about 550-750 bytes from IPv4 and up to about 1 KB from IPv6 (short to 200-character usernames; a key is never longer than 200 characters, even when NFKC expands the typed name), so at most about 210 MB when full |
| `LOGIN_THROTTLE_PRESSURE_RATIO` | `0.1` | The throttle counts as under pressure once a counter holds this share of `LOGIN_THROTTLE_MAX_TRACKED` (20,000 keys by default). `0` enforces the per-client cap always, `1` only when full |
| `LOGIN_THROTTLE_BACKEND` | `memory` | Where the login throttle keeps its counters: `memory` (this process), `postgres` (shared by all instances; needs the app's database to be Postgres and the `login_throttle` migration) or `shadow` (memory decides, Postgres runs alongside for comparison; same requirements as `postgres`) |
| `LOGIN_THROTTLE_DB_TIMEOUT_SECONDS` | `3` | Postgres throttle and shared IP limit: upper bound for each query, including waiting for a connection (must be positive). Advisory-lock waits inside the database get half of it |
| `LOGIN_THROTTLE_LOAD_CACHE_SECONDS` | `5` | Postgres throttle: how long each instance reuses its reading of how full the throttle is (must be positive) |
| `LOGIN_IP_LIMIT_BACKEND` | `memory` | `postgres` adds the `RATE_LIMIT_LOGIN` limit for `/auth/login` and `/auth/token` counted across all instances, after the in-memory one. For hosting without a shared rate-limit layer such as WAF rules |
| `LOGIN_THROTTLE_SWEEP_BATCH` | `200` | Postgres backends: expired rows deleted per login request (during the check and when recording the outcome) and per shared IP-limit check. Bounded so one sweep stays a few milliseconds (must be positive) |
| `LOGIN_FAILURE_WINDOW_SECONDS` | `900` | Sliding window for all failure limits |

Limit strings use the [limits](https://limits.readthedocs.io/en/stable/quickstart.html#rate-limit-string-notation) notation, for example `10/minute` or `20/minute;100/hour`. Invalid values stop the app at startup.

Only failed logins count, and a successful login clears the counter for that username and IP. Here "IP" means an IPv4 address or an IPv6 /64. The username is counted in its stored form (trimmed, NFKC-normalized, lowercased), so every spelling that reaches the same account, such as fullwidth or partly fullwidth letters, shares one set of limits. A blocked IP is refused even with the correct password, so a guesser cannot tell when they hit it. Because the block is per IP, someone guessing a user's password cannot lock the real user out from another network; once the per-username cap is reached, a new IP still gets through until its first failure. For IPv6, failures are also counted per /48 (`LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX`), so someone holding a /48 cannot get a fresh guess from each of its 65,536 /64s.

**Accepted trade-off of the /48 counter.** Some mobile carriers and ISPs put many subscribers in one /48. Once that /48 has reached `LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX` failures on a username, that username is blocked for every subscriber in the /48 until the count drops below the cap again. This includes the real user with the correct password, and a successful login from elsewhere in the /48 does not reset it. Successful logins do not prolong it either: each failure expires one window after it happened, however often someone logs in successfully meanwhile. The lock is not limited to one window: it lasts as long as failures keep coming. Each failure keeps the count up for a full window (`LOGIN_FAILURE_WINDOW_SECONDS`, 15 minutes by default), so someone who keeps adding about one failure a minute keeps the username locked indefinitely. They need only a few /64s in that /48, since each /64 may fail up to `LOGIN_MAX_FAILURES` times before its own limit applies, or a mobile connection that gets a new /64 on every reconnect. The owner accepted this to stop guessing from large IPv6 ranges. Users on IPv4, or in another /48, are not affected.

The throttle keeps at most `LOGIN_THROTTLE_MAX_TRACKED` entries and never forgets an active one early, because that would reset its counter. If it is full of active entries it fails closed: every username without recent failures from that IP gets `429` until the oldest entry expires, and a warning is logged (at most once a minute). `LOGIN_MAX_USERNAMES_PER_CLIENT` makes that hard: once the throttle is under pressure, filling the rest of the table takes failed logins from about 9,000 different IPs within one window. For IPv6 an "IP" here is a /64, and one holder of a /48 has 65,536 of them, so in theory a single /48 holder could reach pressure, or fill the table, alone. The practical bound is Argon2: every failed login costs a full password hash, so failing on hundreds of thousands of usernames per window takes far more hashing than one worker does. That limits the attacker's rate, but it also slows the API for everyone while it lasts. The cap is not applied before that, so a few people mistyping on a shared address (CGNAT, a gym's wifi, one IPv6 /64) do not lock everyone else there out. When the cap applies, the `Retry-After` is the full window, an upper bound. A user who has not failed recently is only affected while the table is full, or, under pressure, when their address has already failed on many usernames.

**Pressure is a deliberate fail-safe.** An attacker can push the throttle under pressure on purpose by failing on about 20,000 usernames (10% of `LOGIN_THROTTLE_MAX_TRACKED` by default) within one window. That switches the per-client username cap back on for everyone, so shared addresses (CGNAT, a gym's wifi, one IPv6 /64) can again be refused new usernames after 20 failed ones, until the pressure drops. This is intended: under a large attack, keeping the table from filling up (which would block every user without recent failures) matters more than convenience on shared addresses.

Counters are kept in memory, which works because the container runs a single uvicorn worker. With several workers or replicas, each process counts on its own; switch the login throttle and login IP limit to the Postgres backends below before scaling out (the other route limits stay per process).

**Shared counters in Postgres.** On serverless hosting (Vercel) every instance has its own memory, so the in-memory counters only limit each instance. Two settings move them to the app's Postgres database (migration `20261118000000_add_login_throttle`):

- `LOGIN_THROTTLE_BACKEND=postgres` keeps the login throttle in the `login_throttle_key` table, one row per counter key with its attempt timestamps, usernames and client keys in plain text (the owner's choice). Rows are not deleted the moment they expire: every login that reaches the database (both the check before the password and the recording of its outcome) deletes up to `LOGIN_THROTTLE_SWEEP_BATCH` expired rows, oldest first. On an idle app expired rows therefore stay until the next login, and deleted rows also remain in Neon's restore history for its retention period. The check-and-reserve runs in one database function under advisory locks, so concurrent requests on different instances cannot slip past a limit. Every call uses its own autocommit connection, never the request's session. How full the throttle is (pressure, capacity) is read at most once per `LOGIN_THROTTLE_LOAD_CACHE_SECONDS` per instance, so those two can lag by that long, and the table can briefly overshoot `LOGIN_THROTTLE_MAX_TRACKED`; the bound now protects table size rather than memory.
- `LOGIN_IP_LIMIT_BACKEND=postgres` adds a shared per-IP limit for `/auth/login` and `/auth/token` with the same `RATE_LIMIT_LOGIN` rules as the in-memory limiter (fixed windows from the first request, every limit checked before any is counted). It has its own small `login_ip_limit` table of counters rather than rows in the throttle table, so it never affects the throttle's pressure or capacity.

If the database is slow (`LOGIN_THROTTLE_DB_TIMEOUT_SECONDS`) or unavailable, logins are refused with `429` and `Retry-After: 10`: long enough not to hammer a struggling database, short enough that a brief outage or cold start does not lock users out for long. A limit that silently stopped counting would fail open. If only the reading of how full the throttle is fails, the throttle assumes it is under pressure but not full. If recording a login's outcome fails, the login still answers normally; the attempt stays counted as a failure. Warnings for all of these are rate-limited and contain no usernames or addresses.

**Shadow mode for the rollout.** With `LOGIN_THROTTLE_BACKEND=shadow` the in-memory throttle answers every login exactly as with `memory`, and the Postgres throttle runs alongside it, so its tables fill as they would in production and the two can be compared before switching. Postgres never changes a response: its 429s and its fail-closed answers are ignored, its errors and timeouts are only counted, and a login waits at most 0.5 s for it at any point (on an allowed login its check runs while the password is hashed). Each store keeps its own reservation, so both record failures, successes and released attempts. At most once a minute a warning line with counts only (no usernames or addresses) summarises the period:

```
Login throttle shadow (last 60 s): calls=812 postgres_errors=0 postgres_timeouts=1 postgres_blocked_memory_allowed=3 memory_blocked_postgres_allowed=0 slow=2
```

- `calls`: logins checked in the period.
- `postgres_blocked_memory_allowed`: Postgres would have refused a login that memory let through. Expected in small numbers on several instances, since Postgres sees failures from all of them; a large count means switching to `postgres` would refuse noticeably more logins.
- `memory_blocked_postgres_allowed`: memory refused a login Postgres would have allowed. Should stay near 0; otherwise the two disagree on the rules.
- `postgres_errors`, `postgres_timeouts`: the database failed or did not answer within 0.5 s (or `LOGIN_THROTTLE_DB_TIMEOUT_SECONDS`). After a switch to `postgres` these would be refused logins (429).
- `slow`: Postgres calls that took over 0.25 s. They show the latency `postgres` would add.

Switch to `postgres` once errors and timeouts stay at or near 0 and the disagreements are explained.

**Behind a reverse proxy** the limiter must see the real client IP. The app uses the connection address and does not read `X-Forwarded-For` itself, because clients can forge it. The Docker image runs uvicorn with `--proxy-headers` and trusts those headers only from the addresses in `FORWARDED_ALLOW_IPS` (uvicorn reads this variable itself; the image passes it explicitly):

| Environment variable | Default | Meaning |
|---|---|---|
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | Comma-separated IPs or CIDR ranges of your reverse proxy, for example `10.0.0.0/8` |
| `TRUST_VERCEL_HEADERS` | `false` | On a Vercel deployment only (requires `VERCEL=1` and `VERCEL_ENV` `production` or `preview`): take the client IP from the `x-real-ip` header |

Set it to your proxy's address or network. Do not use `*` unless the container can only be reached through the proxy, because any client could then forge its IP. Outside Docker, pass the same flags yourself:

```sh
python -m uvicorn main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips="<proxy-ip>"
```

Without this, every request appears to come from the proxy and all clients share one budget. If the app sees a missing or non-IP client address, it uses one shared `unknown` key for all such clients and logs a warning (at most once a minute, without the address).

**On Vercel** there is no uvicorn in front to configure, and the connection address belongs to the platform. Set `TRUST_VERCEL_HEADERS=true` there: the client IP is then taken from the `x-real-ip` header, which Vercel's edge sets (replacing any value the client sent), and the connection address is ignored. The header must hold exactly one IP address; a list, a port, anything else or a missing header gives the shared `unknown` key and the same rate-limited warning, never the connection address. The same IPv6 /64 and /48 grouping and IPv4 mapping apply as above. The app refuses to start with `TRUST_VERCEL_HEADERS=true` unless it runs as a Vercel deployment: `VERCEL=1` and `VERCEL_ENV` is `production` or `preview` (both set by Vercel). Anywhere else clients could choose their own rate-limit key with that header. `VERCEL=1` alone is not enough, because `vercel env pull` writes it into local env files (with `VERCEL_ENV=development`). Do not put another proxy or CDN in front of Vercel: Vercel would then see that proxy as the client, and `x-real-ip` would put every user on the proxy's single key. `FORWARDED_ALLOW_IPS` only affects uvicorn and is not used on Vercel.

Each request's database session is rolled back (after an error) and closed even if the request is cancelled. `DB_CLEANUP_TIMEOUT_SECONDS` (default `10`) bounds each of those steps; if one times out, the session's connections are invalidated instead of being returned to the pool, and only the error type is logged.

Password hashing (Argon2) runs on a small dedicated thread pool so it does not block the event loop. `ARGON2_MAX_CONCURRENCY` (default `4`) caps parallel hashes, and with it peak memory (each hash uses `ARGON2_MEMORY_KIB`, default 64 MiB).

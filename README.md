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
| `LOGIN_THROTTLE_MAX_TRACKED` | `200000` | Entries kept per throttle counter (with the Postgres backend: rows per kind in `login_throttle_key`, see the cache note below). A failed login costs about 550-750 bytes from IPv4 and up to about 1 KB from IPv6 (short to 200-character usernames; a key is never longer than 200 characters, even when NFKC expands the typed name), so at most about 210 MB when full |
| `LOGIN_THROTTLE_PRESSURE_RATIO` | `0.1` | The throttle counts as under pressure once a counter holds this share of `LOGIN_THROTTLE_MAX_TRACKED` (20,000 keys by default). `0` enforces the per-client cap always, `1` only when full |
| `LOGIN_THROTTLE_BACKEND` | `memory` | Where the login throttle keeps its counters: `memory` (this process), `postgres` (shared by all instances; needs the app's database to be Postgres and the `login_throttle` migration) or `shadow` (memory decides, Postgres runs alongside for comparison; same requirements as `postgres`) |
| `LOGIN_THROTTLE_DB_TIMEOUT_SECONDS` | `3` | Postgres throttle and shared IP limit: upper bound for each query, including waiting for a connection (must be positive). Advisory-lock waits inside the database get half of it, and the throttle's reading of how full it is gets a third |
| `LOGIN_THROTTLE_LOAD_CACHE_SECONDS` | `5` | Postgres throttle: how long each instance reuses its reading of how full the throttle is (must be positive) |
| `LOGIN_IP_LIMIT_BACKEND` | `memory` | `postgres` adds the `RATE_LIMIT_LOGIN` limit for `/auth/login` and `/auth/token` counted across all instances, after the in-memory one. For hosting without a shared rate-limit layer such as WAF rules |
| `LOGIN_THROTTLE_SWEEP_BATCH` | `200` | Postgres backends: expired rows deleted per login request (during the check and when recording the outcome) and per shared IP-limit check. Bounded so one sweep stays a few milliseconds (must be positive) |
| `LOGIN_FAILURE_WINDOW_SECONDS` | `900` | Sliding window for all failure limits |

Limit strings use the [limits](https://limits.readthedocs.io/en/stable/quickstart.html#rate-limit-string-notation) notation, for example `10/minute` or `20/minute;100/hour`. Invalid values stop the app at startup.

Only failed logins count, and a successful login clears the counter for that username and IP. Here "IP" means an IPv4 address or an IPv6 /64. The username is counted in its stored form (trimmed, NFKC-normalized, lowercased), so every spelling that reaches the same account, such as fullwidth or partly fullwidth letters, shares one set of limits. A blocked IP is refused even with the correct password, so a guesser cannot tell when they hit it. Because the block is per IP, someone guessing a user's password cannot lock the real user out from another network; once the per-username cap is reached, a new IP still gets through until its first failure. For IPv6, failures are also counted per /48 (`LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX`), so someone holding a /48 cannot get a fresh guess from each of its 65,536 /64s.

**Accepted trade-off of the /48 counter.** Some mobile carriers and ISPs put many subscribers in one /48. Once that /48 has reached `LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX` failures on a username, that username is blocked for every subscriber in the /48 until the count drops below the cap again. This includes the real user with the correct password, and a successful login from elsewhere in the /48 does not reset it. Successful logins do not prolong it either: each failure expires one window after it happened, however often someone logs in successfully meanwhile. The lock is not limited to one window: it lasts as long as failures keep coming. Each failure keeps the count up for a full window (`LOGIN_FAILURE_WINDOW_SECONDS`, 15 minutes by default), so someone who keeps adding about one failure a minute keeps the username locked indefinitely. They need only a few /64s in that /48, since each /64 may fail up to `LOGIN_MAX_FAILURES` times before its own limit applies, or a mobile connection that gets a new /64 on every reconnect. The owner accepted this to stop guessing from large IPv6 ranges. Users on IPv4, or in another /48, are not affected.

The throttle keeps at most `LOGIN_THROTTLE_MAX_TRACKED` entries and never forgets an active one early, because that would reset its counter. If it is full of active entries it fails closed: every username without recent failures from that IP gets `429` until the oldest entry expires, and a warning is logged (at most once a minute). `LOGIN_MAX_USERNAMES_PER_CLIENT` makes that hard: once the throttle is under pressure, filling the rest of the table takes failed logins from about 9,000 different IPs within one window. For IPv6 an "IP" here is a /64, and one holder of a /48 has 65,536 of them, so in theory a single /48 holder could reach pressure, or fill the table, alone. The practical bound is Argon2: every failed login costs a full password hash, so failing on hundreds of thousands of usernames per window takes far more hashing than one worker does. That limits the attacker's rate, but it also slows the API for everyone while it lasts. The cap is not applied before that, so a few people mistyping on a shared address (CGNAT, a gym's wifi, one IPv6 /64) do not lock everyone else there out. When the cap applies, the `Retry-After` is the full window, an upper bound. A user who has not failed recently is only affected while the table is full, or, under pressure, when their address has already failed on many usernames.

**Pressure is a deliberate fail-safe.** An attacker can push the throttle under pressure on purpose by failing on about 20,000 usernames (10% of `LOGIN_THROTTLE_MAX_TRACKED` by default) within one window. That switches the per-client username cap back on for everyone, so shared addresses (CGNAT, a gym's wifi, one IPv6 /64) can again be refused new usernames after 20 failed ones, until the pressure drops. This is intended: under a large attack, keeping the table from filling up (which would block every user without recent failures) matters more than convenience on shared addresses.

By default counters are kept in memory, and the Docker image runs a single uvicorn worker, so there is one set of counters per container. With several workers or replicas, each process counts on its own. `LOGIN_THROTTLE_BACKEND=postgres` shares the login throttle between all of them, and `LOGIN_IP_LIMIT_BACKEND=postgres` also shares the per-IP limit for `/auth/login` and `/auth/token` (see below). The per-IP limits for signup, password reset and refresh always stay per process, and the in-memory limit on the login routes still runs first even with the Postgres one on.

**Shared counters in Postgres.** On serverless hosting (Vercel) every instance has its own memory, so the in-memory counters only limit each instance. Two settings move them to the app's Postgres database (migration `20261118000000_add_login_throttle`):

- `LOGIN_THROTTLE_BACKEND=postgres` keeps the login throttle in the `login_throttle_key` table, one row per counter key with its attempt timestamps, usernames and client keys in plain text (the owner's choice). Rows are not deleted the moment they expire: every login that reaches the database (both the check before the password and the recording of its outcome) deletes up to `LOGIN_THROTTLE_SWEEP_BATCH` expired rows, oldest first. On an idle app expired rows therefore stay until the next login, and deleted rows also remain in Neon's restore history for its retention period. The check-and-reserve runs in one database function under advisory locks, so concurrent requests on different instances cannot slip past a limit. Every call uses its own autocommit connection, never the request's session. How full the throttle is (pressure, capacity) is read at most once per `LOGIN_THROTTLE_LOAD_CACHE_SECONDS` per instance, so those two can lag by that long, and the table can briefly overshoot `LOGIN_THROTTLE_MAX_TRACKED`; the bound now protects table size rather than memory.
- `LOGIN_IP_LIMIT_BACKEND=postgres` adds a shared per-IP limit for `/auth/login` and `/auth/token` with the same `RATE_LIMIT_LOGIN` rules as the in-memory limiter (fixed windows from the first request, every limit checked before any is counted). It has its own small `login_ip_limit` table of counters rather than rows in the throttle table, so it never affects the throttle's pressure or capacity.

If the database is slow (`LOGIN_THROTTLE_DB_TIMEOUT_SECONDS`) or unavailable, logins are refused with `429` and `Retry-After: 10`: long enough not to hammer a struggling database, short enough that a brief outage or cold start does not lock users out for long. A limit that silently stopped counting would fail open. If only the reading of how full the throttle is fails, the throttle assumes it is under pressure but not full. If recording a login's outcome fails, the login still answers normally; the attempt stays counted as a failure. Warnings for all of these are rate-limited and contain no usernames or addresses.

**Shadow mode for the rollout.** With `LOGIN_THROTTLE_BACKEND=shadow` the in-memory throttle answers every login exactly as with `memory`, and the Postgres throttle runs alongside it, so its tables fill as they would in production and the two can be compared before switching. Postgres never changes a response: its 429s and its fail-closed answers are ignored, its errors and timeouts are only counted, and a login waits at most 0.5 s for it at any point (on an allowed login its check runs while the password is hashed). A call that is still running after 0.5 s is not cancelled, because the database may already have recorded it: it runs on, limited only by the Postgres backend's own `LOGIN_THROTTLE_DB_TIMEOUT_SECONDS` (3 s by default), and a reservation it returns late is released in the background. Each store keeps its own reservation, so both record failures, successes and released attempts. At most once a minute a warning line with counts only (no usernames or addresses) summarises the period:

```
Login throttle shadow (last 60 s): calls=812 postgres_errors=0 postgres_timeouts=0 shadow_wait_exceeded=1 postgres_blocked_memory_allowed=3 memory_blocked_postgres_allowed=0 slow=2
```

- `calls`: logins checked in the period.
- `postgres_blocked_memory_allowed`: Postgres would have refused a login that memory let through. Expected in small numbers on several instances, since Postgres sees failures from all of them; a large count means switching to `postgres` would refuse noticeably more logins.
- `memory_blocked_postgres_allowed`: memory refused a login Postgres would have allowed. Should stay near 0; otherwise the two disagree on the rules.
- `postgres_errors`, `postgres_timeouts`: the database failed, or did not answer within its own limit (`LOGIN_THROTTLE_DB_TIMEOUT_SECONDS`, 3 s by default). After a switch to `postgres` these would be refused logins (429).
- `shadow_wait_exceeded`: Postgres had not answered within the shadow's 0.5 s wait. The call was left running, not cancelled, and a late reservation is finished with the login's real outcome. On Vercel the instance can be frozen or reclaimed after the response, so that late finish may never run; Postgres then counts the attempt as a failure until the window expires, which can raise `postgres_blocked_memory_allowed` a little. Not an error by itself and not a 429 after a switch: with `postgres` the limit is 3 s, so only calls that also pass that limit (counted in `postgres_timeouts`) or fail would be refused. It shows how often Postgres is slower than 0.5 s, for example on a Neon cold start.
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

## Rollout runbook

How to move the login throttle on Vercel (Hobby plan) from in-memory counters to the shared Postgres throttle on Neon. The default stays `memory`, so merging and deploying the code changes nothing until you set a variable. Settings and log lines are explained in [Rate limiting](#rate-limiting). Changes to Vercel environment variables only apply to new deployments, so redeploy after each one.

Do not set `LOGIN_THROTTLE_BACKEND=postgres` (or `LOGIN_IP_LIMIT_BACKEND=postgres`) on an environment whose database has not been migrated: every database call then fails and every login is refused with `429` and `Retry-After: 10`. `shadow` is safe in that case, because it never changes a response, but its `postgres_errors` counter will count every login.

### 1. Merge into Dev

Merge pull request #13 into `Dev` once CI is green. Production deploys from `main`, so nothing reaches Production until you merge `Dev` into `main` (step 4). A `Dev` deployment is a Preview deployment.

### 2. Run the migration on Neon

Run it with the direct (non-pooler) connection string: the host in it has no `-pooler` part. Migrations should not go through PgBouncer. Copy the string from the Neon console and keep it out of shell history and files:

```sh
printf 'Direct Neon URL: '; read -rs NEON_DIRECT_URL; echo
export NEON_DIRECT_URL
# Expected shape (placeholders only):
# postgres://<user>:<password>@<direct-host>/<database>?sslmode=require
```

`--env NEON_DIRECT_URL` makes dbmate read the URL from that variable. `--no-dump-schema` stops dbmate from rewriting `db/schema.sql` from Neon. Use `migrate`, not `up`: `up` also tries to create the database first.

```sh
dbmate --env NEON_DIRECT_URL --no-dump-schema status
dbmate --env NEON_DIRECT_URL --no-dump-schema migrate
dbmate --env NEON_DIRECT_URL --no-dump-schema status
```

Before `migrate`, `status` should list only `20261118000000_add_login_throttle.sql` as pending. If older migrations are pending too, stop and find out why before applying them. After `migrate`, `status` ends with `Pending: 0`.

Confirm the objects exist, in the Neon SQL Editor or with `psql "$NEON_DIRECT_URL"`:

```sql
-- Both columns must be non-null.
SELECT to_regclass('public.login_throttle_key') AS throttle_table,
       to_regclass('public.login_ip_limit')     AS ip_limit_table;

-- Must return 9: login_throttle_{recent,push,wait,sweep,acquire,finish,withdraw,load}
-- and login_ip_limit_hit.
SELECT count(*) AS functions
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.proname LIKE 'login\_%';

-- Must return one row.
SELECT version FROM schema_migrations WHERE version = '20261118000000';
```

If Preview deployments use a different Neon database or branch than Production, run this step for each of them. Which database each environment uses is set by `DATABASE_URL` / `ASYNC_DATABASE_URL` in the Vercel project, and I could not check it from the repository. The app's own `DATABASE_URL` on Vercel can stay on the `-pooler` host: the throttle only uses short autocommit statements. The role that runs the migration owns the objects; the app's role must be that role or be granted access to them (the migration contains no `GRANT`).

If dbmate rejects the URL because of extra libpq parameters such as `channel_binding`, remove them from your copy. This could not be tested against Neon.

### 3. Preview: shadow mode and the spoof check

In the Vercel project settings, add these variables for the Preview environment only, then redeploy `Dev`:

| Variable | Value |
|---|---|
| `TRUST_VERCEL_HEADERS` | `true` |
| `LOGIN_THROTTLE_BACKEND` | `shadow` |

`TRUST_VERCEL_HEADERS=true` makes the app take the client IP from `x-real-ip`, which Vercel's edge sets. The app refuses to start with it unless `VERCEL=1` and `VERCEL_ENV` is `production` or `preview`; Vercel sets both on deployed functions. Do not set it for the Development environment: `vercel env pull` would write it into a local env file together with `VERCEL_ENV=development`, and a local run would then stop at startup. Without it on Vercel, every user would share the platform's connection address as one rate-limit key.

Spoof check: send 5 failed logins for a test account, each with a different forged `x-real-ip` and `x-forwarded-for`. If a client could pick its own key, each attempt would get a fresh budget. The app only reads `x-real-ip` on Vercel, and the edge replaces what the client sent, so all attempts share one key. Use an account you can afford to leave blocked for 15 minutes from your IP (`LOGIN_FAILURE_WINDOW_SECONDS`). If Preview deployments are protected by Vercel's Deployment Protection, the requests need a bypass; I did not verify how that is configured for this project.

```sh
BASE="https://<preview-deployment-host>"
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "attempt $i: %{http_code}\n" \
    -X POST "$BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -H "x-real-ip: 203.0.113.$i" \
    -H "x-forwarded-for: 198.51.100.$i" \
    -d '{"username":"<test-account>","password":"wrong-password-on-purpose"}'
done
```

Expected with `LOGIN_THROTTLE_BACKEND=postgres`: attempts 1 to 5 give `401`, attempt 6 gives `429` (`LOGIN_MAX_FAILURES` is 5 failures per username and client). A `401` on attempt 6 means the forged headers changed the key: do not go further, and check `TRUST_VERCEL_HEADERS` and that nothing else sits in front of Vercel. In shadow mode the in-memory throttle decides, and it counts per instance, so attempt 6 may give `401` if the requests reached different instances. Postgres never changes a response there. Repeat the check in step 5, once the Preview runs `postgres`. Afterwards, `SELECT kind, count(*) FROM public.login_throttle_key GROUP BY kind;` shows rows for the test account (kind 1 and 2) once Postgres has recorded the failures.

### 4. Production in shadow mode for about a week

1. Run step 2 against the Production database first.
2. Merge `Dev` into `main` (your decision). With the defaults, the deployed code behaves as before.
3. In the Production environment set `TRUST_VERCEL_HEADERS=true` (if it is not already set) and `LOGIN_THROTTLE_BACKEND=shadow`, then redeploy.

Watch the Vercel runtime logs for the line `Login throttle shadow (last 60 s): ...` (level warning, at most once a minute per instance, written when a login arrives after the minute has passed). The counters are kept per serverless instance, and an instance that is frozen or removed may lose what it has not logged yet, so add up the lines and treat the totals as approximate. What the values mean:

| Counter | Safe to switch | Meaning if not |
|---|---|---|
| `postgres_errors` | 0, or a few isolated ones you can explain | The database call failed. After the switch each of these is a refused login (`429`) |
| `postgres_timeouts` | 0, or a few isolated ones you can explain | No answer within the backend's own 3 s limit. After the switch each of these is a refused login (`429`) |
| `shadow_wait_exceeded` | A small share of `calls` | No answer within the shadow's 0.5 s wait. Not refused after the switch (the limit there is 3 s), but a Neon cold start after scale-to-zero can take longer than 0.5 s (the plan estimated 0.5 to 1 s, not measured); if it is frequent, check latency before relying on it |
| `memory_blocked_postgres_allowed` | Near 0 | The two throttles disagree on the rules |
| `postgres_blocked_memory_allowed` | A small share of `calls`, and explained by failed-login bursts | Logins that would be refused after the switch |
| `slow` | A small share of `calls` | Postgres calls over 0.25 s. If most calls are slow, compare the regions of the Vercel function and the Neon project |

Also look for these warnings (counts only, no usernames or addresses): `Login throttle load query failed`, `Login throttle is full of active entries` (should never appear) and `Login throttle refused a client over its distinct-username cap`. The thresholds above are suggestions, not limits set in code.

### 5. Switch to postgres: Preview first, then Production

1. Set `LOGIN_THROTTLE_BACKEND=postgres` for Preview, redeploy, and repeat the spoof check from step 3. Expect `401` five times and then `429`.
2. If it behaves, set it for Production and redeploy.

After the switch, `Login throttle database unavailable (error or timeout); refusing logins with 429` means users are being refused because the database did not answer. One occurrence around a cold start is expected; a stream of them is not. See the failure behaviour under [Shared counters in Postgres](#rate-limiting).

### 6. Optional: shared per-IP login limit

If the [Vercel firewall](#vercel-firewall) cannot rate limit per IP for you, set `LOGIN_IP_LIMIT_BACKEND=postgres` (Production, and Preview if you want to test it) and redeploy. It is independent of `LOGIN_THROTTLE_BACKEND`, needs the same migration and adds one database call to every `/auth/login` and `/auth/token` request. If the database is unavailable those requests are refused with `429` and `Retry-After: 10`. Rows in `public.login_ip_limit` show that it counts.

### 7. Rollback

Set `LOGIN_THROTTLE_BACKEND=memory` (or remove the variable) and `LOGIN_IP_LIMIT_BACKEND=memory` (or remove it), then redeploy. Nothing reads the tables after that, so they can stay and need no migration rollback. Expired rows are only deleted by a Postgres backend, so they stay until you remove them. They hold usernames and client IPs in plain text, so if you roll back for good you may empty them (counters only, nothing else depends on them):

```sql
TRUNCATE public.login_throttle_key, public.login_ip_limit;
```

### 8. Watch the table size in Neon

These queries only read. Run them in the Neon SQL Editor or with `psql "$NEON_DIRECT_URL"`.

```sql
SELECT 'login_throttle_key' AS table_name,
       count(*) AS row_count,
       pg_size_pretty(pg_total_relation_size('public.login_throttle_key')) AS total_size
FROM public.login_throttle_key
UNION ALL
SELECT 'login_ip_limit',
       count(*),
       pg_size_pretty(pg_total_relation_size('public.login_ip_limit'))
FROM public.login_ip_limit;

-- Rows per kind: 1 = (username, client), 2 = username, 3 = (username, IPv6 /48).
SELECT kind, count(*) AS keys, min(last_at) AS oldest, max(last_at) AS newest
FROM public.login_throttle_key
GROUP BY kind
ORDER BY kind;
```

The tables only hold recent failures (the last `LOGIN_FAILURE_WINDOW_SECONDS`, plus whatever has not been swept yet), so they should stay small in normal use: a failed login adds up to three rows in `login_throttle_key`. Each kind is capped near `LOGIN_THROTTLE_MAX_TRACKED` rows (200,000 by default), which can be overshot briefly. A steadily growing `oldest` age far beyond the window means sweeping is not keeping up or no logins are arriving to sweep. The Neon console shows project storage over time; check the storage limit of your plan there, since I did not verify it.

## Vercel firewall

A per-IP limit at the edge is the preferred first layer: it stops excess requests before they start a function, and it is shared by all instances without touching the database. The app's own limits stay as the second layer. Whether Vercel's rate-limit rules are available, and with which limits, on the Hobby plan is not something I could verify. Open the project's Firewall tab and check what you can create there before relying on it.

If rules are available, mirror the defaults of the `RATE_LIMIT_*` settings (`security/rate_limit.py`) for the paths under `/api/v1/auth/`, per client IP:

| Path | Limit | Setting |
|---|---|---|
| `/api/v1/auth/login` and `/api/v1/auth/token` | 10 per minute, together | `RATE_LIMIT_LOGIN` |
| `/api/v1/auth/signup` | 5 per minute | `RATE_LIMIT_SIGNUP` |
| `/api/v1/auth/password-reset/request` | 3 per minute | `RATE_LIMIT_PASSWORD_RESET_REQUEST` |
| `/api/v1/auth/password-reset/confirm` | 10 per minute | `RATE_LIMIT_PASSWORD_RESET_CONFIRM` |
| `/api/v1/auth/refresh` | 30 per minute | `RATE_LIMIT_REFRESH` |

`/login` and `/token` share one budget in the app. If a rule can only match one path, two rules give each path its own 10 per minute, so choose a lower value (for example 5) or accept the larger total. Do not use one rule for all of `/api/v1/auth/*` with the login limit: it would also throttle `refresh`, which needs 30 per minute. These rules do not replace the per-username failure limits, which only the throttle can enforce.

If rate-limit rules are not available on your plan, set `LOGIN_IP_LIMIT_BACKEND=postgres` ([step 6](#6-optional-shared-per-ip-login-limit)). It covers `/auth/login` and `/auth/token` only; the other routes keep their per-instance limits.

-- migrate:up

-- Shared login throttle (security/login_throttle_postgres.py). One row per
-- counter key, holding that key's attempt timestamps, oldest first:
--   kind 1: (username, client)   scope = client key (IPv4 or IPv6 /64)
--   kind 2: username             scope = ''
--   kind 3: (username, IPv6 /48) scope = /48 network
-- Usernames and client keys are stored in plain text; rows live for one
-- failure window (15 minutes by default). Logged on purpose: an UNLOGGED
-- table is emptied when a Neon compute restarts, which would fail open.
CREATE TABLE public.login_throttle_key (
    kind smallint NOT NULL CHECK (kind IN (1, 2, 3)),
    username text NOT NULL CHECK (char_length(username) <= 200),
    scope text NOT NULL DEFAULT '' CHECK (char_length(scope) <= 64),
    stamps timestamptz[] NOT NULL CHECK (cardinality(stamps) >= 1),
    last_at timestamptz NOT NULL,  -- max(stamps), for expiry and sweeping
    PRIMARY KEY (kind, username, scope),
    CONSTRAINT login_throttle_key_scope_matches_kind CHECK ((kind = 2) = (scope = ''))
);

-- Bounded sweep of expired rows, oldest first.
CREATE INDEX login_throttle_key_last_at_idx ON public.login_throttle_key (last_at);
-- Active keys per kind (load/pressure/capacity) and the oldest one (full).
CREATE INDEX login_throttle_key_kind_last_at_idx ON public.login_throttle_key (kind, last_at);
-- Distinct usernames with recent failures per client.
CREATE INDEX login_throttle_key_client_idx ON public.login_throttle_key (scope, last_at) WHERE kind = 1;

-- Optional shared per-IP limit for /auth/login and /auth/token
-- (LOGIN_IP_LIMIT_BACKEND=postgres). Fixed-window counters like the in-memory
-- limiter (limits' FixedWindowRateLimiter): a window starts at the first hit
-- and lasts period_seconds. A separate table because these are counters, not
-- timestamp lists, and must not count toward the throttle's load.
CREATE TABLE public.login_ip_limit (
    client text NOT NULL CHECK (char_length(client) <= 64),
    period_seconds integer NOT NULL CHECK (period_seconds > 0),
    window_start timestamptz NOT NULL,
    hits integer NOT NULL CHECK (hits >= 1),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (client, period_seconds)
);

CREATE INDEX login_ip_limit_expires_at_idx ON public.login_ip_limit (expires_at);


-- Timestamps of a key that are still inside the window, oldest first, or NULL.
CREATE FUNCTION public.login_throttle_recent(p_stamps timestamptz[], p_cutoff timestamptz)
RETURNS timestamptz[]
LANGUAGE sql IMMUTABLE AS $$
    SELECT array_agg(s ORDER BY s) FROM unnest(p_stamps) AS s WHERE s > p_cutoff
$$;


-- Recent timestamps plus p_at, keeping the newest p_max, oldest first.
CREATE FUNCTION public.login_throttle_push(
    p_stamps timestamptz[], p_cutoff timestamptz, p_at timestamptz, p_max integer
)
RETURNS timestamptz[]
LANGUAGE sql IMMUTABLE AS $$
    SELECT array_agg(s ORDER BY s)
    FROM (
        SELECT s
        FROM (
            SELECT s FROM unnest(p_stamps) AS s WHERE s > p_cutoff
            UNION ALL
            SELECT p_at
        ) AS candidates
        ORDER BY s DESC
        LIMIT greatest(p_max, 1)
    ) AS kept
$$;


-- Whole seconds until p_stamp leaves the window, at least 1. Rounded to the
-- millisecond first, so the microsecond nudges that keep reservation
-- timestamps unique never add a second.
CREATE FUNCTION public.login_throttle_wait(p_stamp timestamptz, p_window interval, p_now timestamptz)
RETURNS integer
LANGUAGE sql IMMUTABLE AS $$
    SELECT greatest(
        1,
        ceil(round(extract(epoch FROM (p_stamp + p_window - p_now))::numeric, 3))
    )::integer
$$;


-- Check every limit and, if none blocks, reserve the attempt, in one call.
-- Port of LoginThrottle._retry_after/acquire (security/login_throttle.py).
-- Transaction-scoped advisory locks on (username, /48 or client) and then the
-- client make check-and-reserve atomic across instances, without making
-- different clients of one username wait for each other; run it in
-- autocommit, so the
-- locks end with the statement. Returns the reservation timestamp (unique
-- per key: later than the key's newest stamp) or the seconds to wait.
CREATE FUNCTION public.login_throttle_acquire(
    p_username text,
    p_client text,
    p_site text,
    p_window interval,
    p_max_failures integer,
    p_max_per_username integer,
    p_max_per_prefix integer,
    p_max_usernames_per_client integer,
    p_under_pressure boolean,
    p_full_kinds smallint[],
    p_lock_timeout_ms integer,
    p_now timestamptz DEFAULT NULL,
    OUT reserved_at timestamptz,
    OUT retry_after integer,
    OUT blocked_by_capacity boolean,
    OUT blocked_by_client_cap boolean
)
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    v_now timestamptz;
    v_cutoff timestamptz;
    v_client timestamptz[];
    v_user timestamptz[];
    v_site timestamptz[];
    v_waits integer[] := '{}';
    v_n integer;
    v_count integer;
    v_oldest timestamptz;
    v_at timestamptz;
BEGIN
    blocked_by_capacity := false;
    blocked_by_client_cap := false;

    PERFORM set_config('lock_timeout', p_lock_timeout_ms::text || 'ms', true);
    -- Lock 1 covers everything this call reads and writes for the name:
    -- the (username, client) key and the (username, /48) key. For IPv6 it is
    -- taken per /48 (every /64 in it shares the /48 count); otherwise per
    -- client. Not per username alone: a flood on one name from many
    -- addresses would then queue the real user's login behind it until
    -- lock_timeout. Lock 2 (the client) keeps the per-client distinct-
    -- username count exact. Always lock 1 first, then lock 2; the two-int
    -- form keeps the namespaces apart, so this cannot deadlock.
    PERFORM pg_advisory_xact_lock(1, hashtext(p_username || '|' || coalesce(p_site, p_client)));
    PERFORM pg_advisory_xact_lock(2, hashtext(p_client));
    -- Read the clock only once the locks are held.
    v_now := coalesce(p_now, clock_timestamp());
    v_cutoff := v_now - p_window;

    SELECT public.login_throttle_recent(k.stamps, v_cutoff) INTO v_client
    FROM public.login_throttle_key AS k
    WHERE k.kind = 1 AND k.username = p_username AND k.scope = p_client;

    IF v_client IS NULL THEN
        -- A (username, client) pair with no recent failures.
        IF p_under_pressure THEN
            SELECT count(*) INTO v_count
            FROM public.login_throttle_key AS k
            WHERE k.kind = 1 AND k.scope = p_client AND k.last_at > v_cutoff;
            IF v_count >= p_max_usernames_per_client THEN
                -- Which of the client's keys expires first is not used, as in
                -- the memory backend: the window is an upper bound.
                v_waits := v_waits || greatest(1, ceil(extract(epoch FROM p_window)))::integer;
                blocked_by_client_cap := true;
            END IF;
        END IF;
        IF 1 = ANY (p_full_kinds) THEN
            SELECT min(k.last_at) INTO v_oldest
            FROM public.login_throttle_key AS k
            WHERE k.kind = 1 AND k.last_at > v_cutoff;
            IF v_oldest IS NOT NULL THEN
                v_waits := v_waits || public.login_throttle_wait(v_oldest, p_window, v_now);
                blocked_by_capacity := true;
            END IF;
        END IF;
        IF 2 = ANY (p_full_kinds) AND NOT EXISTS (
            SELECT 1 FROM public.login_throttle_key AS k
            WHERE k.kind = 2 AND k.username = p_username AND k.scope = '' AND k.last_at > v_cutoff
        ) THEN
            SELECT min(k.last_at) INTO v_oldest
            FROM public.login_throttle_key AS k
            WHERE k.kind = 2 AND k.last_at > v_cutoff;
            IF v_oldest IS NOT NULL THEN
                v_waits := v_waits || public.login_throttle_wait(v_oldest, p_window, v_now);
                blocked_by_capacity := true;
            END IF;
        END IF;
    ELSE
        -- A pair with recent failures: its own limit and the username cap.
        v_n := cardinality(v_client);
        IF v_n >= p_max_failures THEN
            v_waits := v_waits || public.login_throttle_wait(
                v_client[v_n - p_max_failures + 1], p_window, v_now
            );
        END IF;
        SELECT public.login_throttle_recent(k.stamps, v_cutoff) INTO v_user
        FROM public.login_throttle_key AS k
        WHERE k.kind = 2 AND k.username = p_username AND k.scope = '';
        IF v_user IS NOT NULL AND cardinality(v_user) >= p_max_per_username THEN
            -- Lifted when this client's failures expire or the username drops
            -- below its cap, whichever comes first.
            v_waits := v_waits || least(
                public.login_throttle_wait(v_client[v_n], p_window, v_now),
                public.login_throttle_wait(
                    v_user[cardinality(v_user) - p_max_per_username + 1], p_window, v_now
                )
            );
        END IF;
    END IF;

    IF p_site IS NOT NULL THEN
        SELECT public.login_throttle_recent(k.stamps, v_cutoff) INTO v_site
        FROM public.login_throttle_key AS k
        WHERE k.kind = 3 AND k.username = p_username AND k.scope = p_site;
        IF v_site IS NULL AND 3 = ANY (p_full_kinds) THEN
            SELECT min(k.last_at) INTO v_oldest
            FROM public.login_throttle_key AS k
            WHERE k.kind = 3 AND k.last_at > v_cutoff;
            IF v_oldest IS NOT NULL THEN
                v_waits := v_waits || public.login_throttle_wait(v_oldest, p_window, v_now);
                blocked_by_capacity := true;
            END IF;
        END IF;
        IF v_site IS NOT NULL AND cardinality(v_site) >= p_max_per_prefix THEN
            v_waits := v_waits || public.login_throttle_wait(
                v_site[cardinality(v_site) - p_max_per_prefix + 1], p_window, v_now
            );
        END IF;
    END IF;

    IF cardinality(v_waits) = 0 THEN
        -- Strictly later than both keys' newest stamp: the timestamp is the
        -- reservation's id, so withdrawing it never removes another attempt.
        v_at := greatest(
            v_now,
            v_client[cardinality(v_client)] + interval '1 microsecond',
            v_site[cardinality(v_site)] + interval '1 microsecond'
        );
        -- Relative updates, so a concurrent finish is never lost.
        INSERT INTO public.login_throttle_key AS k (kind, username, scope, stamps, last_at)
        VALUES (1, p_username, p_client, ARRAY[v_at], v_at)
        ON CONFLICT (kind, username, scope) DO UPDATE
        SET stamps = public.login_throttle_push(k.stamps, v_cutoff, v_at, p_max_failures),
            last_at = v_at;
        IF p_site IS NOT NULL THEN
            INSERT INTO public.login_throttle_key AS k (kind, username, scope, stamps, last_at)
            VALUES (3, p_username, p_site, ARRAY[v_at], v_at)
            ON CONFLICT (kind, username, scope) DO UPDATE
            SET stamps = public.login_throttle_push(k.stamps, v_cutoff, v_at, p_max_per_prefix),
                last_at = v_at;
        END IF;
        reserved_at := v_at;
    ELSE
        retry_after := (SELECT max(w) FROM unnest(v_waits) AS w);
    END IF;

    -- Sweep a bounded batch of expired keys (any kind).
    DELETE FROM public.login_throttle_key
    WHERE ctid = ANY (ARRAY(
        SELECT k.ctid FROM public.login_throttle_key AS k
        WHERE k.last_at <= v_cutoff
        ORDER BY k.last_at
        LIMIT 16
        FOR UPDATE SKIP LOCKED
    ));
END
$$;


-- Record the outcome of a reserved attempt: 1 failed, 2 succeeded,
-- 3 released (server error).
-- * 2 and 3 change the (username, client) and (username, /48) keys, so they
--   take the same locks as login_throttle_acquire, in the same order: a
--   withdrawal can never interleave with a concurrent reservation.
-- * 1 only appends to the username key and takes no lock; a concurrent
--   acquire may miss that failure (the per-username cap is approximate under
--   a burst, as in the memory backend).
CREATE FUNCTION public.login_throttle_finish(
    p_outcome smallint,
    p_username text,
    p_client text,
    p_site text,
    p_at timestamptz,
    p_window interval,
    p_max_per_username integer,
    p_full_kinds smallint[],
    p_lock_timeout_ms integer,
    p_now timestamptz DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    v_now timestamptz := coalesce(p_now, clock_timestamp());
    v_cutoff timestamptz := v_now - p_window;
BEGIN
    IF p_outcome NOT IN (1, 2, 3) THEN
        RAISE EXCEPTION 'login_throttle_finish: unknown outcome %', p_outcome;
    END IF;

    IF p_outcome = 1 THEN
        IF 2 = ANY (p_full_kinds) THEN
            -- Full: only an existing, active username key may grow.
            UPDATE public.login_throttle_key AS k
            SET stamps = public.login_throttle_push(k.stamps, v_cutoff, v_now, p_max_per_username),
                last_at = greatest(k.last_at, v_now)
            WHERE k.kind = 2 AND k.username = p_username AND k.scope = '' AND k.last_at > v_cutoff;
        ELSE
            INSERT INTO public.login_throttle_key AS k (kind, username, scope, stamps, last_at)
            VALUES (2, p_username, '', ARRAY[v_now], v_now)
            ON CONFLICT (kind, username, scope) DO UPDATE
            SET stamps = public.login_throttle_push(k.stamps, v_cutoff, v_now, p_max_per_username),
                last_at = greatest(
                    v_now,
                    (SELECT max(s) FROM unnest(k.stamps) AS s WHERE s > v_cutoff)
                );
        END IF;
        RETURN;
    END IF;

    PERFORM set_config('lock_timeout', p_lock_timeout_ms::text || 'ms', true);
    PERFORM pg_advisory_xact_lock(1, hashtext(p_username || '|' || coalesce(p_site, p_client)));
    PERFORM pg_advisory_xact_lock(2, hashtext(p_client));
    IF p_outcome = 2 THEN
        -- Proving the password resets this client's failures for the name.
        DELETE FROM public.login_throttle_key AS k
        WHERE k.kind = 1 AND k.username = p_username AND k.scope = p_client;
    ELSE
        PERFORM public.login_throttle_withdraw(1::smallint, p_username, p_client, p_at);
    END IF;
    IF p_site IS NOT NULL THEN
        -- Only this attempt's own /48 stamp; other failures there stay.
        PERFORM public.login_throttle_withdraw(3::smallint, p_username, p_site, p_at);
    END IF;
END
$$;


-- Remove exactly the stamp p_at from one key; delete the key if it was the
-- only one. The UPDATE goes first and only touches keys with other stamps,
-- so it never leaves an empty array. Callers hold the key's advisory lock
-- (see login_throttle_finish), so no reservation can be added between the
-- two statements.
CREATE FUNCTION public.login_throttle_withdraw(
    p_kind smallint, p_username text, p_scope text, p_at timestamptz
)
RETURNS void
LANGUAGE plpgsql VOLATILE AS $$
BEGIN
    UPDATE public.login_throttle_key AS k
    SET stamps = array_remove(k.stamps, p_at),
        last_at = (SELECT max(s) FROM unnest(array_remove(k.stamps, p_at)) AS s)
    WHERE k.kind = p_kind AND k.username = p_username AND k.scope = p_scope
      AND p_at = ANY (k.stamps) AND cardinality(k.stamps) > 1;
    DELETE FROM public.login_throttle_key AS k
    WHERE k.kind = p_kind AND k.username = p_username AND k.scope = p_scope
      AND k.stamps = ARRAY[p_at];
END
$$;


-- Active keys per kind, each count capped at p_limit (index on kind, last_at).
CREATE FUNCTION public.login_throttle_load(p_cutoff timestamptz, p_limit integer)
RETURNS TABLE (key_kind smallint, active_keys integer)
LANGUAGE sql STABLE AS $$
    SELECT kinds.kind::smallint,
           (SELECT count(*)::integer
            FROM (SELECT 1 FROM public.login_throttle_key AS t
                  WHERE t.kind = kinds.kind AND t.last_at > p_cutoff
                  LIMIT p_limit) AS active)
    FROM unnest(ARRAY[1, 2, 3]) AS kinds(kind)
$$;


-- Shared fixed-window per-IP limit for the login routes. Checks every limit
-- first and counts the hit in all of them only if none is exceeded, like the
-- in-memory limiter. Returns NULL when allowed, else the seconds to wait.
CREATE FUNCTION public.login_ip_limit_hit(
    p_client text,
    p_amounts integer[],
    p_periods integer[],
    p_lock_timeout_ms integer,
    p_now timestamptz DEFAULT NULL,
    OUT retry_after integer
)
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    v_now timestamptz;
    v_hits integer;
    v_expires timestamptz;
    i integer;
BEGIN
    IF cardinality(p_amounts) IS DISTINCT FROM cardinality(p_periods) THEN
        RAISE EXCEPTION 'login_ip_limit_hit: amounts and periods differ in length';
    END IF;
    PERFORM set_config('lock_timeout', p_lock_timeout_ms::text || 'ms', true);
    PERFORM pg_advisory_xact_lock(4, hashtext(p_client));
    v_now := coalesce(p_now, clock_timestamp());

    FOR i IN 1 .. coalesce(cardinality(p_amounts), 0) LOOP
        SELECT l.hits, l.expires_at INTO v_hits, v_expires
        FROM public.login_ip_limit AS l
        WHERE l.client = p_client AND l.period_seconds = p_periods[i] AND l.expires_at > v_now;
        IF FOUND AND v_hits >= p_amounts[i] THEN
            retry_after := greatest(
                coalesce(retry_after, 1),
                public.login_throttle_wait(v_expires, interval '0', v_now)
            );
        END IF;
    END LOOP;

    IF retry_after IS NULL THEN
        FOR i IN 1 .. coalesce(cardinality(p_amounts), 0) LOOP
            INSERT INTO public.login_ip_limit AS l (client, period_seconds, window_start, hits, expires_at)
            VALUES (p_client, p_periods[i], v_now, 1, v_now + make_interval(secs => p_periods[i]))
            ON CONFLICT (client, period_seconds) DO UPDATE
            SET hits = CASE WHEN l.expires_at > v_now THEN l.hits + 1 ELSE 1 END,
                window_start = CASE WHEN l.expires_at > v_now THEN l.window_start ELSE v_now END,
                expires_at = CASE WHEN l.expires_at > v_now THEN l.expires_at
                                  ELSE v_now + make_interval(secs => p_periods[i]) END;
        END LOOP;
    END IF;

    DELETE FROM public.login_ip_limit
    WHERE ctid = ANY (ARRAY(
        SELECT l.ctid FROM public.login_ip_limit AS l
        WHERE l.expires_at <= v_now
        ORDER BY l.expires_at
        LIMIT 16
        FOR UPDATE SKIP LOCKED
    ));
END
$$;


-- migrate:down

DROP FUNCTION IF EXISTS public.login_ip_limit_hit(text, integer[], integer[], integer, timestamptz);
DROP FUNCTION IF EXISTS public.login_throttle_load(timestamptz, integer);
DROP FUNCTION IF EXISTS public.login_throttle_finish(smallint, text, text, text, timestamptz, interval, integer, smallint[], integer, timestamptz);
DROP FUNCTION IF EXISTS public.login_throttle_withdraw(smallint, text, text, timestamptz);
DROP FUNCTION IF EXISTS public.login_throttle_acquire(text, text, text, interval, integer, integer, integer, integer, boolean, smallint[], integer, timestamptz);
DROP FUNCTION IF EXISTS public.login_throttle_wait(timestamptz, interval, timestamptz);
DROP FUNCTION IF EXISTS public.login_throttle_push(timestamptz[], timestamptz, timestamptz, integer);
DROP FUNCTION IF EXISTS public.login_throttle_recent(timestamptz[], timestamptz);
DROP TABLE IF EXISTS public.login_ip_limit;
DROP TABLE IF EXISTS public.login_throttle_key;

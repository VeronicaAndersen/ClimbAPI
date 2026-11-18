\restrict dbmate

-- Dumped from database version 17.11 (Homebrew)
-- Dumped by pg_dump version 17.11 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: comp_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.comp_type AS ENUM (
    'QUALIFIER',
    'FINAL'
);


--
-- Name: user_scope_t; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.user_scope_t AS ENUM (
    'climber',
    'admin'
);


--
-- Name: login_ip_limit_hit(text, integer[], integer[], integer, integer, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.login_ip_limit_hit(p_client text, p_amounts integer[], p_periods integer[], p_lock_timeout_ms integer, p_sweep_batch integer, p_now timestamp with time zone DEFAULT NULL::timestamp with time zone, OUT retry_after integer) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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
        LIMIT greatest(p_sweep_batch, 0)
        FOR UPDATE SKIP LOCKED
    ));
END
$$;


--
-- Name: login_throttle_acquire(text, text, text, interval, integer, integer, integer, integer, boolean, smallint[], integer, integer, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.login_throttle_acquire(p_username text, p_client text, p_site text, p_window interval, p_max_failures integer, p_max_per_username integer, p_max_per_prefix integer, p_max_usernames_per_client integer, p_under_pressure boolean, p_full_kinds smallint[], p_lock_timeout_ms integer, p_sweep_batch integer, p_now timestamp with time zone DEFAULT NULL::timestamp with time zone, OUT reserved_at timestamp with time zone, OUT retry_after integer, OUT blocked_by_capacity boolean, OUT blocked_by_client_cap boolean) RETURNS record
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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

    -- Last, after every write: see login_throttle_sweep.
    PERFORM public.login_throttle_sweep(v_cutoff, p_sweep_batch);
END
$$;


--
-- Name: login_throttle_finish(smallint, text, text, text, timestamp with time zone, interval, integer, smallint[], integer, integer, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.login_throttle_finish(p_outcome smallint, p_username text, p_client text, p_site text, p_at timestamp with time zone, p_window interval, p_max_per_username integer, p_full_kinds smallint[], p_lock_timeout_ms integer, p_sweep_batch integer, p_now timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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
    ELSE
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
    END IF;

    -- Every outcome sweeps, so rows are cleaned on every login that reaches
    -- the database. Last, after every write and lock wait: a sweep's row
    -- locks taken before the advisory locks could deadlock with an acquire
    -- that upserts onto an expired row.
    PERFORM public.login_throttle_sweep(v_cutoff, p_sweep_batch);
END
$$;


--
-- Name: login_throttle_load(timestamp with time zone, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.login_throttle_load(p_cutoff timestamp with time zone, p_limit integer) RETURNS TABLE(key_kind smallint, active_keys integer)
    LANGUAGE sql STABLE
    AS $$
    SELECT kinds.kind::smallint,
           (SELECT count(*)::integer
            FROM (SELECT 1 FROM public.login_throttle_key AS t
                  WHERE t.kind = kinds.kind AND t.last_at > p_cutoff
                  LIMIT p_limit) AS active)
    FROM unnest(ARRAY[1, 2, 3]) AS kinds(kind)
$$;


--
-- Name: login_throttle_push(timestamp with time zone[], timestamp with time zone, timestamp with time zone, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.login_throttle_push(p_stamps timestamp with time zone[], p_cutoff timestamp with time zone, p_at timestamp with time zone, p_max integer) RETURNS timestamp with time zone[]
    LANGUAGE sql IMMUTABLE
    AS $$
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


--
-- Name: login_throttle_recent(timestamp with time zone[], timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.login_throttle_recent(p_stamps timestamp with time zone[], p_cutoff timestamp with time zone) RETURNS timestamp with time zone[]
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT array_agg(s ORDER BY s) FROM unnest(p_stamps) AS s WHERE s > p_cutoff
$$;


--
-- Name: login_throttle_sweep(timestamp with time zone, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.login_throttle_sweep(p_cutoff timestamp with time zone, p_batch integer) RETURNS integer
    LANGUAGE sql
    AS $$
    WITH swept AS (
        DELETE FROM public.login_throttle_key
        WHERE ctid = ANY (ARRAY(
            SELECT k.ctid FROM public.login_throttle_key AS k
            WHERE k.last_at <= p_cutoff
            ORDER BY k.last_at
            LIMIT greatest(p_batch, 0)
            FOR UPDATE SKIP LOCKED
        ))
        RETURNING 1
    )
    SELECT count(*)::integer FROM swept
$$;


--
-- Name: login_throttle_wait(timestamp with time zone, interval, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.login_throttle_wait(p_stamp timestamp with time zone, p_window interval, p_now timestamp with time zone) RETURNS integer
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT greatest(1, ceil(extract(epoch FROM (p_stamp + p_window - p_now))))::integer
$$;


--
-- Name: login_throttle_withdraw(smallint, text, text, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.login_throttle_withdraw(p_kind smallint, p_username text, p_scope text, p_at timestamp with time zone) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: climber; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.climber (
    id bigint NOT NULL,
    username text NOT NULL,
    password text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_scope public.user_scope_t DEFAULT 'climber'::public.user_scope_t NOT NULL,
    email text,
    firstname text,
    lastname text,
    club text
);


--
-- Name: climber_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.climber_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: climber_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.climber_id_seq OWNED BY public.climber.id;


--
-- Name: competition; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competition (
    id bigint NOT NULL,
    name text NOT NULL,
    description text,
    comp_type public.comp_type NOT NULL,
    comp_date date NOT NULL,
    season_id bigint NOT NULL,
    round_no integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT qualifier_round_ck CHECK ((((comp_type = 'QUALIFIER'::public.comp_type) AND (round_no >= 1) AND (round_no <= 4)) OR ((comp_type = 'FINAL'::public.comp_type) AND (round_no IS NULL))))
);


--
-- Name: competition_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.competition_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: competition_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.competition_id_seq OWNED BY public.competition.id;


--
-- Name: login_ip_limit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_ip_limit (
    client text NOT NULL,
    period_seconds integer NOT NULL,
    window_start timestamp with time zone NOT NULL,
    hits integer NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT login_ip_limit_client_check CHECK ((char_length(client) <= 64)),
    CONSTRAINT login_ip_limit_hits_check CHECK ((hits >= 1)),
    CONSTRAINT login_ip_limit_period_seconds_check CHECK ((period_seconds > 0))
);


--
-- Name: login_throttle_key; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_throttle_key (
    kind smallint NOT NULL,
    username text NOT NULL,
    scope text DEFAULT ''::text NOT NULL,
    stamps timestamp with time zone[] NOT NULL,
    last_at timestamp with time zone NOT NULL,
    CONSTRAINT login_throttle_key_kind_check CHECK ((kind = ANY (ARRAY[1, 2, 3]))),
    CONSTRAINT login_throttle_key_scope_check CHECK ((char_length(scope) <= 64)),
    CONSTRAINT login_throttle_key_scope_matches_kind CHECK (((kind = 2) = (scope = ''::text))),
    CONSTRAINT login_throttle_key_stamps_check CHECK ((cardinality(stamps) >= 1)),
    CONSTRAINT login_throttle_key_username_check CHECK ((char_length(username) <= 200))
);


--
-- Name: password_reset_token; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_reset_token (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    token text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: password_reset_token_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.password_reset_token_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: password_reset_token_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.password_reset_token_id_seq OWNED BY public.password_reset_token.id;


--
-- Name: problem; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.problem (
    id bigint NOT NULL,
    competition_id bigint NOT NULL,
    level_no integer NOT NULL,
    problem_no integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT problem_level_no_check CHECK (((level_no >= 1) AND (level_no <= 10))),
    CONSTRAINT problem_problem_no_check CHECK (((problem_no >= 1) AND (problem_no <= 8)))
);


--
-- Name: problem_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.problem_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: problem_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.problem_id_seq OWNED BY public.problem.id;


--
-- Name: problem_score; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.problem_score (
    competition_id bigint NOT NULL,
    problem_id bigint NOT NULL,
    user_id bigint NOT NULL,
    attempts_total integer NOT NULL,
    got_bonus boolean NOT NULL,
    got_top boolean NOT NULL,
    attempts_to_bonus integer,
    attempts_to_top integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    ifsc_score double precision DEFAULT 0 NOT NULL,
    CONSTRAINT problem_score_attempts_total_check CHECK ((attempts_total >= 0))
);


--
-- Name: registration; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.registration (
    comp_id bigint NOT NULL,
    user_id bigint NOT NULL,
    level integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    approved boolean DEFAULT false NOT NULL,
    CONSTRAINT level_range CHECK (((level >= 1) AND (level <= 10)))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: season; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.season (
    id bigint NOT NULL,
    name text NOT NULL,
    year integer NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: season_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.season_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: season_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.season_id_seq OWNED BY public.season.id;


--
-- Name: climber id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.climber ALTER COLUMN id SET DEFAULT nextval('public.climber_id_seq'::regclass);


--
-- Name: competition id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competition ALTER COLUMN id SET DEFAULT nextval('public.competition_id_seq'::regclass);


--
-- Name: password_reset_token id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_token ALTER COLUMN id SET DEFAULT nextval('public.password_reset_token_id_seq'::regclass);


--
-- Name: problem id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.problem ALTER COLUMN id SET DEFAULT nextval('public.problem_id_seq'::regclass);


--
-- Name: season id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.season ALTER COLUMN id SET DEFAULT nextval('public.season_id_seq'::regclass);


--
-- Name: climber climber_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.climber
    ADD CONSTRAINT climber_pkey PRIMARY KEY (id);


--
-- Name: climber climber_username_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.climber
    ADD CONSTRAINT climber_username_unique UNIQUE (username);


--
-- Name: competition competition_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competition
    ADD CONSTRAINT competition_pkey PRIMARY KEY (id);


--
-- Name: login_ip_limit login_ip_limit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_ip_limit
    ADD CONSTRAINT login_ip_limit_pkey PRIMARY KEY (client, period_seconds);


--
-- Name: login_throttle_key login_throttle_key_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_throttle_key
    ADD CONSTRAINT login_throttle_key_pkey PRIMARY KEY (kind, username, scope);


--
-- Name: password_reset_token password_reset_token_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_token
    ADD CONSTRAINT password_reset_token_pkey PRIMARY KEY (id);


--
-- Name: problem problem_competition_id_level_no_problem_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.problem
    ADD CONSTRAINT problem_competition_id_level_no_problem_no_key UNIQUE (competition_id, level_no, problem_no);


--
-- Name: problem problem_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.problem
    ADD CONSTRAINT problem_pkey PRIMARY KEY (id);


--
-- Name: problem_score problem_score_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.problem_score
    ADD CONSTRAINT problem_score_pkey PRIMARY KEY (problem_id, user_id);


--
-- Name: registration registration_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_pkey PRIMARY KEY (comp_id, user_id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: season season_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.season
    ADD CONSTRAINT season_pkey PRIMARY KEY (id);


--
-- Name: login_ip_limit_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX login_ip_limit_expires_at_idx ON public.login_ip_limit USING btree (expires_at);


--
-- Name: login_throttle_key_client_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX login_throttle_key_client_idx ON public.login_throttle_key USING btree (scope, last_at) WHERE (kind = 1);


--
-- Name: login_throttle_key_kind_last_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX login_throttle_key_kind_last_at_idx ON public.login_throttle_key USING btree (kind, last_at);


--
-- Name: login_throttle_key_last_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX login_throttle_key_last_at_idx ON public.login_throttle_key USING btree (last_at);


--
-- Name: password_reset_token_token_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX password_reset_token_token_idx ON public.password_reset_token USING btree (token);


--
-- Name: password_reset_token_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX password_reset_token_user_id_idx ON public.password_reset_token USING btree (user_id);


--
-- Name: problem_comp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX problem_comp_idx ON public.problem USING btree (competition_id);


--
-- Name: problem_comp_level_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX problem_comp_level_idx ON public.problem USING btree (competition_id, level_no);


--
-- Name: reg_comp_level_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX reg_comp_level_idx ON public.registration USING btree (comp_id, level);


--
-- Name: reg_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX reg_user_idx ON public.registration USING btree (user_id);


--
-- Name: score_comp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX score_comp_idx ON public.problem_score USING btree (competition_id);


--
-- Name: score_user_comp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX score_user_comp_idx ON public.problem_score USING btree (user_id, competition_id);


--
-- Name: competition competition_season_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competition
    ADD CONSTRAINT competition_season_id_fkey FOREIGN KEY (season_id) REFERENCES public.season(id) ON DELETE CASCADE;


--
-- Name: password_reset_token password_reset_token_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_token
    ADD CONSTRAINT password_reset_token_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.climber(id) ON DELETE CASCADE;


--
-- Name: problem problem_competition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.problem
    ADD CONSTRAINT problem_competition_id_fkey FOREIGN KEY (competition_id) REFERENCES public.competition(id) ON DELETE CASCADE;


--
-- Name: problem_score problem_score_competition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.problem_score
    ADD CONSTRAINT problem_score_competition_id_fkey FOREIGN KEY (competition_id) REFERENCES public.competition(id) ON DELETE CASCADE;


--
-- Name: problem_score problem_score_problem_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.problem_score
    ADD CONSTRAINT problem_score_problem_id_fkey FOREIGN KEY (problem_id) REFERENCES public.problem(id) ON DELETE CASCADE;


--
-- Name: problem_score problem_score_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.problem_score
    ADD CONSTRAINT problem_score_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.climber(id) ON DELETE CASCADE;


--
-- Name: registration registration_comp_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_comp_id_fkey FOREIGN KEY (comp_id) REFERENCES public.competition(id) ON DELETE CASCADE;


--
-- Name: registration registration_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.climber(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict dbmate


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20251020064831'),
    ('20251023174145'),
    ('20251124201937'),
    ('20260112'),
    ('20260125143541'),
    ('20260125144000'),
    ('20260329000000'),
    ('20260403000000'),
    ('20260403000001'),
    ('20261118000000');

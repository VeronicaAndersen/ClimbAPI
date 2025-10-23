\restrict wCyH0lSx46CnKYYfBvxedN17xrqYEYoljFmgSWRD8UKlM9saf27T1I1sJkxDTgS

-- Dumped from database version 15.14 (Postgres.app)
-- Dumped by pg_dump version 17.6 (Homebrew)

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


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: climber; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.climber (
    id bigint NOT NULL,
    name text NOT NULL,
    password text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_scope public.user_scope_t DEFAULT 'climber'::public.user_scope_t NOT NULL
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
    CONSTRAINT qualifier_round_ck CHECK ((((comp_type = 'QUALIFIER'::public.comp_type) AND ((round_no >= 1) AND (round_no <= 3))) OR ((comp_type = 'FINAL'::public.comp_type) AND (round_no IS NULL))))
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
    CONSTRAINT level_range CHECK (((level >= 1) AND (level <= 10)))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying(128) NOT NULL
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
-- Name: competition competition_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competition
    ADD CONSTRAINT competition_pkey PRIMARY KEY (id);


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

\unrestrict wCyH0lSx46CnKYYfBvxedN17xrqYEYoljFmgSWRD8UKlM9saf27T1I1sJkxDTgS


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20251020064831'),
    ('20251023174145');

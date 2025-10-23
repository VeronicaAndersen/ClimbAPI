-- migrate:up

CREATE TYPE public.comp_type AS ENUM (
    'QUALIFIER',
    'FINAL'
);

CREATE TABLE public.climber (
    id bigint NOT NULL,
    name text NOT NULL,
    password text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    is_admin boolean DEFAULT false
);


CREATE SEQUENCE public.climber_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.climber_id_seq OWNED BY public.climber.id;


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

CREATE SEQUENCE public.competition_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.competition_id_seq OWNED BY public.competition.id;


CREATE TABLE public.problem (
    id bigint NOT NULL,
    competition_id bigint NOT NULL,
    level_no integer NOT NULL,
    problem_no integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT problem_level_no_check CHECK (((level_no >= 1) AND (level_no <= 10))),
    CONSTRAINT problem_problem_no_check CHECK (((problem_no >= 1) AND (problem_no <= 8)))
);

CREATE SEQUENCE public.problem_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.problem_id_seq OWNED BY public.problem.id;

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

CREATE TABLE public.registration (
    comp_id bigint NOT NULL,
    user_id bigint NOT NULL,
    level integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT level_range CHECK (((level >= 1) AND (level <= 10)))
);


CREATE TABLE public.season (
    id bigint NOT NULL,
    name text NOT NULL,
    year integer NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


CREATE SEQUENCE public.season_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.season_id_seq OWNED BY public.season.id;

ALTER TABLE ONLY public.climber ALTER COLUMN id SET DEFAULT nextval('public.climber_id_seq'::regclass);

ALTER TABLE ONLY public.competition ALTER COLUMN id SET DEFAULT nextval('public.competition_id_seq'::regclass);

ALTER TABLE ONLY public.problem ALTER COLUMN id SET DEFAULT nextval('public.problem_id_seq'::regclass);

ALTER TABLE ONLY public.season ALTER COLUMN id SET DEFAULT nextval('public.season_id_seq'::regclass);


ALTER TABLE ONLY public.climber
    ADD CONSTRAINT climber_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.competition
    ADD CONSTRAINT competition_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.problem
    ADD CONSTRAINT problem_competition_id_level_no_problem_no_key UNIQUE (competition_id, level_no, problem_no);

ALTER TABLE ONLY public.problem
    ADD CONSTRAINT problem_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.problem_score
    ADD CONSTRAINT problem_score_pkey PRIMARY KEY (problem_id, user_id);

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_pkey PRIMARY KEY (comp_id, user_id);

ALTER TABLE ONLY public.season
    ADD CONSTRAINT season_pkey PRIMARY KEY (id);

CREATE INDEX problem_comp_idx ON public.problem USING btree (competition_id);

CREATE INDEX problem_comp_level_idx ON public.problem USING btree (competition_id, level_no);

CREATE INDEX reg_comp_level_idx ON public.registration USING btree (comp_id, level);

CREATE INDEX reg_user_idx ON public.registration USING btree (user_id);

CREATE INDEX score_comp_idx ON public.problem_score USING btree (competition_id);

CREATE INDEX score_user_comp_idx ON public.problem_score USING btree (user_id, competition_id);

ALTER TABLE ONLY public.competition
    ADD CONSTRAINT competition_season_id_fkey FOREIGN KEY (season_id) REFERENCES public.season(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.problem
    ADD CONSTRAINT problem_competition_id_fkey FOREIGN KEY (competition_id) REFERENCES public.competition(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.problem_score
    ADD CONSTRAINT problem_score_competition_id_fkey FOREIGN KEY (competition_id) REFERENCES public.competition(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.problem_score
    ADD CONSTRAINT problem_score_problem_id_fkey FOREIGN KEY (problem_id) REFERENCES public.problem(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.problem_score
    ADD CONSTRAINT problem_score_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.climber(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_comp_id_fkey FOREIGN KEY (comp_id) REFERENCES public.competition(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.climber(id) ON DELETE CASCADE;

-- migrate:down


-- migrate:up
CREATE TABLE public.password_reset_token (
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES public.climber(id) ON DELETE CASCADE,
    token text NOT NULL,
    expires_at timestamptz NOT NULL,
    used boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX password_reset_token_token_idx ON public.password_reset_token (token);
CREATE INDEX password_reset_token_user_id_idx ON public.password_reset_token (user_id);

-- migrate:down
DROP TABLE IF EXISTS public.password_reset_token;

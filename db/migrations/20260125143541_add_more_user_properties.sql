-- migrate:up
ALTER TABLE public.climber
ADD COLUMN email text,
ADD COLUMN firstname text,
ADD COLUMN lastname text,
ADD COLUMN club text;

ALTER TABLE public.climber
ADD CONSTRAINT climber_email_unique UNIQUE (email);

-- migrate:down
ALTER TABLE public.climber
DROP CONSTRAINT IF EXISTS climber_email_unique;

ALTER TABLE public.climber
DROP COLUMN IF EXISTS email,
DROP COLUMN IF EXISTS firstname,
DROP COLUMN IF EXISTS lastname,
DROP COLUMN IF EXISTS club;

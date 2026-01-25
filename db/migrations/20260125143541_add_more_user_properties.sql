-- migrate:up
ALTER TABLE public.climber
ADD COLUMN email text,
ADD COLUMN firstname text,
ADD COLUMN lastname text,
ADD COLUMN club text;

-- migrate:down
ALTER TABLE public.climber
DROP COLUMN IF EXISTS email,
DROP COLUMN IF EXISTS firstname,
DROP COLUMN IF EXISTS lastname,
DROP COLUMN IF EXISTS club;

-- migrate:up
ALTER TABLE public.climber
DROP CONSTRAINT IF EXISTS climber_email_unique;

-- migrate:down
ALTER TABLE public.climber
ADD CONSTRAINT climber_email_unique UNIQUE (email);

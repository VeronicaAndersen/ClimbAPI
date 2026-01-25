-- migrate:up
ALTER TABLE public.climber
RENAME COLUMN name TO username;

ALTER TABLE public.climber
ADD CONSTRAINT climber_username_unique UNIQUE (username);

-- migrate:down
ALTER TABLE public.climber
DROP CONSTRAINT IF EXISTS climber_username_unique;

ALTER TABLE public.climber
RENAME COLUMN username TO name;

-- migrate:up
ALTER TABLE public.climber
RENAME COLUMN name TO username;

-- migrate:down
ALTER TABLE public.climber
RENAME COLUMN username TO name;

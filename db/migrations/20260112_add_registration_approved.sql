-- migrate:up
ALTER TABLE registration ADD COLUMN approved BOOLEAN NOT NULL DEFAULT false;

-- migrate:down
ALTER TABLE registration DROP COLUMN approved;

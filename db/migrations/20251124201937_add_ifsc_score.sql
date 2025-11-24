-- migrate:up
    ALTER TABLE problem_score ADD COLUMN ifsc_score FLOAT NOT NULL DEFAULT 0;


-- migrate:down
    ALTER TABLE problem_score DROP COLUMN ifsc_score;


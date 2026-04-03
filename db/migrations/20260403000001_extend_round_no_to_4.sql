-- migrate:up
ALTER TABLE public.competition
DROP CONSTRAINT qualifier_round_ck,
ADD CONSTRAINT qualifier_round_ck CHECK (
    (comp_type = 'QUALIFIER' AND round_no >= 1 AND round_no <= 4)
    OR
    (comp_type = 'FINAL' AND round_no IS NULL)
);

-- migrate:down
ALTER TABLE public.competition
DROP CONSTRAINT qualifier_round_ck,
ADD CONSTRAINT qualifier_round_ck CHECK (
    (comp_type = 'QUALIFIER' AND round_no >= 1 AND round_no <= 3)
    OR
    (comp_type = 'FINAL' AND round_no IS NULL)
);

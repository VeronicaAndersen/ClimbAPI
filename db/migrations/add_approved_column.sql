-- Quick fix: Add approved column to registration table
-- Run this if dbmate migration hasn't been applied yet

-- Add the approved column with default false
ALTER TABLE registration ADD COLUMN IF NOT EXISTS approved BOOLEAN NOT NULL DEFAULT false;

-- Verify the column was added
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'registration' AND column_name = 'approved';

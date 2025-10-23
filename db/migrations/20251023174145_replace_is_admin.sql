-- migrate:up
    CREATE TYPE user_scope_t AS ENUM ('climber', 'admin');
    ALTER TABLE climber ADD COLUMN user_scope user_scope_t;

    UPDATE climber
    SET user_scope = CASE
      WHEN COALESCE(is_admin, false) THEN 'admin'::user_scope_t
      ELSE 'climber'::user_scope_t
    END;

    ALTER TABLE climber
      ALTER COLUMN user_scope SET NOT NULL,
      ALTER COLUMN user_scope SET DEFAULT 'climber'::user_scope_t;

    ALTER TABLE climber DROP COLUMN is_admin;

-- migrate:down

    ALTER TABLE climber ADD COLUMN is_admin boolean NOT NULL DEFAULT false;

    UPDATE climber SET is_admin = (user_scope = 'admin'::user_scope_t);

    ALTER TABLE climber DROP COLUMN user_scope;
    DROP TYPE user_scope_t;
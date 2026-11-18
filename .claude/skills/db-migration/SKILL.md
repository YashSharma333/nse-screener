---
name: db-migration
description: Use for any change to database tables or columns (new table, new column, index, constraint). Gives the Alembic steps, a MySQL-compatibility checklist, and the test and README updates required.
---

# Database migrations (Alembic)

Migrations live in `migrations/versions/`. Production is MySQL, tests use SQLite,
CI runs a MySQL upgrade, downgrade, upgrade job. A migration must work in both.

## Steps

1. Update the SQLAlchemy model in `src/db/models.py`.
2. Find the current head: `alembic heads`. Your new revision must chain from it.
3. Create the revision: `alembic revision -m "NNN short name"` (keep the numbering
   style of existing files) and write `upgrade()` and a real `downgrade()`.
4. Add a test that upgrades and downgrades on SQLite.
5. Update the "Database Migrations" section of `README.md`.
6. In the PR "Commands" tell the owner to: back up MySQL (`mysqldump`), then run
   `alembic upgrade head`.

## MySQL compatibility checklist

- [ ] Every `VARCHAR`/`String` has an explicit length (primary keys especially).
- [ ] Booleans use `server_default=sa.text("0")` or `sa.false()`, not Python values.
- [ ] `Numeric` precision is large enough (market caps reach six figures in crore).
- [ ] Index and constraint names are 64 characters or shorter and unique.
- [ ] No SQLite-only or MySQL-only SQL without a dialect check.
- [ ] SQLite is not MySQL. Any SQL written for MySQL must also be covered by a compile test or the MySQL CI job.
- [ ] `downgrade()` removes everything `upgrade()` created, in reverse order.
- [ ] Nothing in `upgrade()` deletes or rewrites existing price rows.

## Never

- Never run a migration against the owner's real database yourself. The agent's
  MySQL user cannot change the schema; the owner runs `alembic upgrade head`.
- Never edit a migration that is already merged; add a new one.

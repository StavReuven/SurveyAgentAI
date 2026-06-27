"""Migrate all data from SQLite to PostgreSQL."""
import sqlite3
import psycopg2
import psycopg2.extras

PG_URL = "postgresql://postgres:nuttman1@localhost:5432/surveyai"
SQLITE_PATH = "survey_agent_ai.db"


# Order matters for FK constraints
TABLES = [
    "campaigns",
    "questions",
    "participants",
    "branch_rules",
    "calling_policies",
    "campaign_executions",
    "call_attempts",
    "interviewees",
    "call_logs",
    "answers",
    "conversation_turns",
    "free_text_labels",
    "demographic_weights",
    "answer_labels",
    "entity_mentions",
    "free_text_analyses",
    "cross_survey_matches",
    "answer_fact_checks",
]

def get_bool_cols(pg, table):
    """Return set of boolean column names for a table from PG schema."""
    cur = pg.cursor()
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s AND data_type = 'boolean'
    """, (table,))
    return {r[0] for r in cur.fetchall()}


def migrate():
    sqlite = sqlite3.connect(SQLITE_PATH)
    sqlite.row_factory = sqlite3.Row
    pg = psycopg2.connect(PG_URL)
    pg.autocommit = False

    try:
        for table in TABLES:
            bool_cols_for_table = get_bool_cols(pg, table)
            cur_s = sqlite.cursor()
            cur_s.execute(f"SELECT * FROM {table}")
            rows = cur_s.fetchall()
            if not rows:
                print(f"  {table}: empty, skip")
                continue

            cols = [d[0] for d in cur_s.description]
            placeholders = ",".join(["%s"] * len(cols))
            col_names = ",".join(f'"{c}"' for c in cols)
            sql = f'INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

            cur_p = pg.cursor()
            bool_indices = {i for i, c in enumerate(cols) if c in bool_cols_for_table}

            def fix_row(row):
                row = list(row)
                for i in bool_indices:
                    if row[i] is not None:
                        row[i] = bool(row[i])
                return tuple(row)

            data = [fix_row(row) for row in rows]
            psycopg2.extras.execute_batch(cur_p, sql, data, page_size=200)
            print(f"  {table}: {len(data)} rows")

        # Fix sequences so next INSERT gets correct id
        cur_p = pg.cursor()
        for table in TABLES:
            try:
                cur_p.execute(f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1)
                    )
                """)
            except Exception:
                pg.rollback()
                cur_p = pg.cursor()

        pg.commit()
        print("\nDone! All data migrated to PostgreSQL.")

    except Exception as e:
        pg.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        sqlite.close()
        pg.close()

if __name__ == "__main__":
    migrate()

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./survey_agent_ai.db")

_connect_args = (
    {"check_same_thread": False} if DATABASE_URL.startswith("sqlite")
    # The DB server's session timezone defaults to the OS timezone (e.g.
    # Asia/Jerusalem). Our naive `timestamp` columns always receive
    # datetime.now(timezone.utc) values, but psycopg2 converts aware
    # datetimes into the *session* timezone before Postgres strips the
    # offset — so without forcing UTC here, every stored timestamp is
    # silently shifted by the local UTC offset, breaking any comparison
    # against a freshly computed real UTC "now" (e.g. retry-delay checks).
    else {"options": "-c timezone=utc"}
)

# Keep connections alive across requests instead of reopening one per
# request — each new connection to a remote DB (e.g. Neon) pays a full
# network round-trip plus SSL handshake, which is the main source of the
# "every click takes seconds" feel once the DB is no longer on localhost.
# pool_pre_ping guards against Neon closing idle connections server-side.
_pool_kwargs = {} if DATABASE_URL.startswith("sqlite") else {
    "pool_pre_ping": True,
    "pool_size": 10,
    "max_overflow": 20,
    "pool_recycle": 300,
}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, **_pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

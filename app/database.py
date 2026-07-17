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

engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

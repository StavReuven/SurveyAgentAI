"""One-off: dump the local Postgres DB and restore it into the cloud DB.

Run this yourself from a terminal in the project root:

    py -3.12 scripts/migrate_to_cloud.py

Reads DATABASE_URL (local) and CLOUD_DATABASE_URL (Neon) from .env.
Never prints either connection string.
"""
import subprocess
import sys
from dotenv import dotenv_values

PG_BIN = r"C:\Program Files\PostgreSQL\18\bin"

vals = dotenv_values(".env")
local_url = vals.get("DATABASE_URL")
cloud_url = vals.get("CLOUD_DATABASE_URL")

if not local_url or not cloud_url:
    print("Missing DATABASE_URL or CLOUD_DATABASE_URL in .env")
    sys.exit(1)

print("Dumping local database...")
dump = subprocess.run(
    [f"{PG_BIN}\\pg_dump.exe", local_url, "--format=plain", "--no-owner", "--no-privileges", "--no-comments"],
    capture_output=True,
)
if dump.returncode != 0:
    print("DUMP FAILED:")
    print(dump.stderr.decode(errors="replace"))
    sys.exit(1)
print(f"Dump ok, {len(dump.stdout)} bytes. Restoring into cloud DB...")

restore = subprocess.run(
    [f"{PG_BIN}\\psql.exe", cloud_url, "-v", "ON_ERROR_STOP=1"],
    input=dump.stdout,
    capture_output=True,
)
if restore.returncode != 0:
    print("RESTORE FAILED (tail):")
    print(restore.stderr.decode(errors="replace")[-3000:])
    sys.exit(1)

print("Done. Cloud DB now has a full copy of the local data.")

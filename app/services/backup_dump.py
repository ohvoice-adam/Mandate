"""pg_dump wrapper: produce a custom-format dump of every table except voters."""

import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime

logger = logging.getLogger(__name__)

# All tables to include in backup — excludes voters and voters_backup_* tables
BACKUP_TABLES = [
    "users",
    "signatures",
    "books",
    "batches",
    "collectors",
    "organizations",
    "data_enterers",
    "paid_collectors",
    "settings",
    "voter_imports",
    "petition_print_jobs",
]


def server_major_version() -> int | None:
    """Return the connected PostgreSQL server's major version, or None."""
    from sqlalchemy import text

    from app import db

    try:
        version_num = db.session.execute(text("SHOW server_version_num")).scalar()
        return int(version_num) // 10000
    except Exception:
        logger.warning("Could not determine PostgreSQL server version", exc_info=True)
        return None


def find_pg_dump(server_major: int | None) -> str:
    """Return the path to a pg_dump binary matching *server_major*.

    On Debian/Ubuntu, versioned binaries live at
    /usr/lib/postgresql/{version}/bin/pg_dump.  Falls back to whatever
    'pg_dump' resolves to in PATH if no versioned binary is found.
    """
    if server_major:
        versioned = f"/usr/lib/postgresql/{server_major}/bin/pg_dump"
        if os.path.isfile(versioned) and os.access(versioned, os.X_OK):
            logger.info("Using versioned pg_dump: %s", versioned)
            return versioned
        logger.warning(
            "pg_dump for PostgreSQL %s not found at %s; "
            "falling back to default. Install with: "
            "sudo apt install postgresql-client-%s",
            server_major, versioned, server_major,
        )

    default = shutil.which("pg_dump") or "pg_dump"
    return default


def create_pg_dump(db_url: str, server_major: int | None = None) -> str:
    """Run pg_dump for BACKUP_TABLES only and return the path to the dump file."""
    # Strip SQLAlchemy driver prefixes (e.g. postgresql+psycopg2://)
    clean_url = db_url.replace("+psycopg2", "").replace("+pg8000", "")

    pg_dump = find_pg_dump(server_major)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    fd, dump_path = tempfile.mkstemp(suffix=f"-petition-qc-{timestamp}.dump")
    os.close(fd)

    # Pass the full URI via --dbname so pg_dump receives the password and all
    # connection parameters exactly as SQLAlchemy uses them.
    cmd = [pg_dump, "--format=custom", "--dbname", clean_url]
    for table in BACKUP_TABLES:
        cmd.extend(["--table", table])

    with open(dump_path, "wb") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)

    if result.returncode != 0:
        os.unlink(dump_path)
        stderr = result.stderr.decode().strip()
        # Provide an actionable message when the version mismatch is the cause.
        if "server version mismatch" in stderr and server_major:
            raise RuntimeError(
                f"pg_dump version mismatch (server is PostgreSQL {server_major}). "
                f"Install the matching client: sudo apt install postgresql-client-{server_major}"
            )
        raise RuntimeError(
            f"pg_dump exited with code {result.returncode}: {stderr}"
        )

    return dump_path

"""
Flask application configuration.

Flask concepts used here:
- ``app.config.from_object(Config)`` reads every UPPER_CASE class attribute
  as a config key.  Lowercase attributes are ignored, which is why all the
  settings below are SCREAMING_SNAKE_CASE.
- ``SECRET_KEY`` signs Flask sessions and all itsdangerous tokens (invite
  links, password-reset links).  Without it, anyone could forge a session
  cookie or a token.
- ``SQLALCHEMY_DATABASE_URI`` is the only connection string Flask-SQLAlchemy
  needs; it handles pooling internally.
- ``SQLALCHEMY_ENGINE_OPTIONS`` passes keyword arguments straight through to
  SQLAlchemy's ``create_engine()`` call, which in turn passes
  ``connect_args`` to the underlying psycopg2 driver.
"""

import os
from dotenv import load_dotenv

# load_dotenv() reads a .env file in the project root (if it exists) and
# injects its contents into os.environ before any os.environ.get() calls
# below.  This means .env values act exactly like real environment variables.
load_dotenv()


class Config:
    # SECRET_KEY signs session cookies and itsdangerous tokens.  Any value
    # works in dev, but production deployments must set a long random string
    # (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`).
    _secret = os.environ.get("SECRET_KEY", "")
    if not _secret:
        import warnings
        warnings.warn(
            "SECRET_KEY is not set. Sessions and tokens are insecure. Set SECRET_KEY in your environment.",
            stacklevel=2,
        )
        _secret = "dev-key-change-me"
    SECRET_KEY = _secret

    # PostgreSQL connection string.  Flask-SQLAlchemy passes this to
    # SQLAlchemy's create_engine(), which manages a connection pool.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql://localhost:5432/mandate"
    )

    # Disable the SQLAlchemy event system used for change tracking — it adds
    # overhead we don't need and emits a deprecation warning if left True.
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Engine-level options passed directly to create_engine().
    # connect_timeout=5 tells psycopg2 to give up after 5 seconds if
    # PostgreSQL is unreachable (avoids long hangs on startup).
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"connect_timeout": 5},
        "pool_size": 3,
        "max_overflow": 2,
        "pool_pre_ping": True,
    }

    # Search settings
    SEARCH_RESULTS_LIMIT = 100  # Fewer results = faster response
    SEARCH_SIMILARITY_THRESHOLD = 0.2  # Lower = more results but faster

    # File upload settings
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/tmp/petition-qc-uploads")
    # MAX_CONTENT_LENGTH is a built-in Flask limit; requests larger than this
    # are rejected with HTTP 413 before they reach any route handler.
    MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1GB max upload size

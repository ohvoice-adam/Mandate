#!/bin/sh
set -e

echo "Enabling pg_trgm extension..."
python - <<'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.autocommit = True
conn.cursor().execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
conn.close()
PYEOF

# Detect whether this is a brand-new database (no alembic_version table yet).
# Fresh installs use db.create_all() + stamp; existing installs use migrate.
FRESH_DB=$(python - <<'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("""
    SELECT EXISTS(
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'alembic_version'
    )
""")
print("0" if cur.fetchone()[0] else "1")
conn.close()
PYEOF
)

if [ "$FRESH_DB" = "1" ]; then
    echo "Fresh database detected — creating tables with db.create_all()..."
    python - <<'PYEOF'
from app import create_app, db
app = create_app()
with app.app_context():
    db.create_all()
PYEOF
    echo "Stamping migrations as current..."
    flask db stamp head
else
    echo "Existing database — running migrations..."
    flask db upgrade
fi

echo "Seeding default admin user..."
python - <<'PYEOF'
import os
from app import create_app, db
from app.models import User
from app.models.user import UserRole

app = create_app()
with app.app_context():
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")

    if not User.query.filter_by(email=admin_email).first():
        if not admin_password:
            admin_password = "changeme"
        user = User(email=admin_email, first_name="Admin", last_name="User",
                    role=UserRole.ADMIN, must_change_password=True)
        user.set_password(admin_password)
        db.session.add(user)
        db.session.commit()
        print(f"[INFO] Admin user created: {admin_email}", flush=True)
    else:
        print(f"[INFO] Admin user already exists: {admin_email} — skipping.", flush=True)
PYEOF

echo "Starting Gunicorn..."
exec gunicorn \
    --workers 2 \
    --worker-class gthread \
    --threads 4 \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    "app:create_app()"

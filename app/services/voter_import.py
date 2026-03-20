import csv
import os
import shutil
import tempfile
import threading
import zipfile
from datetime import datetime

from flask import current_app
from sqlalchemy import text

from app import db
from app.models.voter import Voter
from app.models.voter_import import VoterImport, ImportStatus


class VoterImportService:
    """Service for importing voter files with progress tracking and rollback support."""

    # Map CSV column names to Voter model fields
    COLUMN_MAPPING = {
        "SOS_VOTERID": "sos_voterid",
        "COUNTY_NUMBER": "county_number",
        "FIRST_NAME": "first_name",
        "MIDDLE_NAME": "middle_name",
        "LAST_NAME": "last_name",
        "RESIDENTIAL_ADDRESS1": "residential_address1",
        "RESIDENTIAL_ADDRESS2": "residential_address2",
        "RESIDENTIAL_CITY": "residential_city",
        "RESIDENTIAL_STATE": "residential_state",
        "RESIDENTIAL_ZIP": "residential_zip",
        "CITY": "city",
        "DATE_OF_BIRTH": "date_of_birth",
        "REGISTRATION_DATE": "registration_date",
        "PRECINCT_CODE": "precinct_code",
        "PRECINCT_NAME": "precinct_name",
        "WARD": "ward",
    }
    # Note: Columns starting with GENERAL, SPECIAL, PRIMARY (voting history)
    # are automatically skipped since they're not in COLUMN_MAPPING

    # Batch size for commits and cancellation checks
    BATCH_SIZE = 1000

    # Track running import threads for cancellation
    _running_imports = {}
    _lock = threading.Lock()

    @classmethod
    def count_lines(cls, filepath):
        """Count lines in a file efficiently."""
        count = 0
        with open(filepath, "rb") as f:
            for _ in f:
                count += 1
        return max(0, count - 1)  # Subtract header row

    @classmethod
    def start_import(cls, import_id, app):
        """Start an import in a background thread."""
        thread = threading.Thread(
            target=cls._run_import,
            args=(import_id, app),
            daemon=True
        )
        with cls._lock:
            cls._running_imports[import_id] = {"thread": thread, "cancel": False}
        thread.start()
        return thread

    @classmethod
    def cancel_import(cls, import_id):
        """Signal cancellation to a running import.

        Returns True if the thread was signalled, False if no thread is running
        (e.g. the process was killed and restarted).
        """
        with cls._lock:
            if import_id in cls._running_imports:
                cls._running_imports[import_id]["cancel"] = True
                return True
        return False

    @classmethod
    def force_cancel_import(cls, import_id):
        """Force-cancel an import whose thread is no longer running.

        Used when the app was killed mid-import and the in-memory thread state
        is gone but the DB record is still marked as running.
        """
        voter_import = db.session.get(VoterImport, import_id)
        if not voter_import:
            return

        voter_import.status = ImportStatus.FAILED
        voter_import.error_message = "Cancelled: import was orphaned after an unexpected shutdown"
        voter_import.completed_at = datetime.utcnow()
        db.session.commit()

        try:
            cls._restore_from_backup(voter_import)
        except Exception:
            db.session.rollback()

    @classmethod
    def recover_stale_imports(cls):
        """Recover imports stuck in running/pending state from a previous crash.

        Called once at app startup.
        """
        stale = VoterImport.query.filter(
            VoterImport.status.in_([ImportStatus.RUNNING, ImportStatus.PENDING])
        ).all()

        for voter_import in stale:
            voter_import.status = ImportStatus.FAILED
            voter_import.error_message = "Failed: application was shut down while import was in progress"
            voter_import.completed_at = datetime.utcnow()

        if stale:
            db.session.commit()

        # Attempt backup restore for any that had backups
        for voter_import in stale:
            if voter_import.backup_table:
                try:
                    cls._restore_from_backup(voter_import)
                except Exception:
                    db.session.rollback()

    @classmethod
    def _is_cancelled(cls, import_id):
        """Check if cancellation was requested."""
        with cls._lock:
            if import_id in cls._running_imports:
                return cls._running_imports[import_id]["cancel"]
        return False

    @classmethod
    def _cleanup_import(cls, import_id):
        """Remove import from running list."""
        with cls._lock:
            cls._running_imports.pop(import_id, None)

    @classmethod
    def _run_import(cls, import_id, app):
        """Execute the import process in a background thread."""
        with app.app_context():
            voter_import = db.session.get(VoterImport, import_id)
            if not voter_import:
                return

            try:
                # Update status to running
                voter_import.status = ImportStatus.RUNNING
                voter_import.started_at = datetime.utcnow()
                db.session.commit()

                # Get the file path
                upload_folder = app.config.get("UPLOAD_FOLDER", "/tmp/petition-qc-uploads")
                filepath = os.path.join(upload_folder, voter_import.filename)

                if not os.path.exists(filepath):
                    raise FileNotFoundError(f"Upload file not found: {filepath}")

                # Count total rows for progress tracking
                voter_import.total_rows = cls.count_lines(filepath)
                db.session.commit()

                # Get county number from the selected county name
                county_number = cls.get_county_number(voter_import.county_name)
                if not county_number:
                    raise ValueError(f"Unknown county: {voter_import.county_name}")

                # Create backup of existing county voters
                cls._create_backup(voter_import, county_number)

                # Delete existing voters for this county
                cls._delete_county_voters(county_number)

                # Import new data
                cls._import_csv(voter_import, filepath)

                # Check final status
                if cls._is_cancelled(import_id):
                    voter_import.status = ImportStatus.CANCELLED
                    cls._restore_from_backup(voter_import)
                else:
                    voter_import.status = ImportStatus.COMPLETED
                    voter_import.completed_at = datetime.utcnow()
                    db.session.commit()
                    # Update query planner statistics after new data is loaded
                    db.session.execute(text("ANALYZE voters"))

                db.session.commit()

            except Exception as e:
                db.session.rollback()
                voter_import = db.session.get(VoterImport, import_id)
                if voter_import:
                    voter_import.status = ImportStatus.FAILED
                    voter_import.error_message = str(e)
                    voter_import.completed_at = datetime.utcnow()
                    db.session.commit()
                    # Restore from backup on error
                    try:
                        cls._restore_from_backup(voter_import)
                    except Exception:
                        pass  # Best effort restore

            finally:
                cls._cleanup_import(import_id)
                # Clean up the uploaded file
                try:
                    upload_folder = app.config.get("UPLOAD_FOLDER", "/tmp/petition-qc-uploads")
                    filepath = os.path.join(upload_folder, voter_import.filename)
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except Exception:
                    pass

    # Allowlisted voter column names (matches COLUMN_MAPPING values)
    _VOTER_COLUMNS = tuple(sorted({
        "sos_voterid", "county_number", "first_name", "middle_name", "last_name",
        "residential_address1", "residential_address2", "residential_city",
        "residential_state", "residential_zip", "city", "date_of_birth",
        "registration_date", "precinct_code", "precinct_name", "ward",
    }))

    @classmethod
    def _safe_backup_table(cls, import_id: int) -> str:
        """Return a safe backup table name for the given import ID."""
        return f"voters_backup_{int(import_id)}"

    @classmethod
    def _create_backup(cls, voter_import, county_number):
        """Create a backup table of existing county voters."""
        backup_table = cls._safe_backup_table(voter_import.id)
        voter_import.backup_table = backup_table

        # Store the detected county number for rollback
        voter_import.detected_county_ids = county_number

        # Get the highest voter ID before import (for rollback reference)
        result = db.session.execute(text("SELECT MAX(id) FROM voters"))
        max_id = result.scalar()
        voter_import.rollback_voter_id = max_id or 0

        # Create backup table with county's existing voters
        db.session.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {backup_table} AS
            SELECT * FROM voters WHERE county_number = :county_number
        """), {"county_number": county_number})

        db.session.commit()

    @classmethod
    def _delete_county_voters(cls, county_number):
        """Delete all voters for the given county number."""
        if not county_number:
            return

        db.session.execute(
            text("DELETE FROM voters WHERE county_number = :county_number"),
            {"county_number": county_number}
        )
        db.session.commit()

    @classmethod
    def delete_all_voters(cls):
        """Delete all voters and return the count."""
        result = db.session.execute(text("SELECT COUNT(*) FROM voters"))
        count = result.scalar() or 0

        db.session.execute(text("DELETE FROM voters"))
        db.session.commit()
        return count

    @classmethod
    def delete_county(cls, county_number):
        """Delete all voters for a county and return the count."""
        if not county_number:
            return 0

        # Get count first
        result = db.session.execute(
            text("SELECT COUNT(*) FROM voters WHERE county_number = :county_number"),
            {"county_number": county_number}
        )
        count = result.scalar() or 0

        # Delete
        db.session.execute(
            text("DELETE FROM voters WHERE county_number = :county_number"),
            {"county_number": county_number}
        )
        db.session.commit()
        return count

    @classmethod
    def get_loaded_counties(cls):
        """Get list of counties that have voters loaded, with counts."""
        # Reverse mapping: county_number -> county_name
        number_to_name = {v: k for k, v in cls.OHIO_COUNTY_NUMBERS.items()}

        result = db.session.execute(
            text("SELECT county_number, COUNT(*) as cnt FROM voters GROUP BY county_number ORDER BY county_number")
        )

        counties = []
        for row in result:
            county_num = row[0]
            count = row[1]
            county_name = number_to_name.get(county_num, f"Unknown ({county_num})")
            counties.append({
                "name": county_name,
                "number": county_num,
                "count": count
            })

        return counties

    @classmethod
    def _import_csv(cls, voter_import, filepath):
        """Stream import a CSV file."""
        processed = 0
        batch = []

        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)

            for row in reader:
                # Check for cancellation every batch
                if processed % cls.BATCH_SIZE == 0 and processed > 0:
                    if cls._is_cancelled(voter_import.id):
                        # Commit what we have and stop
                        if batch:
                            cls._insert_batch(batch)
                            batch = []
                        voter_import.processed_rows = processed
                        db.session.commit()
                        return

                voter_data = cls._map_row(row)
                if voter_data:
                    batch.append(voter_data)

                processed += 1

                # Commit batch
                if len(batch) >= cls.BATCH_SIZE:
                    cls._insert_batch(batch)
                    batch = []
                    voter_import.processed_rows = processed
                    db.session.commit()

            # Insert remaining batch
            if batch:
                cls._insert_batch(batch)
                voter_import.processed_rows = processed
                db.session.commit()

    @classmethod
    def _map_row(cls, row):
        """Map a CSV row to voter data dict."""
        voter_data = {}

        for csv_col, model_field in cls.COLUMN_MAPPING.items():
            value = row.get(csv_col, "").strip() if row.get(csv_col) else None
            if value:
                # Handle date fields
                if model_field in ("date_of_birth", "registration_date"):
                    try:
                        # Try common date formats
                        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                            try:
                                value = datetime.strptime(value, fmt).date()
                                break
                            except ValueError:
                                continue
                        else:
                            value = None
                    except Exception:
                        value = None
                voter_data[model_field] = value

        # Must have at least sos_voterid or county_number
        if not voter_data.get("sos_voterid") and not voter_data.get("county_number"):
            return None

        return voter_data

    @classmethod
    def _insert_batch(cls, batch):
        """Bulk insert a batch of voter records."""
        if not batch:
            return

        # Use only allowlisted columns — never interpolate arbitrary keys into SQL
        allowed = set(cls._VOTER_COLUMNS)
        all_keys: set[str] = set()
        for record in batch:
            all_keys.update(record.keys())
        columns = sorted(all_keys & allowed)

        placeholders = ", ".join([f":{col}" for col in columns])
        column_names = ", ".join(columns)

        sql = text(f"INSERT INTO voters ({column_names}) VALUES ({placeholders})")

        for voter_data in batch:
            # Ensure all columns have a value (None for missing)
            complete_data = {col: voter_data.get(col) for col in columns}
            db.session.execute(sql, complete_data)

    @classmethod
    def rollback_import(cls, import_id):
        """Roll back a completed import to restore previous state."""
        voter_import = db.session.get(VoterImport, import_id)
        if not voter_import:
            raise ValueError("Import not found")

        if not voter_import.can_rollback:
            raise ValueError("Import cannot be rolled back (must be completed within 24 hours)")

        try:
            cls._restore_from_backup(voter_import)
            voter_import.status = ImportStatus.CANCELLED
            voter_import.error_message = "Rolled back by user"
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            raise e

    @classmethod
    def _restore_from_backup(cls, voter_import):
        """Restore voters from backup table."""
        if not voter_import.backup_table:
            return

        # Validate backup table name against expected pattern before interpolation
        import re as _re
        if not _re.match(r'^voters_backup_\d+$', voter_import.backup_table):
            current_app.logger.error("Refusing to restore from unexpected backup table name: %s", voter_import.backup_table)
            return

        # Check if backup table exists
        result = db.session.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = :table_name
            )
        """), {"table_name": voter_import.backup_table})

        if not result.scalar():
            return

        # Get the stored county number
        county_number = voter_import.detected_county_ids
        if not county_number:
            return

        # Delete any newly imported voters for this county
        db.session.execute(
            text("DELETE FROM voters WHERE county_number = :county_number"),
            {"county_number": county_number}
        )

        # Restore from backup using allowlisted columns only
        column_list = ", ".join(cls._VOTER_COLUMNS)
        backup_table = voter_import.backup_table  # already validated above

        db.session.execute(text(f"""
            INSERT INTO voters ({column_list})
            SELECT {column_list} FROM {backup_table}
        """))

        db.session.commit()

    @classmethod
    def cleanup_backup(cls, import_id):
        """Drop the backup table for a completed import."""
        voter_import = db.session.get(VoterImport, import_id)
        if not voter_import or not voter_import.backup_table:
            return

        try:
            import re as _re
            if not _re.match(r'^voters_backup_\d+$', voter_import.backup_table):
                return
            db.session.execute(text(f"DROP TABLE IF EXISTS {voter_import.backup_table}"))
            voter_import.backup_table = None
            db.session.commit()
        except Exception:
            db.session.rollback()

    @classmethod
    def handle_upload(cls, file, county_name, app):
        """Handle an uploaded file (CSV or ZIP) and start imports."""
        upload_folder = app.config.get("UPLOAD_FOLDER", "/tmp/petition-qc-uploads")
        os.makedirs(upload_folder, exist_ok=True)

        imports = []

        # Check if it's a ZIP file
        if file.filename.lower().endswith(".zip"):
            # Save ZIP to temp location
            zip_path = os.path.join(upload_folder, file.filename)
            file.save(zip_path)

            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    for name in zf.namelist():
                        if name.lower().endswith((".csv", ".txt")) and not name.startswith("__"):
                            # Extract individual file
                            extracted_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{os.path.basename(name)}"
                            extracted_path = os.path.join(upload_folder, extracted_name)

                            with zf.open(name) as src, open(extracted_path, "wb") as dst:
                                shutil.copyfileobj(src, dst)

                            # Create import record
                            voter_import = VoterImport(
                                filename=extracted_name,
                                county_name=county_name,
                                status=ImportStatus.PENDING
                            )
                            db.session.add(voter_import)
                            db.session.commit()
                            imports.append(voter_import)
            finally:
                # Clean up ZIP file
                if os.path.exists(zip_path):
                    os.remove(zip_path)
        else:
            # Single CSV/TXT file
            safe_filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{file.filename}"
            filepath = os.path.join(upload_folder, safe_filename)
            file.save(filepath)

            voter_import = VoterImport(
                filename=safe_filename,
                county_name=county_name,
                status=ImportStatus.PENDING
            )
            db.session.add(voter_import)
            db.session.commit()
            imports.append(voter_import)

        # Start imports sequentially (each in its own thread, but they'll run one at a time)
        for imp in imports:
            cls.start_import(imp.id, app)

        return imports

    # Ohio county name to county_number mapping
    OHIO_COUNTY_NUMBERS = {
        "Adams": "01", "Allen": "02", "Ashland": "03", "Ashtabula": "04", "Athens": "05",
        "Auglaize": "06", "Belmont": "07", "Brown": "08", "Butler": "09", "Carroll": "10",
        "Champaign": "11", "Clark": "12", "Clermont": "13", "Clinton": "14", "Columbiana": "15",
        "Coshocton": "16", "Crawford": "17", "Cuyahoga": "18", "Darke": "19", "Defiance": "20",
        "Delaware": "21", "Erie": "22", "Fairfield": "23", "Fayette": "24", "Franklin": "25",
        "Fulton": "26", "Gallia": "27", "Geauga": "28", "Greene": "29", "Guernsey": "30",
        "Hamilton": "31", "Hancock": "32", "Hardin": "33", "Harrison": "34", "Henry": "35",
        "Highland": "36", "Hocking": "37", "Holmes": "38", "Huron": "39", "Jackson": "40",
        "Jefferson": "41", "Knox": "42", "Lake": "43", "Lawrence": "44", "Licking": "45",
        "Logan": "46", "Lorain": "47", "Lucas": "48", "Madison": "49", "Mahoning": "50",
        "Marion": "51", "Medina": "52", "Meigs": "53", "Mercer": "54", "Miami": "55",
        "Monroe": "56", "Montgomery": "57", "Morgan": "58", "Morrow": "59", "Muskingum": "60",
        "Noble": "61", "Ottawa": "62", "Paulding": "63", "Perry": "64", "Pickaway": "65",
        "Pike": "66", "Portage": "67", "Preble": "68", "Putnam": "69", "Richland": "70",
        "Ross": "71", "Sandusky": "72", "Scioto": "73", "Seneca": "74", "Shelby": "75",
        "Stark": "76", "Summit": "77", "Trumbull": "78", "Tuscarawas": "79", "Union": "80",
        "Van Wert": "81", "Vinton": "82", "Warren": "83", "Washington": "84", "Wayne": "85",
        "Williams": "86", "Wood": "87", "Wyandot": "88",
    }

    @classmethod
    def get_ohio_counties(cls):
        """Get list of all Ohio county names."""
        return list(cls.OHIO_COUNTY_NUMBERS.keys())

    @classmethod
    def get_county_number(cls, county_name):
        """Get the county number for a county name."""
        return cls.OHIO_COUNTY_NUMBERS.get(county_name)

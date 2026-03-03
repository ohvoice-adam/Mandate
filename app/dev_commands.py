"""Flask CLI commands for generating and wiping development seed data.

Only available when the app is running in DEBUG mode.

Usage:
    flask dev seed          # Generate fake orgs, collectors, voters, books, signatures
    flask dev seed --voters 500 --books 20
    flask dev wipe          # Remove all seed data (keeps admin users + settings)
    flask dev wipe --yes    # Skip confirmation
"""

import random
from datetime import date, timedelta

import click
from flask import current_app
from flask.cli import AppGroup

from app import db

dev_cli = AppGroup("dev", help="Development data utilities (DEBUG mode only).")

# ---------------------------------------------------------------------------
# Data pools
# ---------------------------------------------------------------------------

_FIRST = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "William", "Barbara", "David", "Susan", "Richard", "Jessica",
    "Joseph", "Sarah", "Thomas", "Karen", "Charles", "Lisa", "Christopher",
    "Nancy", "Daniel", "Betty", "Matthew", "Margaret", "Anthony", "Sandra",
    "Mark", "Ashley", "Donald", "Dorothy", "Steven", "Kimberly", "Emily",
    "Donna", "Kenneth", "Michelle", "Kevin", "Carol",
]

_LAST = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
]

_STREETS = [
    "High St", "Broad St", "Fifth Ave", "Neil Ave", "Lane Ave",
    "Cleveland Ave", "Henderson Rd", "Morse Rd", "Oak St", "Maple Ave",
    "Elm St", "Washington Ave", "Park Ave", "Main St", "Church St",
    "Third St", "Fourth St", "Spring St", "Summit St", "Vine St",
]

_CITIES = [
    "COLUMBUS", "COLUMBUS", "COLUMBUS", "COLUMBUS", "COLUMBUS",
    "WESTERVILLE", "DUBLIN", "HILLIARD", "GROVE CITY", "WORTHINGTON",
]

_ZIPS = [
    "43201", "43202", "43203", "43204", "43205", "43206", "43207",
    "43209", "43210", "43211", "43212", "43213", "43214", "43215",
    "43219", "43220", "43221", "43222", "43223", "43224", "43227",
]

_PRECINCT_CODES = ["01A", "01B", "02A", "02B", "03A", "03B", "04A", "04B"]
_PRECINCT_NAMES = [
    "Columbus 01A", "Columbus 01B", "Columbus 02A", "Columbus 02B",
    "Columbus 03A", "Columbus 03B", "Columbus 04A", "Columbus 04B",
]

_ORG_NAMES = [
    "Ohio Civic Action",
    "Franklin County Residents United",
]

_COLLECTOR_NAMES = [
    ("Rachel", "Torres"),
    ("Derek", "Nguyen"),
    ("Amara", "Okonkwo"),
    ("Tony", "Ferrara"),
    ("Priya", "Patel"),
    ("Marcus", "Jefferson"),
]

_DEV_USERS = [
    ("organizer", "Dev", "Organizer", "organizer@dev.example"),
    ("enterer",   "Dev", "Enterer",   "enterer@dev.example"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guard():
    if not current_app.debug:
        click.echo("Error: 'flask dev' commands are only available in DEBUG mode.", err=True)
        raise SystemExit(1)


def _rdate(start=(2023, 1, 1), end=(2024, 12, 31)) -> date:
    s = date(*start)
    e = date(*end)
    return s + timedelta(days=random.randint(0, (e - s).days))


def _raddr():
    return (
        f"{random.randint(100, 9999)} {random.choice(_STREETS)}",
        random.choice(_CITIES),
        random.choice(_ZIPS),
    )


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

@dev_cli.command("seed")
@click.option("--voters", default=200, show_default=True,
              help="Number of fake voters to create.")
@click.option("--books", default=10, show_default=True,
              help="Number of petition books to create.")
def seed(voters, books):
    """Generate dummy organizations, collectors, voters, books, and signatures."""
    from app.models import (
        Organization, Collector, User, Voter, Book, Batch, Signature,
    )

    _guard()

    existing = Voter.query.filter(Voter.sos_voterid.like("SEED-%")).count()
    if existing:
        click.echo(
            f"Warning: {existing} seed voters already exist. "
            "Run 'flask dev wipe' first to start fresh.",
            err=True,
        )

    # -- Organizations -------------------------------------------------------
    orgs = []
    for name in _ORG_NAMES:
        org = Organization(name=name)
        db.session.add(org)
        orgs.append(org)
    db.session.flush()
    click.echo(f"  Created {len(orgs)} organizations.")

    # -- Collectors ----------------------------------------------------------
    collectors = []
    for i, (fn, ln) in enumerate(_COLLECTOR_NAMES):
        c = Collector(
            first_name=fn,
            last_name=ln,
            phone=f"614-555-{1000 + i:04d}",
            email=f"{fn.lower()}.{ln.lower()}@example.com",
            organization_id=orgs[i % len(orgs)].id,
        )
        db.session.add(c)
        collectors.append(c)
    db.session.flush()
    click.echo(f"  Created {len(collectors)} collectors.")

    # -- Dev users -----------------------------------------------------------
    created_users = []
    for role, fn, ln, email in _DEV_USERS:
        if not User.query.filter_by(email=email).first():
            u = User(
                email=email,
                first_name=fn,
                last_name=ln,
                role=role,
                organization_id=orgs[0].id,
            )
            u.set_password("devpassword")
            db.session.add(u)
            created_users.append(u)
    db.session.flush()
    if created_users:
        emails = ", ".join(u.email for u in created_users)
        click.echo(f"  Created {len(created_users)} dev users ({emails}, password: devpassword).")
    else:
        click.echo("  Dev users already exist — skipped.")

    # Gather all enterer-role users for batch assignment
    enterers = User.query.filter_by(role="enterer").all()

    # -- Voters --------------------------------------------------------------
    voter_list = []
    for i in range(voters):
        addr1, city, zip_ = _raddr()
        prec_idx = random.randint(0, len(_PRECINCT_CODES) - 1)
        v = Voter(
            sos_voterid=f"SEED-{i:06d}",
            county_number="SEED",
            first_name=random.choice(_FIRST),
            middle_name=random.choice(_FIRST) if random.random() > 0.5 else "",
            last_name=random.choice(_LAST),
            residential_address1=addr1,
            residential_city=city,
            residential_state="OH",
            residential_zip=zip_,
            city=city,
            date_of_birth=_rdate((1940, 1, 1), (2005, 12, 31)),
            registration_date=_rdate((1990, 1, 1), (2023, 12, 31)),
            precinct_code=_PRECINCT_CODES[prec_idx],
            precinct_name=_PRECINCT_NAMES[prec_idx],
        )
        db.session.add(v)
        voter_list.append(v)
    db.session.flush()
    click.echo(f"  Created {voters} fake voters (sos_voterid prefix: SEED-).")

    # -- Books, Batches, Signatures ------------------------------------------
    sig_count = 0
    for book_num in range(1, books + 1):
        collector = random.choice(collectors)
        date_out = _rdate((2024, 1, 1), (2024, 9, 30))
        date_back = date_out + timedelta(days=random.randint(7, 30))

        book = Book(
            book_number=f"B{book_num:03d}",
            collector_id=collector.id,
            date_out=date_out,
            date_back=date_back,
        )
        db.session.add(book)
        db.session.flush()

        num_batches = random.randint(1, 2)
        for b_idx in range(num_batches):
            enterer = random.choice(enterers) if enterers else None
            batch = Batch(
                book_id=book.id,
                book_number=book.book_number,
                collector_id=collector.id,
                enterer_id=enterer.id if enterer else None,
                enterer_first=enterer.first_name if enterer else None,
                enterer_last=enterer.last_name if enterer else None,
                enterer_email=enterer.email if enterer else None,
                date_entered=date_back + timedelta(days=b_idx),
            )
            db.session.add(batch)
            db.session.flush()

            for _ in range(random.randint(6, 18)):
                if random.random() < 0.72 and voter_list:
                    voter = random.choice(voter_list)
                    sig = Signature(
                        sos_voterid=voter.sos_voterid,
                        county_number=voter.county_number,
                        book_id=book.id,
                        batch_id=batch.id,
                        residential_address1=voter.residential_address1,
                        residential_address2=voter.residential_address2,
                        residential_city=voter.residential_city,
                        residential_state=voter.residential_state,
                        residential_zip=voter.residential_zip,
                        registered_city=voter.city,
                        matched=True,
                    )
                else:
                    addr1, city, zip_ = _raddr()
                    sig = Signature(
                        book_id=book.id,
                        batch_id=batch.id,
                        residential_address1=addr1,
                        residential_city=city,
                        residential_state="OH",
                        residential_zip=zip_,
                        matched=False,
                    )
                db.session.add(sig)
                sig_count += 1

    db.session.commit()
    click.echo(f"  Created {books} books with batches and {sig_count} signatures.")
    click.echo("Seed complete.")


# ---------------------------------------------------------------------------
# wipe
# ---------------------------------------------------------------------------

@dev_cli.command("wipe")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def wipe(yes):
    """Remove all seed/dummy data (admin users and settings are preserved)."""
    from app.models import (
        Signature, Batch, Book, PaidCollector, Collector,
        Organization, User, Voter, DataEnterer,
    )

    _guard()

    if not yes:
        click.confirm(
            "Delete all signatures, books, batches, collectors, organizations, "
            "dev users, data enterers, and SEED-prefixed voters?",
            abort=True,
        )

    # Deletion order respects FK constraints.
    sig_n   = db.session.query(Signature).delete()
    batch_n = db.session.query(Batch).delete()
    book_n  = db.session.query(Book).delete()
    pc_n    = db.session.query(PaidCollector).delete()
    coll_n  = db.session.query(Collector).delete()
    user_n  = (
        db.session.query(User)
        .filter(User.role != "admin")
        .delete(synchronize_session=False)
    )
    org_n   = db.session.query(Organization).delete()
    de_n    = db.session.query(DataEnterer).delete()
    voter_n = (
        db.session.query(Voter)
        .filter(Voter.sos_voterid.like("SEED-%"))
        .delete(synchronize_session=False)
    )

    db.session.commit()

    click.echo(f"  Deleted {sig_n} signatures")
    click.echo(f"  Deleted {batch_n} batches")
    click.echo(f"  Deleted {book_n} books")
    click.echo(f"  Deleted {pc_n} paid collector links")
    click.echo(f"  Deleted {coll_n} collectors")
    click.echo(f"  Deleted {user_n} non-admin users")
    click.echo(f"  Deleted {org_n} organizations")
    click.echo(f"  Deleted {de_n} data enterers")
    click.echo(f"  Deleted {voter_n} seed voters")
    click.echo("Wipe complete.")

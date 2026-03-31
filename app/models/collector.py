"""
Collector, DataEnterer, Organization, and PaidCollector models.

These represent the people and groups involved in gathering petition
signatures.  The relationship graph is:

    Organization ──< Collector ──< Book ──< Batch ──< Signature
                  └─< User (organizers scoped to an org)
                  └─< PaidCollector (explicit org↔collector link)

SQLAlchemy concept:
- Multiple ``db.relationship()`` declarations on one model are fine —
  SQLAlchemy generates a separate SQL query (or JOIN) for each one lazily,
  only when you first access the attribute.
"""

from app import db


class Collector(db.Model):
    """Petition signature collectors."""

    __tablename__ = "collectors"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # Relationships
    books = db.relationship("Book", back_populates="collector")
    organization = db.relationship("Organization", back_populates="collectors")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def display_name(self):
        return f"{self.last_name}, {self.first_name}"

    def __repr__(self):
        return f"<Collector {self.full_name}>"


class DataEnterer(db.Model):
    """Data entry staff."""

    __tablename__ = "data_enterers"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def display_name(self):
        return f"{self.last_name}, {self.first_name}"

    def __repr__(self):
        return f"<DataEnterer {self.full_name}>"


class Organization(db.Model):
    """Organizations that manage collectors."""

    __tablename__ = "organizations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # Relationships
    collectors = db.relationship("Collector", back_populates="organization")
    users = db.relationship("User", back_populates="organization")

    def __repr__(self):
        return f"<Organization {self.name}>"


class PaidCollector(db.Model):
    """Links collectors to organizations (for paid signature collection)."""

    __tablename__ = "paid_collectors"

    id = db.Column(db.Integer, primary_key=True)
    collector_id = db.Column(db.Integer, db.ForeignKey("collectors.id"), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    collector = db.relationship("Collector")
    organization = db.relationship("Organization")

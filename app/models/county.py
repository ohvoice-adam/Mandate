from app import db


class County(db.Model):
    """Lookup table of Ohio's 88 counties and their SOS-assigned numbers.

    Numbers are stored as zero-padded two-digit strings ("01"–"88") to match
    the COUNTY_NUMBER field format in Ohio SOS voter export files.
    """

    __tablename__ = "counties"

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(2), unique=True, nullable=False)  # "01"–"88"
    name = db.Column(db.String(100), unique=True, nullable=False)  # "Franklin"

    def __repr__(self):
        return f"<County {self.number} {self.name}>"

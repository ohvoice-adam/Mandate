"""
Help route — serves the static help/documentation page.

This is the simplest blueprint in the codebase: one route, one template, no
DB access.  It exists as a blueprint rather than a plain function so that
``url_for("help.index")`` resolves correctly and the URL can be mounted at
``/help`` via ``url_prefix`` in the app factory.
"""

from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("help", __name__)


@bp.route("/")
@login_required
def index():
    return render_template("help/index.html")

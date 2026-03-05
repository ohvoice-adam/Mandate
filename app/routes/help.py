from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("help", __name__)


@bp.route("/")
@login_required
def index():
    return render_template("help/index.html")

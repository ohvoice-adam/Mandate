"""
Application settings routes — admin-only configuration UI.

One blueprint (``settings``) split across modules by area; each module
attaches its routes to the shared ``bp`` defined here, so endpoint names such
as ``settings.save_backup_config`` are unchanged.

- ``general``:   main settings page (target city, goal, PDF deletion)
- ``backup``:    backup destinations, connection tests, run/download backup
- ``smtp``:      outgoing email configuration and test
- ``branding``:  logo, colors and fonts
- ``config_io``: export/import of non-sensitive settings
- ``health``:    system health dashboard

Flask concepts used in this package:
- **jsonify(...)**: converts a dict/list to a JSON HTTP response with
  ``Content-Type: application/json``.  Used by connection-test endpoints that
  JS polls for ``{ok, message}``.
- **abort(404)**: immediately raises an HTTP 404 response, halting route
  execution — no ``return`` needed after the call.
- **after_this_request(fn)**: registers *fn* as a callback that runs after
  Flask sends the response.  Used in ``download_backup`` to delete the temp
  dump file *after* it has been fully streamed to the browser.
- **send_file(path, as_attachment=True)**: streams a file from disk to the
  browser and sets ``Content-Disposition: attachment`` so the browser prompts
  a Save dialog instead of rendering the file inline.
- **Response(data, mimetype=..., headers=...)**: builds a raw HTTP response
  when ``render_template`` / ``send_file`` don't fit — used for JSON exports
  and for serving the stored logo image.
- **current_app._get_current_object()**: ``current_app`` is a proxy; passing
  it to a background thread or APScheduler would fail because the proxy is
  only valid inside a request context.  ``_get_current_object()`` unwraps the
  real ``Flask`` instance so it can be used outside the request context.
"""

from flask import Blueprint

bp = Blueprint("settings", __name__)

# Imported for their side effect of registering routes on ``bp``.
from app.routes.settings import (  # noqa: F401
    backup,
    branding,
    config_io,
    general,
    health,
    smtp,
)

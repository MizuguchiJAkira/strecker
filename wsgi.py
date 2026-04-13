"""WSGI entry points for Gunicorn.

Gunicorn can't call create_app(site='strecker') inline — it needs a
module-level application object. This file provides one for each site.

Usage:
    gunicorn wsgi:strecker_app   # Hunter-facing
    gunicorn wsgi:basal_app      # Enterprise-facing
"""

import os

from web.app import create_app

# Determine mode from environment
_demo = os.environ.get("DEMO_MODE", "0") == "1"

# Pre-built application objects for Gunicorn
strecker_app = create_app(demo=_demo, site="strecker")
basal_app = create_app(demo=_demo, site="basal")

# Default: whatever SITE env var says (falls back to strecker)
_site = os.environ.get("SITE", "strecker")
app = strecker_app if _site == "strecker" else basal_app

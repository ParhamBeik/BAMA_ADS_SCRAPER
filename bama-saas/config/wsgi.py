"""WSGI entry point (gunicorn)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

# #region agent log
try:
    import json
    import sys
    import time
    _p = "/Users/parham/Downloads/GITHUB_PROJECTS/BAMA_ADS_SCRAPER/.cursor/debug-92a022.log"
    with open(_p, "a") as _f:
        _f.write(json.dumps({
            "sessionId": "92a022", "runId": "pre-fix", "hypothesisId": "A",
            "location": "config/wsgi.py:boot", "message": "wsgi loaded",
            "data": {
                "server_software": os.environ.get("SERVER_SOFTWARE", ""),
                "argv": sys.argv,
                "has_worker_class_flag": "--worker-class" in sys.argv,
                "has_access_logfile_flag": "--access-logfile" in sys.argv,
            },
            "timestamp": int(time.time() * 1000),
        }) + "\n")
except Exception:
    pass
# #endregion

import os
import sys
import logging

logging.basicConfig(level=logging.INFO)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from scraper.api import app
    from mangum import Mangum
    handler = Mangum(app, lifespan="off")
except Exception as e:
    import json
    logging.error("Import failed: %s", e)
    def handler(event, context):
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e), "root": ROOT, "path": sys.path})
        }
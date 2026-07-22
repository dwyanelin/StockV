import os

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

# Public HTTPS base URL of this service (e.g. https://stockv-xxxx.a.run.app).
# If unset, it is derived from the incoming request.
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

CHART_DIR = os.environ.get("CHART_DIR", "/tmp/stockv-charts")
CHART_TTL_SECONDS = 30 * 60

import os

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

# Public HTTPS base URL of this service (e.g. https://stockv-xxxx.a.run.app).
# If unset, it is derived from the incoming request.
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

CHART_DIR = os.environ.get("CHART_DIR", "/tmp/stockv-charts")
CHART_TTL_SECONDS = 30 * 60

USER_COOLDOWN_SECONDS = float(os.environ.get("USER_COOLDOWN_SECONDS", "4"))
GROUP_CMD_COOLDOWN_SECONDS = float(os.environ.get("GROUP_CMD_COOLDOWN_SECONDS", "30"))
DAILY_REQUEST_LIMIT = int(os.environ.get("DAILY_REQUEST_LIMIT", "1500"))

TDCC_SNAPSHOT_URL = os.environ.get(
    "TDCC_SNAPSHOT_URL",
    "https://storage.googleapis.com/stockv-linebot-data/tdcc_latest.json",
)

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
HITPAY_MCP_URL = "https://hitpay-knowledge-mcp.vercel.app/api/mcp"
OPENROUTER_MODEL = "anthropic/claude-sonnet-4.6"
OPENROUTER_HAIKU_MODEL = "anthropic/claude-haiku-4.5"

# Supabase PostgreSQL connection string
DATABASE_URL = os.getenv("DATABASE_URL")

# Storage paths for markdown post files
_on_vercel = bool(os.getenv("VERCEL"))
_railway_volume = os.path.isdir("/data")

if _railway_volume:
    POSTS_DIR = "/data/posts"
elif _on_vercel:
    POSTS_DIR = "/tmp/posts"
else:
    POSTS_DIR = "posts"

# Google OAuth — create credentials at console.cloud.google.com
# Authorized redirect URI must be set to: {BASE_URL}/auth/callback
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-please-set-in-env")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").strip().rstrip("/")
ALLOWED_DOMAIN = "hit-pay.com"

# SME Growth Hub brand config
SME_BLOG_BASE_URL = os.getenv("SME_BLOG_BASE_URL", "https://smegrowthhub.com/blog")

# Secret key for the automation endpoint (GitHub Actions weekly-post trigger)
AUTOMATION_SECRET = os.getenv("AUTOMATION_SECRET", "")

# GA4 Data API — same property geo-tracker tracks for hitpayapp.com. No service
# account: uses the signed-in user's own Google OAuth token (see api.py auth flow),
# scoped to whatever GA4 access their @hit-pay.com account already has.
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "")

"""Load telephony credentials from environment."""
import os
from dotenv import load_dotenv

load_dotenv()

TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str  = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER", "")
TWILIO_WEBHOOK_BASE_URL: str = os.getenv("TWILIO_WEBHOOK_BASE_URL", "http://localhost:8000")

VONAGE_API_KEY: str = os.getenv("VONAGE_API_KEY", "")
VONAGE_API_SECRET: str = os.getenv("VONAGE_API_SECRET", "")
VONAGE_WEBHOOK_BASE_URL: str = os.getenv("VONAGE_WEBHOOK_BASE_URL", "http://localhost:8000")

def is_configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER)

def vonage_is_configured() -> bool:
    return bool(VONAGE_API_KEY and VONAGE_API_SECRET)

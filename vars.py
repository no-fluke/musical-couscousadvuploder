# MRBERLIN Bot — Configuration
import os
from os import environ

API_ID    = int(environ.get("API_ID", "21866171"))
API_HASH  = environ.get("API_HASH", "5788dba8f23fade5edda55948e985f06")
BOT_TOKEN = environ.get("BOT_TOKEN", "")

MONGO_URI = environ.get("MONGO_URI", "")  # MongoDB Atlas connection string

OWNER  = int(environ.get("OWNER", "1289248746"))
CREDIT = environ.get("CREDIT", "MRBERLIN")

# Legacy list kept so old code that still imports it doesn't crash at import time
# All real auth is now handled by db.py / MongoDB
TOTAL_USER  = os.environ.get('TOTAL_USERS', str(OWNER)).split(',')
TOTAL_USERS = [int(u) for u in TOTAL_USER]

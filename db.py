"""
db.py — Unified MongoDB database layer for MRBERLIN bot
Handles: user auth (with expiry), channel/group auth, topic whitelisting
Inspired by ITsGOLU db.py design, adapted for MRBERLIN's call signatures.
"""

import time
import certifi
import colorama
from colorama import Fore, Style
from datetime import datetime, timedelta
from typing import Optional, List

from pymongo import MongoClient, errors
from vars import MONGO_URI, OWNER

colorama.init()


class Database:
    def __init__(self, max_retries: int = 3, retry_delay: float = 2.0):
        self._print_banner()
        self.client = None
        self.db     = None
        # Collections
        self.users    = None   # authorized users (with expiry)
        self.chats    = None   # authorized channels / groups
        self._connect(max_retries, retry_delay)

    # ──────────────────────────────── internal ────────────────────────────────

    def _print_banner(self):
        print(f"\n{Fore.CYAN}{'='*52}")
        print(f"🤖  MRBERLIN Bot — Database Initialization")
        print(f"{'='*52}{Style.RESET_ALL}\n")

    def _connect(self, max_retries: int, retry_delay: float):
        for attempt in range(1, max_retries + 1):
            try:
                print(f"{Fore.YELLOW}⌛ Attempt {attempt}/{max_retries}: connecting to MongoDB…{Style.RESET_ALL}")
                self.client = MongoClient(
                    MONGO_URI,
                    serverSelectionTimeoutMS=20_000,
                    connectTimeoutMS=20_000,
                    socketTimeoutMS=30_000,
                    tlsCAFile=certifi.where(),
                    retryWrites=True,
                    retryReads=True,
                )
                self.client.server_info()          # raises if unreachable
                self.db    = self.client["mrberlin_db"]
                self.users = self.db["users"]
                self.chats = self.db["chats"]
                self._setup_indexes()
                print(f"{Fore.GREEN}✓ MongoDB connected!{Style.RESET_ALL}\n")
                return
            except errors.ServerSelectionTimeoutError as e:
                print(f"{Fore.RED}✕ Attempt {attempt} failed: {e}{Style.RESET_ALL}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    raise ConnectionError(f"MongoDB unreachable after {max_retries} attempts") from e
            except Exception as e:
                print(f"{Fore.RED}✕ Unexpected error: {e}{Style.RESET_ALL}")
                raise

    def _setup_indexes(self):
        try:
            self.users.create_index("user_id", unique=True, name="user_id_unique")
            self.users.create_index("expiry_date", name="user_expiry", expireAfterSeconds=0)
            self.chats.create_index("chat_id", unique=True, name="chat_id_unique")
            print(f"{Fore.GREEN}✓ Indexes ready{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.YELLOW}⚠ Index setup warning: {e}{Style.RESET_ALL}")

    # ──────────────────────────── USER AUTH ──────────────────────────────────

    def is_owner(self, user_id: int) -> bool:
        return user_id == OWNER

    def is_user_authorized(self, user_id: int, bot_username: str = None) -> bool:
        """Returns True for owner or any user with a valid (non-expired) subscription."""
        if self.is_owner(user_id):
            return True
        try:
            doc = self.users.find_one({"user_id": user_id})
            if not doc:
                return False
            expiry = doc.get("expiry_date")
            if not expiry:
                return False
            if isinstance(expiry, str):
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
            return expiry > datetime.now()
        except Exception as e:
            print(f"{Fore.RED}is_user_authorized error for {user_id}: {e}{Style.RESET_ALL}")
            return False

    def is_admin(self, user_id: int) -> bool:
        """Owner always counts as admin."""
        return self.is_owner(user_id)

    def add_user(self, user_id: int, name: str, days: int,
                 bot_username: str = None) -> tuple:
        """
        Add or renew a user.
        Returns (True, expiry_datetime) on success, (False, None) on failure.
        """
        try:
            expiry = datetime.now() + timedelta(days=days)
            result = self.users.update_one(
                {"user_id": user_id},
                {"$set": {
                    "user_id":      user_id,
                    "name":         name,
                    "expiry_date":  expiry,
                    "added_date":   datetime.now(),
                    "last_updated": datetime.now(),
                }},
                upsert=True,
            )
            if result.upserted_id or result.modified_count > 0:
                return True, expiry
            return False, None
        except Exception as e:
            print(f"{Fore.RED}add_user error for {user_id}: {e}{Style.RESET_ALL}")
            return False, None

    def remove_user(self, user_id: int, bot_username: str = None) -> bool:
        try:
            result = self.users.delete_one({"user_id": user_id})
            return result.deleted_count > 0
        except Exception as e:
            print(f"{Fore.RED}remove_user error for {user_id}: {e}{Style.RESET_ALL}")
            return False

    def list_users(self, bot_username: str = None) -> List[dict]:
        try:
            return list(self.users.find(
                {}, {"_id": 0, "user_id": 1, "name": 1, "expiry_date": 1}
            ))
        except Exception as e:
            print(f"{Fore.RED}list_users error: {e}{Style.RESET_ALL}")
            return []

    def get_user_expiry_info(self, user_id: int, bot_username: str = None) -> Optional[dict]:
        try:
            doc = self.users.find_one({"user_id": user_id})
            if not doc:
                return None
            expiry = doc.get("expiry_date")
            if not expiry:
                return None
            if isinstance(expiry, str):
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
            days_left = (expiry - datetime.now()).days
            return {
                "name":       doc.get("name", "Unknown"),
                "user_id":    user_id,
                "expiry_date": expiry.strftime("%d-%m-%Y"),
                "days_left":  days_left,
                "added_date": doc.get("added_date", "Unknown"),
                "is_active":  days_left > 0,
            }
        except Exception as e:
            print(f"{Fore.RED}get_user_expiry_info error for {user_id}: {e}{Style.RESET_ALL}")
            return None

    # ──────────────────── CHANNEL / GROUP / TOPIC AUTH ───────────────────────

    def add_chat(self, chat_id: int, title: str, chat_type: str, days: int = 36500) -> bool:
        """
        Authorize a channel or group.
        days=36500 means permanent (100 years).
        """
        try:
            expiry = None if days >= 36500 else datetime.now() + timedelta(days=days)
            result = self.chats.update_one(
                {"chat_id": chat_id},
                {"$set": {
                    "chat_id":        chat_id,
                    "title":          title,
                    "chat_type":      chat_type,
                    "expiry_date":    expiry,
                    "added_date":     datetime.now(),
                    "allowed_topics": [],   # [] means all topics allowed
                }},
                upsert=True,
            )
            return bool(result.upserted_id or result.modified_count > 0)
        except Exception as e:
            print(f"{Fore.RED}add_chat error for {chat_id}: {e}{Style.RESET_ALL}")
            return False

    def remove_chat(self, chat_id: int) -> bool:
        try:
            result = self.chats.delete_one({"chat_id": chat_id})
            return result.deleted_count > 0
        except Exception as e:
            print(f"{Fore.RED}remove_chat error for {chat_id}: {e}{Style.RESET_ALL}")
            return False

    def get_chat(self, chat_id: int) -> Optional[dict]:
        try:
            return self.chats.find_one({"chat_id": chat_id})
        except Exception as e:
            print(f"{Fore.RED}get_chat error for {chat_id}: {e}{Style.RESET_ALL}")
            return None

    def list_chats(self) -> List[dict]:
        try:
            return list(self.chats.find(
                {}, {"_id": 0, "chat_id": 1, "title": 1, "chat_type": 1,
                     "expiry_date": 1, "allowed_topics": 1}
            ))
        except Exception as e:
            print(f"{Fore.RED}list_chats error: {e}{Style.RESET_ALL}")
            return []

    def is_channel_authorized(self, chat_id: int, bot_username: str = None,
                               topic_id: int = None) -> bool:
        """
        Returns True if the chat is authorized AND (if topic filtering is active)
        the topic_id is in the allowed list.
        """
        try:
            doc = self.get_chat(chat_id)
            if not doc:
                return False
            # Check expiry
            expiry = doc.get("expiry_date")
            if expiry:
                if isinstance(expiry, str):
                    expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                if expiry < datetime.now():
                    return False
            # Topic filtering: only active when allowed_topics is non-empty
            allowed_topics = doc.get("allowed_topics", [])
            if allowed_topics and topic_id is not None:
                return topic_id in allowed_topics
            return True
        except Exception as e:
            print(f"{Fore.RED}is_channel_authorized error for {chat_id}: {e}{Style.RESET_ALL}")
            return False

    def add_topic(self, chat_id: int, topic_id: int) -> bool:
        """Whitelist a topic thread inside an already-authorized group."""
        try:
            result = self.chats.update_one(
                {"chat_id": chat_id},
                {"$addToSet": {"allowed_topics": topic_id}},
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"{Fore.RED}add_topic error: {e}{Style.RESET_ALL}")
            return False

    def remove_topic(self, chat_id: int, topic_id: int) -> bool:
        try:
            result = self.chats.update_one(
                {"chat_id": chat_id},
                {"$pull": {"allowed_topics": topic_id}},
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"{Fore.RED}remove_topic error: {e}{Style.RESET_ALL}")
            return False

    # ──────────────────────── LOG CHANNEL (compat) ───────────────────────────

    def get_log_channel(self, bot_username: str) -> Optional[int]:
        try:
            doc = self.db.bot_settings.find_one({"bot_username": bot_username})
            return doc["log_channel"] if doc and "log_channel" in doc else None
        except Exception as e:
            print(f"get_log_channel error: {e}")
            return None

    def set_log_channel(self, bot_username: str, channel_id: int) -> bool:
        try:
            self.db.bot_settings.update_one(
                {"bot_username": bot_username},
                {"$set": {"log_channel": channel_id}},
                upsert=True,
            )
            return True
        except Exception as e:
            print(f"set_log_channel error: {e}")
            return False

    # ──────────────────────────── cleanup ────────────────────────────────────

    def close(self):
        if self.client:
            self.client.close()
            print(f"{Fore.YELLOW}✓ MongoDB connection closed{Style.RESET_ALL}")

    def __enter__(self):  return self
    def __exit__(self, *_): self.close()


# ── Singleton ─────────────────────────────────────────────────────────────────
try:
    db = Database(max_retries=3, retry_delay=2)
except Exception as e:
    print(f"{Fore.RED}✕ Fatal: DB init failed — {e}{Style.RESET_ALL}")
    raise

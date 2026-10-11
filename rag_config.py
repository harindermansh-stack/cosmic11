"""Where the app finds its book database. One place, so the app and the builder never disagree.
To fall back to the old database temporarily, set environment variables:
    CHROMA_PATH=./chroma_db   COLLECTION_NAME=vedic_wisdom_v2
"""
import os

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db_v2")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "cosmic11_astrology_v1")

# ---- public app settings (beta) ----
PRICE_INR = int(os.getenv("PRICE_INR", "29"))            # shown upfront; free during beta
DAILY_PER_USER = int(os.getenv("DAILY_PER_USER", "3"))   # questions per person per day
DAILY_TOTAL = int(os.getenv("DAILY_TOTAL", "150"))       # all users together per day (protects the Groq budget)

# ---- 5-Year Guidance Report ----
REPORT_PRICES_INR = {"year1": int(os.getenv("REPORT1_PRICE_INR", "99")),   # 1-Year Monthly Guidance
                     "year5": int(os.getenv("REPORT5_PRICE_INR", "499"))}  # 5-Year Complete Report
REPORT_MODE = os.getenv("REPORT_MODE", "beta")            # "beta": invite code unlocks it free; "paid": after Razorpay is set up
REPORTS_PER_USER = int(os.getenv("REPORTS_PER_USER", "1"))  # per person per day
REPORTS_TOTAL = int(os.getenv("REPORTS_TOTAL", "10"))       # all users together per day

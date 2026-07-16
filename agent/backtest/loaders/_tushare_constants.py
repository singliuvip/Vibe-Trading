"""Shared Tushare constants used across multiple loader/provider modules.

Extracted to avoid duplicate definitions of TUSHARE_TOKEN_PLACEHOLDERS
across tushare.py, tushare_fundamentals.py, tushare_realtime.py, and
tushare_auction.py.
"""

TUSHARE_TOKEN_PLACEHOLDERS = {"", "your-tushare-token"}

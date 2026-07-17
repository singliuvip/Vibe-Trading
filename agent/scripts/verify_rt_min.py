"""Verify rt_min real-time minute bars end-to-end."""
import json
from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

# Test: 浦发银行 1MIN, last 5 bars
print("=== Testing get_realtime_minute_bars('600000.SH', '1MIN', max_rows=5) ===")
result = get_realtime_minute_bars.invoke({"codes": "600000.SH", "frequency": "1MIN", "max_rows": 5})
data = json.loads(result)

# Meta
meta = data.get('_meta', {})
print(f"\n--- Meta ---")
print(f"  data_source      : {meta.get('data_source')}")
print(f"  endpoint         : {meta.get('endpoint')}")
print(f"  frequency        : {meta.get('frequency')}")
print(f"  is_provisional   : {meta.get('is_provisional')}")
print(f"  market_session   : {meta.get('market_session')}")
print(f"  possibly_truncated: {meta.get('possibly_truncated')}")

# Data
quotes = data.get('data', {})
for code, bars in quotes.items():
    print(f"\n--- {code} ({len(bars)} bars) ---")
    for b in bars:
        print(f"  {b['timestamp']} | O:{b['open']:>8} H:{b['high']:>8} L:{b['low']:>8} C:{b['close']:>8} V:{b['volume']:>10}")

if 'error' in data:
    print(f"\nERROR: {data['error']}")

# Test 2: multi-code 5MIN
print("\n\n=== Testing get_realtime_minute_bars('600000.SH,000001.SZ', '5MIN', max_rows=3) ===")
result2 = get_realtime_minute_bars.invoke({"codes": "600000.SH,000001.SZ", "frequency": "5MIN", "max_rows": 3})
data2 = json.loads(result2)
meta2 = data2.get('_meta', {})
print(f"\n--- Meta ---")
print(f"  frequency        : {meta2.get('frequency')}")
print(f"  possibly_truncated: {meta2.get('possibly_truncated')}")
quotes2 = data2.get('data', {})
for code, bars in quotes2.items():
    print(f"\n--- {code} ({len(bars)} bars) ---")
    for b in bars:
        print(f"  {b['timestamp']} | O:{b['open']:>8} H:{b['high']:>8} L:{b['low']:>8} C:{b['close']:>8} V:{b['volume']:>10}")

if 'error' in data2:
    print(f"\nERROR: {data2['error']}")

print("\n=== ALL TESTS DONE ===")

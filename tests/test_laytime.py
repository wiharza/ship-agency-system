"""
Self-check unit test untuk Engine Kalkulasi Laytime (Python stdlib).
Dapat dijalankan langsung dengan: python tests/test_laytime.py
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from decimal import Decimal
from engine.laytime import LaytimeCalculator, parse_iso


def test_laytime_calculation():
    # Skenario: Muatan 50,000 MT, loading rate 10,000 MT/hari -> Laytime allowed = 5 hari (120 jam)
    # Demurrage $20,000/hari, Despatch $10,000/hari
    params = {
        "cargo_quantity": 50000,
        "loading_rate_per_day": 10000,
        "terms": "SHINC",
        "demurrage_rate_per_day": 20000,
        "despatch_rate_per_day": 10000,
        "nor_notice_hours": 6
    }
    calc = LaytimeCalculator(params)
    assert calc.laytime_allowed_seconds == 5 * 86400, "Laytime allowed harus tepat 5 hari"

    # Kasus 1: Selesai lebih cepat (Despatch)
    # NOR tendered: 2026-11-01 08:00 (notice period selesai 14:00)
    # Commenced: 2026-11-01 14:00
    # Completed: 2026-11-04 14:00 (Gross = 3 hari = 72 jam)
    # Deduksi hujan: 6 jam (0% counted) -> Net used = 66 jam (2 hari 18 jam)
    # Saved: 120 jam - 66 jam = 54 jam = 2.25 hari
    # Despatch amount: 2.25 * $10,000 = $22,500
    stoppages = [
        {
            "start": "2026-11-02 00:00",
            "end": "2026-11-02 06:00",
            "category": "RAIN",
            "percentage_counted": 0,
            "description": "Heavy rain, hatches closed"
        }
    ]
    res1 = calc.calculate(
        nor_tendered_str="2026-11-01 08:00",
        comm_str="2026-11-01 14:00",
        comp_str="2026-11-04 14:00",
        stoppages=stoppages
    )
    assert res1["result"]["claim_type"] == "DESPATCH", "Harus Despatch"
    assert res1["result"]["amount_usd"] == 22500.0, f"Expected $22,500, got {res1['result']['amount_usd']}"
    assert res1["laytime_used"]["deducted_formatted"] == "0d 06h 00m"

    # Kasus 2: Terlambat (Demurrage)
    # Gross = 6 hari (144 jam)
    # Tanpa jeda -> Over time = 24 jam = 1.0 hari
    # Demurrage amount: 1.0 * $20,000 = $20,000
    res2 = calc.calculate(
        nor_tendered_str="2026-11-01 08:00",
        comm_str="2026-11-01 14:00",
        comp_str="2026-11-07 14:00",
        stoppages=[]
    )
    assert res2["result"]["claim_type"] == "DEMURRAGE", "Harus Demurrage"
    assert res2["result"]["amount_usd"] == 20000.0, f"Expected $20,000, got {res2['result']['amount_usd']}"

    print("ALL TESTS PASSED: Laytime Engine deterministik dan presisi.")


if __name__ == "__main__":
    test_laytime_calculation()

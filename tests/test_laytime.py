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


def test_shex_excludes_sundays():
    # ADR-0016: SHEX mengecualikan hari Minggu (dihitung dari kalender).
    # Muatan 10.000 MT, rate 10.000 MT/hari -> allowed 1 hari.
    # NOR Jumat 2026-10-30 08:00, mulai 08:00, selesai Senin 2026-11-02
    # 08:00. Gross = 3 hari; Minggu 2026-11-01 ada di tengah.
    params = {
        "cargo_quantity": 10000,
        "loading_rate_per_day": 10000,
        "terms": "SHEX",
        "demurrage_rate_per_day": 20000,
        "despatch_rate_per_day": 10000,
        "nor_notice_hours": 6
    }
    calc = LaytimeCalculator(params)
    res = calc.calculate(
        nor_tendered_str="2026-10-30 08:00",
        comm_str="2026-10-30 08:00",
        comp_str="2026-11-02 08:00",
        stoppages=[]
    )
    assert res["terms"] == "SHEX"
    assert res["sundays_excluded"]["count"] == 1
    assert res["sundays_excluded"]["dates"] == ["2026-11-01"]
    assert res["sundays_excluded"]["seconds"] == 86400
    assert res["laytime_used"]["net_seconds"] == 2 * 86400
    assert res["result"]["claim_type"] == "DEMURRAGE"
    assert res["result"]["amount_usd"] == 20000.0, f"Expected $20,000, got {res['result']['amount_usd']}"

    # Pembanding SHINC pada pelayaran yang sama: Minggu tetap dihitung.
    params["terms"] = "SHINC"
    res_shinc = LaytimeCalculator(params).calculate(
        nor_tendered_str="2026-10-30 08:00",
        comm_str="2026-10-30 08:00",
        comp_str="2026-11-02 08:00",
        stoppages=[]
    )
    assert res_shinc["sundays_excluded"]["count"] == 0
    assert res_shinc["laytime_used"]["net_seconds"] == 3 * 86400
    assert res_shinc["result"]["amount_usd"] == 40000.0

    # Minggu sebagian: NOR Sabtu 2026-10-31 08:00, selesai Minggu
    # 2026-11-01 12:00. Gross 28 jam; Minggu terpotong 12 jam -> net 16 jam.
    params["terms"] = "SHEX"
    res_part = LaytimeCalculator(params).calculate(
        nor_tendered_str="2026-10-31 08:00",
        comm_str="2026-10-31 08:00",
        comp_str="2026-11-01 12:00",
        stoppages=[]
    )
    assert res_part["sundays_excluded"]["seconds"] == 12 * 3600
    assert res_part["laytime_used"]["net_seconds"] == 16 * 3600
    assert res_part["result"]["claim_type"] == "DESPATCH"
    assert res_part["result"]["amount_usd"] == 3333.33

    print("SHEX TESTS PASSED: Minggu dikecualikan (penuh & sebagian).")


def test_holiday_not_auto_excluded():
    # ADR-0016: hari libur TIDAK dikecualikan otomatis — sistem tidak punya
    # kalender libur. Senin 2026-11-02 08:00 -> Rabu 2026-11-04 08:00 (tidak
    # ada Minggu di rentang) dihitung penuh, sama seperti SHINC. Libur yang
    # jatuh di hari kerja harus dicatat sebagai jeda SOF manual.
    params = {
        "cargo_quantity": 10000,
        "loading_rate_per_day": 10000,
        "terms": "SHEX",
        "demurrage_rate_per_day": 20000,
        "despatch_rate_per_day": 10000,
        "nor_notice_hours": 6
    }
    res = LaytimeCalculator(params).calculate(
        nor_tendered_str="2026-11-02 08:00",
        comm_str="2026-11-02 08:00",
        comp_str="2026-11-04 08:00",
        stoppages=[]
    )
    assert res["sundays_excluded"]["count"] == 0
    assert res["laytime_used"]["net_seconds"] == 2 * 86400
    print("HOLIDAY TEST PASSED: libur hari kerja tetap dihitung (tanpa kalender).")


def test_unknown_terms_rejected():
    params = {
        "cargo_quantity": 10000,
        "loading_rate_per_day": 10000,
        "terms": "WWD",
        "demurrage_rate_per_day": 20000,
        "despatch_rate_per_day": 10000,
        "nor_notice_hours": 6
    }
    try:
        LaytimeCalculator(params)
    except ValueError as exc:
        assert "SHINC atau SHEX" in str(exc)
    else:
        raise AssertionError("terms WWD harus ditolak dengan ValueError")
    print("TERMS TEST PASSED: istilah laytime tak dikenal ditolak.")


if __name__ == "__main__":
    test_laytime_calculation()
    test_shex_excludes_sundays()
    test_holiday_not_auto_excluded()
    test_unknown_terms_rejected()

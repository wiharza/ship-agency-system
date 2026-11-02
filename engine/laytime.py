"""
Engine Kalkulasi Laytime & Demurrage/Despatch Deterministik (Python stdlib)
Merujuk pada ADR-0003, BIMCO Laytime Definitions 2013, dan GENCON Charter Party.
"""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json
from typing import Dict, List, Any, Optional


def parse_iso(ts_str: str) -> datetime:
    """Parse string ISO/RFC timestamp ke datetime."""
    # Menangani format 'YYYY-MM-DD HH:MM' atau 'YYYY-MM-DDTHH:MM:SS'
    clean_str = ts_str.replace("T", " ")
    try:
        if len(clean_str) == 16:
            return datetime.strptime(clean_str, "%Y-%m-%d %H:%M")
        elif len(clean_str) >= 19:
            return datetime.strptime(clean_str[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    # Satu pesan yang konsisten untuk semua kegagalan parse (ADR-0016):
    # handler laytime membedakan error waktu dari error lain lewat frasa
    # "Format timestamp" — dulu strptime melempar pesannya sendiri
    # ("time data ... does not match format ...") sehingga pembedaan itu
    # gagal dan Test 23 menerima pesan mentah.
    raise ValueError(f"Format timestamp tidak dikenali: {ts_str}")


class LaytimeCalculator:
    def __init__(self, cp_params: Dict[str, Any]):
        """
        cp_params:
          - cargo_quantity: float/Decimal (MT)
          - loading_rate_per_day: float/Decimal (MT/day)
          - terms: "SHINC" | "SHEX" — SHEX mengecualikan hari Minggu
            (dihitung dari kalender, ADR-0016); hari LIBUR tidak
            dikecualikan otomatis karena sistem tidak punya kalender
            libur — libur dicatat sebagai jeda SOF (kategori OTHER,
            remarks "Libur: ...") agar ikut terdeduksi.
          - demurrage_rate_per_day: float/Decimal (USD/day)
          - despatch_rate_per_day: float/Decimal (USD/day, usually 50% demurrage)
          - nor_notice_hours: int (jam tunggu setelah NOR sebelum laytime mulai, misal 6 atau 12 jam)
        """
        self.cargo_qty = Decimal(str(cp_params["cargo_quantity"]))
        self.rate_per_day = Decimal(str(cp_params["loading_rate_per_day"]))
        self.terms = cp_params.get("terms", "SHINC").upper()
        if self.terms not in ("SHINC", "SHEX"):
            # Kolom DB sudah CHECK (SHINC, SHEX); ini pertahanan kedua
            # bila parameter datang dari luar DB.
            raise ValueError(f"Istilah laytime tidak dikenal: {self.terms} (harus SHINC atau SHEX)")
        self.demurrage_rate = Decimal(str(cp_params["demurrage_rate_per_day"]))
        self.despatch_rate = Decimal(str(cp_params.get(
            "despatch_rate_per_day", self.demurrage_rate / Decimal("2")
        )))
        self.notice_hours = int(cp_params.get("nor_notice_hours", 0))

        # Laytime allowed dalam hari (desimal) dan detik
        self.laytime_allowed_days = (self.cargo_qty / self.rate_per_day)
        self.laytime_allowed_seconds = int((self.laytime_allowed_days * Decimal("86400")).to_integral_value(ROUND_HALF_UP))

    def calculate(self, nor_tendered_str: str, comm_str: str, comp_str: str, stoppages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Kalkulasi laytime used vs allowed berdasarkan log SOF.
        stoppages: list of {
            "start": "YYYY-MM-DD HH:MM",
            "end": "YYYY-MM-DD HH:MM",
            "category": "RAIN" | "MEAL" | "BREAKDOWN" | etc.,
            "percentage_counted": 0.0 s.d. 100.0,
            "description": str
        }
        """
        nor_time = parse_iso(nor_tendered_str)
        comm_time = parse_iso(comm_str)
        comp_time = parse_iso(comp_str)

        # Commencement of laytime: biasanya NOR + notice_hours ATAU saat kargo dimulai (mana yang lebih awal)
        laytime_start_standard = nor_time + timedelta(hours=self.notice_hours)
        if comm_time < laytime_start_standard:
            actual_start = comm_time  # jika charterer mulai muat lebih awal, waktu mulai dihitung
        else:
            actual_start = laytime_start_standard

        total_gross_seconds = int((comp_time - actual_start).total_seconds())
        if total_gross_seconds < 0:
            total_gross_seconds = 0

        # Hitung deduksi jeda (stoppages) yang terjadi antara actual_start dan comp_time
        deducted_seconds = 0
        stoppage_details = []

        for stp in stoppages:
            s_start = max(parse_iso(stp["start"]), actual_start)
            s_end = min(parse_iso(stp["end"]), comp_time)

            if s_end > s_start:
                duration_sec = int((s_end - s_start).total_seconds())
                pct = Decimal(str(stp.get("percentage_counted", 0))) / Decimal("100")
                non_counted_pct = Decimal("1") - pct
                deduction = int((Decimal(duration_sec) * non_counted_pct).to_integral_value(ROUND_HALF_UP))
                deducted_seconds += deduction

                stoppage_details.append({
                    "category": stp.get("category", "OTHER"),
                    "start": stp["start"],
                    "end": stp["end"],
                    "duration_minutes": duration_sec // 60,
                    "deducted_minutes": deduction // 60,
                    "description": stp.get("description", "")
                })

        # SHEX (ADR-0016): hari Minggu tidak dihitung. Minggu ditentukan
        # dari kalender (weekday == 6) — tidak butuh data eksternal.
        # Hari LIBUR tidak dikecualikan otomatis: sistem tidak punya
        # kalender libur (libur berbeda per pelabuhan/negara). Libur dicatat
        # sebagai jeda SOF manual (kategori OTHER + remarks "Libur: ...")
        # dan ikut terdeduksi lewat mekanisme stoppage di atas.
        sundays_excluded_seconds = 0
        sundays_excluded_dates = []
        if self.terms == "SHEX":
            day = actual_start.date()
            last_day = comp_time.date()
            while day <= last_day:
                if day.weekday() == 6:  # Minggu
                    s_start = max(datetime(day.year, day.month, day.day), actual_start)
                    s_end = min(datetime(day.year, day.month, day.day) + timedelta(days=1), comp_time)
                    if s_end > s_start:
                        duration_sec = int((s_end - s_start).total_seconds())
                        sundays_excluded_seconds += duration_sec
                        sundays_excluded_dates.append(day.isoformat())
                        stoppage_details.append({
                            "category": "SHEX_SUNDAY",
                            "start": s_start.strftime("%Y-%m-%d %H:%M"),
                            "end": s_end.strftime("%Y-%m-%d %H:%M"),
                            "duration_minutes": duration_sec // 60,
                            "deducted_minutes": duration_sec // 60,
                            "description": "Hari Minggu dikecualikan (SHEX)"
                        })
                day += timedelta(days=1)
            deducted_seconds += sundays_excluded_seconds

        net_used_seconds = max(0, total_gross_seconds - deducted_seconds)

        # Selisih laytime
        diff_seconds = net_used_seconds - self.laytime_allowed_seconds

        is_demurrage = diff_seconds > 0
        diff_days = Decimal(abs(diff_seconds)) / Decimal("86400")

        if is_demurrage:
            claim_type = "DEMURRAGE"
            amount_usd = (diff_days * self.demurrage_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            claim_type = "DESPATCH"
            amount_usd = (diff_days * self.despatch_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        def sec_to_dhms(sec: int) -> str:
            d = sec // 86400
            rem = sec % 86400
            h = rem // 3600
            rem = rem % 3600
            m = rem // 60
            return f"{d}d {h:02d}h {m:02d}m"

        return {
            "terms": self.terms,
            "sundays_excluded": {
                "count": len(sundays_excluded_dates),
                "seconds": sundays_excluded_seconds,
                "dates": sundays_excluded_dates
            },
            "laytime_allowed": {
                "days_decimal": float(self.laytime_allowed_days.quantize(Decimal("0.0001"))),
                "formatted": sec_to_dhms(self.laytime_allowed_seconds),
                "seconds": self.laytime_allowed_seconds
            },
            "laytime_used": {
                "gross_formatted": sec_to_dhms(total_gross_seconds),
                "deducted_formatted": sec_to_dhms(deducted_seconds),
                "sundays_excluded_formatted": sec_to_dhms(sundays_excluded_seconds),
                "net_formatted": sec_to_dhms(net_used_seconds),
                "net_seconds": net_used_seconds
            },
            "result": {
                "claim_type": claim_type,
                "difference_formatted": sec_to_dhms(abs(diff_seconds)),
                "difference_days": float(diff_days.quantize(Decimal("0.0004"))),
                "amount_usd": float(amount_usd),
                "is_demurrage": is_demurrage
            },
            "commenced_laytime": actual_start.strftime("%Y-%m-%d %H:%M"),
            "completed_laytime": comp_time.strftime("%Y-%m-%d %H:%M"),
            "stoppage_details": stoppage_details
        }

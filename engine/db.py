"""
Database & Domain Repository Menggunakan SQLite (Python stdlib sqlite3).
Memenuhi kebutuhan PRD, ADR-0003, dan Putusan Q18, Q20, Q23, Q25, Q26, Q27, Q30, Q32.
"""

import sqlite3
import hashlib
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

DB_FILE = os.path.join(os.path.dirname(__file__), "..", "agency.db")


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    with conn:
        # Master Pelabuhan & Profil
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_ports (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            province TEXT NOT NULL,
            unlocode TEXT NOT NULL,
            inaportnet_level TEXT NOT NULL, -- Kelas Pelabuhan
            vts_channel TEXT,
            max_draft REAL,
            notes TEXT
        )
        """)

        # Master Terminal / Dermaga / Tersus
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_terminals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_code TEXT NOT NULL,
            name TEXT NOT NULL,
            terminal_type TEXT DEFAULT 'UMUM', -- UMUM, PETIKEMAS, TERSUS, TUKS, CURAH
            max_draft REAL,
            FOREIGN KEY (port_code) REFERENCES master_ports (code),
            UNIQUE(port_code, name)
        )
        """)

        # Master Kapal
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_vessels (
            imo TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            flag TEXT NOT NULL,
            vessel_type TEXT NOT NULL,
            call_sign TEXT,
            grt REAL NOT NULL,
            dwt REAL NOT NULL,
            loa REAL NOT NULL,
            max_draft REAL NOT NULL
        )
        """)

        # Master Cabang Agen
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_branches (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            port_code TEXT NOT NULL,
            address TEXT,
            phone TEXT,
            FOREIGN KEY (port_code) REFERENCES master_ports (code)
        )
        """)

        # Master Principal
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_principals (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT NOT NULL,
            email TEXT NOT NULL,
            contact_person TEXT
        )
        """)

        # Port Call (Agregat Root)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS port_calls (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            vessel_imo TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            vessel_category TEXT NOT NULL CHECK(vessel_category IN ('FOREIGN', 'DOMESTIC')),
            port_code TEXT NOT NULL,
            terminal_name TEXT,
            status TEXT NOT NULL CHECK(status IN ('PRE_ARRIVAL', 'ARRIVED', 'BERTHED', 'DEPARTED', 'CLEARANCE_CLOSED', 'CLOSED')),
            eta TEXT NOT NULL,
            ata TEXT,
            etd TEXT,
            atd TEXT,
            cargo_name TEXT,
            cargo_tonnage REAL,
            spb_issued_at TEXT,
            spb_expiry_at TEXT, -- Wajib 24 jam pasca spb_issued_at
            ops_closed_by TEXT,
            ops_closed_at TEXT,
            finance_closed_by TEXT,
            finance_closed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (branch_id) REFERENCES master_branches (id),
            FOREIGN KEY (vessel_imo) REFERENCES master_vessels (imo),
            FOREIGN KEY (principal_id) REFERENCES master_principals (id),
            FOREIGN KEY (port_code) REFERENCES master_ports (code)
        )
        """)

        # Parameter Charter Party Port Call
        conn.execute("""
        CREATE TABLE IF NOT EXISTS port_call_charter_parties (
            port_call_id TEXT PRIMARY KEY,
            charterer_name TEXT NOT NULL,
            loading_rate_per_day REAL NOT NULL,
            laytime_terms TEXT NOT NULL CHECK(laytime_terms IN ('SHINC', 'SHEX')),
            demurrage_usd_per_day REAL NOT NULL,
            despatch_usd_per_day REAL NOT NULL,
            nor_notice_hours INTEGER DEFAULT 6,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
        )
        """)

        # Dokumen Perizinan CIGP & Inaportnet
        conn.execute("""
        CREATE TABLE IF NOT EXISTS clearances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT NOT NULL,
            agency TEXT NOT NULL CHECK(agency IN ('INAPORTNET_SIMPADU', 'INAPORTNET_SPS', 'BEA_CUKAI', 'IMIGRASI', 'KARANTINA_KESEHATAN', 'BARANTIN', 'BUP_PELINDO')),
            doc_type TEXT NOT NULL,
            doc_number TEXT,
            submission_time TEXT,
            approval_time TEXT,
            expiry_time TEXT,
            status TEXT NOT NULL CHECK(status IN ('PENDING', 'SUBMITTED', 'APPROVED', 'REJECTED', 'EXPIRED')),
            file_name TEXT,
            file_sha256 TEXT,
            remarks TEXT,
            submitted_by TEXT,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
        )
        """)

        # Tonggak Waktu Lapangan (Milestones)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT NOT NULL,
            milestone_code TEXT NOT NULL,
            event_time TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            remarks TEXT,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
        )
        """)

        # Jeda Operasi SOF (Stoppages)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sof_stoppages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('RAIN', 'WEATHER_OTHER', 'MEAL_BREAK', 'SHIFT_CHANGE', 'CRANE_BREAKDOWN_SHORE', 'WINCH_BREAKDOWN_SHIP', 'CARGO_WAITING', 'SHIFTING_BERTH', 'OTHER')),
            percentage_counted REAL NOT NULL DEFAULT 0.0,
            remarks TEXT,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
        )
        """)

        # Pos Rekonsiliasi Finansial PDA vs FDA
        conn.execute("""
        CREATE TABLE IF NOT EXISTS disbursements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('PNBP_KEMENHUB', 'BUP_PELINDO', 'CIGP_INSTANSI', 'HUSBANDRY', 'AGENCY_FEE')),
            item_name TEXT NOT NULL,
            pda_estimate REAL NOT NULL DEFAULT 0.0,
            fda_actual REAL NOT NULL DEFAULT 0.0,
            simponi_billing TEXT,
            ntpn TEXT,
            invoice_number TEXT,
            voucher_sha256 TEXT,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
        )
        """)

        # Sengketa Biaya FDA (Dispute Tracking)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS fda_disputes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT NOT NULL,
            disbursement_id INTEGER NOT NULL,
            disputed_amount REAL NOT NULL,
            principal_reason TEXT NOT NULL,
            deadline_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('OPEN', 'UNDER_REVIEW', 'ACCEPTED_REVISED', 'REJECTED_FINAL')),
            resolution_notes TEXT,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id),
            FOREIGN KEY (disbursement_id) REFERENCES disbursements (id)
        )
        """)

        # Audit Trail (Immutable Log 10 Tahun)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT,
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            timestamp TEXT NOT NULL
        )
        """)

    conn.close()


def seed_sample_data():
    """Mengisi data awal master dan port call pengujian jika kosong."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM master_terminals")
    if cursor.fetchone()[0] == 0:
        with conn:
            conn.execute("""
            INSERT OR IGNORE INTO master_ports VALUES 
            ('IDTPP', 'Tanjung Priok', 'DKI Jakarta', 'IDTPP', 'Kelas Utama', 'VHF Ch 12/16', 14.0, 'Pelabuhan Utama Nasional'),
            ('IDTPS', 'Tanjung Perak', 'Jawa Timur', 'IDTPS', 'Kelas Utama', 'VHF Ch 14/16', 12.5, 'Gerbang Logistik Timur'),
            ('IDKOL', 'Kolonodale', 'Sulawesi Tengah', 'IDKOL', 'Kelas III (KUPP)', 'VHF Ch 16', 11.0, 'Pelabuhan Tambang Nikel & Curah'),
            ('IDBTM', 'Batu Ampar', 'Kepulauan Riau', 'IDBTM', 'Kelas I', 'VHF Ch 16/71', 13.0, 'Kawasan Perbatasan & FTZ')
            """)

            conn.execute("""
            INSERT INTO master_terminals (port_code, name, terminal_type, max_draft) VALUES
            ('IDTPP', 'JICT (Jakarta Int. Container Terminal)', 'PETIKEMAS', 14.0),
            ('IDTPP', 'TPK Koja', 'PETIKEMAS', 13.5),
            ('IDTPP', 'NPCT1 (New Priok Container Terminal 1)', 'PETIKEMAS', 16.0),
            ('IDTPP', 'Dermaga 101-106 (Terminal Jamrud/Umum)', 'UMUM', 10.5),
            ('IDTPP', 'Terminal Curah Kering Bogasari', 'CURAH', 12.0),
            ('IDTPS', 'Terminal Petikemas Surabaya (TPS)', 'PETIKEMAS', 12.5),
            ('IDTPS', 'Terminal Teluk Lamong (TTL)', 'PETIKEMAS', 14.0),
            ('IDTPS', 'Terminal Nilam & Mirah', 'UMUM', 9.5),
            ('IDTPS', 'Terminal Jamrud Barat/Utara', 'CURAH', 10.5),
            ('IDKOL', 'Pelabuhan Umum KUPP Kolonodale', 'UMUM', 9.0),
            ('IDKOL', 'Tersus PT COR Industri Indonesia', 'TERSUS', 11.5),
            ('IDKOL', 'TUKS PT Bumanik', 'TUKS', 10.0),
            ('IDBTM', 'Dermaga Utara Batu Ampar', 'UMUM', 12.0),
            ('IDBTM', 'Terminal Petikemas Batu Ampar', 'PETIKEMAS', 13.0),
            ('IDBTM', 'Tersus Kabil Integrated Port', 'TERSUS', 12.5)
            """)

    cursor.execute("SELECT COUNT(*) FROM master_ports")
    if cursor.fetchone()[0] == 0:
        with conn:
            # Ports
            conn.execute("""
            INSERT INTO master_ports VALUES 
            ('IDTPP', 'Tanjung Priok', 'DKI Jakarta', 'IDTPP', 'Kelas Utama', 'VHF Ch 12/16', 14.0, 'Pelabuhan Utama Nasional'),
            ('IDTPS', 'Tanjung Perak', 'Jawa Timur', 'IDTPS', 'Kelas Utama', 'VHF Ch 14/16', 12.5, 'Gerbang Logistik Timur'),
            ('IDKOL', 'Kolonodale', 'Sulawesi Tengah', 'IDKOL', 'Kelas III (KUPP)', 'VHF Ch 16', 11.0, 'Pelabuhan Tambang Nikel & Curah'),
            ('IDBTM', 'Batu Ampar', 'Kepulauan Riau', 'IDBTM', 'Kelas I', 'VHF Ch 16/71', 13.0, 'Kawasan Perbatasan & FTZ')
            """)

            # Pre-determined Terminals / Jetties
            conn.execute("""
            INSERT INTO master_terminals (port_code, name, terminal_type, max_draft) VALUES
            ('IDTPP', 'JICT (Jakarta Int. Container Terminal)', 'PETIKEMAS', 14.0),
            ('IDTPP', 'TPK Koja', 'PETIKEMAS', 13.5),
            ('IDTPP', 'NPCT1 (New Priok Container Terminal 1)', 'PETIKEMAS', 16.0),
            ('IDTPP', 'Dermaga 101-106 (Terminal Jamrud/Umum)', 'UMUM', 10.5),
            ('IDTPP', 'Terminal Curah Kering Bogasari', 'CURAH', 12.0),
            ('IDTPS', 'Terminal Petikemas Surabaya (TPS)', 'PETIKEMAS', 12.5),
            ('IDTPS', 'Terminal Teluk Lamong (TTL)', 'PETIKEMAS', 14.0),
            ('IDTPS', 'Terminal Nilam & Mirah', 'UMUM', 9.5),
            ('IDTPS', 'Terminal Jamrud Barat/Utara', 'CURAH', 10.5),
            ('IDKOL', 'Pelabuhan Umum KUPP Kolonodale', 'UMUM', 9.0),
            ('IDKOL', 'Tersus PT COR Industri Indonesia', 'TERSUS', 11.5),
            ('IDKOL', 'TUKS PT Bumanik', 'TUKS', 10.0),
            ('IDBTM', 'Dermaga Utara Batu Ampar', 'UMUM', 12.0),
            ('IDBTM', 'Terminal Petikemas Batu Ampar', 'PETIKEMAS', 13.0),
            ('IDBTM', 'Tersus Kabil Integrated Port', 'TERSUS', 12.5)
            """)

            # Branches
            conn.execute("""
            INSERT INTO master_branches VALUES 
            ('BR-JKT', 'Cabang Jakarta Raya', 'IDTPP', 'Jl. Pelabuhan Raya No. 10, Tj. Priok', '+62-21-439001'),
            ('BR-SUB', 'Cabang Surabaya', 'IDTPS', 'Jl. Perak Timur No. 44, Surabaya', '+62-31-329112')
            """)

            # Principals
            conn.execute("""
            INSERT INTO master_principals VALUES 
            ('PR-OCN', 'Ocean Shipping Line Ltd.', 'Singapura', 'operations@oceanshipping.sg', 'Capt. Wong'),
            ('PR-PAC', 'Pacific Maritime Corp', 'Panama', 'agency@pacificmaritime.pa', 'Mr. David Miller')
            """)

            # Vessels
            conn.execute("""
            INSERT INTO master_vessels VALUES 
            ('9876543', 'MV. SAMUDRA JAYA', 'Indonesia', 'Bulk Carrier', 'YBXX', 32500, 55000, 189.9, 12.8),
            ('9431289', 'MV. PACIFIC GLORY', 'Panama', 'Container Ship', '3EMA9', 41000, 52000, 222.0, 13.2)
            """)

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            eta_sample = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

            # Sample Active Port Call 1 (Asing di Tj Priok)
            conn.execute("""
            INSERT INTO port_calls VALUES (
                'PC-2026-JKT-0001', 'BR-JKT', '9431289', 'PR-PAC', 'FOREIGN', 'IDTPP', 'Terminal Nusantara 2',
                'PRE_ARRIVAL', ?, NULL, NULL, NULL, 'Peti Kemas Campuran', 25000, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?
            )
            """, (eta_sample, now_str, now_str))

            # Charter Party Params
            conn.execute("""
            INSERT INTO port_call_charter_parties VALUES (
                'PC-2026-JKT-0001', 'Global Chartering Ltd.', 8000, 'SHINC', 18000, 9000, 6
            )
            """)

            # Clearances awal
            conn.execute("""
            INSERT INTO clearances (port_call_id, agency, doc_type, doc_number, submission_time, status, file_name, file_sha256, remarks, submitted_by) VALUES
            ('PC-2026-JKT-0001', 'INAPORTNET_SIMPADU', 'PPKKA', 'PPKKA-2026-10-881', ?, 'APPROVED', 'ppkka_mv_pacific.pdf', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 'Izin trayek luar negeri disetujui', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'BEA_CUKAI', 'BC 1.0 (RKSP)', 'RKSP-002198', ?, 'APPROVED', 'rksp_002198.pdf', '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08', 'Disetujui sistem CEISA', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'BEA_CUKAI', 'BC 1.1 Masuk', 'BC11-IN-7712', ?, 'APPROVED', 'bc11_in.pdf', '4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a', 'Pos pendaftaran terbit', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'KARANTINA_KESEHATAN', 'MDH & Free Pratique', 'MDH-JKT-992', ?, 'APPROVED', 'free_pratique.pdf', 'ef2d127de37b942baad06145e54b0c619a1f22327b2ebbcfbec78f5564afe39d', 'SSCEC valid hingga 2027', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'INAPORTNET_SPS', 'SPB (Surat Persetujuan Berlayar)', NULL, NULL, 'PENDING', NULL, NULL, 'Belum diterbitkan (kapal belum sandar)', NULL)
            """, (now_str, now_str, now_str, now_str))

            # Disbursements awal
            conn.execute("""
            INSERT INTO disbursements (port_call_id, category, item_name, pda_estimate, fda_actual, simponi_billing, ntpn) VALUES
            ('PC-2026-JKT-0001', 'PNBP_KEMENHUB', 'Uang Rambu & Labuh', 15000000, 15000000, 'BILL-99120', '9812A9B12874'),
            ('PC-2026-JKT-0001', 'BUP_PELINDO', 'Jasa Tambat, Pandu & Tunda', 55000000, 58200000, NULL, NULL),
            ('PC-2026-JKT-0001', 'CIGP_INSTANSI', 'Clearance Karantina & Imigrasi', 6000000, 6000000, NULL, NULL),
            ('PC-2026-JKT-0001', 'HUSBANDRY', 'Pasokan Air Tawar 150 Ton', 10500000, 10500000, NULL, NULL),
            ('PC-2026-JKT-0001', 'AGENCY_FEE', 'Official Agency Fee', 25000000, 25000000, NULL, NULL)
            """)

    conn.close()


def calculate_file_sha256(content_bytes: bytes) -> str:
    """Menghasilkan hash SHA-256 untuk pembuktian integritas berkas (Putusan Q18)."""
    return hashlib.sha256(content_bytes).hexdigest()

import os
import re
import secrets
from fastapi import FastAPI, Query, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
import sqlite3
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Literal

# The main app object. All routes (URLs) are defined on this.
app = FastAPI(title="CareNear API")

# CORS lets our HTML page (opened separately in the browser) call this backend.
# Without it, the browser blocks the requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for learning; restrict this before going live
    allow_methods=["*"],
    allow_headers=["*"],
)

# If a request has invalid data (for example the value NaN), FastAPI normally echoes the bad
# value back in its error message. Some values can't be turned into JSON, which caused a
# 500 error. This handler sends back only where the problem is and what it is.
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


DB_FILE = "hospitals.db"


def get_connection():
    # Every time we talk to the database, we need a connection.
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # lets us read columns by name
    return conn


def setup_database():
    conn = get_connection()
    cursor = conn.cursor()

    # ---- Table 1: hospitals ----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hospitals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            area TEXT,
            address TEXT,
            phone TEXT,
            specialties TEXT
        )
    """)

    # ---- Location columns (added later, so we also add them to older databases) ----
    cursor.execute("PRAGMA table_info(hospitals)")
    existing_columns = [c["name"] for c in cursor.fetchall()]
    for column in ("latitude", "longitude"):
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE hospitals ADD COLUMN {column} REAL")

    # ---- Table 2: beds ----
    # One row per hospital, linked to the hospitals table through hospital_id.
    # last_updated stays empty (NULL) until someone updates the beds for the first time.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS beds (
            hospital_id INTEGER PRIMARY KEY,
            general_available INTEGER DEFAULT 0,
            icu_available INTEGER DEFAULT 0,
            ventilator_available INTEGER DEFAULT 0,
            last_updated TEXT,
            FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
        )
    """)

    # ---- Table 3: doctors ----
    # Many doctors can belong to one hospital (linked by hospital_id).
    # opd_days is stored as text like "Mon,Wed,Fri"; times are "HH:MM" in 24-hour format.
    # on_leave is 0 (working) or 1 (on leave), because SQLite has no true/false type.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hospital_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            specialty TEXT NOT NULL,
            opd_days TEXT NOT NULL,
            opd_start TEXT NOT NULL,
            opd_end TEXT NOT NULL,
            on_leave INTEGER DEFAULT 0,
            FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
        )
    """)

    # ---- Table 4: appointments (requests from patients) ----
    # status is 'pending' until hospital staff accept or decline it.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            hospital_id INTEGER NOT NULL,
            patient_name TEXT NOT NULL,
            patient_phone TEXT NOT NULL,
            preferred_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY (doctor_id) REFERENCES doctors(id),
            FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM hospitals")
    if cursor.fetchone()[0] == 0:
        sample_hospitals = [
            ("Saugor District Hospital", "Civil Lines", "Civil Lines Rd, Sagar, MP", "0000-000001", "General,Emergency,Maternity"),
            ("BMC Cardiology & Multispecialty", "Makronia", "Makronia Rd, Sagar, MP", "0000-000002", "Cardiology,General,ICU"),
            ("Sagar Orthopaedic Centre", "Tili Rd", "Tili Rd, Sagar, MP", "0000-000003", "Orthopaedics,Physiotherapy"),
            ("City Maternity & Child Care", "Bhagwanganj", "Bhagwanganj Chowk, Sagar, MP", "0000-000004", "Maternity,Paediatrics"),
            ("Sagar Eye & ENT Hospital", "Katra Bazar", "Katra Bazar, Sagar, MP", "0000-000005", "Ophthalmology,ENT"),
            ("New Life General Hospital", "Shastri Ward", "Shastri Ward, Sagar, MP", "0000-000006", "General,Emergency,Surgery"),
        ]
        cursor.executemany(
            "INSERT INTO hospitals (name, area, address, phone, specialties) VALUES (?, ?, ?, ?, ?)",
            sample_hospitals,
        )

    # Rough positions for the made-up SAMPLE hospitals, around Sagar city.
    # They are only filled in when a hospital has no location yet, so real data is never overwritten.
    sample_locations = {
        "Saugor District Hospital": (23.8450, 78.7420),
        "BMC Cardiology & Multispecialty": (23.8600, 78.7650),
        "Sagar Orthopaedic Centre": (23.8300, 78.7300),
        "City Maternity & Child Care": (23.8330, 78.7450),
        "Sagar Eye & ENT Hospital": (23.8370, 78.7360),
        "New Life General Hospital": (23.8420, 78.7330),
    }
    for hospital_name, (lat, lng) in sample_locations.items():
        cursor.execute(
            "UPDATE hospitals SET latitude = ?, longitude = ? WHERE name = ? AND latitude IS NULL AND longitude IS NULL",
            (lat, lng, hospital_name),
        )

    # Create an empty beds row for any hospital that doesn't have one yet.
    # Safe to run on an existing database: nothing gets deleted.
    cursor.execute("""
        INSERT INTO beds (hospital_id)
        SELECT id FROM hospitals WHERE id NOT IN (SELECT hospital_id FROM beds)
    """)

    # Add SAMPLE doctors (made-up names) only if the doctors table is empty.
    # We look up each hospital by name, so this works even if the ids are different.
    cursor.execute("SELECT COUNT(*) FROM doctors")
    if cursor.fetchone()[0] == 0:
        sample_doctors = [
            # (hospital name, doctor name, specialty, OPD days, start, end)
            ("Saugor District Hospital", "Dr. Asha Verma", "General", "Mon,Tue,Wed,Thu,Fri,Sat", "09:00", "13:00"),
            ("Saugor District Hospital", "Dr. Rakesh Jain", "Maternity", "Mon,Wed,Fri", "10:00", "14:00"),
            ("BMC Cardiology & Multispecialty", "Dr. Neha Kapoor", "Cardiology", "Mon,Tue,Thu,Sat", "11:00", "16:00"),
            ("BMC Cardiology & Multispecialty", "Dr. Sameer Khan", "General", "Mon,Tue,Wed,Thu,Fri", "09:00", "12:00"),
            ("Sagar Orthopaedic Centre", "Dr. Vikram Rao", "Orthopaedics", "Tue,Thu,Sat", "10:00", "15:00"),
            ("City Maternity & Child Care", "Dr. Pooja Mishra", "Paediatrics", "Mon,Tue,Wed,Thu,Fri", "10:00", "13:00"),
            ("Sagar Eye & ENT Hospital", "Dr. Anil Soni", "Ophthalmology", "Mon,Wed,Fri", "09:30", "13:30"),
            ("New Life General Hospital", "Dr. Kavita Singh", "Surgery", "Tue,Thu,Sat", "12:00", "17:00"),
        ]
        for hospital_name, doc_name, specialty, days, start, end in sample_doctors:
            cursor.execute("SELECT id FROM hospitals WHERE name = ?", (hospital_name,))
            found = cursor.fetchone()
            if found:
                cursor.execute(
                    "INSERT INTO doctors (hospital_id, name, specialty, opd_days, opd_start, opd_end) VALUES (?, ?, ?, ?, ?, ?)",
                    (found["id"], doc_name, specialty, days, start, end),
                )

    conn.commit()
    conn.close()


setup_database()


# =====================  ADMIN KEY  =====================
# Only people who know this secret key can change data (beds, doctors).
# Reading data stays public.
#
# Where the key comes from (in this order):
#   1. An environment variable named ADMIN_KEY (the standard way when you deploy)
#   2. A file called admin_key.txt next to main.py
#   3. If neither exists, we generate a random key and save it in admin_key.txt
KEY_FILE = "admin_key.txt"


def load_admin_key():
    env_key = os.environ.get("ADMIN_KEY", "").strip()
    if env_key:
        return env_key

    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            saved = f.read().strip()
        if saved:
            return saved

    new_key = secrets.token_urlsafe(16)   # random, hard to guess
    with open(KEY_FILE, "w") as f:
        f.write(new_key)
    print(f"\nA new admin key was created and saved in {KEY_FILE}. Keep that file private!\n")
    return new_key


ADMIN_KEY = load_admin_key()


def require_admin(x_admin_key: Optional[str] = Header(None)):
    """Runs before a protected endpoint. The caller must send the key in the X-Admin-Key header."""
    # compare_digest compares in constant time, so attackers can't guess the key
    # letter by letter from tiny timing differences. We compare bytes so odd characters can't crash it.
    if not x_admin_key or not secrets.compare_digest(x_admin_key.encode(), ADMIN_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")


# Helper: turns a database row into a clean JSON dictionary,
# so we don't repeat the same code in every endpoint.
def hospital_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "area": row["area"],
        "address": row["address"],
        "phone": row["phone"],
        "specialties": row["specialties"].split(","),
        "latitude": row["latitude"],     # None means no location set yet
        "longitude": row["longitude"],
        "beds": {
            "general": row["general_available"],
            "icu": row["icu_available"],
            "ventilator": row["ventilator_available"],
            "last_updated": row["last_updated"],  # None means never updated
        },
    }


# This query joins the hospitals and beds tables (a JOIN).
BASE_QUERY = """
    SELECT h.*, b.general_available, b.icu_available, b.ventilator_available, b.last_updated
    FROM hospitals h
    LEFT JOIN beds b ON b.hospital_id = h.id
"""


@app.get("/admin/check", dependencies=[Depends(require_admin)])
def admin_check():
    """The admin page calls this to check whether the typed key is correct."""
    return {"ok": True}


@app.get("/admin/stale", dependencies=[Depends(require_admin)])
def stale_hospitals(hours: float = Query(2, gt=0, le=720, description="Flag beds not updated for more than this many hours")):
    """
    Lists hospitals whose bed numbers are missing (never updated) or older than `hours`.
    Never-updated hospitals come first, then the oldest updates.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(BASE_QUERY + " ORDER BY h.name")
    rows = cursor.fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    result = []

    for row in rows:
        last = row["last_updated"]
        minutes = None   # None means "never updated"

        if last:
            try:
                minutes = int((now - datetime.fromisoformat(last)).total_seconds() // 60)
            except ValueError:
                minutes = None   # unreadable time: treat like never updated, so someone checks it
            if minutes is not None and minutes <= hours * 60:
                continue   # recently updated, so skip it

        result.append({
            "id": row["id"],
            "name": row["name"],
            "area": row["area"],
            "phone": row["phone"],
            "last_updated": last,
            "minutes_since_update": minutes,
        })

    # Sort: never-updated first, then the oldest update first
    result.sort(key=lambda r: (r["minutes_since_update"] is not None, -(r["minutes_since_update"] or 0)))
    return result


# The pages are served by the backend too, so you can open http://127.0.0.1:8000/app
# (instead of double-clicking the file). This matters for the map: map tile servers and
# browser location features work reliably on a real http address, but not on file:// pages.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.get("/app", include_in_schema=False)
def public_page():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "admin.html"))


@app.get("/")
def home():
    return {"message": "CareNear backend is running! Go to /hospitals or /docs."}


@app.get("/hospitals")
def get_hospitals(
    search: Optional[str] = Query(None, description="Search by hospital name or area"),
    specialty: Optional[str] = Query(None, description="Filter by specialty, e.g. ICU"),
):
    """
    Returns the list of hospitals with their bed counts.
    - ?search=sagar   -> matches 'sagar' in the name or area
    - ?specialty=ICU  -> only hospitals that list ICU
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = BASE_QUERY + " WHERE 1=1"
    params = []

    if search:
        query += " AND (h.name LIKE ? OR h.area LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    if specialty:
        query += " AND h.specialties LIKE ?"
        params.append(f"%{specialty}%")

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [hospital_to_dict(r) for r in rows]


@app.get("/hospitals/{hospital_id}")
def get_hospital(hospital_id: int):
    """Returns the full details of one hospital."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(BASE_QUERY + " WHERE h.id = ?", (hospital_id,))
    row = cursor.fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Hospital not found")

    return hospital_to_dict(row)


# BaseModel describes what data we expect from whoever updates the beds.
# ge=0 means "greater than or equal to 0", so negative numbers are rejected.
class BedUpdate(BaseModel):
    general_available: int = Field(ge=0)
    icu_available: int = Field(ge=0)
    ventilator_available: int = Field(ge=0)


@app.put("/hospitals/{hospital_id}/beds", dependencies=[Depends(require_admin)])
def update_beds(hospital_id: int, beds: BedUpdate):
    """Updates a hospital's bed counts. last_updated is set to the current time automatically."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM hospitals WHERE id = ?", (hospital_id,))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Hospital not found")

    # Store the time in UTC so it's the same for everyone;
    # the frontend turns it into "X min ago".
    now = datetime.now(timezone.utc).isoformat()

    cursor.execute(
        """
        UPDATE beds
        SET general_available = ?, icu_available = ?, ventilator_available = ?, last_updated = ?
        WHERE hospital_id = ?
        """,
        (beds.general_available, beds.icu_available, beds.ventilator_available, now, hospital_id),
    )
    conn.commit()
    conn.close()

    return {"message": "Beds updated", "last_updated": now}


# =====================  LOCATION  =====================
class LocationIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


@app.put("/hospitals/{hospital_id}/location", dependencies=[Depends(require_admin)])
def set_location(hospital_id: int, location: LocationIn):
    """Saves a hospital's position (latitude and longitude) so it can appear on the map."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE hospitals SET latitude = ?, longitude = ? WHERE id = ?",
        (location.latitude, location.longitude, hospital_id),
    )
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Hospital not found")
    conn.commit()
    conn.close()
    return {"id": hospital_id, "latitude": location.latitude, "longitude": location.longitude}


# =====================  DOCTORS  =====================

VALID_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"   # must look like 09:30 or 14:00


def doctor_to_dict(row):
    return {
        "id": row["id"],
        "hospital_id": row["hospital_id"],
        "name": row["name"],
        "specialty": row["specialty"],
        "opd_days": row["opd_days"].split(","),
        "opd_start": row["opd_start"],
        "opd_end": row["opd_end"],
        "on_leave": bool(row["on_leave"]),
    }


class DoctorIn(BaseModel):
    name: str = Field(min_length=2)
    specialty: str = Field(min_length=2)
    opd_days: list[str]
    opd_start: str = Field(pattern=TIME_PATTERN)
    opd_end: str = Field(pattern=TIME_PATTERN)


class LeaveUpdate(BaseModel):
    on_leave: bool


@app.get("/hospitals/{hospital_id}/doctors")
def get_doctors(hospital_id: int):
    """Returns all doctors of one hospital."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM hospitals WHERE id = ?", (hospital_id,))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Hospital not found")

    cursor.execute("SELECT * FROM doctors WHERE hospital_id = ? ORDER BY name", (hospital_id,))
    rows = cursor.fetchall()
    conn.close()

    return [doctor_to_dict(r) for r in rows]


@app.post("/hospitals/{hospital_id}/doctors", dependencies=[Depends(require_admin)])
def add_doctor(hospital_id: int, doctor: DoctorIn):
    """Adds a new doctor to a hospital."""
    # Extra checks that the Field() rules above can't do
    if len(doctor.opd_days) == 0 or any(d not in VALID_DAYS for d in doctor.opd_days):
        raise HTTPException(status_code=422, detail="opd_days must be a list of: Mon, Tue, Wed, Thu, Fri, Sat, Sun")
    if doctor.opd_start >= doctor.opd_end:
        raise HTTPException(status_code=422, detail="opd_start must be earlier than opd_end")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM hospitals WHERE id = ?", (hospital_id,))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Hospital not found")

    cursor.execute(
        "INSERT INTO doctors (hospital_id, name, specialty, opd_days, opd_start, opd_end) VALUES (?, ?, ?, ?, ?, ?)",
        (hospital_id, doctor.name, doctor.specialty, ",".join(doctor.opd_days), doctor.opd_start, doctor.opd_end),
    )
    new_id = cursor.lastrowid
    conn.commit()

    cursor.execute("SELECT * FROM doctors WHERE id = ?", (new_id,))
    row = cursor.fetchone()
    conn.close()

    return doctor_to_dict(row)


@app.put("/doctors/{doctor_id}/leave", dependencies=[Depends(require_admin)])
def set_leave(doctor_id: int, update: LeaveUpdate):
    """Marks a doctor as on leave (true) or back at work (false)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("UPDATE doctors SET on_leave = ? WHERE id = ?", (1 if update.on_leave else 0, doctor_id))
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Doctor not found")

    conn.commit()
    cursor.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,))
    row = cursor.fetchone()
    conn.close()

    return doctor_to_dict(row)


# =====================  APPOINTMENT REQUESTS  =====================
# A patient sends a REQUEST (not a confirmed booking). Hospital staff see it in the
# admin page, call the patient, and mark it accepted or declined.
# We only collect: name, mobile number, preferred date. Nothing medical.

# India time is UTC+5:30 with no daylight saving, so a fixed offset is enough.
LOCAL_TZ = timezone(timedelta(hours=5, minutes=30))
MAX_DAYS_AHEAD = 30
MAX_PENDING_PER_PHONE = 3   # a simple guard against spam


class AppointmentIn(BaseModel):
    patient_name: str = Field(min_length=2, max_length=80)
    patient_phone: str = Field(min_length=10, max_length=20)
    preferred_date: str   # written as YYYY-MM-DD


class AppointmentStatus(BaseModel):
    status: Literal["pending", "accepted", "declined"]


@app.post("/doctors/{doctor_id}/appointments", status_code=201)
def request_appointment(doctor_id: int, req: AppointmentIn):
    """Public: a patient asks for an appointment with a doctor on a given date."""
    # --- clean and check the inputs ---
    name = " ".join(req.patient_name.split())   # removes extra spaces
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Please enter your name")

    phone = re.sub(r"[ \-]", "", req.patient_phone)   # remove spaces and dashes
    if not re.fullmatch(r"(\+91)?[6-9]\d{9}", phone):
        raise HTTPException(status_code=422, detail="Please enter a valid 10-digit mobile number")
    phone = phone[-10:]   # keep only the 10 digits

    try:
        wanted = date.fromisoformat(req.preferred_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Date must look like 2026-10-15")

    today = datetime.now(LOCAL_TZ).date()
    if wanted < today:
        raise HTTPException(status_code=422, detail="Please pick today or a future date")
    if wanted > today + timedelta(days=MAX_DAYS_AHEAD):
        raise HTTPException(status_code=422, detail=f"Please pick a date within the next {MAX_DAYS_AHEAD} days")

    # --- check the doctor ---
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT d.*, h.name AS hospital_name FROM doctors d
           JOIN hospitals h ON h.id = d.hospital_id WHERE d.id = ?""",
        (doctor_id,),
    )
    doc = cursor.fetchone()
    if doc is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Doctor not found")

    if doc["on_leave"]:
        conn.close()
        raise HTTPException(status_code=409, detail="This doctor is currently on leave. Please call the hospital.")

    weekday = VALID_DAYS[wanted.weekday()]   # Monday = 0, so it matches our Mon..Sun list
    opd_days = doc["opd_days"].split(",")
    if weekday not in opd_days:
        conn.close()
        raise HTTPException(status_code=422, detail=f"No OPD on {weekday}. OPD days: {', '.join(opd_days)}")

    # --- spam guards ---
    cursor.execute("SELECT COUNT(*) FROM appointments WHERE patient_phone = ? AND status = 'pending'", (phone,))
    if cursor.fetchone()[0] >= MAX_PENDING_PER_PHONE:
        conn.close()
        raise HTTPException(status_code=429, detail="This number already has several pending requests. Please wait for the hospital to call you.")

    cursor.execute(
        "SELECT id FROM appointments WHERE doctor_id = ? AND patient_phone = ? AND preferred_date = ? AND status = 'pending'",
        (doctor_id, phone, wanted.isoformat()),
    )
    if cursor.fetchone() is not None:
        conn.close()
        raise HTTPException(status_code=409, detail="You have already requested this doctor on that date.")

    # --- save it ---
    cursor.execute(
        """INSERT INTO appointments (doctor_id, hospital_id, patient_name, patient_phone, preferred_date, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (doctor_id, doc["hospital_id"], name, phone, wanted.isoformat(), datetime.now(timezone.utc).isoformat()),
    )
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": new_id,
        "doctor": doc["name"],
        "hospital": doc["hospital_name"],
        "preferred_date": wanted.isoformat(),
        "message": "Request sent. The hospital will call you to confirm. This is not a confirmed appointment yet.",
    }


# ---- Admin side (needs the key): see and manage requests ----
@app.get("/admin/appointments", dependencies=[Depends(require_admin)])
def list_appointments(status: Optional[Literal["pending", "accepted", "declined"]] = None):
    """All requests, soonest date first. Add ?status=pending to see only pending ones."""
    query = """
        SELECT a.*, d.name AS doctor_name, d.specialty, h.name AS hospital_name
        FROM appointments a
        JOIN doctors d ON d.id = a.doctor_id
        JOIN hospitals h ON h.id = a.hospital_id
    """
    params = []
    if status:
        query += " WHERE a.status = ?"
        params.append(status)
    query += " ORDER BY a.preferred_date, a.created_at"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": r["id"],
            "patient_name": r["patient_name"],
            "patient_phone": r["patient_phone"],
            "preferred_date": r["preferred_date"],
            "status": r["status"],
            "created_at": r["created_at"],
            "doctor_name": r["doctor_name"],
            "specialty": r["specialty"],
            "hospital_name": r["hospital_name"],
        }
        for r in rows
    ]


@app.put("/admin/appointments/{appointment_id}", dependencies=[Depends(require_admin)])
def update_appointment(appointment_id: int, update: AppointmentStatus):
    """Mark a request pending / accepted / declined."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE appointments SET status = ? WHERE id = ?", (update.status, appointment_id))
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Request not found")
    conn.commit()
    conn.close()
    return {"id": appointment_id, "status": update.status}


@app.delete("/admin/appointments/{appointment_id}", dependencies=[Depends(require_admin)])
def delete_appointment(appointment_id: int):
    """Permanently deletes a request (so patient details don't pile up forever)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Request not found")
    conn.commit()
    conn.close()
    return {"deleted": appointment_id}

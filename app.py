import os
import json
import random
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

MAIL_USER = os.getenv("MAIL_USER")
MAIL_PASS = os.getenv("MAIL_PASSWORD")
DB_FILE   = "laundry.db"

# ─── DATABASE SETUP ────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bookings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                email       TEXT NOT NULL,
                room_number TEXT NOT NULL,
                facility    TEXT NOT NULL,
                room        TEXT NOT NULL,
                date        TEXT NOT NULL,
                slot        TEXT NOT NULL,
                status      TEXT DEFAULT 'confirmed',
                checked_in  INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS otp_store (
                email      TEXT PRIMARY KEY,
                otp        TEXT NOT NULL,
                payload    TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS machines (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                facility TEXT NOT NULL,
                room     TEXT NOT NULL,
                working  INTEGER DEFAULT 1,
                note     TEXT DEFAULT '',
                UNIQUE(facility, room)
            );
        """)

init_db()

# ─── EMAIL ─────────────────────────────────────────────────────────
def send_email(to_addr, subject, html_body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Leo Marquard Laundry <{MAIL_USER}>"
        msg["To"]      = to_addr
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(MAIL_USER, MAIL_PASS)
            server.sendmail(MAIL_USER, to_addr, msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

def otp_email_html(otp):
    return f"""
    <div style="font-family:monospace;background:#060a12;color:#eef2ff;padding:2rem;border-radius:1rem;max-width:400px;margin:auto">
      <h2 style="color:#00e5b0;margin-bottom:0.5rem">Leo Marquard Laundry</h2>
      <p style="color:#7a8aaa;font-size:0.85rem;margin-bottom:1.5rem">UCT Res · Booking Verification</p>
      <p style="margin-bottom:1rem">Your one-time verification code is:</p>
      <div style="background:#0f1a2e;border:2px solid #00e5b0;border-radius:0.8rem;padding:1.2rem;text-align:center;font-size:2rem;font-weight:700;letter-spacing:0.5rem;color:#00e5b0">{otp}</div>
      <p style="color:#7a8aaa;font-size:0.8rem;margin-top:1rem">Expires in 10 minutes. Do not share this code.</p>
    </div>
    """

def reminder_email_html(name, slot, facility, room, date):
    return f"""
    <div style="font-family:monospace;background:#060a12;color:#eef2ff;padding:2rem;border-radius:1rem;max-width:400px;margin:auto">
      <h2 style="color:#00e5b0;margin-bottom:0.5rem">⏰ Laundry Reminder</h2>
      <p style="color:#7a8aaa;margin-bottom:1.5rem">Your slot starts in 10 minutes</p>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Name</td><td style="color:#eef2ff">{name}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Date</td><td style="color:#eef2ff">{date}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Time</td><td style="color:#00e5b0;font-weight:700">{slot}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Facility</td><td style="color:#eef2ff">{facility}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Room</td><td style="color:#eef2ff">{room}</td></tr>
      </table>
      <p style="color:#7a8aaa;font-size:0.8rem;margin-top:1.5rem">Head to reception to collect your key. Your slot will be marked abandoned if unchecked 15 minutes after start.</p>
    </div>
    """

def confirmation_email_html(name, slot, facility, room, date, booking_id):
    return f"""
    <div style="font-family:monospace;background:#060a12;color:#eef2ff;padding:2rem;border-radius:1rem;max-width:400px;margin:auto">
      <h2 style="color:#00e5b0;margin-bottom:0.5rem">✅ Booking Confirmed</h2>
      <p style="color:#7a8aaa;margin-bottom:1.5rem">Leo Marquard Laundry · UCT Res</p>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Booking ID</td><td style="color:#eef2ff">#{ booking_id}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Name</td><td style="color:#eef2ff">{name}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Date</td><td style="color:#eef2ff">{date}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Time</td><td style="color:#00e5b0;font-weight:700">{slot}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Facility</td><td style="color:#eef2ff">{facility}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Room</td><td style="color:#eef2ff">{room}</td></tr>
      </table>
      <p style="color:#7a8aaa;font-size:0.8rem;margin-top:1.5rem">To cancel, visit the booking page. Cancellations are only allowed more than 24 hours before your slot.</p>
    </div>
    """

# ─── BACKGROUND JOBS ───────────────────────────────────────────────
def job_send_reminders():
    """Send reminder emails 10 minutes before slot start."""
    now        = datetime.now()
    target     = now + timedelta(minutes=10)
    date_str   = target.strftime("%Y-%m-%d")
    time_str   = target.strftime("%H:%M")

    with get_db() as conn:
        bookings = conn.execute("""
            SELECT * FROM bookings
            WHERE date = ? AND status = 'confirmed' AND checked_in = 0
        """, (date_str,)).fetchall()

    for b in bookings:
        start_time = b["slot"].split(" - ")[0]
        if start_time == time_str:
            facility_name = "In-House Laundry" if b["facility"] == "in-house" else "Basement Laundry"
            send_email(
                b["email"],
                "⏰ Your laundry slot starts in 10 minutes",
                reminder_email_html(b["name"], b["slot"], facility_name, b["room"], b["date"])
            )

def job_scavenge_abandoned():
    """Mark bookings as abandoned 15 mins after start if not checked in."""
    now = datetime.now()

    with get_db() as conn:
        bookings = conn.execute("""
            SELECT * FROM bookings
            WHERE status = 'confirmed' AND checked_in = 0
        """).fetchall()

        for b in bookings:
            try:
                start_str  = f"{b['date']} {b['slot'].split(' - ')[0]}"
                start_time = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
                if now >= start_time + timedelta(minutes=15):
                    conn.execute(
                        "UPDATE bookings SET status = 'abandoned' WHERE id = ?",
                        (b["id"],)
                    )
            except Exception as e:
                print(f"[SCAVENGE ERROR] {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(job_send_reminders,    "interval", minutes=1)
scheduler.add_job(job_scavenge_abandoned,"interval", minutes=1)
scheduler.start()

# ─── HELPERS ───────────────────────────────────────────────────────
def slot_to_datetime(date_str, slot_str):
    start = slot_str.split(" - ")[0]
    return datetime.strptime(f"{date_str} {start}", "%Y-%m-%d %H:%M")

# Booking opens 2hrs before first slot of each facility:
# In-house first slot: 08:00 → opens at 06:00
# Basement first slot: 08:00 → opens at 06:00 (same opening, 2hr rule)
FACILITY_OPEN_HOUR = {
    "in-house": 6,   # bookings open at 06:00 for 08:00 first slot
    "basement": 6,   # bookings open at 06:00 for 08:00 first slot
}

def is_booking_allowed(date_str, facility):
    """Only allow booking for today, and only after the facility opens."""
    try:
        today = datetime.now().date()
        d     = datetime.strptime(date_str, "%Y-%m-%d").date()
        if d != today:
            return False, "You can only book for today"
        open_hour = FACILITY_OPEN_HOUR.get(facility, 6)
        if datetime.now().hour < open_hour:
            return False, f"Bookings open at {open_hour:02d}:00"
        return True, ""
    except:
        return False, "Invalid date"

# ─── ROUTES ────────────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/reception")
def reception():
    return render_template("reception.html")

# ── OTP: send ──
@app.route("/api/send-otp", methods=["POST"])
def send_otp():
    data    = request.json or {}
    email   = data.get("email", "").strip().lower()
    payload = data.get("payload", {})

    if not email.endswith("@myuct.ac.za"):
        return jsonify({"error": "Must use a @myuct.ac.za email"}), 400

    facility = payload.get("facility", "")
    allowed, reason = is_booking_allowed(payload.get("date", ""), facility)
    if not allowed:
        return jsonify({"error": reason}), 400

    otp     = str(random.randint(100000, 999999))
    expires = (datetime.now() + timedelta(minutes=10)).isoformat()

    with get_db() as conn:
        conn.execute("""
            INSERT INTO otp_store (email, otp, payload, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET otp=excluded.otp, payload=excluded.payload, expires_at=excluded.expires_at
        """, (email, otp, json.dumps(payload), expires))

    sent = send_email(email, "Your Leo Marquard Laundry verification code", otp_email_html(otp))
    if not sent:
        return jsonify({"error": "Failed to send email. Check the address and try again."}), 500

    return jsonify({"message": "OTP sent"})

# ── OTP: verify & create booking ──
@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    data  = request.json or {}
    email = data.get("email", "").strip().lower()
    otp   = data.get("otp", "").strip()

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM otp_store WHERE email = ?", (email,)
        ).fetchone()

        if not row:
            return jsonify({"error": "No OTP found. Request a new one."}), 400

        if datetime.now() > datetime.fromisoformat(row["expires_at"]):
            conn.execute("DELETE FROM otp_store WHERE email = ?", (email,))
            return jsonify({"error": "OTP expired. Request a new one."}), 400

        if row["otp"] != otp:
            return jsonify({"error": "Incorrect code. Try again."}), 400

        payload = json.loads(row["payload"])

        # Re-validate everything server-side
        required = ["name", "email", "roomNumber", "facility", "room", "date", "slot"]
        for r in required:
            if r not in payload or not str(payload[r]).strip():
                return jsonify({"error": f"Missing field: {r}"}), 400

        if not is_booking_allowed(payload["date"], payload.get("facility", ""))[0]:
            return jsonify({"error": "Booking window is not open"}), 400

        # Check for slot conflict
        taken = conn.execute("""
            SELECT id FROM bookings
            WHERE facility=? AND room=? AND date=? AND slot=? AND status IN ('confirmed')
        """, (payload["facility"], payload["room"], payload["date"], payload["slot"])).fetchone()

        if taken:
            conn.execute("DELETE FROM otp_store WHERE email = ?", (email,))
            return jsonify({"error": "That slot was just taken. Please choose another."}), 409

        # Check one booking per person per day
        existing = conn.execute("""
            SELECT id FROM bookings
            WHERE email=? AND date=? AND status='confirmed'
        """, (email, payload["date"])).fetchone()

        if existing:
            conn.execute("DELETE FROM otp_store WHERE email = ?", (email,))
            return jsonify({"error": "You already have a booking on this date"}), 409

        # Check machine is working
        machine = conn.execute("""
            SELECT working FROM machines WHERE facility=? AND room=?
        """, (payload["facility"], payload["room"])).fetchone()

        if machine and not machine["working"]:
            return jsonify({"error": "That machine is currently out of service"}), 400

        # Save booking
        cursor = conn.execute("""
            INSERT INTO bookings (name, email, room_number, facility, room, date, slot)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            payload["name"], email, payload["roomNumber"],
            payload["facility"], payload["room"],
            payload["date"], payload["slot"]
        ))
        booking_id = cursor.lastrowid

        conn.execute("DELETE FROM otp_store WHERE email = ?", (email,))

    facility_name = "In-House Laundry" if payload["facility"] == "in-house" else "Basement Laundry"
    send_email(
        email,
        "✅ Laundry booking confirmed — Leo Marquard",
        confirmation_email_html(
            payload["name"], payload["slot"],
            facility_name, payload["room"],
            payload["date"], booking_id
        )
    )

    return jsonify({"message": "Booking confirmed", "booking_id": booking_id})

# ── Availability ──
@app.route("/api/availability")
def availability():
    facility = request.args.get("facility")
    room     = request.args.get("room")
    date     = request.args.get("date")

    with get_db() as conn:
        rows = conn.execute("""
            SELECT slot FROM bookings
            WHERE facility=? AND room=? AND date=? AND status='confirmed'
        """, (facility, room, date)).fetchall()

        machine = conn.execute(
            "SELECT working, note FROM machines WHERE facility=? AND room=?",
            (facility, room)
        ).fetchone()

    taken = [r["slot"] for r in rows]
    machine_ok   = machine["working"] if machine else True
    machine_note = machine["note"]    if machine else ""

    return jsonify({"taken": taken, "machine_working": bool(machine_ok), "machine_note": machine_note})

# ── All bookings (reception) ──
@app.route("/api/bookings")
def get_bookings():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, name, email, room_number, facility, room, date, slot, status, checked_in
            FROM bookings ORDER BY date, slot
        """).fetchall()
    return jsonify([dict(r) for r in rows])

# ── Check-in (reception) ──
@app.route("/api/checkin/<int:booking_id>", methods=["POST"])
def checkin(booking_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE bookings SET checked_in=1 WHERE id=?", (booking_id,)
        )
    return jsonify({"message": "Checked in"})

# ── Cancel booking ──
@app.route("/api/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    email = (request.json or {}).get("email", "").strip().lower()

    with get_db() as conn:
        b = conn.execute(
            "SELECT * FROM bookings WHERE id=?", (booking_id,)
        ).fetchone()

        if not b:
            return jsonify({"error": "Booking not found"}), 404

        if b["email"].lower() != email:
            return jsonify({"error": "Unauthorized"}), 403

        if b["status"] != "confirmed":
            return jsonify({"error": "Booking cannot be cancelled"}), 400

        slot_start = slot_to_datetime(b["date"], b["slot"])
        if datetime.now() >= slot_start - timedelta(hours=24):
            return jsonify({"error": "Cannot cancel within 24 hours of your slot"}), 400

        conn.execute(
            "UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,)
        )

    return jsonify({"message": "Booking cancelled"})

# ── My bookings (by email) ──
@app.route("/api/my-bookings")
def my_bookings():
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify([])

    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, name, date, slot, facility, room, status, checked_in
            FROM bookings WHERE email=? ORDER BY date DESC, slot DESC
        """, (email,)).fetchall()

    return jsonify([dict(r) for r in rows])

# ── Machine status (reception toggle) ──
@app.route("/api/machines", methods=["GET"])
def get_machines():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM machines").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/machines/toggle", methods=["POST"])
def toggle_machine():
    data     = request.json or {}
    facility = data.get("facility")
    room     = data.get("room")
    working  = data.get("working")
    note     = data.get("note", "")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO machines (facility, room, working, note)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(facility, room) DO UPDATE SET working=excluded.working, note=excluded.note
        """, (facility, room, 1 if working else 0, note))

    return jsonify({"message": "Machine status updated"})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
import os
import json
import random
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

MAIL_USER    = os.getenv("MAIL_USER")
MAIL_PASS    = os.getenv("MAIL_PASSWORD")
DATABASE_URL = os.getenv("DATABASE_URL")

# ─── DATABASE ──────────────────────────────────────────────────────
def get_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    conn.autocommit = False
    return conn

def init_db():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    id          SERIAL PRIMARY KEY,
                    name        TEXT NOT NULL,
                    email       TEXT NOT NULL,
                    room_number TEXT NOT NULL,
                    facility    TEXT NOT NULL,
                    room        TEXT NOT NULL,
                    date        TEXT NOT NULL,
                    slot        TEXT NOT NULL,
                    status      TEXT DEFAULT 'confirmed',
                    checked_in  BOOLEAN DEFAULT FALSE,
                    created_at  TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS otp_store (
                    email      TEXT PRIMARY KEY,
                    otp        TEXT NOT NULL,
                    payload    TEXT NOT NULL,
                    expires_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS machines (
                    id       SERIAL PRIMARY KEY,
                    facility TEXT NOT NULL,
                    room     TEXT NOT NULL,
                    working  BOOLEAN DEFAULT TRUE,
                    note     TEXT DEFAULT '',
                    UNIQUE(facility, room)
                );
            """)
        conn.commit()
    finally:
        conn.close()

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
      <h2 style="color:#00e5b0;margin-bottom:0.5rem">Laundry Reminder</h2>
      <p style="color:#7a8aaa;margin-bottom:1.5rem">Your slot starts in 10 minutes</p>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Name</td><td style="color:#eef2ff">{name}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Date</td><td style="color:#eef2ff">{date}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Time</td><td style="color:#00e5b0;font-weight:700">{slot}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Facility</td><td style="color:#eef2ff">{facility}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Room</td><td style="color:#eef2ff">{room}</td></tr>
      </table>
      <p style="color:#7a8aaa;font-size:0.8rem;margin-top:1.5rem">Head to reception to collect your key. Slot marked abandoned if unchecked 15 mins after start.</p>
    </div>
    """

def confirmation_email_html(name, slot, facility, room, date, booking_id):
    return f"""
    <div style="font-family:monospace;background:#060a12;color:#eef2ff;padding:2rem;border-radius:1rem;max-width:400px;margin:auto">
      <h2 style="color:#00e5b0;margin-bottom:0.5rem">Booking Confirmed</h2>
      <p style="color:#7a8aaa;margin-bottom:1.5rem">Leo Marquard Laundry · UCT Res</p>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Booking ID</td><td style="color:#eef2ff">#{booking_id}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Name</td><td style="color:#eef2ff">{name}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Date</td><td style="color:#eef2ff">{date}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Time</td><td style="color:#00e5b0;font-weight:700">{slot}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Facility</td><td style="color:#eef2ff">{facility}</td></tr>
        <tr><td style="color:#7a8aaa;padding:0.4rem 0">Room</td><td style="color:#eef2ff">{room}</td></tr>
      </table>
      <p style="color:#7a8aaa;font-size:0.8rem;margin-top:1.5rem">Cancellations only allowed more than 24 hours before your slot.</p>
    </div>
    """

# ─── BACKGROUND JOBS ───────────────────────────────────────────────
def job_send_reminders():
    now      = datetime.now()
    target   = now + timedelta(minutes=10)
    date_str = target.strftime("%Y-%m-%d")
    time_str = target.strftime("%H:%M")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM bookings WHERE date=%s AND status='confirmed' AND checked_in=FALSE", (date_str,))
            for b in cur.fetchall():
                if b["slot"].split(" - ")[0] == time_str:
                    facility_name = "In-House Laundry" if b["facility"] == "in-house" else "Basement Laundry"
                    send_email(b["email"], "Your laundry slot starts in 10 minutes",
                               reminder_email_html(b["name"], b["slot"], facility_name, b["room"], b["date"]))
    finally:
        conn.close()

def job_scavenge_abandoned():
    now  = datetime.now()
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM bookings WHERE status='confirmed' AND checked_in=FALSE")
            for b in cur.fetchall():
                try:
                    start_time = datetime.strptime(f"{b['date']} {b['slot'].split(' - ')[0]}", "%Y-%m-%d %H:%M")
                    if now >= start_time + timedelta(minutes=15):
                        cur.execute("UPDATE bookings SET status='abandoned' WHERE id=%s", (b["id"],))
                except Exception as e:
                    print(f"[SCAVENGE ERROR] {e}")
        conn.commit()
    finally:
        conn.close()

scheduler = BackgroundScheduler()
scheduler.add_job(job_send_reminders,     "interval", minutes=1)
scheduler.add_job(job_scavenge_abandoned, "interval", minutes=1)
scheduler.start()

# ─── HELPERS ───────────────────────────────────────────────────────
def slot_to_datetime(date_str, slot_str):
    return datetime.strptime(f"{date_str} {slot_str.split(' - ')[0]}", "%Y-%m-%d %H:%M")

FACILITY_OPEN_HOUR = {
    "in-house": 6,
    "basement": 6,
}

def is_booking_allowed(date_str, facility):
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

@app.route("/api/send-otp", methods=["POST"])
def send_otp():
    data    = request.json or {}
    email   = data.get("email", "").strip().lower()
    payload = data.get("payload", {})

    if not email.endswith("@myuct.ac.za"):
        return jsonify({"error": "Must use a @myuct.ac.za email"}), 400

    allowed, reason = is_booking_allowed(payload.get("date", ""), payload.get("facility", ""))
    if not allowed:
        return jsonify({"error": reason}), 400

    otp     = str(random.randint(100000, 999999))
    expires = datetime.now() + timedelta(minutes=10)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO otp_store (email, otp, payload, expires_at) VALUES (%s,%s,%s,%s)
                ON CONFLICT (email) DO UPDATE SET otp=EXCLUDED.otp, payload=EXCLUDED.payload, expires_at=EXCLUDED.expires_at
            """, (email, otp, json.dumps(payload), expires))
        conn.commit()
    finally:
        conn.close()

    sent = send_email(email, "Your Leo Marquard Laundry verification code", otp_email_html(otp))
    if not sent:
        return jsonify({"error": "Failed to send email. Check the address and try again."}), 500
    return jsonify({"message": "OTP sent"})

@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    data  = request.json or {}
    email = data.get("email", "").strip().lower()
    otp   = data.get("otp", "").strip()

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM otp_store WHERE email=%s", (email,))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": "No OTP found. Request a new one."}), 400
            if datetime.now() > row["expires_at"]:
                cur.execute("DELETE FROM otp_store WHERE email=%s", (email,))
                conn.commit()
                return jsonify({"error": "OTP expired. Request a new one."}), 400
            if row["otp"] != otp:
                return jsonify({"error": "Incorrect code. Try again."}), 400

            payload = json.loads(row["payload"])

            for r in ["name","email","roomNumber","facility","room","date","slot"]:
                if r not in payload or not str(payload[r]).strip():
                    return jsonify({"error": f"Missing field: {r}"}), 400

            allowed, reason = is_booking_allowed(payload["date"], payload.get("facility",""))
            if not allowed:
                return jsonify({"error": reason}), 400

            cur.execute("""
                SELECT id FROM bookings
                WHERE facility=%s AND room=%s AND date=%s AND slot=%s AND status='confirmed'
            """, (payload["facility"], payload["room"], payload["date"], payload["slot"]))
            if cur.fetchone():
                cur.execute("DELETE FROM otp_store WHERE email=%s", (email,))
                conn.commit()
                return jsonify({"error": "That slot was just taken. Please choose another."}), 409

            cur.execute("SELECT id FROM bookings WHERE email=%s AND date=%s AND status='confirmed'", (email, payload["date"]))
            if cur.fetchone():
                cur.execute("DELETE FROM otp_store WHERE email=%s", (email,))
                conn.commit()
                return jsonify({"error": "You already have a booking on this date"}), 409

            cur.execute("SELECT working FROM machines WHERE facility=%s AND room=%s", (payload["facility"], payload["room"]))
            machine = cur.fetchone()
            if machine and not machine["working"]:
                return jsonify({"error": "That machine is currently out of service"}), 400

            cur.execute("""
                INSERT INTO bookings (name, email, room_number, facility, room, date, slot)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (payload["name"], email, payload["roomNumber"], payload["facility"], payload["room"], payload["date"], payload["slot"]))
            booking_id = cur.fetchone()["id"]

            cur.execute("DELETE FROM otp_store WHERE email=%s", (email,))
            conn.commit()

    finally:
        conn.close()

    facility_name = "In-House Laundry" if payload["facility"] == "in-house" else "Basement Laundry"
    send_email(email, "Laundry booking confirmed — Leo Marquard",
               confirmation_email_html(payload["name"], payload["slot"], facility_name, payload["room"], payload["date"], booking_id))

    return jsonify({"message": "Booking confirmed", "booking_id": booking_id})

@app.route("/api/availability")
def availability():
    facility = request.args.get("facility")
    room     = request.args.get("room")
    date     = request.args.get("date")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT slot FROM bookings WHERE facility=%s AND room=%s AND date=%s AND status='confirmed'", (facility, room, date))
            taken = [r["slot"] for r in cur.fetchall()]
            cur.execute("SELECT working, note FROM machines WHERE facility=%s AND room=%s", (facility, room))
            machine = cur.fetchone()
    finally:
        conn.close()

    return jsonify({
        "taken":           taken,
        "machine_working": machine["working"] if machine else True,
        "machine_note":    machine["note"]    if machine else ""
    })

@app.route("/api/bookings")
def get_bookings():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, email, room_number, facility, room, date, slot, status, checked_in FROM bookings ORDER BY date, slot")
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/checkin/<int:booking_id>", methods=["POST"])
def checkin(booking_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE bookings SET checked_in=TRUE WHERE id=%s", (booking_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": "Checked in"})

@app.route("/api/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    email = (request.json or {}).get("email", "").strip().lower()
    conn  = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM bookings WHERE id=%s", (booking_id,))
            b = cur.fetchone()
            if not b:
                return jsonify({"error": "Booking not found"}), 404
            if b["email"].lower() != email:
                return jsonify({"error": "Unauthorized"}), 403
            if b["status"] != "confirmed":
                return jsonify({"error": "Booking cannot be cancelled"}), 400
            if datetime.now() >= slot_to_datetime(b["date"], b["slot"]) - timedelta(hours=24):
                return jsonify({"error": "Cannot cancel within 24 hours of your slot"}), 400
            cur.execute("UPDATE bookings SET status='cancelled' WHERE id=%s", (booking_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": "Booking cancelled"})

@app.route("/api/my-bookings")
def my_bookings():
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify([])
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, date, slot, facility, room, status, checked_in FROM bookings WHERE email=%s ORDER BY date DESC, slot DESC", (email,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/machines")
def get_machines():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM machines")
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/machines/toggle", methods=["POST"])
def toggle_machine():
    data = request.json or {}
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO machines (facility, room, working, note) VALUES (%s,%s,%s,%s)
                ON CONFLICT (facility, room) DO UPDATE SET working=EXCLUDED.working, note=EXCLUDED.note
            """, (data.get("facility"), data.get("room"), data.get("working"), data.get("note", "")))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": "Machine status updated"})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
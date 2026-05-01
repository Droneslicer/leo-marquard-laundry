import os
import json
import psycopg2
import psycopg2.extras
import threading
import smtplib
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from apscheduler.schedulers.background import BackgroundScheduler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
socketio = SocketIO(app, cors_allowed_origins="*")

# Environment variables
MAIL_USER = os.getenv("MAIL_USER")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")  # This is the one!
DATABASE_URL = os.getenv("DATABASE_URL")

print(f"[STARTUP] MAIL_USER: {'set' if MAIL_USER else 'MISSING'}")
print(f"[STARTUP] MAIL_PASSWORD: {'set' if MAIL_PASSWORD else 'MISSING'}")
print(f"[STARTUP] DATABASE_URL: {'set' if DATABASE_URL else 'MISSING'}")


# Helper function to make datetime JSON serializable
def make_json_serializable(obj):
    """Convert datetime objects to ISO format strings"""
    if isinstance(obj, dict):
        return {key: make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [make_json_serializable(item) for item in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    else:
        return obj


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
                    id           SERIAL PRIMARY KEY,
                    name         TEXT NOT NULL,
                    email        TEXT NOT NULL,
                    room_number  TEXT NOT NULL,
                    facility     TEXT NOT NULL,
                    room         TEXT NOT NULL,
                    date         TEXT NOT NULL,
                    slot         TEXT NOT NULL,
                    status       TEXT DEFAULT 'confirmed',
                    checked_in   BOOLEAN DEFAULT FALSE,
                    checked_in_at  TIMESTAMP,
                    checked_out  BOOLEAN DEFAULT FALSE,
                    checked_out_at TIMESTAMP,
                    late_return  BOOLEAN DEFAULT FALSE,
                    late_minutes INTEGER DEFAULT 0,
                    created_at   TIMESTAMP DEFAULT NOW()
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
            for col, definition in [
                ("checked_in_at", "TIMESTAMP"),
                ("checked_out", "BOOLEAN DEFAULT FALSE"),
                ("checked_out_at", "TIMESTAMP"),
                ("late_return", "BOOLEAN DEFAULT FALSE"),
                ("late_minutes", "INTEGER DEFAULT 0"),
            ]:
                try:
                    cur.execute(f"ALTER TABLE bookings ADD COLUMN IF NOT EXISTS {col} {definition}")
                except:
                    pass
        conn.commit()
    finally:
        conn.close()


init_db()


# ─── EMAIL USING PORT 2525 (Allowed on Render Free Tier) ─────────────────
def send_email(to_addr, subject, html_body):
    """Send email using Gmail SMTP on port 2525 (bypasses Render block)"""

    def _send():
        try:
            if not MAIL_USER or not MAIL_PASSWORD:
                print(
                    f"[EMAIL] Missing credentials - MAIL_USER: {bool(MAIL_USER)}, MAIL_PASSWORD: {bool(MAIL_PASSWORD)}")
                return

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = MAIL_USER
            msg["To"] = to_addr
            msg.attach(MIMEText(html_body, "html"))

            # Try port 2525 first (allowed on free tier), then fallback
            ports_to_try = [2525, 587, 465]

            for port in ports_to_try:
                try:
                    if port == 465:
                        server = smtplib.SMTP_SSL("smtp.gmail.com", port, timeout=30)
                    else:
                        server = smtplib.SMTP("smtp.gmail.com", port, timeout=30)
                        server.starttls()

                    server.login(MAIL_USER, MAIL_PASSWORD)
                    server.sendmail(MAIL_USER, to_addr, msg.as_string())
                    server.quit()
                    print(f"[EMAIL] ✅ Sent to {to_addr} via port {port}")
                    return
                except Exception as e:
                    print(f"[EMAIL] Port {port} failed: {e}")
                    continue

            print(f"[EMAIL] ❌ All ports failed for {to_addr}")

        except Exception as e:
            print(f"[EMAIL] ❌ Error: {e}")

    thread = threading.Thread(target=_send)
    thread.daemon = True
    thread.start()
    return True


def confirmation_email_html(name, slot, facility, room, date, booking_id):
    return f"""
    <div style="font-family:Arial, sans-serif; max-width:500px; margin:auto; padding:20px; background:#060a12; color:#eef2ff; border-radius:10px;">
      <h2 style="color:#00e5b0;">✅ Booking Confirmed</h2>
      <p><strong>Booking ID:</strong> #{booking_id}</p>
      <p><strong>Name:</strong> {name}</p>
      <p><strong>Date:</strong> {date}</p>
      <p><strong>Time:</strong> {slot}</p>
      <p><strong>Facility:</strong> {facility}</p>
      <p><strong>Room:</strong> {room}</p>
      <hr style="border-color:#333;">
      <p>Head to reception to collect your access card at your slot time.</p>
      <p style="color:#7a8aaa; font-size:12px;">Leo Marquard Laundry · UCT Res</p>
    </div>
    """


def reminder_email_html(name, slot, facility, room, date):
    return f"""
    <div style="font-family:Arial, sans-serif; max-width:500px; margin:auto; padding:20px; background:#060a12; color:#eef2ff; border-radius:10px;">
      <h2 style="color:#ffa500;">⏰ Laundry Reminder</h2>
      <p>Your slot starts in 10 minutes!</p>
      <p><strong>Name:</strong> {name}</p>
      <p><strong>Date:</strong> {date}</p>
      <p><strong>Time:</strong> {slot}</p>
      <p><strong>Facility:</strong> {facility}</p>
      <p><strong>Room:</strong> {room}</p>
      <hr style="border-color:#333;">
      <p>Head to reception to collect your access card.</p>
    </div>
    """


# ─── BACKGROUND JOBS ──────────────────────────────────────────────
def job_send_reminders():
    now = datetime.now()
    target = now + timedelta(minutes=10)
    date_str = target.strftime("%Y-%m-%d")
    time_str = target.strftime("%H:%M")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM bookings WHERE date=%s AND status='confirmed' AND checked_in=FALSE", (date_str,))
            for b in cur.fetchall():
                if b["slot"].split(" - ")[0] == time_str:
                    facility_name = "In-House Laundry" if b["facility"] == "in-house" else "Basement Laundry"
                    send_email(b["email"], "⏰ Laundry slot starts in 10 minutes",
                               reminder_email_html(b["name"], b["slot"], facility_name, b["room"], b["date"]))
    finally:
        conn.close()


def job_scavenge_abandoned():
    now = datetime.now()
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
scheduler.add_job(job_send_reminders, "interval", minutes=5)
scheduler.add_job(job_scavenge_abandoned, "interval", minutes=5)
scheduler.start()


# ─── HELPERS ───────────────────────────────────────────────────────
def slot_end_datetime(date_str, slot_str):
    return datetime.strptime(f"{date_str} {slot_str.split(' - ')[1]}", "%Y-%m-%d %H:%M")


FACILITY_OPEN_HOUR = {
    "in-house": 6,
    "basement": 6,
}


def is_booking_allowed(date_str, facility, slot_str=None):
    try:
        today = datetime.now().date()
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        if d != today:
            return False, "You can only book for today"
        open_hour = FACILITY_OPEN_HOUR.get(facility, 6)
        if datetime.now().hour < open_hour:
            return False, f"Bookings open at {open_hour:02d}:00"
        if slot_str:
            slot_start = datetime.strptime(f"{date_str} {slot_str.split(' - ')[0]}", "%Y-%m-%d %H:%M")
            if datetime.now() >= slot_start - timedelta(minutes=30):
                return False, "Booking window for this slot has closed"
        return True, ""
    except:
        return False, "Invalid date"


# ─── WEBSOCKET HANDLERS ────────────────────────────────────────────
@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')


@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')


@socketio.on('join_reception')
def handle_join_reception():
    join_room('reception_dashboard')
    print(f'Reception joined: {request.sid}')


@socketio.on('leave_reception')
def handle_leave_reception():
    leave_room('reception_dashboard')


# ─── ROUTES ────────────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/reception")
def reception():
    return render_template("reception.html")


@app.route("/api/book", methods=["POST"])
def book_slot():
    data = request.json or {}
    email = data.get("email", "").strip().lower()

    required = ["name", "email", "roomNumber", "facility", "room", "date", "slot"]
    for r in required:
        if r not in data or not str(data[r]).strip():
            return jsonify({"error": f"Missing field: {r}"}), 400

    if not email.endswith("@myuct.ac.za"):
        return jsonify({"error": "Must use a @myuct.ac.za email"}), 400

    allowed, reason = is_booking_allowed(data["date"], data["facility"], data["slot"])
    if not allowed:
        return jsonify({"error": reason}), 400

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id FROM bookings
                WHERE facility=%s AND room=%s AND date=%s AND slot=%s AND status='confirmed'
            """, (data["facility"], data["room"], data["date"], data["slot"]))
            if cur.fetchone():
                return jsonify({"error": "That slot was just taken. Please choose another."}), 409

            cur.execute("SELECT id FROM bookings WHERE email=%s AND date=%s AND status='confirmed'",
                        (email, data["date"]))
            if cur.fetchone():
                return jsonify({"error": "You already have a booking today"}), 409

            cur.execute("SELECT working FROM machines WHERE facility=%s AND room=%s", (data["facility"], data["room"]))
            machine = cur.fetchone()
            if machine and not machine["working"]:
                return jsonify({"error": "That machine is currently out of service"}), 400

            cur.execute("""
                INSERT INTO bookings (name, email, room_number, facility, room, date, slot)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (data["name"], email, data["roomNumber"], data["facility"], data["room"], data["date"], data["slot"]))
            booking_id = cur.fetchone()["id"]

            cur.execute("SELECT * FROM bookings WHERE id=%s", (booking_id,))
            new_booking = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    facility_name = "In-House Laundry" if data["facility"] == "in-house" else "Basement Laundry"

    send_email(email, "✅ Laundry Booking Confirmed — Leo Marquard",
               confirmation_email_html(data["name"], data["slot"], facility_name, data["room"], data["date"],
                                       booking_id))

    socketio.emit('new_booking', {
        'booking': make_json_serializable(dict(new_booking)),
        'timestamp': datetime.now().isoformat()
    }, room='reception_dashboard')

    return jsonify({"message": "Booking confirmed", "booking_id": booking_id})


@app.route("/api/availability")
def availability():
    facility = request.args.get("facility")
    room = request.args.get("room")
    date = request.args.get("date")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT slot FROM bookings WHERE facility=%s AND room=%s AND date=%s AND status='confirmed'",
                        (facility, room, date))
            taken = [r["slot"] for r in cur.fetchall()]
            cur.execute("SELECT working, note FROM machines WHERE facility=%s AND room=%s", (facility, room))
            machine = cur.fetchone()
    finally:
        conn.close()

    return jsonify({
        "taken": taken,
        "machine_working": machine["working"] if machine else True,
        "machine_note": machine["note"] if machine else ""
    })


@app.route("/api/bookings")
def get_bookings():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, email, room_number, facility, room, date, slot,
                       status, checked_in, checked_in_at, checked_out, checked_out_at,
                       late_return, late_minutes
                FROM bookings ORDER BY date, slot
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/checkin/<int:booking_id>", methods=["POST"])
def checkin(booking_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE bookings SET checked_in=TRUE, checked_in_at=NOW() WHERE id=%s RETURNING *",
                        (booking_id,))
            updated_booking = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    socketio.emit('booking_updated', {
        'booking_id': booking_id,
        'action': 'checkin',
        'booking': make_json_serializable(dict(updated_booking)) if updated_booking else None,
        'timestamp': datetime.now().isoformat()
    }, room='reception_dashboard')

    return jsonify({"message": "Checked in — card issued"})


@app.route("/api/checkout/<int:booking_id>", methods=["POST"])
def checkout(booking_id):
    now = datetime.now()
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM bookings WHERE id=%s", (booking_id,))
            b = cur.fetchone()
            if not b:
                return jsonify({"error": "Booking not found"}), 404

            slot_end = slot_end_datetime(b["date"], b["slot"])
            is_late = now > slot_end
            late_mins = max(0, int((now - slot_end).total_seconds() / 60)) if is_late else 0

            cur.execute("""
                UPDATE bookings
                SET checked_out=TRUE, checked_out_at=%s, late_return=%s, late_minutes=%s
                WHERE id=%s RETURNING *
            """, (now, is_late, late_mins, booking_id))
            updated_booking = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    socketio.emit('booking_updated', {
        'booking_id': booking_id,
        'action': 'checkout',
        'late': is_late,
        'late_minutes': late_mins,
        'booking': make_json_serializable(dict(updated_booking)) if updated_booking else None,
        'timestamp': now.isoformat()
    }, room='reception_dashboard')

    if is_late:
        socketio.emit('late_return_alert', {
            'booking_id': booking_id,
            'name': b['name'],
            'room': b['room_number'],
            'late_minutes': late_mins,
            'timestamp': now.isoformat()
        }, room='reception_dashboard')
        return jsonify(
            {"message": f"Card returned — LATE by {late_mins} minute(s)", "late": True, "late_minutes": late_mins})
    return jsonify({"message": "Card returned on time", "late": False})


@app.route("/api/my-bookings")
def my_bookings():
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify([])
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, date, slot, facility, room, status, checked_in, checked_out, late_return, late_minutes
                FROM bookings WHERE email=%s ORDER BY date DESC, slot DESC
            """, (email,))
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
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO machines (facility, room, working, note) VALUES (%s,%s,%s,%s)
                ON CONFLICT (facility, room) DO UPDATE SET working=EXCLUDED.working, note=EXCLUDED.note
                RETURNING *
            """, (data.get("facility"), data.get("room"), data.get("working"), data.get("note", "")))
            updated_machine = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    socketio.emit('machine_updated', {
        'machine': make_json_serializable(dict(updated_machine)) if updated_machine else None,
        'timestamp': datetime.now().isoformat()
    }, room='reception_dashboard')

    return jsonify({"message": "Machine status updated"})


@app.route("/test-email")
def test_email():
    email = request.args.get("email", MAIL_USER)
    if not email:
        return "No email provided. Use ?email=your@email.com"

    result = send_email(email, "Test Email from Leo Marquard Laundry",
                        "<h1>✅ Test Successful!</h1><p>If you see this, email is working!</p>")

    if result:
        return f"✅ Test email sent to {email}. Check your inbox (and spam folder)."
    else:
        return f"❌ Failed to send test email to {email}. Check logs."


# For gunicorn on Render
application = app

if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
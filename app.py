import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from psycopg2 import pool
from functools import lru_cache

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

DATABASE_URL = os.getenv("DATABASE_URL")

# Database connection pool
db_pool = None


def init_db_pool():
    global db_pool
    if not db_pool:
        db_pool = pool.SimpleConnectionPool(1, 20, DATABASE_URL, sslmode="require")
    return db_pool


def get_db():
    pool = init_db_pool()
    return pool.getconn()


def return_db(conn):
    if db_pool:
        db_pool.putconn(conn)


def make_json_serializable(obj):
    if isinstance(obj, dict):
        return {key: make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [make_json_serializable(item) for item in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    else:
        return obj


# ─── DATABASE INIT ──────────────────────────────────────────────
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
                CREATE TABLE IF NOT EXISTS machines (
                    id       SERIAL PRIMARY KEY,
                    facility TEXT NOT NULL,
                    room     TEXT NOT NULL,
                    working  BOOLEAN DEFAULT TRUE,
                    note     TEXT DEFAULT '',
                    UNIQUE(facility, room)
                );
            """)
            cur.execute("SELECT COUNT(*) FROM machines")
            if cur.fetchone()[0] == 0:
                default_machines = [
                    ('in-house', '2nd Floor', True, ''),
                    ('in-house', '3rd Floor', True, ''),
                    ('in-house', '5th Floor', True, ''),
                    ('in-house', '7th Floor', True, ''),
                    ('in-house', '9th Floor', True, ''),
                    ('basement', 'Basement 1', True, ''),
                    ('basement', 'Basement 3', True, ''),
                ]
                for m in default_machines:
                    cur.execute("INSERT INTO machines (facility, room, working, note) VALUES (%s,%s,%s,%s)", m)
        conn.commit()
    finally:
        return_db(conn)


init_db()


# ─── BACKGROUND JOBS ──────────────────────────────────────────────
def job_scavenge_abandoned():
    now = datetime.now()
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE bookings 
                SET status = 'abandoned' 
                WHERE status = 'confirmed' 
                AND checked_in = FALSE 
                AND (date || ' ' || split_part(slot, ' - ', 1))::timestamp < %s - interval '15 minutes'
            """, (now,))
        conn.commit()
    except Exception as e:
        print(f"[SCAVENGE] {e}")
    finally:
        return_db(conn)


scheduler = BackgroundScheduler()
scheduler.add_job(job_scavenge_abandoned, "interval", minutes=10)
scheduler.start()

# ─── HELPERS ───────────────────────────────────────────────────────
FACILITY_OPEN_HOUR = {"in-house": 6, "basement": 6}


def is_booking_allowed(date_str, facility, slot_str=None):
    try:
        today = datetime.now().date()
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        if d != today:
            return False, "You can only book for today"
        if datetime.now().hour < FACILITY_OPEN_HOUR.get(facility, 6):
            return False, "Bookings open at 06:00 AM"
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
        if not data.get(r):
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
        return_db(conn)

    socketio.emit('new_booking', {
        'booking': make_json_serializable(dict(new_booking)),
        'timestamp': datetime.now().isoformat()
    }, room='reception_dashboard')

    return jsonify({
        "message": "Booking confirmed!",
        "booking_id": booking_id,
        "booking": make_json_serializable(dict(new_booking))
    })


@app.route("/api/availability")
@lru_cache(maxsize=128)
def availability():
    facility = request.args.get("facility")
    room = request.args.get("room")
    date = request.args.get("date")
    cache_buster = request.args.get("_t", "")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT slot FROM bookings 
                WHERE facility=%s AND room=%s AND date=%s AND status='confirmed'
            """, (facility, room, date))
            taken = [r["slot"] for r in cur.fetchall()]
            cur.execute("SELECT working, note FROM machines WHERE facility=%s AND room=%s", (facility, room))
            machine = cur.fetchone()
    finally:
        return_db(conn)

    return jsonify({
        "taken": taken,
        "machine_working": machine["working"] if machine else True,
        "machine_note": machine["note"] if machine else "",
        "_cache": cache_buster
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
        return_db(conn)
    return jsonify([dict(r) for r in rows])


@app.route("/api/my-bookings")
def my_bookings():
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify([])

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, date, slot, facility, room, status, 
                       checked_in, checked_out, late_return, late_minutes
                FROM bookings 
                WHERE email = %s 
                ORDER BY date DESC, slot DESC
            """, (email,))
            rows = cur.fetchall()
    finally:
        return_db(conn)
    return jsonify([dict(r) for r in rows])


@app.route("/api/checkin/<int:booking_id>", methods=["POST"])
def checkin(booking_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE bookings 
                SET checked_in = TRUE, checked_in_at = NOW() 
                WHERE id = %s AND checked_in = FALSE
                RETURNING *
            """, (booking_id,))
            updated = cur.fetchone()
        conn.commit()
    finally:
        return_db(conn)

    if updated:
        socketio.emit('booking_updated', {
            'booking_id': booking_id,
            'action': 'checkin',
            'booking': make_json_serializable(dict(updated))
        }, room='reception_dashboard')
        return jsonify({"message": "Checked in — card issued"})
    return jsonify({"error": "Already checked in or not found"}), 400


@app.route("/api/checkout/<int:booking_id>", methods=["POST"])
def checkout(booking_id):
    now = datetime.now()
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT date, slot FROM bookings WHERE id = %s", (booking_id,))
            b = cur.fetchone()
            if not b:
                return jsonify({"error": "Booking not found"}), 404

            slot_end = datetime.strptime(f"{b['date']} {b['slot'].split(' - ')[1]}", "%Y-%m-%d %H:%M")
            is_late = now > slot_end
            late_mins = max(0, int((now - slot_end).total_seconds() / 60)) if is_late else 0

            cur.execute("""
                UPDATE bookings
                SET checked_out = TRUE, checked_out_at = %s, late_return = %s, late_minutes = %s
                WHERE id = %s AND checked_out = FALSE
                RETURNING *
            """, (now, is_late, late_mins, booking_id))
            updated = cur.fetchone()
        conn.commit()
    finally:
        return_db(conn)

    if updated:
        socketio.emit('booking_updated', {
            'booking_id': booking_id,
            'action': 'checkout',
            'late': is_late,
            'late_minutes': late_mins,
            'booking': make_json_serializable(dict(updated))
        }, room='reception_dashboard')

        if is_late:
            socketio.emit('late_return_alert', {
                'booking_id': booking_id,
                'late_minutes': late_mins
            }, room='reception_dashboard')

        msg = f"Card returned — LATE by {late_mins} min" if is_late else "Card returned on time"
        return jsonify({"message": msg, "late": is_late, "late_minutes": late_mins})
    return jsonify({"error": "Already checked out"}), 400


@app.route("/api/machines")
def get_machines():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM machines ORDER BY facility, room")
            rows = cur.fetchall()
    finally:
        return_db(conn)
    return jsonify([dict(r) for r in rows])


@app.route("/api/machines/toggle", methods=["POST"])
def toggle_machine():
    data = request.json or {}
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO machines (facility, room, working, note) 
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (facility, room) 
                DO UPDATE SET working = EXCLUDED.working, note = EXCLUDED.note
                RETURNING *
            """, (data.get("facility"), data.get("room"), data.get("working"), data.get("note", "")))
            updated = cur.fetchone()
        conn.commit()
    finally:
        return_db(conn)

    socketio.emit('machine_updated', {
        'machine': make_json_serializable(dict(updated))
    }, room='reception_dashboard')

    return jsonify({"message": "Machine status updated"})


application = app

if __name__ == "__main__":
    socketio.run(app, debug=False, host="0.0.0.0", port=5000)
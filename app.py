import os
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, join_room
from psycopg2 import pool

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-on-render")

FACILITIES = {
    "in-house": {
        "label": "In-House Laundry",
        "rooms": ["2nd Floor", "3rd Floor", "5th Floor", "7th Floor", "9th Floor"],
        "slots": ["08:00-11:00", "11:00-14:00", "14:00-17:00", "17:00-20:00", "20:00-23:00"],
    },
    "basement": {
        "label": "Basement Laundry",
        "rooms": ["Basement 1", "Basement 3"],
        "slots": ["08:00-10:00", "10:00-12:00", "12:00-14:00", "14:00-16:00", "16:00-18:00", "18:00-20:00", "20:00-22:00"],
    },
}

BOOKING_OPEN_HOUR = 6
CHECKIN_GRACE_MINUTES = 15

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

db_pool = None


def init_db_pool():
    global db_pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    if db_pool is None:
        db_pool = pool.SimpleConnectionPool(1, 10, DATABASE_URL, sslmode="require")
    return db_pool


def get_db():
    return init_db_pool().getconn()


def return_db(conn):
    if conn and db_pool:
        db_pool.putconn(conn)


def as_dict(row):
    if not row:
        return None
    out = dict(row)
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


def now_local():
    return datetime.now()


def today_str():
    return now_local().date().isoformat()


def parse_slot_start(date_str, slot):
    start = slot.split("-")[0]
    return datetime.strptime(f"{date_str} {start}", "%Y-%m-%d %H:%M")


def parse_slot_end(date_str, slot):
    end = slot.split("-")[1]
    return datetime.strptime(f"{date_str} {end}", "%Y-%m-%d %H:%M")


def validate_facility_room_slot(facility, room, slot):
    config = FACILITIES.get(facility)
    if not config:
        return False
    return room in config["rooms"] and slot in config["slots"]


def require_database():
    if not DATABASE_URL:
        return jsonify({"error": "DATABASE_URL is not configured"}), 503
    return None


def init_db():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bookings (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    room_number TEXT NOT NULL,
                    facility TEXT NOT NULL,
                    room TEXT NOT NULL,
                    date TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    checked_in BOOLEAN NOT NULL DEFAULT FALSE,
                    checked_in_at TIMESTAMP,
                    checked_out BOOLEAN NOT NULL DEFAULT FALSE,
                    checked_out_at TIMESTAMP,
                    late_return BOOLEAN NOT NULL DEFAULT FALSE,
                    late_minutes INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS machines (
                    id SERIAL PRIMARY KEY,
                    facility TEXT NOT NULL,
                    room TEXT NOT NULL,
                    working BOOLEAN NOT NULL DEFAULT TRUE,
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(facility, room)
                );
                """
            )

            for facility, config in FACILITIES.items():
                for room in config["rooms"]:
                    cur.execute(
                        """
                        INSERT INTO machines (facility, room)
                        VALUES (%s, %s)
                        ON CONFLICT (facility, room) DO NOTHING
                        """,
                        (facility, room),
                    )

            cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_email_date ON bookings (email, date)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings (status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_date_slot ON bookings (date, slot)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bookings_machine_date ON bookings (facility, room, date, slot)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        return_db(conn)


def mark_abandoned_bookings():
    if not DATABASE_URL:
        return 0
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bookings
                SET status = 'abandoned'
                WHERE status = 'confirmed'
                  AND checked_in = FALSE
                  AND (date || ' ' || split_part(slot, '-', 1))::timestamp < %s
                RETURNING id
                """,
                (now_local() - timedelta(minutes=CHECKIN_GRACE_MINUTES),),
            )
            rows = cur.fetchall()
        conn.commit()
        if rows:
            socketio.emit("bookings_changed", {"reason": "abandoned", "count": len(rows)})
        return len(rows)
    except Exception as exc:
        conn.rollback()
        print(f"[abandoned cleanup] {exc}")
        return 0
    finally:
        return_db(conn)


def booking_rules_error(email, date_str, facility, room, slot):
    if not email.endswith("@myuct.ac.za"):
        return "Use your @myuct.ac.za email address"
    if date_str != today_str():
        return "Bookings are only available for today"
    if now_local().hour < BOOKING_OPEN_HOUR:
        return "Bookings open at 06:00 AM"
    if not validate_facility_room_slot(facility, room, slot):
        return "Invalid laundry room or time slot"
    if now_local() >= parse_slot_start(date_str, slot):
        return "This slot has already started"
    return None


@app.before_request
def cleanup_before_request():
    if request.path.startswith("/api/"):
        mark_abandoned_bookings()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/reception")
def reception():
    return render_template("reception.html")


@app.route("/api/config")
def config():
    return jsonify({
        "today": today_str(),
        "openHour": BOOKING_OPEN_HOUR,
        "graceMinutes": CHECKIN_GRACE_MINUTES,
        "facilities": FACILITIES,
    })


@app.route("/api/book", methods=["POST"])
def book():
    error_response = require_database()
    if error_response:
        return error_response

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    room_number = data.get("roomNumber", "").strip()
    facility = data.get("facility", "").strip()
    room = data.get("room", "").strip()
    date_str = data.get("date", "").strip()
    slot = data.get("slot", "").strip()

    if not all([name, email, room_number, facility, room, date_str, slot]):
        return jsonify({"error": "Complete all booking fields"}), 400

    rule_error = booking_rules_error(email, date_str, facility, room, slot)
    if rule_error:
        return jsonify({"error": rule_error}), 400

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT working FROM machines WHERE facility=%s AND room=%s",
                (facility, room),
            )
            machine = cur.fetchone()
            if not machine or not machine["working"]:
                return jsonify({"error": "This machine is currently unavailable"}), 409

            cur.execute(
                """
                SELECT id FROM bookings
                WHERE email=%s AND date=%s AND status IN ('confirmed', 'checked_in')
                """,
                (email, date_str),
            )
            if cur.fetchone():
                return jsonify({"error": "You already have a booking today"}), 409

            cur.execute(
                """
                SELECT id FROM bookings
                WHERE facility=%s AND room=%s AND date=%s AND slot=%s
                  AND status IN ('confirmed', 'checked_in')
                """,
                (facility, room, date_str, slot),
            )
            if cur.fetchone():
                return jsonify({"error": "That slot is already booked"}), 409

            cur.execute(
                """
                INSERT INTO bookings (name, email, room_number, facility, room, date, slot)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (name, email, room_number, facility, room, date_str, slot),
            )
            booking_row = cur.fetchone()
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"Booking failed: {exc}"}), 500
    finally:
        return_db(conn)

    booking_data = as_dict(booking_row)
    socketio.emit("booking_created", {"booking": booking_data})
    socketio.emit("bookings_changed", {"reason": "created"})
    return jsonify({"message": "Booking confirmed", "booking": booking_data})


@app.route("/api/availability")
def availability():
    error_response = require_database()
    if error_response:
        return error_response

    facility = request.args.get("facility", "")
    room = request.args.get("room", "")
    date_str = request.args.get("date", today_str())

    if not validate_facility_room_slot(facility, room, FACILITIES.get(facility, {}).get("slots", [""])[0]):
        return jsonify({"error": "Invalid facility or room"}), 400

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT slot FROM bookings
                WHERE facility=%s AND room=%s AND date=%s
                  AND status IN ('confirmed', 'checked_in')
                """,
                (facility, room, date_str),
            )
            taken = [row["slot"] for row in cur.fetchall()]
            cur.execute(
                "SELECT working, note FROM machines WHERE facility=%s AND room=%s",
                (facility, room),
            )
            machine = cur.fetchone()
    finally:
        return_db(conn)

    return jsonify({
        "taken": taken,
        "machine": as_dict(machine) or {"working": True, "note": ""},
    })


@app.route("/api/my-bookings")
def my_bookings():
    error_response = require_database()
    if error_response:
        return error_response

    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify([])

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM bookings
                WHERE email=%s
                ORDER BY date DESC, slot DESC, id DESC
                LIMIT 20
                """,
                (email,),
            )
            rows = cur.fetchall()
    finally:
        return_db(conn)
    return jsonify([as_dict(row) for row in rows])


@app.route("/api/bookings")
def bookings():
    error_response = require_database()
    if error_response:
        return error_response

    date_str = request.args.get("date", today_str())
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM bookings
                WHERE date=%s
                ORDER BY facility, room, slot, created_at
                """,
                (date_str,),
            )
            rows = cur.fetchall()
    finally:
        return_db(conn)
    return jsonify([as_dict(row) for row in rows])


@app.route("/api/stats")
def stats():
    error_response = require_database()
    if error_response:
        return error_response

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status='confirmed') AS confirmed,
                    COUNT(*) FILTER (WHERE status='checked_in') AS checked_in,
                    COUNT(*) FILTER (WHERE status='completed') AS completed,
                    COUNT(*) FILTER (WHERE status='abandoned') AS abandoned,
                    COUNT(*) FILTER (WHERE late_return=TRUE) AS late
                FROM bookings
                WHERE date=%s
                """,
                (today_str(),),
            )
            result = cur.fetchone()
    finally:
        return_db(conn)
    return jsonify(as_dict(result))


@app.route("/api/checkin/<int:booking_id>", methods=["POST"])
def checkin(booking_id):
    error_response = require_database()
    if error_response:
        return error_response

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE bookings
                SET checked_in=TRUE, checked_in_at=NOW(), status='checked_in'
                WHERE id=%s AND status='confirmed' AND checked_in=FALSE
                RETURNING *
                """,
                (booking_id,),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"Check-in failed: {exc}"}), 500
    finally:
        return_db(conn)

    if not row:
        return jsonify({"error": "Booking cannot be checked in"}), 400
    booking_data = as_dict(row)
    socketio.emit("booking_updated", {"booking": booking_data, "action": "checkin"})
    socketio.emit("bookings_changed", {"reason": "checkin"})
    return jsonify({"message": "Student checked in", "booking": booking_data})


@app.route("/api/checkout/<int:booking_id>", methods=["POST"])
def checkout(booking_id):
    error_response = require_database()
    if error_response:
        return error_response

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM bookings WHERE id=%s", (booking_id,))
            booking = cur.fetchone()
            if not booking:
                return jsonify({"error": "Booking not found"}), 404
            if booking["checked_out"] or booking["status"] == "completed":
                return jsonify({"error": "Booking is already checked out"}), 400

            slot_end = parse_slot_end(booking["date"], booking["slot"])
            late_minutes = max(0, int((now_local() - slot_end).total_seconds() // 60))
            is_late = late_minutes > 0

            cur.execute(
                """
                UPDATE bookings
                SET checked_out=TRUE,
                    checked_out_at=NOW(),
                    late_return=%s,
                    late_minutes=%s,
                    status='completed'
                WHERE id=%s
                RETURNING *
                """,
                (is_late, late_minutes, booking_id),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"Check-out failed: {exc}"}), 500
    finally:
        return_db(conn)

    booking_data = as_dict(row)
    socketio.emit("booking_updated", {"booking": booking_data, "action": "checkout"})
    socketio.emit("bookings_changed", {"reason": "checkout"})
    return jsonify({
        "message": "Student checked out",
        "late": booking_data["late_return"],
        "lateMinutes": booking_data["late_minutes"],
        "booking": booking_data,
    })


@app.route("/api/machines")
def machines():
    error_response = require_database()
    if error_response:
        return error_response

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM machines
                ORDER BY
                    CASE facility WHEN 'in-house' THEN 1 ELSE 2 END,
                    id
                """
            )
            rows = cur.fetchall()
    finally:
        return_db(conn)
    return jsonify([as_dict(row) for row in rows])


@app.route("/api/machines/<int:machine_id>", methods=["PATCH"])
def update_machine(machine_id):
    error_response = require_database()
    if error_response:
        return error_response

    data = request.get_json(silent=True) or {}
    working = bool(data.get("working"))
    note = data.get("note", "").strip()

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE machines
                SET working=%s, note=%s, updated_at=NOW()
                WHERE id=%s
                RETURNING *
                """,
                (working, note, machine_id),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"Machine update failed: {exc}"}), 500
    finally:
        return_db(conn)

    if not row:
        return jsonify({"error": "Machine not found"}), 404
    machine_data = as_dict(row)
    socketio.emit("machine_updated", {"machine": machine_data})
    socketio.emit("bookings_changed", {"reason": "machine"})
    return jsonify({"message": "Machine updated", "machine": machine_data})


@socketio.on("connect")
def socket_connect():
    join_room("laundry")


@socketio.on("join_reception")
def socket_join_reception():
    join_room("reception")


if DATABASE_URL:
    init_db()
else:
    print("DATABASE_URL is not set. Configure it on Render before using the app.")

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(mark_abandoned_bookings, "interval", minutes=2, id="abandoned-bookings", replace_existing=True)
scheduler.start()

application = app

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)

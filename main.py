from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, date, time, timedelta
import uuid
import sqlite3
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# База данных SQLite (проще некуда)
conn = sqlite3.connect("scheduler.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL,
    telegram_id TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS slots (
    id TEXT PRIMARY KEY,
    expert_id TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    status TEXT DEFAULT 'free',
    FOREIGN KEY (expert_id) REFERENCES users (id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    slot_id TEXT NOT NULL,
    manager_id TEXT NOT NULL,
    client_name TEXT NOT NULL,
    client_phone TEXT,
    client_email TEXT,
    status TEXT DEFAULT 'pending',
    zoom_link TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (slot_id) REFERENCES slots (id),
    FOREIGN KEY (manager_id) REFERENCES users (id)
)
""")
conn.commit()

# Модели данных
class UserCreate(BaseModel):
    name: str
    email: str
    role: str

class SlotCreate(BaseModel):
    expert_id: str
    date: str
    start_time: str
    end_time: str

class BookingCreate(BaseModel):
    slot_id: str
    manager_id: str
    client_name: str
    client_phone: Optional[str] = None
    client_email: Optional[str] = None

class ConfirmBooking(BaseModel):
    booking_id: str
    expert_id: str

# Генерация Zoom-подобной ссылки (простая заглушка)
def generate_zoom_link():
    meeting_id = str(uuid.uuid4())[:8]
    return f"https://zoom.us/j/{meeting_id}?pwd=dummy"

# API Эндпоинты

@app.post("/api/users")
def create_user(user: UserCreate):
    user_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO users (id, name, email, role) VALUES (?, ?, ?, ?)",
                   (user_id, user.name, user.email, user.role))
    conn.commit()
    return {"id": user_id, "name": user.name, "email": user.email, "role": user.role}

@app.get("/api/users")
def get_users():
    cursor.execute("SELECT id, name, email, role FROM users")
    users = [{"id": row[0], "name": row[1], "email": row[2], "role": row[3]} for row in cursor.fetchall()]
    return users

@app.post("/api/slots")
def create_slot(slot: SlotCreate):
    slot_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO slots (id, expert_id, date, start_time, end_time, status) VALUES (?, ?, ?, ?, ?, 'free')",
                   (slot_id, slot.expert_id, slot.date, slot.start_time, slot.end_time))
    conn.commit()
    return {"id": slot_id, "expert_id": slot.expert_id, "date": slot.date, "start_time": slot.start_time, "end_time": slot.end_time}

@app.get("/api/slots")
def get_slots(expert_id: Optional[str] = None):
    if expert_id:
        cursor.execute("SELECT id, expert_id, date, start_time, end_time, status FROM slots WHERE expert_id = ?", (expert_id,))
    else:
        cursor.execute("SELECT id, expert_id, date, start_time, end_time, status FROM slots")
    slots = [{"id": row[0], "expert_id": row[1], "date": row[2], "start_time": row[3], "end_time": row[4], "status": row[5]} for row in cursor.fetchall()]
    return slots

@app.get("/api/slots/free")
def get_free_slots():
    cursor.execute("SELECT id, expert_id, date, start_time, end_time FROM slots WHERE status = 'free'")
    slots = [{"id": row[0], "expert_id": row[1], "date": row[2], "start_time": row[3], "end_time": row[4]} for row in cursor.fetchall()]
    
    # Добавляем имя эксперта
    result = []
    for slot in slots:
        cursor.execute("SELECT name FROM users WHERE id = ?", (slot["expert_id"],))
        expert_name = cursor.fetchone()[0]
        slot["expert_name"] = expert_name
        result.append(slot)
    return result

@app.post("/api/bookings")
def create_booking(booking: BookingCreate):
    booking_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    zoom_link = generate_zoom_link()
    
    # Проверяем, что слот свободен
    cursor.execute("SELECT status FROM slots WHERE id = ?", (booking.slot_id,))
    if cursor.fetchone()[0] != "free":
        raise HTTPException(status_code=400, detail="Slot already booked")
    
    cursor.execute("INSERT INTO bookings (id, slot_id, manager_id, client_name, client_phone, client_email, status, zoom_link, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                   (booking_id, booking.slot_id, booking.manager_id, booking.client_name, booking.client_phone, booking.client_email, zoom_link, now))
    conn.commit()
    
    # Меняем статус слота на booked
    cursor.execute("UPDATE slots SET status = 'booked' WHERE id = ?", (booking.slot_id,))
    conn.commit()
    
    return {"id": booking_id, "slot_id": booking.slot_id, "zoom_link": zoom_link, "status": "pending"}

@app.post("/api/bookings/confirm")
def confirm_booking(data: ConfirmBooking):
    # Проверяем, что эксперт имеет право подтверждать эту запись
    cursor.execute("SELECT slots.expert_id FROM slots JOIN bookings ON bookings.slot_id = slots.id WHERE bookings.id = ?", (data.booking_id,))
    result = cursor.fetchone()
    if not result or result[0] != data.expert_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    cursor.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (data.booking_id,))
    conn.commit()
    
    cursor.execute("UPDATE slots SET status = 'confirmed' WHERE id = (SELECT slot_id FROM bookings WHERE id = ?)", (data.booking_id,))
    conn.commit()
    
    return {"status": "confirmed"}

@app.get("/api/bookings")
def get_bookings(role: str, user_id: str):
    if role == "admin":
        cursor.execute("SELECT b.id, b.client_name, b.client_phone, b.status, b.zoom_link, b.created_at, s.date, s.start_time, u.name as expert_name FROM bookings b JOIN slots s ON b.slot_id = s.id JOIN users u ON s.expert_id = u.id")
    elif role == "manager":
        cursor.execute("SELECT b.id, b.client_name, b.client_phone, b.status, b.zoom_link, b.created_at, s.date, s.start_time, u.name as expert_name FROM bookings b JOIN slots s ON b.slot_id = s.id JOIN users u ON s.expert_id = u.id WHERE b.manager_id = ?", (user_id,))
    elif role == "expert":
        cursor.execute("SELECT b.id, b.client_name, b.client_phone, b.status, b.zoom_link, b.created_at, s.date, s.start_time FROM bookings b JOIN slots s ON b.slot_id = s.id WHERE s.expert_id = ?", (user_id,))
    else:
        return []
    
    bookings = []
    for row in cursor.fetchall():
        bookings.append({
            "id": row[0],
            "client_name": row[1],
            "client_phone": row[2],
            "status": row[3],
            "zoom_link": row[4],
            "created_at": row[5],
            "date": row[6],
            "start_time": row[7],
            "expert_name": row[8] if len(row) > 8 else None
        })
    return bookings

@app.get("/")
def root():
    return {"message": "Expert Scheduler API is running. Use /docs for interactive API documentation."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
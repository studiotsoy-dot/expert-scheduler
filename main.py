from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Простая база данных в памяти (для теста)
DATABASE = {
    "users": {},
    "slots": {},
    "bookings": {}
}

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

def generate_zoom_link():
    return f"https://zoom.us/j/{str(uuid.uuid4())[:8]}"

# API Эндпоинты
@app.post("/api/users")
def create_user(user: UserCreate):
    user_id = str(uuid.uuid4())
    DATABASE["users"][user_id] = {
        "id": user_id,
        "name": user.name,
        "email": user.email,
        "role": user.role
    }
    return DATABASE["users"][user_id]

@app.get("/api/users")
def get_users():
    return list(DATABASE["users"].values())

@app.post("/api/slots")
def create_slot(slot: SlotCreate):
    slot_id = str(uuid.uuid4())
    DATABASE["slots"][slot_id] = {
        "id": slot_id,
        "expert_id": slot.expert_id,
        "date": slot.date,
        "start_time": slot.start_time,
        "end_time": slot.end_time,
        "status": "free"
    }
    return DATABASE["slots"][slot_id]

@app.get("/api/slots")
def get_slots(expert_id: Optional[str] = None):
    slots = list(DATABASE["slots"].values())
    if expert_id:
        slots = [s for s in slots if s["expert_id"] == expert_id]
    return slots

@app.get("/api/slots/free")
def get_free_slots():
    slots = [s for s in DATABASE["slots"].values() if s["status"] == "free"]
    result = []
    for slot in slots:
        expert = DATABASE["users"].get(slot["expert_id"])
        slot["expert_name"] = expert["name"] if expert else "Unknown"
        result.append(slot)
    return result

@app.post("/api/bookings")
def create_booking(booking: BookingCreate):
    booking_id = str(uuid.uuid4())
    zoom_link = generate_zoom_link()
    
    if booking.slot_id not in DATABASE["slots"]:
        raise HTTPException(status_code=404, detail="Slot not found")
    
    if DATABASE["slots"][booking.slot_id]["status"] != "free":
        raise HTTPException(status_code=400, detail="Slot already booked")
    
    DATABASE["bookings"][booking_id] = {
        "id": booking_id,
        "slot_id": booking.slot_id,
        "manager_id": booking.manager_id,
        "client_name": booking.client_name,
        "client_phone": booking.client_phone,
        "client_email": booking.client_email,
        "status": "pending",
        "zoom_link": zoom_link,
        "created_at": datetime.now().isoformat()
    }
    
    DATABASE["slots"][booking.slot_id]["status"] = "booked"
    
    return {"id": booking_id, "zoom_link": zoom_link, "status": "pending"}

@app.post("/api/bookings/confirm")
def confirm_booking(data: ConfirmBooking):
    if data.booking_id not in DATABASE["bookings"]:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    booking = DATABASE["bookings"][data.booking_id]
    slot = DATABASE["slots"][booking["slot_id"]]
    
    if slot["expert_id"] != data.expert_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    booking["status"] = "confirmed"
    slot["status"] = "confirmed"
    
    return {"status": "confirmed"}

@app.get("/api/bookings")
def get_bookings(role: str, user_id: str):
    bookings = []
    for b in DATABASE["bookings"].values():
        slot = DATABASE["slots"].get(b["slot_id"])
        if not slot:
            continue
        expert = DATABASE["users"].get(slot["expert_id"])
        
        if role == "admin":
            bookings.append(b)
        elif role == "manager" and b["manager_id"] == user_id:
            bookings.append(b)
        elif role == "expert" and slot["expert_id"] == user_id:
            bookings.append(b)
    
    result = []
    for b in bookings:
        slot = DATABASE["slots"][b["slot_id"]]
        expert = DATABASE["users"].get(slot["expert_id"])
        result.append({
            "id": b["id"],
            "client_name": b["client_name"],
            "client_phone": b["client_phone"],
            "status": b["status"],
            "zoom_link": b["zoom_link"],
            "created_at": b["created_at"],
            "date": slot["date"],
            "start_time": slot["start_time"],
            "expert_name": expert["name"] if expert else "Unknown"
        })
    return result

@app.get("/")
def root():
    return {"message": "Expert Scheduler API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

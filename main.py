from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# База данных
DATABASE = {
    "users": {},
    "slots": {},
    "bookings": {}
}

# Статусы созвона
CALL_STATUSES = [
    "pending",           # ожидает подтверждения
    "confirmed",         # подтверждён
    "success",           # созвон успешный
    "cancelled_by_client",  # отменился клиентом
    "cancelled_by_expert",  # отменился экспертом
    "failed",            # созвон не успешный
    "reschedule_request" # клиент просил другие дату/время
]

STATUS_NAMES = {
    "pending": "⏳ Ожидает подтверждения",
    "confirmed": "✅ Подтверждён",
    "success": "🎉 Созвон успешный",
    "cancelled_by_client": "❌ Отменён клиентом",
    "cancelled_by_expert": "⚠️ Отменён экспертом",
    "failed": "💔 Созвон не успешный",
    "reschedule_request": "🔄 Клиент просил перенести"
}

# Модели
class UserCreate(BaseModel):
    name: str
    email: str
    role: str
    portfolio_url: Optional[str] = None

class UserUpdate(BaseModel):
    user_id: str
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    portfolio_url: Optional[str] = None

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

class UpdateBookingStatus(BaseModel):
    booking_id: str
    expert_id: str
    status: str
    comment: Optional[str] = None

class RescheduleBooking(BaseModel):
    booking_id: str
    manager_id: str
    new_date: str
    new_start_time: str
    new_end_time: str

def generate_zoom_link():
    return f"https://zoom.us/j/{str(uuid.uuid4())[:8]}"

# ========== USERS ==========
@app.post("/api/users")
def create_user(user: UserCreate):
    for existing in DATABASE["users"].values():
        if existing["email"] == user.email:
            if existing.get("is_active", True):
                if existing["role"] != user.role:
                    raise HTTPException(status_code=403, detail="Эта почта уже зарегистрирована с другой ролью")
                return existing
            else:
                raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован администратором")
    
    user_id = str(uuid.uuid4())
    DATABASE["users"][user_id] = {
        "id": user_id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "portfolio_url": user.portfolio_url or "",
        "is_active": True,
        "created_at": datetime.now().isoformat()
    }
    return DATABASE["users"][user_id]

@app.get("/api/users")
def get_users():
    return list(DATABASE["users"].values())

@app.put("/api/users")
def update_user(update: UserUpdate):
    if update.user_id not in DATABASE["users"]:
        raise HTTPException(status_code=404, detail="User not found")
    
    user = DATABASE["users"][update.user_id]
    if update.name:
        user["name"] = update.name
    if update.role:
        if update.role not in ["admin", "expert", "manager"]:
            raise HTTPException(status_code=400, detail="Invalid role")
        user["role"] = update.role
    if update.is_active is not None:
        user["is_active"] = update.is_active
    if update.portfolio_url is not None:
        user["portfolio_url"] = update.portfolio_url
    
    DATABASE["users"][update.user_id] = user
    return user

@app.delete("/api/users/{user_id}")
def delete_user(user_id: str):
    if user_id not in DATABASE["users"]:
        raise HTTPException(status_code=404, detail="User not found")
    
    admins = [u for u in DATABASE["users"].values() if u["role"] == "admin" and u.get("is_active", True)]
    if DATABASE["users"][user_id]["role"] == "admin" and len(admins) <= 1:
        raise HTTPException(status_code=403, detail="Cannot delete the last admin")
    
    if DATABASE["users"][user_id]["role"] == "expert":
        slots_to_delete = [s_id for s_id, s in DATABASE["slots"].items() if s["expert_id"] == user_id]
        for s_id in slots_to_delete:
            del DATABASE["slots"][s_id]
    
    del DATABASE["users"][user_id]
    return {"status": "deleted"}

# ========== SLOTS ==========
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
        if expert and expert.get("is_active", True):
            slot["expert_name"] = expert["name"] if expert else "Unknown"
            slot["expert_portfolio"] = expert.get("portfolio_url", "")
            result.append(slot)
    return result

@app.get("/api/slots/admin/all")
def get_all_slots_with_experts():
    result = []
    for slot in DATABASE["slots"].values():
        expert = DATABASE["users"].get(slot["expert_id"])
        if not expert:
            continue
        slot_copy = slot.copy()
        slot_copy["expert_name"] = expert["name"] if expert else "Unknown"
        slot_copy["expert_email"] = expert["email"] if expert else "Unknown"
        
        booking = None
        for b in DATABASE["bookings"].values():
            if b["slot_id"] == slot["id"]:
                manager = DATABASE["users"].get(b["manager_id"])
                booking = {
                    "client_name": b["client_name"],
                    "client_phone": b["client_phone"],
                    "client_email": b["client_email"],
                    "status": b["status"],
                    "call_status": b.get("call_status", "pending"),
                    "call_comment": b.get("call_comment", ""),
                    "zoom_link": b["zoom_link"],
                    "manager_name": manager["name"] if manager else "Unknown",
                    "manager_email": manager["email"] if manager else "Unknown"
                }
                break
        slot_copy["booking"] = booking
        result.append(slot_copy)
    return result

# ========== BOOKINGS ==========
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
        "call_status": "pending",
        "call_comment": "",
        "zoom_link": zoom_link,
        "created_at": datetime.now().isoformat(),
        "status_history": [{"status": "pending", "comment": "", "changed_by": booking.manager_id, "changed_at": datetime.now().isoformat()}]
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
    booking["call_status"] = "confirmed"
    slot["status"] = "confirmed"
    
    if "status_history" not in booking:
        booking["status_history"] = []
    booking["status_history"].append({
        "status": "confirmed", 
        "comment": "", 
        "changed_by": data.expert_id, 
        "changed_at": datetime.now().isoformat()
    })
    
    return {"status": "confirmed"}

@app.post("/api/bookings/update-status")
def update_booking_status(update: UpdateBookingStatus):
    if update.booking_id not in DATABASE["bookings"]:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    booking = DATABASE["bookings"][update.booking_id]
    slot = DATABASE["slots"][booking["slot_id"]]
    
    if slot["expert_id"] != update.expert_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if update.status not in CALL_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    old_status = booking.get("call_status", "pending")
    booking["call_status"] = update.status
    booking["call_comment"] = update.comment or ""
    
    if "status_history" not in booking:
        booking["status_history"] = []
    booking["status_history"].append({
        "status": update.status,
        "comment": update.comment,
        "changed_by": update.expert_id,
        "changed_at": datetime.now().isoformat(),
        "old_status": old_status
    })
    
    return {"status": update.status, "comment": update.comment}

@app.post("/api/bookings/reschedule")
def reschedule_booking(reschedule: RescheduleBooking):
    if reschedule.booking_id not in DATABASE["bookings"]:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    booking = DATABASE["bookings"][reschedule.booking_id]
    old_slot_id = booking["slot_id"]
    old_slot = DATABASE["slots"][old_slot_id]
    
    # Создаём новый слот
    new_slot_id = str(uuid.uuid4())
    DATABASE["slots"][new_slot_id] = {
        "id": new_slot_id,
        "expert_id": old_slot["expert_id"],
        "date": reschedule.new_date,
        "start_time": reschedule.new_start_time,
        "end_time": reschedule.new_end_time,
        "status": "booked"
    }
    
    # Обновляем бронирование
    old_slot_id_for_history = booking["slot_id"]
    booking["slot_id"] = new_slot_id
    booking["call_status"] = "pending"
    booking["status"] = "pending"
    
    # Освобождаем старый слот
    DATABASE["slots"][old_slot_id]["status"] = "free"
    
    if "status_history" not in booking:
        booking["status_history"] = []
    booking["status_history"].append({
        "action": "rescheduled",
        "from_slot": old_slot_id_for_history,
        "to_slot": new_slot_id,
        "changed_by": reschedule.manager_id,
        "changed_at": datetime.now().isoformat(),
        "new_date": reschedule.new_date,
        "new_time": f"{reschedule.new_start_time} - {reschedule.new_end_time}"
    })
    
    return {
        "new_slot_id": new_slot_id, 
        "date": reschedule.new_date, 
        "start_time": reschedule.new_start_time,
        "end_time": reschedule.new_end_time
    }

@app.get("/api/bookings")
def get_bookings(role: str, user_id: str):
    bookings = []
    for b in DATABASE["bookings"].values():
        slot = DATABASE["slots"].get(b["slot_id"])
        if not slot:
            continue
        expert = DATABASE["users"].get(slot["expert_id"])
        manager = DATABASE["users"].get(b["manager_id"])
        
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
        manager = DATABASE["users"].get(b["manager_id"])
        result.append({
            "id": b["id"],
            "client_name": b["client_name"],
            "client_phone": b["client_phone"],
            "client_email": b["client_email"],
            "status": b["status"],
            "call_status": b.get("call_status", "pending"),
            "call_comment": b.get("call_comment", ""),
            "zoom_link": b["zoom_link"],
            "created_at": b["created_at"],
            "date": slot["date"],
            "start_time": slot["start_time"],
            "end_time": slot["end_time"],
            "expert_name": expert["name"] if expert else "Unknown",
            "expert_id": slot["expert_id"],
            "expert_portfolio": expert.get("portfolio_url", ""),
            "manager_name": manager["name"] if manager else "Unknown",
            "status_history": b.get("status_history", [])
        })
    return result

@app.get("/api/bookings/statuses")
def get_statuses():
    return {"statuses": CALL_STATUSES, "names": STATUS_NAMES}

# Статика
@app.get("/")
def root():
    return FileResponse("index.html")

@app.get("/index.html")
def index():
    return FileResponse("index.html")

# ========== УПРАВЛЕНИЕ СЛОТАМИ ДЛЯ ЭКСПЕРТА ==========
@app.put("/api/slots/{slot_id}")
def update_slot(slot_id: str, slot: SlotCreate):
    if slot_id not in DATABASE["slots"]:
        raise HTTPException(status_code=404, detail="Slot not found")
    
    existing_slot = DATABASE["slots"][slot_id]
    
    # Проверяем, что слот принадлежит эксперту
    if existing_slot["expert_id"] != slot.expert_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Проверяем, что слот свободен (можно редактировать только free)
    if existing_slot["status"] != "free":
        raise HTTPException(status_code=400, detail="Cannot edit booked or confirmed slot")
    
    existing_slot["date"] = slot.date
    existing_slot["start_time"] = slot.start_time
    existing_slot["end_time"] = slot.end_time
    
    return existing_slot

@app.delete("/api/slots/{slot_id}")
def delete_slot(slot_id: str, expert_id: str):
    if slot_id not in DATABASE["slots"]:
        raise HTTPException(status_code=404, detail="Slot not found")
    
    slot = DATABASE["slots"][slot_id]
    
    if slot["expert_id"] != expert_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if slot["status"] != "free":
        raise HTTPException(status_code=400, detail="Cannot delete booked or confirmed slot")
    
    del DATABASE["slots"][slot_id]
    return {"status": "deleted"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

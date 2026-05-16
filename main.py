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

# Модели
class UserCreate(BaseModel):
    name: str
    email: str
    role: str

class UserUpdate(BaseModel):
    user_id: str
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

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

# ========== USERS ==========
@app.post("/api/users")
def create_user(user: UserCreate):
    # Проверяем, не существует ли пользователь с такой почтой
    for existing in DATABASE["users"].values():
        if existing["email"] == user.email:
            # Если существует и активен — возвращаем
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
        "is_active": True,
        "created_at": datetime.now().isoformat()
    }
    return DATABASE["users"][user_id]

@app.get("/api/users")
def get_users():
    return list(DATABASE["users"].values())

@app.get("/api/users/role/{email}")
def get_user_role(email: str):
    for user in DATABASE["users"].values():
        if user["email"] == email:
            return {"role": user["role"], "is_active": user.get("is_active", True)}
    return {"role": None, "is_active": False}

@app.put("/api/users")
def update_user(update: UserUpdate):
    """Только для админа: обновление пользователя"""
    if update.user_id not in DATABASE["users"]:
        raise HTTPException(status_code=404, detail="User not found")
    
    user = DATABASE["users"][update.user_id]
    if update.name:
        user["name"] = update.name
    if update.role:
        # Проверяем, что роль валидная
        if update.role not in ["admin", "expert", "manager"]:
            raise HTTPException(status_code=400, detail="Invalid role")
        user["role"] = update.role
    if update.is_active is not None:
        user["is_active"] = update.is_active
    
    DATABASE["users"][update.user_id] = user
    return user

@app.delete("/api/users/{user_id}")
def delete_user(user_id: str):
    """Только для админа: удаление пользователя"""
    if user_id not in DATABASE["users"]:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Нельзя удалить последнего админа
    admins = [u for u in DATABASE["users"].values() if u["role"] == "admin" and u.get("is_active", True)]
    if DATABASE["users"][user_id]["role"] == "admin" and len(admins) <= 1:
        raise HTTPException(status_code=403, detail="Cannot delete the last admin")
    
    # Удаляем все слоты эксперта, если он эксперт
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
            result.append(slot)
    return result

@app.get("/api/slots/admin/all")
def get_all_slots_with_experts():
    """Для админа: все слоты всех экспертов с данными эксперта"""
    result = []
    for slot in DATABASE["slots"].values():
        expert = DATABASE["users"].get(slot["expert_id"])
        if not expert:
            continue
        slot_copy = slot.copy()
        slot_copy["expert_name"] = expert["name"] if expert else "Unknown"
        slot_copy["expert_email"] = expert["email"] if expert else "Unknown"
        
        # Добавляем информацию о бронировании, если есть
        booking = None
        for b in DATABASE["bookings"].values():
            if b["slot_id"] == slot["id"]:
                manager = DATABASE["users"].get(b["manager_id"])
                booking = {
                    "client_name": b["client_name"],
                    "client_phone": b["client_phone"],
                    "client_email": b["client_email"],
                    "status": b["status"],
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
            "zoom_link": b["zoom_link"],
            "created_at": b["created_at"],
            "date": slot["date"],
            "start_time": slot["start_time"],
            "end_time": slot["end_time"],
            "expert_name": expert["name"] if expert else "Unknown",
            "expert_id": slot["expert_id"],
            "manager_name": manager["name"] if manager else "Unknown"
        })
    return result

# Статика
@app.get("/")
def root():
    return FileResponse("index.html")

@app.get("/index.html")
def index():
    return FileResponse("index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

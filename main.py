from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
import os
from supabase import create_client, Client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== ПОДКЛЮЧЕНИЕ К SUPABASE ==========
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("⚠️ ОШИБКА: Supabase не настроен! Проверьте переменные окружения")
    supabase = None
else:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase подключен!")

# Статусы созвона
CALL_STATUSES = [
    "pending", "confirmed", "success", "cancelled_by_client",
    "cancelled_by_expert", "failed", "reschedule_request"
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

# ========== МОДЕЛИ ==========
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

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_user_by_email(email: str):
    if not supabase: return None
    email_lower = email.lower()
    response = supabase.table("users").select("*").eq("email", email_lower).execute()
    if response.data:
        return response.data[0]
    return None

# ========== API USERS ==========
@app.post("/api/users")
def create_user(user: UserCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    email_lower = user.email.lower()
    existing = get_user_by_email(email_lower)
    
    if existing:
        if existing["is_active"]:
            if existing["role"] != user.role:
                raise HTTPException(status_code=403, detail="Эта почта уже зарегистрирована с другой ролью")
            return existing
        else:
            raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован")
    
    user_id = str(uuid.uuid4())
    new_user = {
        "id": user_id,
        "name": user.name,
        "email": email_lower,
        "role": user.role,
        "portfolio_url": user.portfolio_url or "",
        "is_active": 1,
        "created_at": datetime.now().isoformat()
    }
    
    response = supabase.table("users").insert(new_user).execute()
    return response.data[0]

@app.get("/api/users")
def get_users():
    if not supabase: return []
    response = supabase.table("users").select("*").execute()
    return response.data

@app.put("/api/users")
def update_user(update: UserUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    update_data = {}
    if update.name: update_data["name"] = update.name
    if update.role: update_data["role"] = update.role
    if update.is_active is not None: update_data["is_active"] = 1 if update.is_active else 0
    if update.portfolio_url is not None: update_data["portfolio_url"] = update.portfolio_url
    
    if not update_data:
        raise HTTPException(status_code=400, detail="Нет данных для обновления")
    
    response = supabase.table("users").update(update_data).eq("id", update.user_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return response.data[0]

@app.delete("/api/users/{user_id}")
def delete_user(user_id: str):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    # Проверка последнего админа
    admins_response = supabase.table("users").select("id").eq("role", "admin").eq("is_active", 1).execute()
    if len(admins_response.data) <= 1:
        user_response = supabase.table("users").select("role").eq("id", user_id).execute()
        if user_response.data and user_response.data[0]["role"] == "admin":
            raise HTTPException(status_code=403, detail="Нельзя удалить последнего администратора")
    
    # Удаляем слоты эксперта
    user_response = supabase.table("users").select("role").eq("id", user_id).execute()
    if user_response.data and user_response.data[0]["role"] == "expert":
        supabase.table("slots").delete().eq("expert_id", user_id).execute()
    
    supabase.table("users").delete().eq("id", user_id).execute()
    return {"status": "deleted"}

# ========== API SLOTS ==========
@app.post("/api/slots")
def create_slot(slot: SlotCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    slot_id = str(uuid.uuid4())
    new_slot = {
        "id": slot_id,
        "expert_id": slot.expert_id,
        "date": slot.date,
        "start_time": slot.start_time,
        "end_time": slot.end_time,
        "status": "free"
    }
    
    response = supabase.table("slots").insert(new_slot).execute()
    return response.data[0]

@app.get("/api/slots")
def get_slots(expert_id: Optional[str] = None):
    if not supabase: return []
    query = supabase.table("slots").select("*")
    if expert_id:
        query = query.eq("expert_id", expert_id)
    response = query.execute()
    return response.data

@app.get("/api/slots/free")
def get_free_slots():
    if not supabase: return []
    
    slots_response = supabase.table("slots").select("*").eq("status", "free").execute()
    slots = slots_response.data
    
    result = []
    for slot in slots:
        expert_response = supabase.table("users").select("name, portfolio_url").eq("id", slot["expert_id"]).eq("is_active", 1).execute()
        if expert_response.data:
            expert = expert_response.data[0]
            slot["expert_name"] = expert["name"]
            slot["expert_portfolio"] = expert.get("portfolio_url", "")
            result.append(slot)
    return result

@app.put("/api/slots/{slot_id}")
def update_slot(slot_id: str, slot: SlotCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    existing_response = supabase.table("slots").select("*").eq("id", slot_id).execute()
    if not existing_response.data:
        raise HTTPException(status_code=404, detail="Слот не найден")
    
    existing = existing_response.data[0]
    if existing["expert_id"] != slot.expert_id:
        raise HTTPException(status_code=403, detail="Нет прав")
    if existing["status"] != "free":
        raise HTTPException(status_code=400, detail="Нельзя редактировать занятый слот")
    
    response = supabase.table("slots").update({
        "date": slot.date,
        "start_time": slot.start_time,
        "end_time": slot.end_time
    }).eq("id", slot_id).execute()
    
    return response.data[0]

@app.delete("/api/slots/{slot_id}")
def delete_slot(slot_id: str, expert_id: str):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    existing_response = supabase.table("slots").select("*").eq("id", slot_id).execute()
    if not existing_response.data:
        raise HTTPException(status_code=404, detail="Слот не найден")
    
    existing = existing_response.data[0]
    if existing["expert_id"] != expert_id:
        raise HTTPException(status_code=403, detail="Нет прав")
    if existing["status"] != "free":
        raise HTTPException(status_code=400, detail="Нельзя удалить занятый слот")
    
    supabase.table("slots").delete().eq("id", slot_id).execute()
    return {"status": "deleted"}

@app.get("/api/slots/admin/all")
def get_all_slots_with_experts():
    if not supabase: return []
    
    slots_response = supabase.table("slots").select("*").execute()
    slots = slots_response.data
    
    result = []
    for slot in slots:
        expert_response = supabase.table("users").select("name, email, portfolio_url").eq("id", slot["expert_id"]).execute()
        if not expert_response.data:
            continue
        
        expert = expert_response.data[0]
        slot_copy = dict(slot)
        slot_copy["expert_name"] = expert["name"]
        slot_copy["expert_email"] = expert["email"]
        slot_copy["expert_portfolio"] = expert.get("portfolio_url", "")
        
        booking_response = supabase.table("bookings").select("*").eq("slot_id", slot["id"]).execute()
        if booking_response.data:
            booking = booking_response.data[0]
            manager_response = supabase.table("users").select("name").eq("id", booking["manager_id"]).execute()
            slot_copy["booking"] = {
                "client_name": booking["client_name"],
                "client_phone": booking["client_phone"],
                "client_email": booking["client_email"],
                "status": booking["status"],
                "call_status": booking.get("call_status", "pending"),
                "call_comment": booking.get("call_comment", ""),
                "zoom_link": booking["zoom_link"],
                "manager_name": manager_response.data[0]["name"] if manager_response.data else "Unknown"
            }
        result.append(slot_copy)
    return result

# ========== API BOOKINGS ==========
@app.post("/api/bookings")
def create_booking(booking: BookingCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    slot_response = supabase.table("slots").select("status").eq("id", booking.slot_id).execute()
    if not slot_response.data or slot_response.data[0]["status"] != "free":
        raise HTTPException(status_code=400, detail="Слот уже занят")
    
    booking_id = str(uuid.uuid4())
    zoom_link = generate_zoom_link()
    
    new_booking = {
        "id": booking_id,
        "slot_id": booking.slot_id,
        "manager_id": booking.manager_id,
        "client_name": booking.client_name,
        "client_phone": booking.client_phone or "",
        "client_email": booking.client_email or "",
        "status": "pending",
        "call_status": "pending",
        "call_comment": "",
        "zoom_link": zoom_link,
        "created_at": datetime.now().isoformat()
    }
    
    supabase.table("bookings").insert(new_booking).execute()
    supabase.table("slots").update({"status": "booked"}).eq("id", booking.slot_id).execute()
    
    return {"id": booking_id, "zoom_link": zoom_link, "status": "pending"}

@app.post("/api/bookings/confirm")
def confirm_booking(data: ConfirmBooking):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    booking_response = supabase.table("bookings").select("slot_id").eq("id", data.booking_id).execute()
    if not booking_response.data:
        raise HTTPException(status_code=404, detail="Бронирование не найдено")
    
    slot_response = supabase.table("slots").select("expert_id").eq("id", booking_response.data[0]["slot_id"]).execute()
    if not slot_response.data or slot_response.data[0]["expert_id"] != data.expert_id:
        raise HTTPException(status_code=403, detail="Нет прав")
    
     # Обновляем статусы
    supabase.table("bookings").update({
        "status": "confirmed", 
        "call_status": "confirmed"
    }).eq("id", data.booking_id).execute()
    
    supabase.table("slots").update({
        "status": "confirmed"
    }).eq("id", slot_id).execute()
    
    return {"status": "confirmed"}

@app.post("/api/bookings/update-status")
def update_booking_status(update: UpdateBookingStatus):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    if update.status not in CALL_STATUSES:
        raise HTTPException(status_code=400, detail="Неверный статус")
    
    booking_response = supabase.table("bookings").select("slot_id").eq("id", update.booking_id).execute()
    if not booking_response.data:
        raise HTTPException(status_code=404, detail="Бронирование не найдено")
    
    slot_response = supabase.table("slots").select("expert_id").eq("id", booking_response.data[0]["slot_id"]).execute()
    if not slot_response.data or slot_response.data[0]["expert_id"] != update.expert_id:
        raise HTTPException(status_code=403, detail="Нет прав")
    
    supabase.table("bookings").update({
        "call_status": update.status,
        "call_comment": update.comment or ""
    }).eq("id", update.booking_id).execute()
    
    return {"status": update.status, "comment": update.comment}

@app.post("/api/bookings/reschedule")
def reschedule_booking(reschedule: RescheduleBooking):
    if not supabase:
        raise HTTPException(status_code=500, detail="База данных не подключена")
    
    booking_response = supabase.table("bookings").select("slot_id").eq("id", reschedule.booking_id).execute()
    if not booking_response.data:
        raise HTTPException(status_code=404, detail="Бронирование не найдено")
    
    old_slot_id = booking_response.data[0]["slot_id"]
    old_slot_response = supabase.table("slots").select("expert_id").eq("id", old_slot_id).execute()
    
    new_slot_id = str(uuid.uuid4())
    supabase.table("slots").insert({
        "id": new_slot_id,
        "expert_id": old_slot_response.data[0]["expert_id"],
        "date": reschedule.new_date,
        "start_time": reschedule.new_start_time,
        "end_time": reschedule.new_end_time,
        "status": "booked"
    }).execute()
    
    supabase.table("bookings").update({
        "slot_id": new_slot_id,
        "call_status": "pending",
        "status": "pending"
    }).eq("id", reschedule.booking_id).execute()
    
    supabase.table("slots").update({"status": "free"}).eq("id", old_slot_id).execute()
    
    return {"new_slot_id": new_slot_id, "date": reschedule.new_date, "start_time": reschedule.new_start_time, "end_time": reschedule.new_end_time}

@app.get("/api/bookings")
def get_bookings(role: str, user_id: str):
    if not supabase: return []
    
    if role == "admin":
        response = supabase.table("bookings").select("*").execute()
        bookings = response.data
    elif role == "manager":
        response = supabase.table("bookings").select("*").eq("manager_id", user_id).execute()
        bookings = response.data
    elif role == "expert":
        slots_response = supabase.table("slots").select("id").eq("expert_id", user_id).execute()
        slot_ids = [s["id"] for s in slots_response.data]
        if not slot_ids:
            return []
        response = supabase.table("bookings").select("*").in_("slot_id", slot_ids).execute()
        bookings = response.data
    else:
        return []
    
    result = []
    for booking in bookings:
        slot_response = supabase.table("slots").select("*").eq("id", booking["slot_id"]).execute()
        if not slot_response.data:
            continue
        slot = slot_response.data[0]
        
        expert_response = supabase.table("users").select("name, portfolio_url").eq("id", slot["expert_id"]).execute()
        expert = expert_response.data[0] if expert_response.data else None
        
        manager_response = supabase.table("users").select("name").eq("id", booking["manager_id"]).execute()
        manager = manager_response.data[0] if manager_response.data else None
        
        result.append({
            "id": booking["id"],
            "client_name": booking["client_name"],
            "client_phone": booking["client_phone"],
            "client_email": booking["client_email"],
            "status": booking["status"],
            "call_status": booking.get("call_status", "pending"),
            "call_comment": booking.get("call_comment", ""),
            "zoom_link": booking["zoom_link"],
            "created_at": booking["created_at"],
            "date": slot["date"],
            "start_time": slot["start_time"],
            "end_time": slot["end_time"],
            "expert_name": expert["name"] if expert else "Unknown",
            "expert_id": slot["expert_id"],
            "expert_portfolio": expert.get("portfolio_url", "") if expert else "",
            "manager_name": manager["name"] if manager else "Unknown"
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

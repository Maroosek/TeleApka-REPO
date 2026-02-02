import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from contextlib import asynccontextmanager
from typing import List, Optional
from pymongo.errors import DuplicateKeyError

# --- KONFIGURACJA LOGOWANIA ---
GLOBAL_ACCESS_TOKEN = "admin123"

# Próba importu konfigu
try:
    from config import MongoCredentials
    mongo_url = MongoCredentials.MONGODB_URL
    db_name = MongoCredentials.DATABASE_NAME
    collection_name = MongoCredentials.COLLECTION_NAME
    collection_users_name = MongoCredentials.COLLECTION_USERS
    port = int(os.getenv("PORT", 8020))
except ImportError:
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "system_powiadomien")
    collection_name = os.getenv("COLLECTION_NAME", "alerts")
    collection_users_name = os.getenv("COLLECTION_USERS", "user")
    port = int(os.getenv("PORT", 8020))

# Zmienne globalne
client: Optional[AsyncIOMotorClient] = None
collection = None
collection_users = None

# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, collection, collection_users
    print("LOG: Uruchamianie serwera... Łączenie z MongoDB.")
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        collection = db[collection_name]
        await collection.create_index("call_id", unique=True)
        collection_users = db[collection_users_name]
        await collection_users.create_index("username", unique=True)
        print(f"✅ POŁĄCZONO Z MONGODB: {db_name} -> {collection_name} oraz {collection_users_name}")
    except Exception as e:
        print(f"❌ BŁĄD POŁĄCZENIA Z MONGODB: {e}")
    yield
    print("LOG: Zamykanie serwera.")
    if client:
        client.close()

app = FastAPI(lifespan=lifespan, title="System Powiadomień API - Telestrada")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- MODELE DANYCH ---
class AlertSchema(BaseModel):
    id: str
    call_id: str
    caller: str
    source: str
    message: str
    menu_name: Optional[str] = None
    agent_name: Optional[str] = None
    status: Optional[str] = None
    timestamp: datetime

class UserCreate(BaseModel):
    username: str
    assigned_ag: Optional[str] = None
    is_admin: bool = False

class LoginSchema(BaseModel):
    username: str
    token: str

class StatusResponse(BaseModel):
    status: str
    count: int
    alerts: List[AlertSchema]

# --- ENDPOINTY ---

@app.get("/")
async def root():
    return {"message": "API Telestrady działa", "docs": "/docs"}

# --- ZMIENIONY ENDPOINT LOGOWANIA ---
@app.post("/login")
async def login(data: LoginSchema):
    """
    Sprawdza token i usera. Zwraca assigned_ag i is_admin.
    """
    if collection_users is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych")

    if data.token != GLOBAL_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu (token).")

    user = await collection_users.find_one({"username": data.username})
    if not user:
        raise HTTPException(status_code=404, detail="Taki użytkownik nie istnieje.")

    # Zwracamy kluczowe dane do filtrowania w kliencie
    return {
        "message": "Zalogowano",
        "username": user["username"],
        "is_admin": user.get("is_admin", False),
        "assigned_ag": user.get("assigned_ag")  # <--- TO DODANO
    }

@app.get("/status", response_model=StatusResponse)
async def get_status():
    if collection is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych")

    cursor = collection.find().sort("last_updated", -1).limit(10)
    alerts_docs = await cursor.to_list(length=10)

    mapped_alerts = []
    for doc in alerts_docs:
        mapped_alerts.append({
            "id": str(doc["_id"]),
            "call_id": doc.get("call_id", "manual"),
            "caller": doc.get("caller", "Nieznany"),
            "source": doc.get("source", "Nieznane"),
            "message": doc.get("message", ""),
            "menu_name": doc.get("menu_name"),
            "agent_name": doc.get("agent_name"),
            "status": doc.get("status"),
            "timestamp": doc.get("last_updated", datetime.now())
        })

    return {
        "status": "ok",
        "count": len(mapped_alerts),
        "alerts": mapped_alerts
    }

@app.post("/users", status_code=201)
async def create_user(user: UserCreate):
    if collection_users is None:
        raise HTTPException(status_code=503, detail="Brak połączenia z bazą użytkowników")
    user_data = user.dict()
    #user_data["created_at"] = datetime.now()
    try:
        result = await collection_users.insert_one(user_data)
        return {"message": "Użytkownik dodany", "username": user.username, "id": str(result.inserted_id)}
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail=f"Użytkownik '{user.username}' już istnieje.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy: {str(e)}")

# --- WEBHOOK  ---
@app.get("/webhook/telestrada")
async def telestrada_webhook(
        id: str = Query(..., description="Unikalne ID połączenia (#call_id#)"),
        numer: str = Query(..., description="Numer dzwoniącego (#num_a#)"),
        ag: Optional[str] = Query(None, description="Numer docelowy/agenta (#num_b#)"),
        czy_trwa: bool = Query(True, description="Status online: true/false (#online#)"),
        status: Optional[str] = Query(None, description="Status tekstowy (#status#)"),
        menu: Optional[str] = Query(None, description="Nazwa menu (#menu_name#)"),
        agent_name: Optional[str] = Query(None, description="Nazwa agenta (#agent_name#)")
):
    if collection is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych")
    if not czy_trwa:
        print(f"📞 Koniec połączenia {id} ({status}). Usuwam.")
        await collection.delete_one({"call_id": id})
        return "OK_DELETED"
    existing = await collection.find_one({"call_id": id})
    if not existing:
        count = await collection.count_documents({})
        if count >= 10:
            print("⚠️ Osiągnięto limit 10 połączeń. Ignoruję nowe.")
            return "LIMIT_REACHED"
    caller_num = numer
    if ag: source_num = ag
    elif menu: source_num = menu
    else: source_num = "Infolinia"
    display_message = f"{caller_num} ➡️ {source_num}"
    if menu: display_message += f" [{menu}]"
    alert_data = {
        "call_id": id,
        "caller": caller_num,
        "source": source_num,
        "message": display_message,
        "menu_name": menu,
        "agent_name": agent_name,
        "status": status,
        "last_updated": datetime.now()
    }
    await collection.update_one({"call_id": id}, {"$set": alert_data}, upsert=True)
    print(f"📞 Aktualizacja połączenia {id}: {caller_num} -> {source_num} (Status: {status})")
    return "OK_UPDATED"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
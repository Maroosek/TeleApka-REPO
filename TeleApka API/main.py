import os, random, time
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from contextlib import asynccontextmanager
from typing import List, Optional, Union
from pymongo.errors import DuplicateKeyError
from urllib.parse import parse_qs
import httpx

from Bitrix24 import find_owner_by_incoming_sms, add_new_activity
from config import Telestrada

import urllib.request
import urllib.parse

# Próba importu konfigu
try:
    from config import MongoCredentials

    mongo_url = MongoCredentials.MONGODB_URL
    db_name = MongoCredentials.DATABASE_NAME
    collection_name = MongoCredentials.COLLECTION_NAME
    collection_users_name = MongoCredentials.COLLECTION_USERS
    collection_logs_name = MongoCredentials.COLLECTION_HISTORY
    collection_sms_name = MongoCredentials.COLLECTION_SMS
    GLOBAL_ACCESS_TOKEN = MongoCredentials.GLOBAL_ACCESS_TOKEN
    port = int(os.getenv("PORT", 8020))
except ImportError:
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "system_powiadomien")
    collection_name = os.getenv("COLLECTION_NAME", "alerts")
    collection_users_name = os.getenv("COLLECTION_USERS", "user")
    collection_logs_name = os.getenv("COLLECTION_HISTORY", "history")
    collection_sms_name = os.getenv("COLLECTION_SMS", "smsReceived")
    GLOBAL_ACCESS_TOKEN = os.getenv("GLOBAL_ACCESS_TOKEN", "admin123")
    port = int(os.getenv("PORT", 8020))

# Zmienne globalne
client: Optional[AsyncIOMotorClient] = None
collection = None
collection_users = None
collection_logs = None
collection_sms = None


# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, collection, collection_users, collection_logs, collection_sms
    print("LOG: Uruchamianie serwera... Łączenie z MongoDB.")

    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]

        collection = db[collection_name]
        collection_users = db[collection_users_name]
        collection_logs = db[collection_logs_name]
        collection_sms = db[collection_sms_name]

        print(f"✅ POŁĄCZONO Z MONGODB: {db_name}")

        try:
            await collection.create_index("call_id", unique=True)
        except Exception as e:
            print(f"⚠️ Nie udało się utworzyć indeksu dla ALERTS: {e}")

        try:
            await collection_users.create_index("username", unique=True)
        except Exception as e:
            print(f"⚠️ Nie udało się utworzyć indeksu dla USERS: {e}")

        try:
            await collection_logs.create_index("phone_number", unique=True)
        except Exception as e:
            print(f"⚠️ Nie udało się utworzyć indeksu dla HISTORY: {e}")

    except Exception as e:
        print(f"❌ KRYTYCZNY BŁĄD POŁĄCZENIA Z BAZĄ: {e}")

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
    assigned_ag: Optional[List[str]] = None
    is_admin: bool = False


class LoginSchema(BaseModel):
    username: str
    token: str


class StatusResponse(BaseModel):
    status: str
    count: int
    alerts: List[AlertSchema]


class LogEntrySchema(BaseModel):
    id: str
    phone_number: str
    last_call: datetime
    last_menu_full: Optional[str] = None


class HistoryResponse(BaseModel):
    count: int
    logs: List[LogEntrySchema]


# --- ENDPOINTY ---

@app.get("/")
async def root():
    return {"message": "API Telestrady działa", "docs": "/docs"}


@app.post("/login")
async def login(data: LoginSchema):
    if collection_users is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych")

    if data.token != GLOBAL_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu (token).")

    user = await collection_users.find_one({"username": data.username})
    if not user:
        raise HTTPException(status_code=404, detail="Taki użytkownik nie istnieje.")

    # ZMIANA 2: Normalizacja assigned_ag, aby zawsze zwracać listę (nawet dla starych rekordów)
    raw_assigned = user.get("assigned_ag")
    assigned_list = []

    if raw_assigned:
        if isinstance(raw_assigned, list):
            assigned_list = raw_assigned
        else:
            # Jeśli w bazie jest stary format (string), zamień go na listę jednoelementową
            assigned_list = [str(raw_assigned)]

    return {
        "message": "Zalogowano",
        "username": user["username"],
        "is_admin": user.get("is_admin", False),
        "assigned_ag": assigned_list  # Zwracamy zawsze listę
    }


@app.get("/status", response_model=StatusResponse)
async def get_status():
    if collection is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych")

    cursor = collection.find().sort("last_updated", -1).limit(25)
    alerts_docs = await cursor.to_list(length=25)

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


@app.get("/telestrada/connections")
async def get_telestrada_connections(
        date: str = Query(..., description="Data w formacie YYYY-MM-DD", example="2026-02-05"),
        token: str = Query(..., description="Token administratora")
):
    # ZABEZPIECZENIE
    if token != GLOBAL_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu (token).")

    api_key = getattr(Telestrada, "API_KEY", None)
    if not api_key:
        api_key = os.getenv("TELESTRADA_API_KEY")

    if not api_key:
        raise HTTPException(status_code=500, detail="Brak skonfigurowanego klucza API Telestrady (Telestrada.API_KEY).")

    url = "https://api.telestrada.pl/api/v1/callcontact/connections"
    headers = {
        "api-key": api_key,
        "Accept": "application/json"
    }
    params = {
        "date": date
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, params=params, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Błąd API Telestrady: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Błąd połączenia z Telestradą: {str(e)}")


@app.get("/history", response_model=HistoryResponse)
async def get_history(token: str = Query(..., description="Token administratora")):
    # ZABEZPIECZENIE
    if token != GLOBAL_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu (token).")

    if collection_logs is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych logów")

    cursor = collection_logs.find().sort("last_call", -1)
    logs_docs = await cursor.to_list(length=None)

    mapped_logs = []
    for doc in logs_docs:
        mapped_logs.append({
            "id": str(doc["_id"]),
            "phone_number": doc.get("phone_number"),
            "last_call": doc.get("last_call"),
            "last_menu_full": doc.get("last_menu_full")
        })

    return {
        "count": len(mapped_logs),
        "logs": mapped_logs
    }


@app.post("/users", status_code=201)
async def create_user(user: UserCreate, token: str):
    if collection_users is None:
        raise HTTPException(status_code=503, detail="Brak połączenia z bazą użytkowników")

    # Pydantic sam zwaliduje, że assigned_ag to lista stringów lub None
    user_data = user.dict()

    if token != GLOBAL_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu (token).")

    try:
        result = await collection_users.insert_one(user_data)
        return {"message": "Użytkownik dodany", "username": user.username, "id": str(result.inserted_id)}
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail=f"Użytkownik '{user.username}' już istnieje.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd bazy: {str(e)}")


@app.post("/SMSAPI/addSMS")
async def add_sms(request: Request):
    if collection_sms is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8")
        data = parse_qs(body_str)

        def get_val(key):
            return data.get(key, [None])[0]

        sms_to = get_val("sms_to")
        sms_from = get_val("sms_from")
        sms_text = get_val("sms_text")
        sms_date = get_val("sms_date")
        username = get_val("username")
        msg_id_raw = get_val("MsgId")

        # Tu byłby kod zapisu do mongo (pominięty w skrócie, bo nie dotyczy pytania)

        print("🔄 Próba dodania aktywności do Bitrix24...")

        try:
            bitrixData = find_owner_by_incoming_sms(sms_from)
            if bitrixData:
                Owner_id = bitrixData["OWNER_ID"]
                Owner_type = bitrixData["OWNER_TYPE_ID"]
                Responsible = bitrixData.get("RESPONSIBLE_ID", "1")
                description = f"[B]SMS od:[/B] {sms_from}\n[B]Treść:[/B]\n{sms_text}"
                add_new_activity(Owner_id, Owner_type, Responsible, description)
            else:
                print("ℹ️ Nie dodano aktywności do Bitrix (brak powiązanego Deala/Leada).")
        except Exception as e:
            print(f"❌ Błąd integracji Bitrix: {e}")
            import traceback
            traceback.print_exc()

        return Response(content="OK", media_type="text/plain")

    except DuplicateKeyError:
        print(f"⚠️ Duplikat SMS. Ignoruję.")
        return Response(content="OK", media_type="text/plain")
    except Exception as e:
        print(f"❌ Błąd ogólny zapisu SMS: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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

    if menu and collection_logs is not None:
        try:
            target_number = menu.strip()[-9:]
            if len(target_number) == 9:
                await collection_logs.update_one(
                    {"phone_number": target_number},
                    {
                        "$set": {
                            "last_call": datetime.now(),
                            "last_menu_full": menu
                        },
                    },
                    upsert=True
                )
                print(f"💾 Zalogowano połączenie dla numeru z menu: {target_number}")
        except Exception as e:
            print(f"❌ Błąd podczas logowania numeru z menu: {e}")

    if not czy_trwa:
        print(f"📞 Koniec połączenia {id} ({status}). Usuwam.")
        await collection.delete_one({"call_id": id})
        return "OK_DELETED"

    existing = await collection.find_one({"call_id": id})
    if not existing:
        count = await collection.count_documents({})
        if count >= 50:
            print("⚠️ Osiągnięto limit 50 połączeń. Ignoruję nowe.")
            return "LIMIT_REACHED"

    caller_num = numer
    source_num = ag if ag else (menu if menu else "Infolinia")

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
import os
from fastapi import FastAPI, HTTPException, Query, Request, Response, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from contextlib import asynccontextmanager
from typing import List, Optional
from pymongo.errors import DuplicateKeyError
from urllib.parse import parse_qs
import httpx

from Bitrix24 import find_owner_by_incoming_sms, add_new_activity
from config import Telestrada

# Próba importu konfigu
try:
    from config import MongoCredentials

    mongo_url = MongoCredentials.MONGODB_URL
    db_name = MongoCredentials.DATABASE_NAME
    collection_name = MongoCredentials.COLLECTION_NAME
    collection_users_name = MongoCredentials.COLLECTION_USERS
    collection_logs_name = MongoCredentials.COLLECTION_HISTORY
    collection_sms_name = MongoCredentials.COLLECTION_SMS
    collection_stats_name = MongoCredentials.COLLECTION_STATS
    collection_stats_data_name = MongoCredentials.COLLECTION_STATS_DATA
    GLOBAL_ACCESS_TOKEN = MongoCredentials.GLOBAL_ACCESS_TOKEN
    port = int(os.getenv("PORT", 8020))
except ImportError:
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "system_powiadomien")
    collection_name = os.getenv("COLLECTION_NAME", "alerts")
    collection_users_name = os.getenv("COLLECTION_USERS", "user")
    collection_logs_name = os.getenv("COLLECTION_HISTORY", "history")
    collection_stats_name = os.getenv("COLLECTION_STATS", "stats")
    collection_stats_data_name = os.getenv("COLLECTION_STATS_DATA", "statsData")
    collection_sms_name = os.getenv("COLLECTION_SMS", "smsReceived")
    GLOBAL_ACCESS_TOKEN = os.getenv("GLOBAL_ACCESS_TOKEN", "admin123")
    port = int(os.getenv("PORT", 8020))

# Zmienne globalne
client: Optional[AsyncIOMotorClient] = None
collection = None
collection_users = None
collection_logs = None
collection_sms = None
collection_stats = None
collection_stats_data = None


# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, collection, collection_users, collection_logs, collection_sms, collection_stats, collection_stats_data
    print("LOG: Uruchamianie serwera... Łączenie z MongoDB.")

    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]

        collection = db[collection_name]
        collection_users = db[collection_users_name]
        collection_logs = db[collection_logs_name]
        collection_sms = db[collection_sms_name]
        collection_stats = db[collection_stats_name]
        collection_stats_data = db[collection_stats_data_name]

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

    # ZMIANA: Normalizacja assigned_ag
    raw_assigned = user.get("assigned_ag")
    assigned_list = []

    if raw_assigned:
        if isinstance(raw_assigned, list):
            assigned_list = raw_assigned
        else:
            assigned_list = [str(raw_assigned)]

    return {
        "message": "Zalogowano",
        "username": user["username"],
        "is_admin": user.get("is_admin", False),
        "assigned_ag": assigned_list
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
            "ivr": doc.get("ivr", "Nieznany"),
            "caller": doc.get("caller", "Nieznany"),
            "source": doc.get("source", "Nieznane"),
            "message": doc.get("message", ""),
            "menu_name": doc.get("menu_name"),
            "agent_name": doc.get("agent_name"),
            "status": doc.get("status"),
            "answered": doc.get("answered", False),
            "timestamp": doc.get("last_updated", datetime.now())
        })

    return {
        "status": "ok",
        "count": len(mapped_alerts),
        "alerts": mapped_alerts
    }


@app.post("/stats", status_code=201)
async def add_stats(payload: dict = Body(...)):
    """
    Zapisuje raport statystyczny.
    """
    if collection_stats is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych statystyk")

    if "date" not in payload:
        raise HTTPException(status_code=400, detail="Brak pola 'date' (YYYY-MM-DD) w przesłanym JSON.")

    try:
        payload["_created_at"] = datetime.now()
        result = await collection_stats.insert_one(payload)
        return {"status": "saved", "id": str(result.inserted_id)}
    except Exception as e:
        print(f"❌ Błąd zapisu statystyk: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def get_stats(
        date_from: str = Query(..., description="Data początkowa (YYYY-MM-DD)", example="2026-02-01"),
        date_to: str = Query(..., description="Data końcowa (YYYY-MM-DD)", example="2026-02-28"),
        token: str = Query(..., description="Token administratora")
):
    if token != GLOBAL_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu (token).")

    if collection_stats is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych statystyk")

    try:
        query = {
            "date": {
                "$gte": date_from,
                "$lte": date_to
            }
        }

        cursor = collection_stats.find(query).sort("date", 1)
        stats_docs = await cursor.to_list(length=None)

        mapped_stats = []
        for doc in stats_docs:
            doc["id"] = str(doc.pop("_id"))
            if "_created_at" in doc:
                del doc["_created_at"]
            mapped_stats.append(doc)

        return {
            "count": len(mapped_stats),
            "date_from": date_from,
            "date_to": date_to,
            "data": mapped_stats
        }

    except Exception as e:
        print(f"❌ Błąd odczytu statystyk: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- NOWY ENDPOINT POST ---
@app.post("/stats-data", status_code=201)
async def add_stats_data(
        payload: dict = Body(...),
        token: str = Query(..., description="Token administratora")
):
    """
    Zapisuje dane konfiguracyjne do statystyk.
    Dodaje automatycznie pole created_at.
    """
    if token != GLOBAL_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu (token).")

    if collection_stats_data is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych stats_data")

    try:
        # Dodajemy datę stworzenia
        payload["created_at"] = datetime.now()

        result = await collection_stats_data.insert_one(payload)

        return {
            "status": "saved",
            "id": str(result.inserted_id),
            "created_at": payload["created_at"]
        }
    except Exception as e:
        print(f"❌ Błąd zapisu stats_data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- ZMODYFIKOWANY ENDPOINT GET ---
@app.get("/stats-data")
async def get_stats_data_list(token: str = Query(..., description="Token administratora")):
    """
    Pobiera dane konfiguracyjne do statystyk.
    Zwraca tylko NAJNOWSZY wpis (sortowanie po created_at malejąco).
    """
    if token != GLOBAL_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu (token).")

    if collection_stats_data is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych stats_data")

    try:
        # Pobieramy tylko jeden, najnowszy rekord
        cursor = collection_stats_data.find().sort("created_at", -1).limit(1)
        docs = await cursor.to_list(length=1)

        mapped_data = []
        for doc in docs:
            doc["id"] = str(doc.pop("_id"))
            mapped_data.append(doc)

        return {
            "count": len(mapped_data),
            "data": mapped_data
        }

    except Exception as e:
        print(f"❌ Błąd odczytu stats_data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/telestrada/connections")
async def get_telestrada_connections(
        date: str = Query(..., description="Data w formacie YYYY-MM-DD", example="2026-02-05"),
        token: str = Query(..., description="Token administratora")
):
    if token != GLOBAL_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Nieprawidłowy kod dostępu (token).")

    api_key = getattr(Telestrada, "API_KEY", None)
    if not api_key:
        api_key = os.getenv("TELESTRADA_API_KEY")

    if not api_key:
        raise HTTPException(status_code=500, detail="Brak skonfigurowanego klucza API Telestrady.")

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
        # sms_date = get_val("sms_date")
        # username = get_val("username")
        # msg_id_raw = get_val("MsgId")

        print("🔄 Próba dodania aktywności do Bitrix24...")

        try:
            bitrixData = find_owner_by_incoming_sms(sms_from)
            if bitrixData:
                owner_id = bitrixData["OWNER_ID"]
                owner_type = bitrixData["OWNER_TYPE_ID"]
                responsible = bitrixData.get("RESPONSIBLE_ID", "1")
                description = f"[B]SMS od:[/B] {sms_from}\n[B]Treść:[/B]\n{sms_text}"
                add_new_activity(owner_id, owner_type, responsible, description)
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
        ivr: str = Query(..., description="Identyfikator IVR (#ivr_id#)"),
        id: str = Query(..., description="Unikalne ID połączenia (#call_id#)"),
        numer: str = Query(..., description="Numer dzwoniącego (#num_a#)"),
        ag: Optional[str] = Query(None, description="Numer docelowy/agenta (#num_b#)"),
        czy_trwa: bool = Query(True, description="Status online: true/false (#online#)"),
        status: Optional[str] = Query(None, description="Status tekstowy (#status#)"),
        answered: Optional[str] = Query(None, description="Status odpowiedzi (#answered#)"),
        menu: Optional[str] = Query(None, description="Nazwa menu (#menu_name#)"),
        agent_name: Optional[str] = Query(None, description="Nazwa agenta (#agent_name#)")
):
    if collection is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych")

    if ivr and len(ivr) == 11 and ivr.startswith("48"):
        ivr = ivr[2:]

    # --- ZMIANA: Logowanie numeru z IVR ---
    if collection_logs is not None:
        try:
            # Używamy bezpośrednio numeru IVR
            target_number = ivr

            # (Opcjonalnie) Możemy dodać walidację czy ivr nie jest puste
            if target_number:
                await collection_logs.update_one(
                    {"phone_number": target_number},
                    {
                        "$set": {
                            "last_call": datetime.now(),
                            "last_menu_full": menu  # Zapisujemy nazwę menu informacyjnie, jeśli jest
                        },
                    },
                    upsert=True
                )
                print(f"💾 Zalogowano połączenie dla numeru IVR: {target_number}")
        except Exception as e:
            print(f"❌ Błąd podczas logowania numeru IVR: {e}")

    if not czy_trwa:
        print(f"📞 Koniec połączenia {id} ({status}). Usuwam.")
        await collection.delete_one({"call_id": id})
        return "OK_DELETED"

    existing = await collection.find_one({"call_id": id})
    if not existing:
        count = await collection.count_documents({})
        if count >= 100:
            print("⚠️ Osiągnięto limit 100 połączeń. Ignoruję nowe.")
            return "LIMIT_REACHED"

    caller_num = numer
    source_num = ag if ag else (menu if menu else "Infolinia")

    display_message = f"{caller_num} ➡️ {source_num}"
    if menu: display_message += f" [{menu}]"

    alert_data = {
        "call_id": id,
        "ivr": ivr,
        "caller": caller_num,
        "source": source_num,
        "message": display_message,
        "menu_name": menu,
        "agent_name": agent_name,
        "status": status,
        "answered": answered,
        "last_updated": datetime.now()
    }

    await collection.update_one({"call_id": id}, {"$set": alert_data}, upsert=True)
    print(f"📞 Aktualizacja połączenia {id}: {caller_num} -> {source_num} (Status: {status})")
    return "OK_UPDATED"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=port)
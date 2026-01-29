import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from contextlib import asynccontextmanager
from bson import ObjectId
from typing import List, Optional

# Próba importu konfigu, ale z fallbackiem (żeby nie wywalało błędu jak brakuje pliku na serwerze)
try:
    from config import MongoCredentials

    mongo_url = MongoCredentials.MONGODB_URL
    db_name = MongoCredentials.DATABASE_NAME
    collection_name = MongoCredentials.COLLECTION_NAME
except ImportError:
    # Wartości domyślne lub pobierane ze zmiennych środowiskowych (bezpieczniejsze na produkcji)
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "system_powiadomien")
    collection_name = os.getenv("COLLECTION_NAME", "alerts")

# Zmienne globalne dla klienta bazy
client: Optional[AsyncIOMotorClient] = None
collection = None


# --- LIFESPAN (Zarządzanie życiem aplikacji) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, collection

    print("LOG: Uruchamianie serwera... Łączenie z MongoDB.")
    try:
        # Inicjalizacja klienta
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        collection = db[collection_name]

        # Test połączenia (Ping)
        await client.admin.command('ping')
        print(f"✅ POŁĄCZONO Z MONGODB: {db_name} -> {collection_name}")

    except Exception as e:
        print(f"❌ BŁĄD POŁĄCZENIA Z MONGODB: {e}")
        # Nie przerywamy startu, żeby API mogło zwrócić błąd w /health

    yield  # Tu aplikacja działa

    print("LOG: Zamykanie serwera.")
    if client:
        client.close()


app = FastAPI(lifespan=lifespan, title="System Powiadomień API")

# --- KONFIGURACJA CORS (Dla dostępu "ze świata") ---
# Pozwalamy na wszystko (*), bo ma być testowo i publicznie.
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- MODELE DANYCH (Pydantic) ---

class AlertSchema(BaseModel):
    id: str
    caller: str
    source: str
    message: str
    timestamp: datetime


class StatusResponse(BaseModel):
    count: int
    alerts: List[AlertSchema]
    status: str = "ok"  # Dodatkowe pole statusu


class TriggerRequest(BaseModel):
    caller: str
    source: str
    message: str


class CloseRequest(BaseModel):
    alert_id: str


# --- POMOCNIKI ---
def map_alert(alert):
    return {
        "id": str(alert["_id"]),
        "caller": alert.get("caller", "Nieznany"),
        "source": alert.get("source", "Nieznane"),
        "message": alert.get("message", ""),
        "timestamp": alert.get("last_updated", datetime.now())
    }


# --- ENDPOINTY DIAGNOSTYCZNE ---

@app.get("/")
async def root():
    """Strona startowa - szybki test czy serwer działa."""
    return {
        "message": "System Powiadomień API działa!",
        "docs_url": "/docs",
        "redoc_url": "/redoc"
    }


@app.get("/health")
async def health_check():
    """Sprawdza stan bazy danych."""
    db_status = "disconnected"
    if client:
        try:
            await client.admin.command('ping')
            db_status = "connected"
        except Exception as e:
            db_status = f"error: {str(e)}"

    return {
        "status": "active",
        "database": db_status,
        "timestamp": datetime.now()
    }


# --- ENDPOINTY LOGIKI BIZNESOWEJ ---

@app.get("/status", response_model=StatusResponse)
async def get_status():
    if collection is None:
        raise HTTPException(status_code=503, detail="Brak połączenia z bazą danych")

    cursor = collection.find().sort("last_updated", -1).limit(5)
    alerts_docs = await cursor.to_list(length=5)
    mapped_alerts = [map_alert(doc) for doc in alerts_docs]

    return {
        "status": "ok",
        "count": len(mapped_alerts),
        "alerts": mapped_alerts
    }


@app.post("/trigger")
async def trigger_alert(req: TriggerRequest):
    if collection is None:
        raise HTTPException(status_code=503, detail="Brak połączenia z bazą danych")

    count = await collection.count_documents({})
    if count >= 5:
        # Zamiast błędu, usuń najstarszy (opcja FIFO), żeby system się nie zatykał
        # Lub zostaw return error, jeśli wolisz manualne czyszczenie
        return {"result": "error", "message": "Limit 5 powiadomień. Zamknij stare."}

    new_alert = {
        "status": "active",
        "caller": req.caller,
        "source": req.source,
        "message": req.message,
        "last_updated": datetime.now()
    }

    result = await collection.insert_one(new_alert)
    return {"result": "success", "id": str(result.inserted_id)}


@app.post("/close")
async def close_alert(req: CloseRequest):
    if collection is None:
        raise HTTPException(status_code=503, detail="Brak połączenia z bazą danych")

    try:
        obj_id = ObjectId(req.alert_id)
        result = await collection.delete_one({"_id": obj_id})

        if result.deleted_count == 1:
            return {"result": "closed", "id": req.alert_id}
        else:
            return {"result": "not_found", "message": "Nie znaleziono takiego alertu"}
    except Exception as e:
        return {"result": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn

    # Pobieramy port z env lub domyślnie 8020
    # Host 0.0.0.0 jest KLUCZOWY dla dostępu z sieci
    port = int(os.getenv("PORT", 8020))
    uvicorn.run(app, host="0.0.0.0", port=port)
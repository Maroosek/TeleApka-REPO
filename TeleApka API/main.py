from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from contextlib import asynccontextmanager
from bson import ObjectId
from typing import List
from config import MongoCredentials

# KONFIGURACJA MONGO
client = AsyncIOMotorClient(MongoCredentials.MONGODB_URL)
sync_db = client[MongoCredentials.DATABASE_NAME]
# Używamy tej samej kolekcji, ale teraz będziemy w niej trzymać wiele dokumentów
collection = sync_db[MongoCredentials.COLLECTION_NAME]


# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Przy starcie możemy np. wyczyścić stare wiszące alerty (opcjonalne)
    # await collection.delete_many({})
    print("LOG: Serwer gotowy. Obsługa wielu powiadomień.")
    yield
    print("LOG: Zamykanie serwera.")
    client.close()


app = FastAPI(lifespan=lifespan, title="System Powiadomień API")


# --- MODELE DANYCH (Pydantic) ---

class AlertSchema(BaseModel):
    id: str  # Przekonwertowane z ObjectId
    caller: str
    source: str
    message: str
    timestamp: datetime


class StatusResponse(BaseModel):
    count: int
    alerts: List[AlertSchema]


class TriggerRequest(BaseModel):
    caller: str
    source: str
    message: str


class CloseRequest(BaseModel):
    alert_id: str


# --- POMOCNIKI ---
def map_alert(alert):
    """Pomocnik do konwersji obiektu Mongo na format JSON"""
    return {
        "id": str(alert["_id"]),
        "caller": alert.get("caller", "Nieznany"),
        "source": alert.get("source", "Nieznane"),
        "message": alert.get("message", ""),
        "timestamp": alert.get("last_updated", datetime.now())
    }


# --- ENDPOINTY ---

@app.get("/status", response_model=StatusResponse)
async def get_status():
    """Pobiera listę aktywnych alertów (max 5 najnowszych)."""
    # Pobieramy wszystkie dokumenty, sortujemy od najnowszego
    cursor = collection.find().sort("last_updated", -1).limit(5)
    alerts_docs = await cursor.to_list(length=5)

    # Mapujemy na format wyjściowy
    mapped_alerts = [map_alert(doc) for doc in alerts_docs]

    return {
        "count": len(mapped_alerts),
        "alerts": mapped_alerts
    }


@app.post("/trigger")
async def trigger_alert(req: TriggerRequest):
    """Dodaje nowy alert do listy."""
    # Opcjonalnie: Zabezpieczenie przed spamem (max 5 aktywnych)
    count = await collection.count_documents({})
    if count >= 5:
        return {"result": "error", "message": "Osiągnięto limit 5 powiadomień. Zamknij stare."}

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
    """Usuwa KONKRETNY alert na podstawie ID."""
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

    uvicorn.run(app, host="0.0.0.0", port=8000)
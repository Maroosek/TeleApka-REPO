import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from contextlib import asynccontextmanager
from typing import List, Optional

# Próba importu konfigu, ale z fallbackiem (żeby nie wywalało błędu jak brakuje pliku na serwerze)
try:
    from config import MongoCredentials

    mongo_url = MongoCredentials.MONGODB_URL
    db_name = MongoCredentials.DATABASE_NAME
    collection_name = MongoCredentials.COLLECTION_NAME
    port = int(os.getenv("PORT", 8020))
except ImportError:
    # Wartości domyślne lub pobierane ze zmiennych środowiskowych (bezpieczniejsze na produkcji)
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "system_powiadomien")
    collection_name = os.getenv("COLLECTION_NAME", "alerts")
    port = int(os.getenv("PORT", 8020))

# Zmienne globalne
client: Optional[AsyncIOMotorClient] = None
collection = None


# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, collection
    print("LOG: Uruchamianie serwera... Łączenie z MongoDB.")
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        collection = db[collection_name]

        # Tworzymy indeks na call_id, żeby wyszukiwanie było szybkie
        await collection.create_index("call_id", unique=True)
        print(f"✅ POŁĄCZONO Z MONGODB: {db_name} -> {collection_name}")
    except Exception as e:
        print(f"❌ BŁĄD POŁĄCZENIA Z MONGODB: {e}")

    yield

    print("LOG: Zamykanie serwera.")
    if client:
        client.close()


app = FastAPI(lifespan=lifespan, title="System Powiadomień API - Telestrada")

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- MODELE DANYCH ---

class AlertSchema(BaseModel):
    id: str  # Wewnętrzne ID Mongo
    call_id: str  # ID połączenia z Telestrady
    caller: str
    source: str
    message: str
    menu_name: Optional[str] = None
    agent_name: Optional[str] = None
    status: Optional[str] = None
    timestamp: datetime


class StatusResponse(BaseModel):
    status: str
    count: int
    alerts: List[AlertSchema]


# --- ENDPOINTY ---

@app.get("/")
async def root():
    return {"message": "API Telestrady działa", "docs": "/docs"}


@app.get("/status", response_model=StatusResponse)
async def get_status():
    """Pobiera listę aktywnych alertów (Limit zwiększony do 10)."""
    if collection is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych")

    # Pobieramy 10 najnowszych
    cursor = collection.find().sort("last_updated", -1).limit(10)
    alerts_docs = await cursor.to_list(length=10)

    mapped_alerts = []
    for doc in alerts_docs:
        mapped_alerts.append({
            "id": str(doc["_id"]),
            "call_id": doc.get("call_id", "manual"),  # fallback dla ręcznych testów
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


@app.get("/webhook/telestrada")
async def telestrada_webhook(
        # Parametry mapowane z makr Telestrady
        id: str = Query(..., description="Unikalne ID połączenia (#call_id#)"),
        numer: str = Query(..., description="Numer dzwoniącego (#num_a#)"),
        ag: Optional[str] = Query(None, description="Numer docelowy/agenta (#num_b#)"),
        czy_trwa: bool = Query(True, description="Status online: true/false (#online#)"),
        status: Optional[str] = Query(None, description="Status tekstowy (#status#)"),
        menu: Optional[str] = Query(None, description="Nazwa menu (#menu_name#)"),
        agent_name: Optional[str] = Query(None, description="Nazwa agenta (#agent_name#)")
):
    """
    Endpoint, który wpisujesz w panelu Telestrady.
    Przyjmuje parametry GET i zarządza stanem bazy danych.
    """
    if collection is None:
        raise HTTPException(status_code=503, detail="Brak bazy danych")

    # LOGIKA 1: USUWANIE
    # Usuwamy wpis tylko wtedy, gdy połączenie fizycznie się zakończyło (czy_trwa=False)
    # Statusy typu CALLED, CANCELLED, BUSY zazwyczaj przychodzą z czy_trwa=False
    if not czy_trwa:
        print(f"📞 Koniec połączenia {id} ({status}). Usuwam.")
        await collection.delete_one({"call_id": id})
        return "OK_DELETED"

    # LOGIKA 2: AKTUALIZACJA / NOWE
    # Jeśli status to ANSWERED, to chcemy to zaktualizować w bazie, żeby klient widział "Odebrane" na zielono

    existing = await collection.find_one({"call_id": id})
    if not existing:
        count = await collection.count_documents({})
        if count >= 10:
            print("⚠️ Osiągnięto limit 10 połączeń. Ignoruję nowe.")
            return "LIMIT_REACHED"

        # Przygotowanie danych
    caller_num = numer

        # LOGIKA NAZEWNICTWA CELU (Target)
        # 1. Jeśli mamy numer agenta (ag), to on jest priorytetem
    if ag:
        source_num = ag
        # 2. Jeśli nie ma agenta, ale klient wybrał MENU (np. "Dział Sprzedaży"), pokazujemy to
    elif menu:
        source_num = menu
        # 3. Jeśli to sam początek i nie ma nic, zostaje Infolinia
    else:
        source_num = "Infolinia"


    # Budowanie wiadomości
    display_message = f"{caller_num} ➡️ {source_num}"
    if menu:
        display_message += f" [{menu}]"

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

    await collection.update_one(
        {"call_id": id},
        {"$set": alert_data},
        upsert=True
    )

    print(f"📞 Aktualizacja połączenia {id}: {caller_num} -> {source_num} (Status: {status})")
    return "OK_UPDATED"


# Endpoint do ręcznego testowania (Postman/cURL) - stary endpoint
@app.post("/trigger")
async def manual_trigger(req: BaseModel):
    # Prosty wrapper dla kompatybilności wstecznej, generuje losowe ID
    import uuid
    fake_id = str(uuid.uuid4())
    # ... logika dodawania ...
    return {"status": "deprecated", "message": "Użyj webhooka GET /webhook/telestrada"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=port)
import os
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime
from zoneinfo import ZoneInfo
import re

WARSAW = ZoneInfo("Europe/Warsaw")
import httpx
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
from pymongo.errors import DuplicateKeyError
from urllib.parse import parse_qs

from Bitrix24 import find_owner_by_incoming_sms, add_new_activity, bitrix_call

# Konfiguracja
try:
    from config import MongoCredentials, Config_PlFon, Config_Auth, BitrixConfig

    mongo_url = MongoCredentials.MONGODB_URL
    db_name = MongoCredentials.DATABASE_NAME
    collection_SMS = MongoCredentials.COLLECTION_SMS
    collection_SMS_SEND = MongoCredentials.COLLECTION_SMS_SEND
    PLFON_USERNAME = Config_PlFon.PLFON_USERNAME
    PLFON_URL = Config_PlFon.PLFON_URL
    PLFON_PASSWORD = Config_PlFon.PLFON_PASSWORD
    PLFON_FROM = Config_PlFon.PLFON_FROM
    API_AUTH_TOKEN = Config_Auth.API_TOKEN
    BitrixToken = BitrixConfig.BitrixToken

except ImportError:
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "system_powiadomien")
    collection_SMS = os.getenv("COLLECTION_SMS", "sms_incoming")
    collection_SMS_SEND = os.getenv("COLLECTION_SMS_SEND", "sms_outgoing")
    API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "super-tajny-token-123")

port = int(os.getenv("PORT", 8020))

# Zmienne globalne na kolekcje
collection_incoming: Optional[object] = None
collection_outgoing: Optional[object] = None


# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global collection_incoming, collection_outgoing

    print("LOG: Uruchamianie serwera... Łączenie z MongoDB.")
    client = None

    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]

        collection_incoming = db[collection_SMS]
        collection_outgoing = db[collection_SMS_SEND]

        print(f"✅ POŁĄCZONO Z MONGODB: {db_name}")

    except Exception as e:
        print(f"❌ KRYTYCZNY BŁĄD POŁĄCZENIA Z BAZĄ: {e}")

    yield

    print("LOG: Zamykanie serwera.")
    if client:
        client.close()


app = FastAPI(lifespan=lifespan, title="System SMSowni PlFon")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Sprawdza, czy token Bearer przekazany w nagłówku Authorization
    zgadza się z tokenem zapisanym w konfiguracji (API_AUTH_TOKEN).
    """
    if credentials.credentials != API_AUTH_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Nieprawidłowy lub brakujący token autoryzacyjny",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials

def verify_bitrix(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Sprawdza, czy token Bearer przekazany w nagłówku Authorization
    zgadza się z tokenem zapisanym w konfiguracji (BitrixToken).
    """
    if credentials.credentials != BitrixToken:
        raise HTTPException(
            status_code=401,
            detail="Nieprawidłowy lub brakujący token autoryzacyjny",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials

# --- MODELE ---

class SendSMSRequest(BaseModel):
    to: str  # numer(y) B w formacie e164, np. "48510123123" lub kilka po przecinku
    text: str  # treść SMS (max 918 znaków bez polskich znaków / 402 ze znakami specjalnymi)
    from_: Optional[str] = None  # opcjonalne nadpisanie nadawcy


# --- ENDPOINTY ---

@app.get("/")
async def root():
    return {"message": "API SMSowni 2.0", "docs": "/docs"}


@app.get("/sms/search")
async def search_sms_by_phone(
        phone: str,
        limit: int = 50,
        token: str = Depends(verify_token)
):
    """
    Wyszukuje SMS-y przychodzące po numerze telefonu odbiorcy (pole 'to').
    Wymaga nagłówka: Authorization: Bearer <twój_token>

    Parametry:
    - phone: numer telefonu w formacie e164, np. 48123456789
    - limit: maksymalna liczba wyników (domyślnie 50, max 200)
    """
    if collection_incoming is None:
        raise HTTPException(status_code=503, detail="Database not available")

    if not phone:
        raise HTTPException(status_code=400, detail="Parametr 'phone' jest wymagany")

    if limit > 200:
        limit = 200

    try:
        cursor = collection_incoming.find(
            {"to": phone},
            {"_id": 1, "from": 1, "to": 1, "text": 1, "receive_date": 1, "created_at": 1, "bitrix": 1}
        ).sort("receive_date", -1).limit(limit)

        results = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            if "receive_date" in doc and doc["receive_date"]:
                doc["receive_date"] = doc["receive_date"].isoformat()
            if "created_at" in doc and doc["created_at"]:
                doc["created_at"] = doc["created_at"].isoformat()
            results.append(doc)

        return {
            "phone": phone,
            "count": len(results),
            "results": results
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------
# Nowa potężna taśma na wieki
# -----------------------------

@app.post("/bitrix/reassign-activities")
async def reassign_activities_from_phone_system(
        responsible_id: int = 357,
        # token: str = Depends(verify_bitrix)
):
    """
    Wyszukuje aktywności przypisane do użytkownika responsible_id (domyślnie 301),
    parsuje DESCRIPTION w poszukiwaniu "BitrixId : {id}", a następnie przepisuje
    właścicieli na znaleziony BitrixId.

    Proces:
    1. Pobiera wszystkie aktywności dla responsible_id (z uwzględnieniem paginacji Bitrix)
    2. Znajduje BitrixId w DESCRIPTION (wzorzec: "BitrixId : {id}")
    3. Aktualizuje RESPONSIBLE_ID w aktywności
    4. Aktualizuje ASSIGNED_BY_ID w powiązanym Lead/Deal
    5. Aktualizuje ASSIGNED_BY_ID w powiązanym Contact

    Wymaga nagłówka: Authorization: Bearer <twój_token>
    """
    print(f"\n{'=' * 80}")
    print(f"🔄 START: Przepisywanie właścicieli dla RESPONSIBLE_ID={responsible_id}")
    print(f"{'=' * 80}\n")

    # Regex do wyciągnięcia BitrixId z DESCRIPTION
    bitrix_id_pattern = re.compile(r'BitrixId\s*:\s*(\d+)\s*[:|\n]')

    processed_activities = []
    errors = []

    try:
        # 1. Pobierz wszystkie aktywności dla danego RESPONSIBLE_ID (PAGINACJA)
        print(f"📥 Pobieranie aktywności dla RESPONSIBLE_ID={responsible_id}...")

        activities = []
        start_param = 0

        while True:
            activities_result = bitrix_call(
                BitrixConfig.WEBHOOK_URL_CHATBOT,
                "crm.activity.list.json",
                {
                    "filter": {"RESPONSIBLE_ID": responsible_id},
                    "select": ["ID", "DESCRIPTION", "OWNER_ID", "OWNER_TYPE_ID", "RESPONSIBLE_ID"],
                    "start": start_param
                }
            )

            if "error" in activities_result:
                raise HTTPException(
                    status_code=500,
                    detail=f"Błąd Bitrix API: {activities_result['error']}"
                )

            current_batch = activities_result.get("result", [])
            activities.extend(current_batch)

            # Sprawdź, czy Bitrix zwraca klucz 'next', oznaczający kolejne strony wyników
            if "next" in activities_result:
                start_param = activities_result["next"]
                print(f"   ⏳ Pobrno {len(activities)} aktywności, pobieranie kolejnej paczki (start={start_param})...")
            else:
                break

        print(f"✅ Znaleziono łącznie {len(activities)} aktywności do przetworzenia\n")

        if not activities:
            return {
                "success": True,
                "message": "Brak aktywności do przetworzenia",
                "processed": 0,
                "errors": []
            }

        # 2. Przetwarzaj każdą aktywność
        for activity in activities:
            activity_id = activity.get("ID")
            description = activity.get("DESCRIPTION", "")
            owner_id = activity.get("OWNER_ID")
            owner_type_id = activity.get("OWNER_TYPE_ID")

            # Znajdź BitrixId w DESCRIPTION
            match = bitrix_id_pattern.search(description)

            if not match:
                continue

            new_bitrix_id = match.group(1)
            print(f"   ✅ Znaleziono BitrixId: {new_bitrix_id} w Aktywności ID={activity_id}")

            activity_update_result = {"activity": None, "lead_or_deal": None, "contact": None}

            try:
                # 3. Aktualizuj RESPONSIBLE_ID w aktywności
                print(f"   📝 Aktualizuję RESPONSIBLE_ID w aktywności {activity_id}...")

                update_activity = bitrix_call(
                    BitrixConfig.WEBHOOK_URL_CHATBOT,
                    "crm.activity.update.json",
                    {
                        "id": activity_id,
                        "fields": {"RESPONSIBLE_ID": new_bitrix_id}
                    }
                )

                if "error" in update_activity:
                    raise Exception(f"Błąd aktualizacji aktywności: {update_activity['error']}")

                activity_update_result["activity"] = f"Zaktualizowano RESPONSIBLE_ID na {new_bitrix_id}"
                print(f"   ✅ Aktywność zaktualizowana")

                # 4. Aktualizuj ASSIGNED_BY_ID w Lead/Deal
                if owner_id and owner_type_id:
                    entity_type = "lead" if owner_type_id == "1" else "deal"
                    print(f"   📝 Aktualizuję ASSIGNED_BY_ID w {entity_type} {owner_id}...")

                    # Pobierz Lead/Deal żeby sprawdzić CONTACT_ID
                    get_entity = bitrix_call(
                        BitrixConfig.WEBHOOK_URL_CHATBOT,
                        f"crm.{entity_type}.get.json",
                        {"id": owner_id}
                    )

                    if "error" not in get_entity:
                        entity_data = get_entity.get("result", {})
                        contact_id = entity_data.get("CONTACT_ID")

                        # Aktualizuj ASSIGNED_BY_ID w Lead/Deal
                        update_entity = bitrix_call(
                            BitrixConfig.WEBHOOK_URL_CHATBOT,
                            f"crm.{entity_type}.update.json",
                            {
                                "id": owner_id,
                                "fields": {"ASSIGNED_BY_ID": new_bitrix_id}
                            }
                        )

                        if "error" in update_entity:
                            raise Exception(f"Błąd aktualizacji {entity_type}: {update_entity['error']}")

                        activity_update_result["lead_or_deal"] = f"Zaktualizowano {entity_type} {owner_id}"
                        print(f"   ✅ {entity_type.capitalize()} zaktualizowany")

                        # 5. Aktualizuj ASSIGNED_BY_ID w Contact
                        if contact_id:
                            print(f"   📝 Aktualizuję ASSIGNED_BY_ID w kontakcie {contact_id}...")

                            update_contact = bitrix_call(
                                BitrixConfig.WEBHOOK_URL_CHATBOT,
                                "crm.contact.update.json",
                                {
                                    "id": contact_id,
                                    "fields": {"ASSIGNED_BY_ID": new_bitrix_id}
                                }
                            )

                            if "error" in update_contact:
                                raise Exception(f"Błąd aktualizacji kontaktu: {update_contact['error']}")

                            activity_update_result["contact"] = f"Zaktualizowano kontakt {contact_id}"
                            print(f"   ✅ Kontakt zaktualizowany")
                        else:
                            print(f"   ℹ️ Brak CONTACT_ID w {entity_type}")
                    else:
                        raise Exception(f"Błąd pobierania {entity_type}: {get_entity['error']}")

                processed_activities.append({
                    "activity_id": activity_id,
                    "new_bitrix_id": new_bitrix_id,
                    "owner_id": owner_id,
                    "owner_type_id": owner_type_id,
                    "updates": activity_update_result
                })

                print(f"   ✅ Przetworzono pomyślnie\n")

            except Exception as e:
                error_msg = f"Aktywność {activity_id}: {str(e)}"
                errors.append(error_msg)
                print(f"   ❌ BŁĄD: {error_msg}\n")

        print(f"\n{'=' * 80}")
        print(f"✅ ZAKOŃCZONO: Przetworzono {len(processed_activities)} z {len(activities)} aktywności")
        print(f"{'=' * 80}\n")

        return {
            "success": True,
            "processed_count": len(processed_activities),
            "processed_activities": processed_activities,
            "errors": errors,
            "total_activities_checked": len(activities)
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------------------------------------------
# Odbieranie SMS-ów przychodzących od plfon.pl
# -----------------------------------------------------------------------
@app.get("/plfon/receivedSMS", operation_id="receive_sms_get")
@app.post("/plfon/receivedSMS", operation_id="receive_sms_post")
async def receive_sms(request: Request):
    """
    Webhook wywoływany przez plfon.pl przy każdym przychodzącym SMS-ie.
    Obsługuje GET (parametry URL) oraz POST (form-data lub JSON).
    """
    if collection_incoming is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        # Wyciąganie danych w zależności od metody HTTP
        if request.method == "GET":
            sms_from = request.query_params.get("sms_from") or request.query_params.get("from")
            sms_to = request.query_params.get("sms_to") or request.query_params.get("to")
            sms_text = request.query_params.get("sms_text") or request.query_params.get("text")
            receive_date_raw = request.query_params.get("receive_date")
        else:
            content_type = request.headers.get("content-type", "")

            if "application/json" in content_type:
                data_raw = await request.json()
                sms_from = data_raw.get("sms_from") or data_raw.get("from")
                sms_to = data_raw.get("sms_to") or data_raw.get("to")
                sms_text = data_raw.get("sms_text") or data_raw.get("text")
                receive_date_raw = data_raw.get("receive_date")
            else:
                body_bytes = await request.body()
                body_str = body_bytes.decode("utf-8")
                data = parse_qs(body_str)

                def get_val(key, alt_key):
                    return data.get(key, data.get(alt_key, [None]))[0]

                sms_from = get_val("sms_from", "from")
                sms_to = get_val("sms_to", "to")
                sms_text = get_val("sms_text", "text")
                receive_date_raw = data.get("receive_date", [None])[0]

        # Walidacja wymaganych pól
        if not sms_from or not sms_text:
            print(f"⚠️ Brakujące pola: from={sms_from}, text={sms_text}")
            return Response(content="OK", media_type="text/plain")

        # Konwersja unix timestamp → datetime
        # Konwertuj z UTC na czas warszawski; strip tzinfo przed zapisem do Mongo
        receive_date = (
            datetime.fromtimestamp(int(receive_date_raw), tz=WARSAW).replace(tzinfo=None)
            if receive_date_raw
            else datetime.now(tz=WARSAW).replace(tzinfo=None)
        )

        print(f"📨 SMS od {sms_from} → {sms_to}: {sms_text[:50]}...")

        # ---------------------------------------------------------------
        # Blok Bitrix24 – zbieramy pełne dane do logu PRZED zapisem do bazy
        # ---------------------------------------------------------------
        bitrix_log: dict = {
            "status": None,  # "ok" | "no_contact" | "activity_failed" | "error"
            "contact_ids": [],  # lista ID kontaktów znalezionych po numerze
            "owner_id": None,  # ID Deala lub Leada
            "owner_type_id": None,  # "1" = Lead, "2" = Deal
            "owner_type_label": None,  # czytelna etykieta
            "responsible_id": None,  # ID opiekuna
            "activity_id": None,  # ID dodanej aktywności w Bitrix
            "error": None,  # komunikat błędu (jeśli wystąpił)
        }

        try:
            bitrix_data = find_owner_by_incoming_sms(sms_from)

            if bitrix_data is None:
                bitrix_log["status"] = "no_contact"
                print("ℹ️ Brak powiązanego kontaktu/Deala/Leada w Bitrix24.")
            else:
                owner_id = bitrix_data["OWNER_ID"]
                owner_type_id = bitrix_data["OWNER_TYPE_ID"]
                responsible = bitrix_data.get("RESPONSIBLE_ID", "1")
                type_label = "Lead" if owner_type_id == "1" else "Deal"

                bitrix_log["owner_id"] = owner_id
                bitrix_log["owner_type_id"] = owner_type_id
                bitrix_log["owner_type_label"] = type_label
                bitrix_log["responsible_id"] = responsible

                description = (
                    f"[B]SMS od:[/B] {sms_from}\n"
                    f"[B]Data:[/B] {receive_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"[B]Treść:[/B]\n{sms_text}"
                )
                activity_id = add_new_activity(owner_id, owner_type_id, responsible, description)

                if activity_id:
                    bitrix_log["status"] = "ok"
                    bitrix_log["activity_id"] = str(activity_id)
                    print(f"✅ Aktywność dodana do Bitrix24 ({type_label} ID={owner_id}), activity_id={activity_id}.")
                else:
                    bitrix_log["status"] = "activity_failed"
                    bitrix_log["error"] = "add_new_activity zwróciło None"
                    print(f"⚠️ Właściciel znaleziony ({type_label} ID={owner_id}), ale nie udało się dodać aktywności.")

        except Exception as e:
            import traceback
            bitrix_log["status"] = "error"
            bitrix_log["error"] = str(e)
            print(f"❌ Błąd integracji Bitrix24: {e}")
            traceback.print_exc()

        # ---------------------------------------------------------------
        # Zapis do MongoDB – jeden dokument z pełnym kontekstem
        # ---------------------------------------------------------------
        sms_doc = {
            # --- dane SMS ---
            "from": sms_from,
            "to": sms_to,
            "text": sms_text,
            "receive_date": receive_date,
            "created_at": datetime.now(tz=WARSAW).replace(tzinfo=None),

            # --- wyniki wyszukiwania w Bitrix24 ---
            "bitrix": {
                "status": bitrix_log["status"],
                # "no_contact"      – numer nieznany w CRM / brak Leadów ani Deali
                # "ok"              – aktywność dodana pomyślnie
                # "activity_failed" – właściciel znaleziony, błąd zapisu aktywności
                # "error"           – wyjątek podczas integracji
                "contact_ids": bitrix_log["contact_ids"],
                "owner_id": bitrix_log["owner_id"],
                "owner_type_id": bitrix_log["owner_type_id"],
                "owner_type_label": bitrix_log["owner_type_label"],
                "responsible_id": bitrix_log["responsible_id"],
                "activity_id": bitrix_log["activity_id"],
                "error": bitrix_log["error"],
            },
        }

        try:
            await collection_incoming.insert_one(sms_doc)
            print("✅ SMS zapisany w bazie.")
        except DuplicateKeyError:
            print("⚠️ Duplikat SMS – ignoruję.")

        return Response(content="OK", media_type="text/plain")

    except Exception as e:
        import traceback
        print(f"❌ Błąd ogólny: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# -----------------------------------------------------------------------
# Wysyłanie SMS-ów przez plfon.pl
# -----------------------------------------------------------------------
@app.post("/plfon/sendSMS")
async def send_sms(
        payload: SendSMSRequest,
        token: str = Depends(verify_token)
):
    """
    Wysyła SMS przez API plfon.pl.
    Wymaga nagłówka: Authorization: Bearer <twój_token>
    Pole 'to' przyjmuje jeden numer lub kilka po przecinku (max 10).
    Treść 'text' max 918 znaków (bez PL znaków) / 402 znaki (z PL znakami).
    """
    if collection_outgoing is None:
        raise HTTPException(status_code=503, detail="Database not available")

    if not PLFON_USERNAME or not PLFON_PASSWORD:
        raise HTTPException(status_code=500, detail="Brak konfiguracji danych logowania plfon.pl")

    recipients = [r.strip() for r in payload.to.split(",") if r.strip()]
    if len(recipients) > 10:
        raise HTTPException(
            status_code=400,
            detail=f"Zbyt wielu odbiorców ({len(recipients)}). Maksimum to 10 na jedno wywołanie."
        )

    if len(payload.text) > 918:
        raise HTTPException(
            status_code=400,
            detail=f"Tekst zbyt długi ({len(payload.text)} znaków). Maksimum to 918 znaków."
        )

    sms_from = payload.from_ or PLFON_FROM

    request_body = {
        "username": PLFON_USERNAME,
        "password": PLFON_PASSWORD,
        "from": sms_from,
        "to": payload.to,
        "text": payload.text,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            response = await http_client.post(PLFON_URL, data=request_body, headers=headers)

            if response.status_code != 200:
                print(f"⚠️ Błąd Plfon. Kod: {response.status_code}, Odpowiedź: {response.text}")

            response.raise_for_status()

            try:
                result = response.json()
            except ValueError:
                print(f"⚠️ Odpowiedź Plfon nie jest JSON-em: {response.text}")
                result = {
                    "status": "ok" if response.status_code == 200 else "error",
                    "error_code": "0" if response.status_code == 200 else str(response.status_code),
                    "error_message": response.text
                }

        print(f"📤 Odpowiedź plfon.pl: {result}")

        log_doc = {
            "from": sms_from,
            "to": payload.to,
            "text": payload.text,
            "recipients_count": len(recipients),
            "plfon_status": result.get("status"),
            "plfon_error_code": result.get("error_code"),
            "plfon_error_message": result.get("error_message"),
            "sent_at": datetime.now(tz=WARSAW).replace(tzinfo=None),
        }
        await collection_outgoing.insert_one(log_doc)

        error_code = result.get("error_code")
        if error_code not in ("0", 0, None):
            raise HTTPException(
                status_code=400,
                detail=f"Błąd plfon.pl [{error_code}]: {result.get('error_message')}"
            )

        return {
            "success": True,
            "status": result.get("status"),
            "recipients": recipients,
        }

    except httpx.HTTPError as e:
        print(f"❌ Błąd HTTP do plfon.pl: {e}")
        raise HTTPException(status_code=502, detail=f"Błąd połączenia z plfon.pl: {e}")


from fastapi.responses import HTMLResponse


# Sekcja bitrixowa

@app.post("/bitrix/send-sms-native")
async def bitrix_native_sms(
        request: Request,
        token: Optional[str] = None,
        from_: Optional[str] = None
):
    """
    Endpoint wywoływany przez Bitrix24 z natywnej bramki.
    """
    if token != API_AUTH_TOKEN:
        return Response(content="Nieautoryzowany dostęp (zły token)", status_code=401)

    form_data = await request.form()
    phone = form_data.get("message_to")
    text = form_data.get("message_body")
    message_id = form_data.get("message_id")

    print(f"📥 Żądanie wysyłki Bitrix24: Do={phone}, Nadawca={from_}, Treść={text}")

    if not phone or not text:
        return Response(content="Brak wymaganych danych", status_code=400)

    sms_from = from_ if from_ else PLFON_FROM

    request_body = {
        "username": PLFON_USERNAME,
        "password": PLFON_PASSWORD,
        "from": sms_from,
        "to": phone,
        "text": text,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            response = await http_client.post(PLFON_URL, data=request_body, headers=headers)
            response.raise_for_status()

            try:
                result = response.json()
            except ValueError:
                result = {"status": "ok" if response.status_code == 200 else "error"}

        print(f"📤 PlFon odpowiedział: {result}")

        if collection_outgoing is not None:
            log_doc = {
                "from": sms_from,
                "to": phone,
                "text": text,
                "bitrix_message_id": message_id,
                "plfon_status": result.get("status"),
                "sent_at": datetime.now(tz=WARSAW).replace(tzinfo=None),
            }
            await collection_outgoing.insert_one(log_doc)

        return {"status": "success"}

    except Exception as e:
        print(f"❌ Błąd wysyłki: {e}")
        return Response(content=str(e), status_code=500)


from fastapi.responses import HTMLResponse


@app.get("/app", response_class=HTMLResponse)
@app.post("/app", response_class=HTMLResponse)
async def bitrix_app(request: Request):
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <script src="https://api.bitrix24.com/api/v1/"></script>
        <style>
            body { font-family: Arial, sans-serif; padding: 20px; background-color: #f9f9f9; color: #333; }
            .container { max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            h2, h3 { color: #2fc6f6; }
            .section { margin-bottom: 30px; padding-bottom: 20px; border-bottom: 1px solid #eee; }
            .section:last-child { border-bottom: none; }
            input, textarea { width: 100%; margin-bottom: 10px; padding: 10px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
            button { padding: 10px 15px; border: none; color: white; cursor: pointer; border-radius: 4px; font-weight: bold; margin-right: 5px; margin-top: 5px;}
            .btn-save { background: #ff9900; width: 100%; font-size: 16px; }
            .btn-save:hover { background: #e68a00; }
            .btn-send { background: #2fc6f6; width: 100%; font-size: 16px; }
            .btn-send:hover { background: #1baedb; }
            .btn-install { background: #28a745; width: 100%; font-size: 16px; margin-bottom: 10px; }
            .btn-install:hover { background: #218838; }
            .btn-remove { background: #dc3545; }
            .btn-remove:hover { background: #c82333; }
            .status { margin-top: 10px; font-weight: bold; text-align: center; }
            .provider-list { background: #f1f1f1; padding: 15px; border-radius: 4px; min-height: 50px; margin-top: 10px; }
            .provider-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #ddd; }
            .provider-item:last-child { border-bottom: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📩 Panel SMSownia PlFon</h2>

            <div class="section" id="config_section">
                <h3>⚙️ Konfiguracja Główna</h3>
                <label>Token API (do autoryzacji z Twoim serwerem):</label>
                <input type="password" id="config_token" placeholder="Wprowadź token API">

                <label>Domyślny numer nadawcy (pole from):</label>
                <input type="text" id="config_from" placeholder="np. 48500100200 lub Nazwa">

                <button class="btn-save" onclick="saveConfig()">💾 Zapisz konfigurację</button>
                <div id="config_status" class="status"></div>
            </div>

            <div class="section" id="manual_sms_section">
                <h3>Ręczna wysyłka SMS</h3>
                <label>Numer telefonu docelowy:</label>
                <input id="phone" placeholder="np. 48500100200">

                <label>Numer telefonu dostawcy (możesz nadpisać):</label>
                <input id="from" placeholder="np. 48500100200">

                <label>Treść wiadomości:</label>
                <textarea id="message" rows="4" placeholder="Wpisz treść..."></textarea>

                <button class="btn-send" onclick="sendSMS()">Wyślij SMS</button>
                <div id="send_status" class="status"></div>
            </div>

            <div class="section" id="manager_section">
                <h3>📱 Menedżer Nadawców (Oś czasu CRM)</h3>
                <p style="font-size: 14px; color: #666;">
                    Dodawaj wielu nadawców. Będą oni widoczni jako opcje wyboru w standardowym oknie SMS w Bitrix24.
                </p>

                <label>Nazwa wyświetlana w Bitrix (np. PlFon - Biuro):</label>
                <input type="text" id="new_provider_name" placeholder="Wpisz nazwę">

                <label>Numer nadawcy (pole 'from'):</label>
                <input type="text" id="new_provider_from" placeholder="np. 48500100200">

                <button class="btn-install" onclick="addDynamicProvider()">➕ Dodaj Nadawcę</button>

                <h4>Zainstalowani nadawcy:</h4>
                <div id="provider_list" class="provider-list">
                    Ładowanie listy...
                </div>
            </div>

            <div class="section">
                <h3>⚙️ Instalacja Bramki SMS</h3>
                <p style="font-size: 14px; color: #666;">
                    Instaluje bramkę <strong>SMSownia PlFon</strong> jako stałego dostawcę SMS w Bitrix24.<br>
                    <strong>Ważne:</strong> Przed instalacją zapisz Token i numer nadawcy w konfiguracji powyżej!
                </p>
                <button class="btn-install" style="width:100%; font-size:16px; margin-bottom:10px;" onclick="registerSmsProvider()">➕ Zainstaluj Bramkę SMS</button>
                <button class="btn-remove" style="width:100%; font-size:16px;" onclick="unregisterSmsProvider()">🗑️ Usuń Bramkę SMS</button>
                <div id="install_status" class="status"></div>
            </div>
        </div>

        <script>
        BX24.init(function() {
            let savedToken = BX24.appOption.get('api_token');
            let savedFrom  = BX24.appOption.get('default_from');

            if(savedToken) document.getElementById("config_token").value = savedToken;
            if(savedFrom) {
                document.getElementById("config_from").value = savedFrom;
                document.getElementById("from").value = savedFrom;
            }

            function registerSmsProvider() {
                let token   = BX24.appOption.get('api_token') || "";
                let fromNum = BX24.appOption.get('default_from') || "";

                if(!token || !fromNum) {
                    alert("⚠️ Zapisz najpierw Token i domyślny numer nadawcy w konfiguracji!");
                    return;
                }

                let baseUrl = "https://sms.jenaeuropa.pl/bitrix/send-sms-native";
                let handlerWithConfig = baseUrl + "?token=" + encodeURIComponent(token) + "&from_=" + encodeURIComponent(fromNum) + "&label=" + encodeURIComponent("SMSownia PlFon");

                document.getElementById("install_status").style.color = "#333";
                document.getElementById("install_status").innerText = "Instalowanie...";

                BX24.callMethod(
                    'messageservice.sender.add',
                    {
                        CODE: 'plfon_provider',
                        TYPE: 'SMS',
                        NAME: 'SMSownia PlFon',
                        HANDLER: handlerWithConfig,
                        DESCRIPTION: 'Autorski skrypt do dostarczania SMSów zintegrowany z PlFon'
                    },
                    function(result) {
                        if(result.error()) {
                            document.getElementById("install_status").style.color = "red";
                            document.getElementById("install_status").innerText = "❌ Błąd: " + result.error();
                        } else {
                            saveProviderName('plfon_provider', 'SMSownia PlFon');
                            document.getElementById("install_status").style.color = "green";
                            document.getElementById("install_status").innerText = "✅ Bramka zainstalowana! Nadawca: " + fromNum;
                            setTimeout(loadProviders, 1000);
                        }
                    }
                );
            }

            function unregisterSmsProvider() {
                if(!confirm("Czy na pewno chcesz usunąć bramkę SMSownia PlFon?")) return;

                document.getElementById("install_status").style.color = "#333";
                document.getElementById("install_status").innerText = "Usuwanie...";

                BX24.callMethod('messageservice.sender.delete', { CODE: 'plfon_provider' }, function(result) {
                    if(result.error()) {
                        document.getElementById("install_status").style.color = "red";
                        document.getElementById("install_status").innerText = "❌ Błąd: " + result.error();
                    } else {
                        removeProviderName('plfon_provider');
                        document.getElementById("install_status").style.color = "green";
                        document.getElementById("install_status").innerText = "🗑️ Bramka usunięta.";
                        setTimeout(loadProviders, 1000);
                    }
                });
            }

            loadProviders();

            BX24.placement.info(function(info){
                if(info.options && info.options.ID){
                    document.getElementById("config_section").style.display = "none";
                    document.getElementById("manager_section").style.display = "none";

                    let entityId   = info.options.ID;
                    let entityType = info.placement.includes("LEAD") ? "lead" : "deal";

                    BX24.callMethod("crm." + entityType + ".get", { id: entityId }, function(result){
                        if(result.data()){
                            let data = result.data();
                            if(data.PHONE && data.PHONE.length > 0){
                                document.getElementById("phone").value = data.PHONE[0].VALUE;
                            }
                        }
                    });
                }
            });
        });

        function saveConfig() {
            let token   = document.getElementById("config_token").value;
            let fromNum = document.getElementById("config_from").value;

            BX24.appOption.set('api_token', token);
            BX24.appOption.set('default_from', fromNum);

            document.getElementById("config_status").style.color = "green";
            document.getElementById("config_status").innerText = "✅ Konfiguracja zapisana pomyślnie!";
            document.getElementById("from").value = fromNum;

            setTimeout(() => { document.getElementById("config_status").innerText = ""; }, 3000);
        }

        function sendSMS() {
            let phone   = document.getElementById("phone").value;
            let message = document.getElementById("message").value;
            let from    = document.getElementById("from").value;
            let token   = BX24.appOption.get('api_token') || "";

            if(!phone || !message || !from) {
                alert("Wypełnij numery i treść!");
                return;
            }

            document.getElementById("send_status").style.color = "#333";
            document.getElementById("send_status").innerText = "Wysyłanie...";

            fetch("/plfon/sendSMS", {
                method: "POST",
                headers: { 
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + token
                },
                body: JSON.stringify({ to: phone, text: message, from_: from })
            })
            .then(res => {
                if(!res.ok) throw new Error("Błąd HTTP: " + res.status);
                return res.json();
            })
            .then(data => {
                document.getElementById("send_status").style.color = "green";
                document.getElementById("send_status").innerText = "✅ Wysłano pomyślnie!";
                document.getElementById("message").value = "";
            })
            .catch(err => {
                document.getElementById("send_status").style.color = "red";
                document.getElementById("send_status").innerText = "❌ Błąd wysyłki";
                console.error(err);
            });
        }

        function getSavedProviders() {
            let saved = BX24.appOption.get('plfon_saved_providers');
            if (!saved) return {};
            try { return JSON.parse(saved); } catch(e) { return {}; }
        }

        function saveProviderName(code, name) {
            let saved = getSavedProviders();
            saved[code] = name;
            BX24.appOption.set('plfon_saved_providers', JSON.stringify(saved));
        }

        function removeProviderName(code) {
            let saved = getSavedProviders();
            delete saved[code];
            BX24.appOption.set('plfon_saved_providers', JSON.stringify(saved));
        }

        function loadProviders() {
            BX24.callMethod('messageservice.sender.list', {}, function(result) {
                if(result.error()) {
                    document.getElementById('provider_list').innerHTML = "<span style='color:red;'>Błąd: " + result.error() + "</span>";
                    return;
                }

                let providers = result.data() || [];
                console.log("RAW sender.list:", JSON.stringify(providers));

                let ourProviders = providers.filter(p => {
                    let code = typeof p === 'object' ? p.CODE : p;
                    return code && code.startsWith('plfon_');
                });

                if(ourProviders.length === 0) {
                    document.getElementById('provider_list').innerHTML = 'Brak skonfigurowanych nadawców.';
                    return;
                }

                let html = '';
                ourProviders.forEach(p => {
                    let code    = typeof p === 'object' ? (p.CODE || '') : p;
                    let handler = typeof p === 'object' ? (p.HANDLER || '') : '';

                    let name = '';
                    try {
                        let url = new URL(handler);
                        name = url.searchParams.get('label') || '';
                    } catch(e) {}

                    if(!name) {
                        let savedNames = getSavedProviders();
                        name = savedNames[code] || code;
                    }

                    html += `<div class="provider-item">
                                <div>
                                    <strong>${name}</strong><br>
                                    <small style="color: #666;">Kod techniczny: ${code}</small>
                                </div>
                                <button class="btn-remove" onclick="deleteDynamicProvider('${code}')">Usuń</button>
                             </div>`;
                });
                document.getElementById('provider_list').innerHTML = html;
            });
        }

        function addDynamicProvider() {
            let name    = document.getElementById("new_provider_name").value;
            let fromNum = document.getElementById("new_provider_from").value;
            let token   = BX24.appOption.get('api_token') || "";

            if(!name || !fromNum) {
                alert("Wypełnij nazwę i numer nadawcy!");
                return;
            }
            if(!token) {
                alert("Najpierw wpisz i zapisz Token API w Konfiguracji Głównej!");
                return;
            }

            let uniqueCode        = 'plfon_' + Date.now();
            let baseUrl           = "https://sms.jenaeuropa.pl/bitrix/send-sms-native";
            let handlerWithConfig = baseUrl + "?token=" + encodeURIComponent(token) + "&from_=" + encodeURIComponent(fromNum) + "&label=" + encodeURIComponent(name);

            document.getElementById('provider_list').innerHTML = "Trwa dodawanie do Bitrix24...";

            BX24.callMethod(
                'messageservice.sender.add',
                {
                    CODE: uniqueCode,
                    TYPE: 'SMS',
                    NAME: name,
                    HANDLER: handlerWithConfig,
                    DESCRIPTION: 'Nadawca SMS: ' + fromNum
                },
                function(result) {
                    if(result.error()) {
                        alert("Błąd dodawania: " + result.error());
                        loadProviders();
                    } else {
                        saveProviderName(uniqueCode, name);
                        document.getElementById("new_provider_name").value = "";
                        document.getElementById("new_provider_from").value = "";
                        setTimeout(loadProviders, 1000); 
                    }
                }
            );
        }

        function deleteDynamicProvider(code) {
            if(confirm("Czy na pewno chcesz bezpowrotnie usunąć tego nadawcę?")) {
                document.getElementById('provider_list').innerHTML = "Usuwanie...";

                BX24.callMethod('messageservice.sender.delete', { CODE: code }, function(result) {
                    if(result.error()) {
                        alert("Błąd usuwania: " + result.error());
                    } else {
                        removeProviderName(code);
                    }
                    setTimeout(loadProviders, 1000);
                });
            }
        }
        </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=port)
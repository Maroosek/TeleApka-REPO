import os
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime
import httpx
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
from pymongo.errors import DuplicateKeyError
from urllib.parse import parse_qs

from Bitrix24 import find_owner_by_incoming_sms, add_new_activity

# Konfiguracja
try:
    from config import MongoCredentials, Config_PlFon, Config_Auth
    mongo_url = MongoCredentials.MONGODB_URL
    db_name = MongoCredentials.DATABASE_NAME
    collection_SMS = MongoCredentials.COLLECTION_SMS
    collection_SMS_SEND = MongoCredentials.COLLECTION_SMS_SEND
    PLFON_USERNAME = Config_PlFon.PLFON_USERNAME
    PLFON_URL = Config_PlFon.PLFON_URL
    PLFON_PASSWORD = Config_PlFon.PLFON_PASSWORD
    PLFON_FROM = Config_PlFon.PLFON_FROM
    API_AUTH_TOKEN = Config_Auth.API_TOKEN

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

# --- MODELE ---

class SendSMSRequest(BaseModel):
    to: str          # numer(y) B w formacie e164, np. "48510123123" lub kilka po przecinku
    text: str        # treść SMS (max 918 znaków bez polskich znaków / 402 ze znakami specjalnymi)
    from_: Optional[str] = None  # opcjonalne nadpisanie nadawcy

# --- ENDPOINTY ---

@app.get("/")
async def root():
    return {"message": "API SMSowni 2.0", "docs": "/docs"}

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
            sms_from = request.query_params.get("from")
            sms_to = request.query_params.get("to")
            sms_text = request.query_params.get("text")
            receive_date_raw = request.query_params.get("receive_date")
        else:
            content_type = request.headers.get("content-type", "")

            # Obsługa POST: form-data lub JSON
            if "application/json" in content_type:
                data_raw = await request.json()
                sms_from = data_raw.get("from")
                sms_to = data_raw.get("to")
                sms_text = data_raw.get("text")
                receive_date_raw = data_raw.get("receive_date")
            else:
                body_bytes = await request.body()
                body_str = body_bytes.decode("utf-8")
                data = parse_qs(body_str)

                def get_val(key):
                    return data.get(key, [None])[0]

                sms_from = get_val("from")
                sms_to = get_val("to")
                sms_text = get_val("text")
                receive_date_raw = get_val("receive_date")

        # Walidacja wymaganych pól
        if not sms_from or not sms_text:
            print(f"⚠️ Brakujące pola: from={sms_from}, text={sms_text}")
            return Response(content="OK", media_type="text/plain")

        # Konwersja unix timestamp → datetime
        receive_date = (
            datetime.fromtimestamp(int(receive_date_raw))
            if receive_date_raw
            else datetime.utcnow()
        )

        print(f"📨 SMS od {sms_from} → {sms_to}: {sms_text[:50]}...")

        # Zapis do MongoDB
        sms_doc = {
            "from": sms_from,
            "to": sms_to,
            "text": sms_text,
            "receive_date": receive_date,
            "created_at": datetime.utcnow(),
        }

        try:
            await collection_incoming.insert_one(sms_doc)
            print("✅ SMS zapisany w bazie.")
        except DuplicateKeyError:
            print("⚠️ Duplikat SMS – ignoruję.")

        # Integracja z Bitrix24
        try:
            bitrix_data = find_owner_by_incoming_sms(sms_from)
            if bitrix_data:
                owner_id = bitrix_data["OWNER_ID"]
                owner_type = bitrix_data["OWNER_TYPE_ID"]
                responsible = bitrix_data.get("RESPONSIBLE_ID", "1")
                description = (
                    f"[B]SMS od:[/B] {sms_from}\n"
                    f"[B]Data:[/B] {receive_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"[B]Treść:[/B]\n{sms_text}"
                )
                add_new_activity(owner_id, owner_type, responsible, description)
                print(f"✅ Aktywność dodana do Bitrix24 (owner: {owner_id}).")
            else:
                print("ℹ️ Brak powiązanego Deala/Leada w Bitrix24.")
        except Exception as e:
            import traceback
            print(f"❌ Błąd integracji Bitrix24: {e}")
            traceback.print_exc()

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
    token: str = Depends(verify_token)  # Zabezpieczenie Bearer Tokenem
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

    # Walidacja liczby odbiorców (max 10 wg dokumentacji)
    recipients = [r.strip() for r in payload.to.split(",") if r.strip()]
    if len(recipients) > 10:
        raise HTTPException(
            status_code=400,
            detail=f"Zbyt wielu odbiorców ({len(recipients)}). Maksimum to 10 na jedno wywołanie."
        )

    # Walidacja długości tekstu
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

    # Wymuszenie formatu x-www-form-urlencoded i fałszywy User-Agent, aby obejść 406 Not Acceptable
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

            # Bezpieczne parsowanie odpowiedzi
            try:
                result = response.json()
            except ValueError:
                print(f"⚠️ Odpowiedź Plfon nie jest JSON-em: {response.text}")
                # Fallback, aby aplikacja się nie zawiesiła przy sprawdzaniu 'error_code' i zapisie do bazy
                result = {
                    "status": "ok" if response.status_code == 200 else "error",
                    "error_code": "0" if response.status_code == 200 else str(response.status_code),
                    "error_message": response.text
                }

        print(f"📤 Odpowiedź plfon.pl: {result}")

        # Zapis wyniku do MongoDB
        log_doc = {
            "from": sms_from,
            "to": payload.to,
            "text": payload.text,
            "recipients_count": len(recipients),
            "plfon_status": result.get("status"),
            "plfon_error_code": result.get("error_code"),
            "plfon_error_message": result.get("error_message"),
            "sent_at": datetime.utcnow(),
        }
        await collection_outgoing.insert_one(log_doc)

        # Sprawdzenie czy plfon zwrócił błąd w swoim API
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

#Sekcja bitrixowa

@app.post("/bitrix/send-sms-native")
async def bitrix_native_sms(
    request: Request,
    token: Optional[str] = None, # Pobierane z URL-a: ?token=...
    from_: Optional[str] = None  # Pobierane z URL-a: ?from_=...
):
    """
    Endpoint wywoływany przez Bitrix24 z natywnej bramki.
    """
    # 1. Weryfikacja tokena przekazanego w URL podczas instalacji bramki
    if token != API_AUTH_TOKEN:
        return Response(content="Nieautoryzowany dostęp (zły token)", status_code=401)

    form_data = await request.form()
    phone = form_data.get("message_to")
    text = form_data.get("message_body")
    message_id = form_data.get("message_id")

    print(f"📥 Żądanie wysyłki Bitrix24: Do={phone}, Nadawca={from_}, Treść={text}")

    if not phone or not text:
        return Response(content="Brak wymaganych danych", status_code=400)

    # Używamy nadawcy z konfiguracji Bitrix24 (przekazanego w URL), albo fallback z env
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
        # Wysyłka do PlFon
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            response = await http_client.post(PLFON_URL, data=request_body, headers=headers)
            response.raise_for_status()

            # Bezpieczne parsowanie odpowiedzi PlFon
            try:
                result = response.json()
            except ValueError:
                result = {"status": "ok" if response.status_code == 200 else "error"}

        print(f"📤 PlFon odpowiedział: {result}")

        # Zapis do MongoDB (analogicznie jak w Twojej poprzedniej funkcji)
        if collection_outgoing is not None:
            log_doc = {
                "from": sms_from,
                "to": phone,
                "text": text,
                "bitrix_message_id": message_id,
                "plfon_status": result.get("status"),
                "sent_at": datetime.utcnow(),
            }
            await collection_outgoing.insert_one(log_doc)

        # Bitrix oczekuje odpowiedzi 200 OK, aby uznać, że serwer przyjął zadanie
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
            .btn-install { background: #28a745; }
            .btn-install:hover { background: #218838; }
            .btn-remove { background: #dc3545; }
            .btn-remove:hover { background: #c82333; }
            .status { margin-top: 10px; font-weight: bold; text-align: center; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📩 Panel SMSownia PlFon</h2>

            <div class="section">
                <h3>⚙️ Konfiguracja Główna</h3>
                <label>Token API (do autoryzacji z Twoim serwerem):</label>
                <input type="password" id="config_token" placeholder="Wprowadź token API">

                <label>Domyślny numer nadawcy (pole from):</label>
                <input type="text" id="config_from" placeholder="np. 48500100200 lub Nazwa">

                <button class="btn-save" onclick="saveConfig()">💾 Zapisz konfigurację</button>
                <div id="config_status" class="status"></div>
            </div>

            <div class="section">
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

            <div class="section">
                <h3>Ustawienia Integracji CRM</h3>
                <p style="font-size: 14px; color: #666;">
                    Zainstaluj bramkę, aby wysyłać SMSy bezpośrednio z osi czasu. <br>
                    <strong>Ważne:</strong> Przed instalacją upewnij się, że zapisałeś konfigurację powyżej!
                </p>
                <button class="btn-install" onclick="registerSmsProvider()">➕ Zainstaluj Bramkę SMS</button>
                <button class="btn-remove" onclick="unregisterSmsProvider()">🗑️ Usuń Bramkę SMS</button>
            </div>
        </div>

        <script>
        BX24.init(function() {
            // Pobieranie zapisanych ustawień przy starcie
            let savedToken = BX24.appOption.get('api_token');
            let savedFrom = BX24.appOption.get('default_from');

            if(savedToken) document.getElementById("config_token").value = savedToken;
            if(savedFrom) {
                document.getElementById("config_from").value = savedFrom;
                document.getElementById("from").value = savedFrom; // Automatycznie wypełnia pole w ręcznej wysyłce
            }

            BX24.placement.info(function(info){
                if(info.options && info.options.ID){
                    let entityId = info.options.ID;
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

        // Zapisywanie konfiguracji w Bitrix24
        function saveConfig() {
            let token = document.getElementById("config_token").value;
            let fromNum = document.getElementById("config_from").value;

            BX24.appOption.set('api_token', token);
            BX24.appOption.set('default_from', fromNum);

            document.getElementById("config_status").style.color = "green";
            document.getElementById("config_status").innerText = "✅ Konfiguracja zapisana pomyślnie!";

            // Aktualizacja pola ręcznego from
            document.getElementById("from").value = fromNum;

            setTimeout(() => { document.getElementById("config_status").innerText = ""; }, 3000);
        }

        // Obsługa ręcznej wysyłki z uwzględnieniem Tokena z konfiguracji
        function sendSMS() {
            let phone = document.getElementById("phone").value;
            let message = document.getElementById("message").value;
            let from = document.getElementById("from").value;
            let token = BX24.appOption.get('api_token') || "";

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
                    "Authorization": "Bearer " + token // Przekazujemy token
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

        // Instalacja natywnego dostawcy z wstrzyknięciem konfiguracji do URLa
        function registerSmsProvider() {
            let token = BX24.appOption.get('api_token') || "";
            let fromNum = BX24.appOption.get('default_from') || "";

            if(!token || !fromNum) {
                alert("⚠️ Zapisz najpierw Token i domyślny numer nadawcy w konfiguracji!");
                return;
            }

            // UWAGA: Twój adres serwera. Przekazujemy config jako parametry GET
            let baseUrl = "https://sms.jenaeuropa.pl/bitrix/send-sms-native";
            let handlerWithConfig = baseUrl + "?token=" + encodeURIComponent(token) + "&from_=" + encodeURIComponent(fromNum);

            BX24.callMethod(
                'messageservice.sender.add',
                {
                    CODE: 'plfon_provider',
                    TYPE: 'SMS',
                    NAME: 'SMSownia PlFon',
                    HANDLER: handlerWithConfig,
                    DESCRIPTION: 'Autorski skrypt do dostarczania SMSów zintegrowany z PlFon, napisane i udoskonalane przez Marek Korkosz'
                },
                function(result) {
                    if(result.error()) {
                        alert("Błąd dodawania: " + result.error());
                    } else {
                        alert("✅ Sukces! Dodano bramkę. Używany nadawca: " + fromNum);
                    }
                }
            );
        }

        function unregisterSmsProvider() {
            BX24.callMethod('messageservice.sender.delete', { CODE: 'plfon_provider' }, function(result) {
                if(result.error()) {
                    alert("Błąd usuwania: " + result.error());
                } else {
                    alert("🗑️ Usunięto z listy dostawców SMS.");
                }
            });
        }
        </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=port)
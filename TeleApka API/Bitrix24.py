import requests
import time
import re
from pymongo import MongoClient, DESCENDING

from config import BitrixConfig

def bitrix_call(webhook_url, method, params=None):
    """
    Wysyła zapytanie do Bitrix24 z mechanizmem Retry (ponawiania prób).
    W przypadku błędu czeka 4 sekundy i próbuje ponownie.
    """
    if params is None:
        params = {}

    url = f"{webhook_url}{method}"
    max_retries = 5  # Ile razy próbować zanim się poddamy

    for attempt in range(1, max_retries + 1):
        try:
            # timeout=30 oznacza, że jeśli serwer nie odpowie w 30s, rzuci wyjątek
            response = requests.post(url, json=params, timeout=30)

            # Sprawdza czy status HTTP to 200 (jeśli 4xx lub 5xx -> rzuca błąd)
            response.raise_for_status()

            result = response.json()

            # Opcjonalnie: Czasami Bitrix zwraca 200 OK, ale w środku jsona jest 'error': 'QUERY_LIMIT_EXCEEDED'
            if "error" in result and result.get("error") == "QUERY_LIMIT_EXCEEDED":
                raise requests.exceptions.RequestException("Przekroczono limit zapytań (QUERY_LIMIT_EXCEEDED)")

            return result

        except requests.exceptions.RequestException as e:
            print(f"⚠️ Błąd połączenia/TimeOut (Próba {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                print("⏳ Czekam 4 sekundy przed ponowną próbą...")
                time.sleep(4)
            else:
                print("❌ Błąd krytyczny: Nie udało się połączyć po wszystkich próbach.")
                return {"error": str(e)}

def clean_phone_number(phone):
    """
    Usuwa WSZYSTKIE znaki niebędące cyframi.
    Usuwa '+', spacje, myślniki, nawiasy.
    Zmienia: "+48 500-123-456" -> "48500123456"
    Zmienia: "48500123456"     -> "48500123456"
    """
    if not phone:
        return ""
    # [^0-9] oznacza: znajdź wszystko co nie jest cyfrą i zamień na pusty string
    return re.sub(r"[^0-9]", "", str(phone))


def find_owner_by_incoming_sms(phone_number):
    """
    Szuka w historii Bitrixa (CRM_SMS), do jakiego Deala/Leada (OWNER_ID)
    wysyłaliśmy wiadomość na ten numer.
    Nie łączy się z Mongo - przyjmuje numer prosto z requestu.
    """
    clean_sms_from = clean_phone_number(phone_number)
    print(f">>> Szukanie pasującego Deal'a w Bitrix24 dla numeru {clean_sms_from}...")

    if not clean_sms_from:
        print("❌ Pusty numer telefonu po czyszczeniu.")
        return None

    # Pobieramy aktywności SMS, sortując od najnowszych
    # Dodalem RESPONSIBLE_ID do select, żeby wiedzieć kto opiekował się klientem
    params = {
        "order": {"ID": "DESC"},
        "filter": {
            "PROVIDER_ID": "CRM_SMS",
        },
        "select": ["ID", "OWNER_ID", "OWNER_TYPE_ID", "SETTINGS", "SUBJECT", "RESPONSIBLE_ID"]
    }

    result = bitrix_call(BitrixConfig.WEBHOOK_URL, "crm.activity.list.json", params)

    if "error" in result:
        print(f"❌ Błąd API Bitrix: {result}")
        return None

    activities = result.get("result", [])

    for activity in activities:
        settings = activity.get("SETTINGS", {})

        # Zabezpieczenie przed pustą listą w settings (bug Bitrixa)
        if isinstance(settings, list):
            continue

        original_msg = settings.get("ORIGINAL_MESSAGE", {})
        bitrix_message_to = original_msg.get("MESSAGE_TO", "")

        clean_bitrix_to = clean_phone_number(bitrix_message_to)

        # Porównujemy numer z Bitrixa z numerem przychodzącym
        if clean_bitrix_to and clean_bitrix_to == clean_sms_from:
            owner_id = activity.get("OWNER_ID")
            owner_type_id = activity.get("OWNER_TYPE_ID")
            responsible_id = activity.get("RESPONSIBLE_ID")  # Pobieramy opiekuna

            print(f"✅ SUKCES! Znaleziono dopasowanie.")
            print(f"   Aktywność ID: {activity['ID']}")
            print(f"   OWNER_ID: {owner_id} (Typ: {owner_type_id})")
            print(f"   Opiekun: {responsible_id}")

            return {
                "OWNER_ID": owner_id,
                "OWNER_TYPE_ID": owner_type_id,
                "RESPONSIBLE_ID": responsible_id
            }

    print("⚠️ Nie znaleziono w Bitrix aktywności SMS wysłanej na ten numer telefonu.")
    return None


def add_new_activity(
        owner_id,  # ID elementu nadrzędnego (np. Deal ID)
        owner_type_id,  # Typ elementu (1=Lead, 2=Deal, 3=Contact, 4=Company)
        responsible_id,  # ID osoby odpowiedzialnej
        description,  # Treść (może zawierać BBCode)
):
    """
    Dodaje nową aktywność (np. notatkę o SMS, zadanie) do CRM.
    """

    subject = "SMS odebrany"
    provider_id = "CRM_TODO"
    provider_type_id = "TODO"
    completed = "Y"  # Czy zadanie jest zakończone (Y/N)
    direction = "1"  # 1 = Przychodzące, 2 = Wychodzące

    print(f"🚀 Wysyłam nową aktywność: '{subject}' dla ID {owner_id}...")

    # Budowanie struktury 'fields'
    fields = {
        "OWNER_ID": owner_id,
        "OWNER_TYPE_ID": owner_type_id,
        "PROVIDER_ID": provider_id,
        "PROVIDER_TYPE_ID": provider_type_id,
        "SUBJECT": subject,
        "RESPONSIBLE_ID": responsible_id,
        "DESCRIPTION": description,
        "COMPLETED": completed,
        "DIRECTION": direction
    }

    # Wywołanie API
    # Używamy metody crm.activity.add
    print("Dane aktywności: ", fields)

    # Zakładam użycie standardowego WEBHOOK_URL, chyba że masz dedykowany dla Activity
    url_base = getattr(BitrixConfig, 'WEBHOOK_URL_ACTIVITY_ADD', BitrixConfig.WEBHOOK_URL)

    result = bitrix_call(url_base, "crm.activity.add.json", {"fields": fields})

    if "result" in result:
        new_id = result["result"]
        print(f"✅ Sukces! Dodano aktywność. ID: {new_id}")
        return new_id
    else:
        print(f"❌ Błąd podczas dodawania aktywności: {result}")
        return None
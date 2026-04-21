import requests
import time
import re

from config import BitrixConfig


# ---------------------------------------------------------------------------
# Narzędzia pomocnicze
# ---------------------------------------------------------------------------

def bitrix_call(webhook_url, method, params=None):
    """
    Wysyła zapytanie do Bitrix24 z mechanizmem Retry (ponawiania prób).
    W przypadku błędu czeka 4 sekundy i próbuje ponownie.
    """
    if params is None:
        params = {}

    url = f"{webhook_url}{method}"
    max_retries = 5

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=params, timeout=30)
            response.raise_for_status()
            result = response.json()

            if "error" in result and result.get("error") == "QUERY_LIMIT_EXCEEDED":
                raise requests.exceptions.RequestException(
                    "Przekroczono limit zapytań (QUERY_LIMIT_EXCEEDED)"
                )

            return result

        except requests.exceptions.RequestException as e:
            print(f"⚠️ Błąd połączenia/TimeOut (Próba {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                print("⏳ Czekam 4 sekundy przed ponowną próbą...")
                time.sleep(4)
            else:
                print("❌ Błąd krytyczny: Nie udało się połączyć po wszystkich próbach.")
                return {"error": str(e)}


def clean_phone_number(phone: str) -> str:
    """
    Usuwa wszystkie znaki niebędące cyframi.
    '+48 500-123-456'  →  '48500123456'
    """
    if not phone:
        return ""
    return re.sub(r"[^0-9]", "", str(phone))


def format_phone_for_bitrix(phone: str) -> str | None:
    """
    Przyjmuje dowolny numer (z/bez plusa, z/bez 48) i zwraca
    format akceptowany przez filtr Bitrixa: '+48XXXXXXXXX'.

    Obsługuje:
      '48886778770'     →  '+48886778770'
      '+48886778770'    →  '+48886778770'
      '886778770'       →  '+48886778770'  (9-cyfrowy numer krajowy)
      '0048886778770'   →  '+48886778770'

    Zwraca None, gdy numer nie da się doprowadzić do właściwej postaci.
    """
    digits = clean_phone_number(phone)

    # Usuń ewentualne '00' na początku (00 48 ...)
    if digits.startswith("00"):
        digits = digits[2:]

    # Jeśli sam 9-cyfrowy numer krajowy → dodaj prefix 48
    if len(digits) == 9:
        digits = "48" + digits

    # Teraz powinniśmy mieć 11 cyfr zaczynające się od 48
    if len(digits) == 11 and digits.startswith("48"):
        return f"+{digits}"

    print(f"⚠️ Nie można znormalizować numeru '{phone}' do formatu +48XXXXXXXXX")
    return None


# ---------------------------------------------------------------------------
# Szukanie właściciela po numerze telefonu (nowe podejście: kontakt → CRM)
# ---------------------------------------------------------------------------

def _search_contacts_by_phone(phone_variant: str) -> list[str]:
    """
    Pojedyncze zapytanie crm.contact.list dla jednego wariantu numeru.
    Zwraca listę ID lub [] w przypadku błędu / braku wyników.
    """
    result = bitrix_call(
        BitrixConfig.WEBHOOK_URL,
        "crm.contact.list.json",
        {
            "filter": {"PHONE": phone_variant},
            "select": ["ID", "NAME", "PHONE"],
        },
    )

    if "error" in result:
        print(f"   ⚠️ crm.contact.list błąd dla '{phone_variant}': {result}")
        return []

    contacts = result.get("result", [])
    return [c["ID"] for c in contacts if c.get("ID")]


def _find_contact_ids(formatted_phone: str) -> list[str]:
    """
    Zwraca listę unikalnych ID kontaktów mających podany numer telefonu.

    Próbuje dwóch wariantów numeru, żeby pokryć różne sposoby zapisu w Bitrix:
      1. '+48886778770'  – z plusem (standard E.164)
      2.  '48886778770'  – same cyfry, bez plusa

    Jeśli pierwszy wariant zwróci wyniki, drugi jest pomijany.
    """
    # Wariant 1: z plusem – np. +48886778770
    print(f"   🔍 Szukam kontaktu wg numeru: '{formatted_phone}'")
    ids = _search_contacts_by_phone(formatted_phone)

    if ids:
        print(f"   ✅ Wariant z '+' znalazł {len(ids)} kontakt(ów): {ids}")
        return ids

    # Wariant 2: bez plusa – np. 48886778770
    digits_only = formatted_phone.lstrip("+")
    print(f"   🔍 Brak wyników – ponawiam bez '+': '{digits_only}'")
    ids = _search_contacts_by_phone(digits_only)

    if ids:
        print(f"   ✅ Wariant bez '+' znalazł {len(ids)} kontakt(ów): {ids}")
    else:
        print(f"   ℹ️ Żaden wariant numeru nie zwrócił kontaktów.")

    return ids


def _find_latest_crm_record(contact_ids: list[str]) -> dict | None:
    """
    Dla podanych ID kontaktów przeszukuje Leady i Deale.
    Zwraca jeden rekord (najnowszy wg ID DESC) wraz z typem właściciela.
    Ignoruje Leady ze statusem "CONVERTED".

    Zwracany słownik:
      {
        "OWNER_ID":       str,
        "OWNER_TYPE_ID":  str,   # "1" = Lead, "2" = Deal
        "RESPONSIBLE_ID": str,
      }
    """
    candidates = []  # lista: (int(id), owner_type_id, responsible_id)

    for contact_id in contact_ids:
        # --- Leady ---
        lead_result = bitrix_call(
            BitrixConfig.WEBHOOK_URL,
            "crm.lead.list.json",
            {
                "filter": {"CONTACT_ID": contact_id},
                "select": ["ID", "ASSIGNED_BY_ID", "STATUS_ID"],
                "order": {"ID": "DESC"},
            },
        )
        for lead in lead_result.get("result", []):
            # Zignoruj leady, które zostały już przekonwertowane
            if lead.get("STATUS_ID") == "CONVERTED":
                continue

            candidates.append(
                (int(lead["ID"]), "1", lead.get("ASSIGNED_BY_ID", "1"))
            )

        # --- Deale ---
        deal_result = bitrix_call(
            BitrixConfig.WEBHOOK_URL,
            "crm.deal.list.json",
            {
                "filter": {"CONTACT_ID": contact_id},
                "select": ["ID", "ASSIGNED_BY_ID"],
                "order": {"ID": "DESC"},
            },
        )
        for deal in deal_result.get("result", []):
            candidates.append(
                (int(deal["ID"]), "2", deal.get("ASSIGNED_BY_ID", "1"))
            )

    if not candidates:
        return None

    # Wybieramy rekord z najwyższym ID (= najnowszy)
    latest = max(candidates, key=lambda x: x[0])
    owner_id, owner_type_id, responsible_id = latest

    type_label = "Lead" if owner_type_id == "1" else "Deal"
    print(f"✅ Najnowszy rekord: {type_label} ID={owner_id}, opiekun={responsible_id}")

    return {
        "OWNER_ID": str(owner_id),
        "OWNER_TYPE_ID": owner_type_id,
        "RESPONSIBLE_ID": str(responsible_id),
    }


def find_owner_by_incoming_sms(phone_number: str) -> dict | None:
    """
    Wyszukuje właściciela (Lead/Deal) dla przychodzącego SMS-a.

    Przepływ:
      1. Normalizuj numer → '+48XXXXXXXXX'
      2. Znajdź kontakty w Bitrix24 po tym numerze
      3. Znajdź wszystkie powiązane Leady i Deale
      4. Zwróć najnowszy rekord (najwyższe ID)

    Zwraca słownik { OWNER_ID, OWNER_TYPE_ID, RESPONSIBLE_ID }
    lub None, gdy nic nie znaleziono.
    """
    formatted = format_phone_for_bitrix(phone_number)
    if not formatted:
        return None

    print(f">>> Szukanie kontaktu w Bitrix24 dla numeru {formatted}...")

    contact_ids = _find_contact_ids(formatted)
    if not contact_ids:
        print("⚠️ Brak kontaktów z tym numerem w Bitrix24.")
        return None

    owner = _find_latest_crm_record(contact_ids)
    if not owner:
        print("⚠️ Kontakt(y) znalezione, ale brak powiązanych Leadów/Deali.")

    return owner


# ---------------------------------------------------------------------------
# Dodawanie aktywności – wygląd wiadomości SMS przychodzącej
# ---------------------------------------------------------------------------

def add_new_activity(
    owner_id: str,
    owner_type_id: str,
    responsible_id: str,
    description: str,
) -> str | None:
    """
    Dodaje aktywność "SMS przychodzący" do osi czasu CRM.

    Używamy PROVIDER_ID=CRM_TODO (zamiast CRM_SMS), ponieważ CRM_SMS jest
    w Bitrix24 przeznaczony wyłącznie do wiadomości wychodzących i zawsze
    wyświetla się jako "Wysłano wiadomość SMS" z przyciskiem "Wyślij ponownie".
    CRM_TODO renderuje się jako notatka z naszym własnym tytułem,
    bez mylącego labela i bez przycisku ponownego wysyłania.
    """

    fields = {
        "OWNER_ID":        owner_id,
        "OWNER_TYPE_ID":   owner_type_id,

        "PROVIDER_ID":      "CRM_TODO",
        "PROVIDER_TYPE_ID": "TODO",

        "SUBJECT":          "📨 SMS przychodzący",
        "DESCRIPTION":      description,
        "DESCRIPTION_TYPE": "3",   # 3 = BBCode

        "RESPONSIBLE_ID":   responsible_id,

        "DIRECTION":  "2",   # 2 = Incoming dla CRM_TODO
        "COMPLETED":  "Y",   # Zakończone – nie wymaga akcji
        "PRIORITY":   "2",   # Normalny priorytet
    }

    print(f"🚀 Dodaję aktywność SMS przychodzący dla OWNER_ID={owner_id} (typ {owner_type_id})...")
    print("   Pola:", fields)

    url_base = getattr(
        BitrixConfig, "WEBHOOK_URL_ACTIVITY_ADD", BitrixConfig.WEBHOOK_URL
    )

    result = bitrix_call(url_base, "crm.activity.add.json", {"fields": fields})

    if "result" in result:
        new_id = result["result"]
        print(f"✅ Aktywność SMS dodana. ID: {new_id}")
        return new_id
    else:
        print(f"❌ Błąd podczas dodawania aktywności: {result}")
        return None
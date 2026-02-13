# utils.py
import os

# Obsługa dźwięku
try:
    import winsound
except ImportError:
    winsound = None

def format_phone_number(phone):
    if not phone: return "Nieznany"
    p = str(phone).strip().replace(" ", "").replace("+", "")
    if len(p) == 11 and p.startswith("48"):
        return f"+{p[:2]} {p[2:5]} {p[5:8]} {p[8:]}"
    if len(p) == 9 and p.isdigit():
        return f"{p[:3]} {p[3:6]} {p[6:]}"
    return phone

def normalize_num(n):
    """Pomocnicza funkcja do czyszczenia numerów do porównań."""
    if not n: return ""
    s = str(n).strip().replace(" ", "").replace("-", "").replace("+", "")
    if s.startswith("48") and len(s) > 9:
        s = s[2:]
    return s

def play_sound_loop(sound_file="sound.wav"):
    if winsound:
        try:
            if os.path.exists(sound_file):
                winsound.PlaySound(sound_file, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
            else:
                winsound.PlaySound("SystemHand", winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_LOOP)
        except Exception as e:
            print(f"Błąd startu dźwięku: {e}")

def stop_sound():
    if winsound:
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception as e:
            print(f"Błąd zatrzymania dźwięku: {e}")
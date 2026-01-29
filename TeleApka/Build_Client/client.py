import tkinter as tk
import requests
import threading
import time

# Konfiguracja
#API_URL = "http://localhost:8000"  # Zmiana z 5000 na 8000
API_URL = "http://192.168.10.111:8000"
POLL_INTERVAL = 2  # Co ile sekund sprawdzać (w wątku)


class AlertClient:
    def __init__(self, root):
        self.root = root
        self.root.title("System Powiadomień")
        self.root.geometry("400x250")

        # Zmienne stanu (współdzielone między wątkiem a GUI)
        self.api_connected = True
        self.alert_active = False
        self.alert_message = ""
        self.last_known_status = "unknown"

        # --- BUDOWA GUI ---

        # 1. Główny komunikat alertu (domyślnie ukryty)
        self.alert_frame = tk.Frame(root, bg="red")
        self.alert_label = tk.Label(self.alert_frame, text="", font=("Arial", 16, "bold"), bg="red", fg="white")
        self.alert_label.pack(pady=20)
        self.confirm_btn = tk.Button(self.alert_frame, text="Potwierdzam / Zamknij", command=self.send_close_signal)
        self.confirm_btn.pack(pady=10)

        # 2. Pasek statusu połączenia (na dole)
        self.status_bar = tk.Label(root, text="Uruchamianie...", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 3. Ekran oczekiwania (gdy brak alertu)
        self.idle_label = tk.Label(root, text="System czuwa.\nBrak aktywnych zgłoszeń.", font=("Arial", 10))
        self.idle_label.pack(expand=True)

        # Uruchomienie wątku sieciowego w tle
        # daemon=True oznacza, że wątek zamknie się razem z zamknięciem okna
        self.thread = threading.Thread(target=self.network_loop, daemon=True)
        self.thread.start()

        # Uruchomienie odświeżania GUI (co 500ms)
        self.root.after(500, self.update_gui)

    def network_loop(self):
        """Ta funkcja działa w tle i nigdy nie blokuje okna"""
        while True:
            try:
                # Próba połączenia (timeout ważny, żeby nie wisiało zbyt długo)
                response = requests.get(f"{API_URL}/status", timeout=2)
                data = response.json()

                # Sukces - aktualizujemy zmienne stanu
                self.api_connected = True

                if data.get('status') == 'active':
                    self.alert_active = True
                    self.alert_message = data.get('message', 'Nieznane zgłoszenie')
                else:
                    self.alert_active = False

            except requests.exceptions.ConnectionError:
                self.api_connected = False
            except Exception as e:
                print(f"Inny błąd: {e}")
                self.api_connected = False

            # Czekamy przed kolejnym sprawdzeniem
            time.sleep(POLL_INTERVAL)

    def update_gui(self):
        """Ta funkcja aktualizuje wygląd na podstawie zmiennych stanu"""

        # 1. Obsługa paska statusu (Brak połączenia)
        if not self.api_connected:
            self.status_bar.config(text="⚠ BRAK POŁĄCZENIA Z APLIKACJĄ (API)", bg="orange", fg="black")
            # Opcjonalnie: Jeśli nie ma sieci, nie zmieniamy stanu okna, zostawiamy ostatni znany
        else:
            self.status_bar.config(text="Połączono z serwerem", bg="#f0f0f0", fg="green")

            # 2. Obsługa Alertu (tylko gdy jest połączenie)
            if self.alert_active:
                if not self.alert_frame.winfo_ismapped():
                    self.idle_label.pack_forget()  # Ukryj "czuwanie"
                    self.alert_frame.pack(fill=tk.BOTH, expand=True)  # Pokaż alert
                    self.root.deiconify()  # Wyciągnij okno na wierzch
                    self.root.attributes("-topmost", True)  # Opcjonalnie: zawsze na wierzchu

                self.alert_label.config(text=self.alert_message)
            else:
                if self.alert_frame.winfo_ismapped():
                    self.alert_frame.pack_forget()  # Ukryj alert
                    self.idle_label.pack(expand=True)  # Pokaż "czuwanie"
                    self.root.attributes("-topmost", False)
                    # Opcjonalnie: self.root.withdraw() jeśli chcesz chować całe okno

        # Zaplanuj kolejne odświeżenie GUI
        self.root.after(500, self.update_gui)

    def send_close_signal(self):
        # Wysyłamy żądanie w osobny sposób, żeby nie blokować przycisku
        def _req():
            try:
                requests.post(f"{API_URL}/close", timeout=2)
            except:
                pass  # Błąd obsłuży pętla główna

        threading.Thread(target=_req, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    app = AlertClient(root)
    root.mainloop()
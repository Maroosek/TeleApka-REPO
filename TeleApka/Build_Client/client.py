import tkinter as tk
import requests
import threading
import time

# Konfiguracja
#API_URL = "http://192.168.10.111:8020"
API_URL = "http://64.225.111.62:8020"
POLL_INTERVAL = 2


class AlertClient:
    def __init__(self, root):
        self.root = root
        self.root.title("System Powiadomień")
        self.root.geometry("500x400")

        # Stan
        self.api_connected = True
        self.active_alerts = []
        self.seen_alert_ids = set()  # Zbiór do śledzenia ID powiadomień, które już "mignęły"

        # --- GUI ---
        # 1. Pasek statusu
        self.status_bar = tk.Label(root, text="Uruchamianie...", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 2. Ekran oczekiwania (gdy brak alertów)
        self.idle_frame = tk.Frame(root, bg="#f0f0f0")
        tk.Label(self.idle_frame, text="System czuwa.\nBrak aktywnych zgłoszeń.",
                 font=("Arial", 14), fg="#888888", bg="#f0f0f0").pack(expand=True)

        # 3. Kontener na alerty (domyślnie ukryty, jeśli brak alertów)
        self.alerts_container = tk.Frame(root, bg="#ffffff")

        # Wątek sieciowy
        self.thread = threading.Thread(target=self.network_loop, daemon=True)
        self.thread.start()

        # Odświeżanie GUI
        self.root.after(500, self.update_gui)

    def network_loop(self):
        while True:
            try:
                response = requests.get(f"{API_URL}/status", timeout=2)
                data = response.json()
                self.api_connected = True
                self.active_alerts = data.get("alerts", [])

                # Debug w konsoli
                print(f"Pętla działa. Aktywne alerty: {len(self.active_alerts)}")

            except requests.exceptions.ConnectionError:
                self.api_connected = False
            except Exception as e:
                print(f"Błąd API: {e}")
                self.api_connected = False

            time.sleep(POLL_INTERVAL)

    def update_gui(self):
        # 1. Obsługa paska statusu
        if not self.api_connected:
            self.status_bar.config(text="⚠ BRAK POŁĄCZENIA Z API", bg="orange")
        else:
            self.status_bar.config(text=f"Połączono. Aktywne: {len(self.active_alerts)}", bg="#dddddd")

        # 2. Logika "Wyskakiwania" okna (Pop-up)
        # Pobieramy ID wszystkich aktualnych alertów z serwera
        current_ids = {alert['id'] for alert in self.active_alerts}

        # Sprawdzamy, czy pojawiło się coś nowego (różnica zbiorów)
        new_alerts = current_ids - self.seen_alert_ids

        if new_alerts:
            # Mamy nowe powiadomienie! Wymuszamy okno na wierzch
            print("Nowy alert wykryty! Wyciągam okno.")
            self.force_window_to_front()

        # Aktualizujemy listę znanych alertów, żeby nie wyskakiwało w kółko dla tego samego
        self.seen_alert_ids = current_ids

        # 3. Rysowanie interfejsu (Alerty vs Ekran Czuwania)
        if len(self.active_alerts) > 0:
            # Mamy alerty - chowamy ekran czuwania, pokazujemy listę
            self.idle_frame.pack_forget()
            self.alerts_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            # Odświeżamy listę kafelków
            self.refresh_alerts_list()
        else:
            # Brak alertów - chowamy listę, pokazujemy ekran czuwania
            self.alerts_container.pack_forget()
            self.idle_frame.pack(fill=tk.BOTH, expand=True)

            # WAŻNE: Nie robimy tutaj self.root.withdraw(), więc okno zostaje widoczne!

        self.root.after(1000, self.update_gui)

    def force_window_to_front(self):
        """Wymusza pojawienie się okna na wierzchu pulpitu"""
        self.root.deiconify()  # Jeśli było zminimalizowane -> przywróć
        self.root.lift()  # Wyciągnij warstwę okna nad inne
        self.root.attributes("-topmost", True)  # Ustaw "Zawsze na wierzchu"
        # Opcjonalnie: zdejmij "Zawsze na wierzchu" po chwili, żeby nie blokować komputera
        # self.root.after(1000, lambda: self.root.attributes("-topmost", False))

    def refresh_alerts_list(self):
        # Usuwamy stare widgety
        for widget in self.alerts_container.winfo_children():
            widget.destroy()

        # Rysujemy nowe
        for alert in self.active_alerts:
            self.create_alert_widget(alert)

    def create_alert_widget(self, alert_data):
        frame = tk.Frame(self.alerts_container, bg="white", bd=2, relief=tk.RAISED)
        frame.pack(fill=tk.X, pady=5)

        info_frame = tk.Frame(frame, bg="white")
        info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=5)

        tk.Label(info_frame, text=alert_data['message'], font=("Arial", 12, "bold"),
                 bg="white", fg="red", anchor="w").pack(fill=tk.X)

        details = f"Od: {alert_data['caller']} | Źródło: {alert_data['source']}"
        tk.Label(info_frame, text=details, font=("Arial", 9),
                 bg="white", fg="#555", anchor="w").pack(fill=tk.X)

        btn = tk.Button(frame, text="✖ Zamknij", bg="#ffcccc",
                        command=lambda alert_id=alert_data['id']: self.send_close_signal(alert_id))
        btn.pack(side=tk.RIGHT, padx=10, fill=tk.Y)

    def send_close_signal(self, alert_id):
        def _req():
            try:
                payload = {"alert_id": alert_id}
                requests.post(f"{API_URL}/close", json=payload, timeout=2)
            except Exception as e:
                print(f"Błąd zamykania: {e}")

        threading.Thread(target=_req, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    app = AlertClient(root)
    root.mainloop()
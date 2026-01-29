import tkinter as tk
import requests
import threading
import time

# Konfiguracja
API_URL_SECONDARY = "http://192.168.10.111:8020"  # Lokalne (Domyślne)
API_URL_PRIMARY = "http://64.225.111.62:8020"  # Zapasowe (Świat)
POLL_INTERVAL = 2


class AlertClient:
    def __init__(self, root):
        self.root = root
        self.root.title("Monitor Połączeń - Telestrada")
        self.root.geometry("500x450")

        # Stan
        self.current_api_url = API_URL_PRIMARY  # Zaczynamy od głównego
        self.api_connected = True
        self.active_alerts = []
        self.seen_alert_ids = set()

        # --- GUI ---
        # 1. Pasek statusu
        self.status_bar = tk.Label(root, text="Uruchamianie...", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 2. Ekran oczekiwania
        self.idle_frame = tk.Frame(root, bg="#f0f0f0")
        tk.Label(self.idle_frame, text="System czuwa.\nOczekiwanie na połączenia...",
                 font=("Arial", 14), fg="#888888", bg="#f0f0f0").pack(expand=True)

        # 3. Kontener na alerty
        self.alerts_container = tk.Frame(root, bg="#ffffff")

        # Wątek sieciowy
        self.thread = threading.Thread(target=self.network_loop, daemon=True)
        self.thread.start()

        # Odświeżanie GUI
        self.root.after(500, self.update_gui)

    def network_loop(self):
        while True:
            try:
                # Używamy self.current_api_url
                response = requests.get(f"{self.current_api_url}/status", timeout=2)
                data = response.json()

                self.api_connected = True
                self.active_alerts = data.get("alerts", [])

                # Debug w konsoli
                # print(f"Połączono z {self.current_api_url}. Aktywne: {len(self.active_alerts)}")

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                print(f"⚠️ Błąd połączenia z {self.current_api_url}: {e}")
                self.api_connected = False

                # --- LOGIKA PRZEŁĄCZANIA (FAILOVER) ---
                if self.current_api_url == API_URL_PRIMARY:
                    print(f"🔄 Przełączam na zapasowe API: {API_URL_SECONDARY}")
                    self.current_api_url = API_URL_SECONDARY
                else:
                    print(f"🔄 Przełączam (powrót) na główne API: {API_URL_PRIMARY}")
                    self.current_api_url = API_URL_PRIMARY

                # Opcjonalnie: Możemy spróbować natychmiast połączyć się z nowym URL
                # w tej samej pętli, ale bezpieczniej poczekać do następnego cyklu (za 2s).

            except Exception as e:
                print(f"Inny błąd API: {e}")
                self.api_connected = False

            time.sleep(POLL_INTERVAL)

    def update_gui(self):
        # Określenie, z którego API korzystamy (do wyświetlania)
        source_name = "PRI" if self.current_api_url == API_URL_PRIMARY else "SEC"
        source_color = "#dddddd" if source_name == "PRI" else "#ffeebb"  # Żółty dla backupu

        # 1. Obsługa paska statusu
        if not self.api_connected:
            self.status_bar.config(text=f"⚠ BRAK POŁĄCZENIA ({source_name}) - Próba łączenia...", bg="orange")
        else:
            self.status_bar.config(
                text=f"Połączono [{source_name}]. Trwające rozmowy: {len(self.active_alerts)}",
                bg=source_color
            )

        # 2. Logika "Wyskakiwania" okna
        current_ids = {alert['id'] for alert in self.active_alerts}
        new_alerts = current_ids - self.seen_alert_ids

        if new_alerts:
            self.force_window_to_front()

        self.seen_alert_ids = current_ids

        # 3. Rysowanie interfejsu
        if len(self.active_alerts) > 0:
            self.idle_frame.pack_forget()
            self.alerts_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            self.refresh_alerts_list()
        else:
            self.alerts_container.pack_forget()
            self.idle_frame.pack(fill=tk.BOTH, expand=True)

        self.root.after(1000, self.update_gui)

    def force_window_to_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)

    def refresh_alerts_list(self):
        for widget in self.alerts_container.winfo_children():
            widget.destroy()
        for alert in self.active_alerts:
            self.create_alert_widget(alert)

    def create_alert_widget(self, alert_data):
        frame = tk.Frame(self.alerts_container, bg="white", bd=2, relief=tk.GROOVE)
        frame.pack(fill=tk.X, pady=4)

        caller = alert_data.get('caller', 'Nieznany')
        target = alert_data.get('agent_name') or alert_data.get('source', 'Infolinia')
        menu = alert_data.get('menu_name')
        status = alert_data.get('status') or "Dzwoni..."

        status_color = "#007bff"
        if status in ["ANSWERED", "Odebrane"]:
            status_color = "#28a745"
        elif status in ["BUSY", "Zajęte", "Rozłączono"]:
            status_color = "#dc3545"

        status_frame = tk.Frame(frame, bg=status_color, height=5)
        status_frame.pack(fill=tk.X)

        content_frame = tk.Frame(frame, bg="white", padx=10, pady=5)
        content_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(content_frame, text=f"📞 {caller} ➔ {target}",
                 font=("Arial", 14, "bold"), bg="white", fg="#333").pack(anchor="w")

        details_text = f"Status: {status}"
        if menu:
            details_text += f"  |  📂 Menu: {menu}"

        tk.Label(content_frame, text=details_text,
                 font=("Arial", 10), bg="white", fg="#666").pack(anchor="w")


if __name__ == "__main__":
    root = tk.Tk()
    app = AlertClient(root)
    root.mainloop()
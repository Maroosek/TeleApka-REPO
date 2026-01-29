import tkinter as tk
from tkinter import messagebox
import requests
import threading
import time

# Konfiguracja
API_URL = "http://64.225.111.62:8020"  # Główny adres API
POLL_INTERVAL = 1


class AlertClient:
    def __init__(self, root):
        self.root = root
        self.root.title("Monitor Połączeń - Telestrada")
        self.root.geometry("500x450")

        # Przechwycenie sygnału zamknięcia okna ("X")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Stan
        self.api_connected = True
        self.active_alerts = []
        self.seen_alert_ids = set()

        # --- GUI ---

        # 1. DOLNY PASEK (Kontener na status i przycisk)
        # Tworzymy ramkę na samym dole
        self.bottom_bar = tk.Frame(root, bd=1, relief=tk.SUNKEN)
        self.bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # A. Etykieta statusu (po lewej stronie paska)
        self.status_label = tk.Label(self.bottom_bar, text="Uruchamianie...", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # B. Przycisk Pomocy (po prawej stronie paska, mały)
        self.help_btn = tk.Button(self.bottom_bar, text="?", font=("Arial", 8, "bold"),
                                  bg="#e0e0e0", width=3, bd=1,
                                  command=self.show_help_window)
        self.help_btn.pack(side=tk.RIGHT, padx=2, pady=1)

        # 2. Ekran oczekiwania (gdy brak alertów)
        self.idle_frame = tk.Frame(root, bg="#f0f0f0")
        tk.Label(self.idle_frame, text="System czuwa.\nOczekiwanie na połączenia...",
                 font=("Arial", 14), fg="#888888", bg="#f0f0f0").pack(expand=True)

        # 3. Kontener na alerty z Paskiem Przewijania
        self.canvas_frame = tk.Frame(root, bg="#ffffff")

        self.canvas = tk.Canvas(self.canvas_frame, bg="#ffffff", highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.canvas_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#ffffff")

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # Wątek sieciowy
        self.thread = threading.Thread(target=self.network_loop, daemon=True)
        self.thread.start()

        # Odświeżanie GUI
        self.root.after(500, self.update_gui)

    def on_closing(self):
        if messagebox.askyesno("Zamykanie",
                               "Wyłączysz aplikację i przestaniesz otrzymywać powiadomienia.\nCzy kontynuować?"):
            self.root.destroy()

    def show_help_window(self):
        """Wyświetla okno z legendą i opisem"""
        help_win = tk.Toplevel(self.root)
        help_win.title("Pomoc / Legenda")
        help_win.geometry("400x350")
        help_win.resizable(False, False)
        help_win.attributes("-topmost", True)

        tk.Label(help_win, text="O Aplikacji", font=("Arial", 12, "bold")).pack(pady=(10, 5))
        desc = ("Aplikacja monitoruje system telefoniczny Telestrada.\n"
                "Gdy klient dzwoni na infolinię, okno wyskakuje na wierzch,\n"
                "pokazując kto dzwoni i jaki temat (menu) wybrał.")
        tk.Label(help_win, text=desc, justify="center").pack(pady=5)

        tk.Frame(help_win, height=2, bd=1, relief=tk.SUNKEN).pack(fill=tk.X, padx=20, pady=10)

        tk.Label(help_win, text="Legenda Kolorów", font=("Arial", 12, "bold")).pack(pady=(5, 10))

        legend_frame = tk.Frame(help_win)
        legend_frame.pack(fill=tk.X, padx=40)

        def add_legend_row(color, text):
            row = tk.Frame(legend_frame, pady=3)
            row.pack(fill=tk.X)
            tk.Frame(row, bg=color, width=20, height=20).pack(side=tk.LEFT, padx=(0, 10))
            tk.Label(row, text=text, font=("Arial", 10)).pack(side=tk.LEFT)

        add_legend_row("#007bff", "Niebieski - Połączenie przychodzące / Dzwoni")
        add_legend_row("#28a745", "Zielony - Połączenie odebrane (Rozmowa trwa)")
        add_legend_row("#dc3545", "Czerwony - Zajęte / Rozłączono")

        tk.Button(help_win, text="Zamknij", command=help_win.destroy, width=15).pack(side=tk.BOTTOM, pady=20)

    def on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        if self.canvas_frame.winfo_ismapped():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def network_loop(self):
        while True:
            try:
                response = requests.get(f"{API_URL}/status", timeout=2)
                data = response.json()

                self.api_connected = True
                self.active_alerts = data.get("alerts", [])

            except Exception as e:
                # print(f"Błąd API: {e}")
                self.api_connected = False

            time.sleep(POLL_INTERVAL)

    def update_gui(self):
        # UWAGA: Teraz aktualizujemy self.status_label, a nie self.status_bar
        if not self.api_connected:
            self.status_label.config(text="⚠ Błąd połączenia, upewnij się że Internet działa", fg="red")
        else:
            self.status_label.config(
                text=f"Połączono. Trwające rozmowy: {len(self.active_alerts)}",
                fg="black"
            )

        current_ids = {alert['id'] for alert in self.active_alerts}
        new_alerts = current_ids - self.seen_alert_ids
        if new_alerts:
            self.force_window_to_front()
        self.seen_alert_ids = current_ids

        if len(self.active_alerts) > 0:
            self.idle_frame.pack_forget()
            self.canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            self.refresh_alerts_list()
        else:
            self.canvas_frame.pack_forget()
            self.idle_frame.pack(fill=tk.BOTH, expand=True)

        self.root.after(1000, self.update_gui)

    def force_window_to_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)

    def refresh_alerts_list(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        for alert in self.active_alerts:
            self.create_alert_widget(alert)

    def create_alert_widget(self, alert_data):
        frame = tk.Frame(self.scrollable_frame, bg="white", bd=2, relief=tk.GROOVE)
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

        if menu:
            tk.Label(content_frame, text=f"📂 {menu}",
                     font=("Arial", 12, "bold"), bg="white", fg="#0056b3").pack(anchor="w", pady=(2, 0))

        tk.Label(content_frame, text=f"Status: {status}",
                 font=("Arial", 8), bg="white", fg="#aaaaaa").pack(anchor="e", pady=(5, 0))


if __name__ == "__main__":
    root = tk.Tk()
    app = AlertClient(root)
    root.mainloop()
# main_window.py
import tkinter as tk
from tkinter import messagebox
import threading
import time
import copy
import re
import requests

import config
import utils
import report_windows
import history_window


class AlertClient:
    def __init__(self, root, username, is_admin, assigned_ag):
        self.root = root
        self.username = username
        self.is_admin = is_admin
        self.assigned_ag = assigned_ag
        self.ivr_mapping = {}  # Słownik: { "numer_bez_48": "Nazwa z API" }
        self.last_ivr_update = 0

        # Tytuł i ikona (bez zmian...)
        ag_title_str = ", ".join(map(str, assigned_ag)) if isinstance(assigned_ag, list) else (
            str(assigned_ag) if assigned_ag else "Brak")
        role_info = "ADMIN" if self.is_admin else f"Stanowisko: {ag_title_str}"
        self.root.title(f"Monitor połączeń JET - {self.username} [{role_info}]")
        self.root.geometry("500x450")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Stan
        self.api_connected = True
        self.active_alerts = []
        self.seen_alert_ids = set()
        self.last_data_snapshot = None
        self.is_sound_playing = False
        self.moh_start_time = None

        self.setup_ui()

        # Uruchomienie pętli sieciowej
        self.thread = threading.Thread(target=self.network_loop, daemon=True)
        self.thread.start()

        # --- NOWE: Wątek aktualizacji IVR co godzinę ---
        self.ivr_thread = threading.Thread(target=self.update_ivr_map_loop, daemon=True)
        self.ivr_thread.start()

        self.root.after(500, self.update_gui)

    def setup_ui(self):
        self.bottom_bar = tk.Frame(self.root, bd=1, relief=tk.SUNKEN)
        self.bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_label = tk.Label(self.bottom_bar, text="Uruchamianie...", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.help_btn = tk.Button(self.bottom_bar, text="?", font=("Arial", 8, "bold"), width=3, command=self.show_help)
        self.help_btn.pack(side=tk.RIGHT, padx=2)

        self.sound_enabled = tk.BooleanVar(value=(False if self.is_admin else True))

        if self.is_admin:
            self.admin_frame = tk.Frame(self.bottom_bar)
            self.admin_frame.pack(side=tk.LEFT, padx=2)
            tk.Button(self.admin_frame, text="Historia", font=("Arial", 8, "bold"), bg="#e0e0e0", width=8,
                      command=lambda: history_window.HistoryWindow(self.root)).pack(side=tk.LEFT, padx=1)
            tk.Button(self.admin_frame, text="Raport", font=("Arial", 8, "bold"), bg="#d1ecf1", width=8,
                      command=lambda: report_windows.show_report_selection(self.root)).pack(side=tk.LEFT, padx=1)

        self.idle_frame = tk.Frame(self.root, bg="#f0f0f0")
        wait_msg = "System czuwa.\nAdmin Mode" if self.is_admin else f"System czuwa.\n{self.assigned_ag}"
        tk.Label(self.idle_frame, text=wait_msg, font=("Arial", 14), fg="#888", bg="#f0f0f0").pack(expand=True)

        self.canvas_frame = tk.Frame(self.root, bg="#ffffff")
        self.canvas = tk.Canvas(self.canvas_frame, bg="#ffffff", highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.canvas_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#ffffff")

        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)),
                                                                                "units") if self.canvas_frame.winfo_ismapped() else None)

    def update_ivr_map_loop(self):
        """Pobiera dane i buduje płaską mapę numerów dla szybkiego wyszukiwania."""
        while True:
            try:
                params = {"token": config.TOKEN}
                response = requests.get(f"{config.API_URL}/stats-data", params=params, timeout=5)

                if response.status_code == 200:
                    raw_data = response.json()
                    # Jeśli API zwraca listę dokumentów (z MongoDB), bierzemy najnowszy
                    if isinstance(raw_data, list) and len(raw_data) > 0:
                        tree = raw_data[0]
                    elif isinstance(raw_data, dict) and "data" in raw_data:
                        tree = raw_data["data"][0] if raw_data["data"] else {}
                    else:
                        tree = raw_data

                    new_map = {}
                    # Przetwarzamy drzewko: Branza -> Zrodlo -> [Numery]
                    for branza, zrodla in tree.items():
                        if isinstance(zrodla, dict):
                            for zrodlo, numery in zrodla.items():
                                for nr in numery:
                                    # WAŻNE: Klucz w self.ivr_mapping musi być identyczny
                                    # z tym co wyjdzie z clean_ivr w create_alert_widget
                                    clean_nr = utils.normalize_num(str(nr))
                                    new_map[clean_nr] = f"{branza.upper()} - {zrodlo.capitalize()}"

                    self.ivr_mapping = new_map
                    created_at = raw_data.get("data", [{}])[0].get("created_at")
                    print(f"Zaktualizowano mapę IVR: {len(new_map)} numerów - Aktualizacja z: {created_at}")
                else:
                    print(f"Błąd API /stats-data: {response.status_code}")

            except Exception as e:
                print(f"Błąd przetwarzania mapy IVR: {e}")

            time.sleep(3600)  # Aktualizacja co godzinę

    def network_loop(self):
        while True:
            try:
                response = requests.get(f"{config.API_URL}/status", timeout=2)
                data = response.json()
                self.api_connected = True
                raw = data.get("alerts", [])
                if self.is_admin:
                    self.active_alerts = raw
                else:
                    ag_list = self.assigned_ag if isinstance(self.assigned_ag, list) else [str(self.assigned_ag)]
                    ag_list = [str(s) for s in ag_list]
                    self.active_alerts = [a for a in raw if str(a.get("source")) in ag_list]
            except:
                self.api_connected = False
            time.sleep(config.POLL_INTERVAL)

    def update_gui(self):
        if not self.api_connected:
            self.status_label.config(text="⚠ Brak połączenia z serwerem", fg="red")
        elif "Skopiowano" not in self.status_label.cget("text"):
            msg = f"Zalogowano: {self.username}" + (" (ADMIN)" if self.is_admin else "")
            self.status_label.config(text=f"{msg} | Połączenia: {len(self.active_alerts)}", fg="black")

        current_ids = {a['id'] for a in self.active_alerts}
        if current_ids - self.seen_alert_ids:
            self.root.deiconify();
            self.root.lift();
            self.root.attributes("-topmost", True)

        # Logika dźwięku
        moh_cond = any(a.get("status") == "MOH" and a.get("agent_name") for a in self.active_alerts)
        should_play = False
        if moh_cond:
            if self.moh_start_time is None:
                self.moh_start_time = time.time()
            elif time.time() - self.moh_start_time >= 3:
                should_play = True
        else:
            self.moh_start_time = None

        if self.sound_enabled.get() and should_play and not self.is_sound_playing:
            utils.play_sound_loop()
            self.is_sound_playing = True
        elif (not should_play or not self.sound_enabled.get()) and self.is_sound_playing:
            utils.stop_sound()
            self.is_sound_playing = False

        self.seen_alert_ids = current_ids

        if self.active_alerts != self.last_data_snapshot:
            if self.active_alerts:
                self.idle_frame.pack_forget()
                self.canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
                self.refresh_list()
            else:
                self.canvas_frame.pack_forget()
                self.idle_frame.pack(fill=tk.BOTH, expand=True)
            self.last_data_snapshot = copy.deepcopy(self.active_alerts)

        self.root.after(1000, self.update_gui)

    def refresh_list(self):
        for w in self.scrollable_frame.winfo_children(): w.destroy()
        for alert in self.active_alerts: self.create_alert_widget(alert)

    def create_alert_widget(self, alert):
        frame = tk.Frame(self.scrollable_frame, bg="white", bd=2, relief=tk.GROOVE)
        frame.pack(fill=tk.X, pady=4)

        caller = utils.format_phone_number(alert.get('caller', 'Nieznany'))
        target = alert.get('agent_name') or alert.get('source', 'Infolinia')
        menu_name = alert.get('menu_name', '') or ''

        # --- ZAAWANSOWANA LOGIKA POSZUKIWANIA KLUCZA MAPY ---
        candidates = []

        # 1. KANDYDAT PIERWSZY: Pole 'ivr' z API
        raw_ivr = alert.get('ivr') or alert.get('ivr_phone_number')
        if raw_ivr:
            candidates.append(utils.normalize_num(str(raw_ivr)))

        # 2. KANDYDAT DRUGI: Numer wyciągnięty z 'menu_name' (np. z "Firma - Opis 123456789")
        if menu_name:
            found = re.search(r'(?<!\d)(\d{9})(?!\d)', menu_name.replace(" ", "").replace("-", ""))
            if found:
                candidates.append(found.group(1))

        # 3. KANDYDAT TRZECI: Pole 'source'
        raw_source = alert.get('source')
        if raw_source:
            candidates.append(utils.normalize_num(str(raw_source)))

        # --- WERYFIKACJA KANDYDATÓW W MAPIE ---
        display_menu = menu_name
        matched_key = None

        for num in candidates:
            if num and num in self.ivr_mapping:
                display_menu = self.ivr_mapping[num]
                matched_key = num
                break

        # Logowanie (opcjonalne)
        if matched_key:
            print(f"✅ DOPASOWANO: {matched_key} -> {display_menu}")
        elif candidates:
            print(f"❌ BRAK W MAPIE. Kandydaci: {candidates}")

        status = alert.get('status')

        # Cenzura dla zwykłego użytkownika
        if not self.is_admin:
            target = re.sub(r'\d+', '', str(target)).lstrip('- ').strip()
            if not matched_key and display_menu:
                display_menu = re.sub(r'\d+', '', str(display_menu)).lstrip('- ').strip()

        col = "#28a745" if status in ["ANSWERED", "Odebrane"] else (
            "#dc3545" if status in ["BUSY", "Zajęte"] else "#007bff")

        tk.Frame(frame, bg=col, height=5).pack(fill=tk.X)
        c_frame = tk.Frame(frame, bg="white", padx=10, pady=5)
        c_frame.pack(fill=tk.BOTH)

        h_frame = tk.Frame(c_frame, bg="white")
        h_frame.pack(anchor="w", fill=tk.X)

        # Wyświetlanie dzwoniącego
        lbl = tk.Label(h_frame, text=f"📞 {caller}", font=("Arial", 14, "bold"), bg="white", cursor="hand2")
        lbl.pack(side=tk.LEFT)
        lbl.bind("<Button-1>", lambda e: self.copy_num(caller))

        # Wyświetlanie celu (agent/grupa)
        tk.Label(h_frame, text=f" ➔ {target}", font=("Arial", 14, "bold"), bg="white", fg="#555").pack(side=tk.LEFT)

        # --- WYŚWIETLANIE NAZWY FOLDERU / MENU ---
        if display_menu:
            folder_text = f"📂 {display_menu}"

            # DODATEK DLA ADMINA: Pokaż numer IVR obok nazwy
            if self.is_admin:
                if matched_key:
                    # Pokaż numer, który został zmapowany (kolor szary, mniejsza czcionka w myśli, tutaj w nawiasie)
                    folder_text += f" {matched_key}"
                elif candidates:
                    # Jeśli nie zmapowano, pokaż pierwszy numer po którym próbowaliśmy szukać
                    folder_text += f"   [? {candidates[0]}]"

            tk.Label(c_frame, text=folder_text, font=("Arial", 12, "bold"), fg="#0056b3", bg="white").pack(anchor="w")

    def copy_num(self, n):
        self.root.clipboard_clear();
        self.root.clipboard_append(n);
        self.root.update()
        orig = self.status_label.cget("bg")
        self.status_label.config(text=f"✅ Skopiowano: {n}", bg="#d4edda")
        self.root.after(3000, lambda: self.status_label.config(bg=orig))

    def show_help(self):
        h = tk.Toplevel(self.root)
        h.title("Pomoc");
        h.geometry("300x350")
        tk.Label(h, text="Kliknij numer, aby skopiować.", pady=20).pack()
        desc = ("Aplikacja monitoruje system telefoniczny Telestrada.\n"
                "Kliknij numer telefonu, aby go skopiować.\n"
                "(Działa w oknie głównym i w Historii)\n"
                "Wersja 0.71 [16.02]"
                )

        if not self.is_admin:
            desc += f"\n\nWyświetla połączenia skierowane na: {self.assigned_ag or 'Brak'}"
        tk.Label(h, text=desc, justify="center").pack(pady=5)

        tk.Frame(h, height=2, bd=1, relief=tk.SUNKEN).pack(fill=tk.X, padx=20, pady=10)
        tk.Label(h, text="Legenda Kolorów", font=("Arial", 12, "bold")).pack(pady=(5, 10))

        legend_frame = tk.Frame(h)
        legend_frame.pack(fill=tk.X, padx=40)

        def add_legend_row(color, text):
            row = tk.Frame(legend_frame, pady=3)
            row.pack(fill=tk.X)
            tk.Frame(row, bg=color, width=20, height=20).pack(side=tk.LEFT, padx=(0, 10))
            tk.Label(row, text=text, font=("Arial", 10)).pack(side=tk.LEFT)

        add_legend_row("#007bff", "Niebieski - Dzwoni")
        add_legend_row("#28a745", "Zielony - Odebrane")
        tk.Button(h, text="Zamknij", command=h.destroy, width=15).pack(side=tk.BOTTOM, pady=20)

    def on_closing(self):
        if messagebox.askyesno("Zamykanie", "Zamknąć monitor?"): self.root.destroy()
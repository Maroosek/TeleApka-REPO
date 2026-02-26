# main_window.py
import tkinter as tk
from tkinter import messagebox
import threading
import time
import copy
import re
import requests
import ctypes

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

        try:
            # Nadajemy aplikacji unikalny identyfikator (możesz wpisać cokolwiek)
            myappid = 'mojafirma.monitorjet.wersja.0.731'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass  # Zignoruj, jeśli aplikacja zostanie uruchomiona na innym systemie (np. Linux)

        # 3. Ustawienie ikony w lewym górnym rogu okna
        try:
            self.root.iconbitmap(utils.get_resource_path('app.ico'))
        except Exception as e:
            print(f"Nie udało się załadować ikony: {e}")

        # Stan
        self.api_connected = True
        self.active_alerts = []
        self.seen_alert_ids = set()
        self.last_data_snapshot = None
        self.is_sound_playing = False
        self.moh_start_time = None

        self.time_window = 15 * 60  # Okres czasu w sekundach (np. 15 minut = 900 sekund)
        self.load_threshold = 4  # Ile odebranych połączeń w podanym czasie wyzwala alert
        self.answered_calls_history = {}  # Format: { call_id: {"agent_key": "Agent [Dział]", "time": timestamp} }
        self.dismissed_counts = {}  # Zapisuje przy jakiej liczbie admin kliknął "Ukryj"
        self.current_overload_counts = {}  # Pomocnicza zmienna do odklikiwania

        self.setup_ui()

        # Uruchomienie pętli sieciowej
        self.thread = threading.Thread(target=self.network_loop, daemon=True)
        self.thread.start()

        # --- NOWE: Wątek aktualizacji IVR co godzinę ---
        self.ivr_thread = threading.Thread(target=self.update_ivr_map_loop, daemon=True)
        self.ivr_thread.start()

        self.root.after(500, self.update_gui)

    def setup_ui(self):
        # self.top_alert_frame = tk.Frame(self.root)
        # self.top_alert_frame.pack(side=tk.TOP, fill=tk.X)

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

            # ZMIANA: Podpinamy load_warning_frame bezpośrednio pod self.root
            self.load_warning_frame = tk.Frame(self.root, bg="#ffcc00", bd=2, relief=tk.RAISED)

            # ZMIANA: Tworzymy i pakujemy przycisk "Ukryj" jako PIERWSZY z lewej strony
            dismiss_btn = tk.Button(self.load_warning_frame, text="✖ Ukryj", font=("Arial", 8, "bold"),
                                    bg="#ffaa00", command=self.dismiss_load_warning)
            dismiss_btn.pack(side=tk.LEFT, padx=10, pady=2)

            # ZMIANA: Etykietę pakujemy jako drugą, więc pojawi się po prawej stronie od przycisku
            self.load_warning_label = tk.Label(self.load_warning_frame, text="", bg="#ffcc00",
                                               fg="black", font=("Arial", 10, "bold"))
            self.load_warning_label.pack(side=tk.LEFT, padx=10, pady=5)

        self.idle_frame = tk.Frame(self.root, bg="#f0f0f0")
        wait_msg = "System czuwa.\nAdmin Mode" if self.is_admin else f"System czuwa.\n{self.assigned_ag}"
        tk.Label(self.idle_frame, text=wait_msg, font=("Arial", 14), fg="#888", bg="#f0f0f0").pack(expand=True)

        self.canvas_frame = tk.Frame(self.root, bg="#ffffff")
        self.canvas = tk.Canvas(self.canvas_frame, bg="#ffffff", highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.canvas_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#ffffff")

        self.scrollable_frame.bind("<Configure>", self._update_scrollregion)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # Używamy nowej metody _on_canvas_configure zamiast starej lambdy
        self.canvas.bind("<Configure>", self._on_canvas_configure)
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
            elif time.time() - self.moh_start_time >= 2:
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

        self.check_agent_loads()

        self.root.after(1000, self.update_gui)

    def refresh_list(self):
        for w in self.scrollable_frame.winfo_children(): w.destroy()
        for alert in self.active_alerts: self.create_alert_widget(alert)


    def _update_scrollregion(self, event=None):
        """Wymusza obszar scrollowania nawet dla małej liczby elementów."""
        bbox = self.canvas.bbox("all")
        if not bbox:
            return

        # Pobieramy aktualną wysokość widocznego płótna
        canvas_height = self.canvas.winfo_height()
        if canvas_height <= 1:  # Zabezpieczenie na starcie aplikacji
            canvas_height = self.canvas.winfo_reqheight()

        # Dodajemy mały margines (np. +2 piksele) do wysokości płótna,
        # aby scroll miał zawsze miejsce do "przeskoku"
        min_scroll_height = canvas_height + 2

        # Wybieramy większą wartość: rzeczywisty dół zawartości ALBO wymuszone minimum
        new_bottom = max(bbox[3], min_scroll_height)

        self.canvas.configure(scrollregion=(bbox[0], bbox[1], bbox[2], new_bottom))


    def _on_canvas_configure(self, event):
        """Dostosowuje szerokość elementów do szerokości okna i przelicza scroll."""
        self.canvas.itemconfig(self.canvas_window, width=event.width)
        self._update_scrollregion()


    def check_agent_loads(self):
        """Monitoruje ilość połączeń na agenta/dział w zadanym oknie czasowym."""
        if not self.is_admin:
            return

        current_time = time.time()

        # 1. Zapisywanie nowo odebranych połączeń do pamięci
        for alert in self.active_alerts:
            status = alert.get('status')
            call_id = alert.get('id')
            agent = alert.get('agent_name')

            # Jeśli połączenie jest odebrane i nie ma go jeszcze w historii
            if status in ["ANSWERED", "Odebrane"] and call_id and agent:
                if call_id not in self.answered_calls_history:
                    menu = alert.get('menu_name', '')
                    dzial = menu.split('-')[0].strip() if menu and '-' in menu else "Nieznany Dział"
                    agent_key = f"{agent} [{dzial}]"

                    self.answered_calls_history[call_id] = {
                        "agent_key": agent_key,
                        "time": current_time
                    }

        # 2. Usuwanie starych połączeń (poza okresem np. ostatnich 15 minut)
        keys_to_delete = [
            c_id for c_id, data in self.answered_calls_history.items()
            if current_time - data["time"] > self.time_window
        ]
        for c_id in keys_to_delete:
            del self.answered_calls_history[c_id]

        # 3. Zliczanie połączeń dla poszczególnych agentów w oknie czasowym
        agent_loads = {}
        for data in self.answered_calls_history.values():
            ak = data["agent_key"]
            agent_loads[ak] = agent_loads.get(ak, 0) + 1

        # 4. Obsługa progów i odklikiwania
        messages_to_show = []

        # Resetujemy "odkliknięcie" agenta, jeśli jego obciążenie wróciło do normy
        for ak in list(self.dismissed_counts.keys()):
            if agent_loads.get(ak, 0) < self.load_threshold:
                del self.dismissed_counts[ak]

        self.current_overload_counts = {}

        for agent_key, count in agent_loads.items():
            if count >= self.load_threshold:
                self.current_overload_counts[agent_key] = count
                # Wyświetl w banerze tylko, jeśli aktualna ilość przebija tę odklikniętą
                if count > self.dismissed_counts.get(agent_key, 0):
                    messages_to_show.append(f"{agent_key}: {count} poł.")

        # 5. Wyświetlanie banera
        if messages_to_show:
            mins = int(self.time_window / 60)
            warn_text = f"⚠ DUŻE OBCIĄŻENIE ({mins} min): " + " | ".join(messages_to_show)
            self.load_warning_label.config(text=warn_text)

            # ZMIANA: Wymuszamy, aby baner wskoczył na samą górę, odpychając zawartość w dół
            target = None
            if self.canvas_frame.winfo_ismapped():
                target = self.canvas_frame
            elif self.idle_frame.winfo_ismapped():
                target = self.idle_frame

            if target:
                self.load_warning_frame.pack(side=tk.TOP, fill=tk.X, before=target)
            else:
                self.load_warning_frame.pack(side=tk.TOP, fill=tk.X)
        else:
            if hasattr(self, 'load_warning_frame') and self.load_warning_frame.winfo_ismapped():
                self.load_warning_frame.pack_forget()


    def dismiss_load_warning(self):
        """Zapisuje aktualny stan jako zignorowany, schowa baner aż sytuacja znowu się nie pogorszy."""
        for agent_key, count in self.current_overload_counts.items():
            self.dismissed_counts[agent_key] = count
        self.load_warning_frame.pack_forget()


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
        h.title("Pomoc")
        h.geometry("300x400")  # Delikatnie zwiększyłem okno, by zmieścić nowy przycisk

        tk.Label(h, text="Kliknij numer, aby skopiować.", pady=10).pack()
        desc = ("Aplikacja monitoruje system telefoniczny Telestrada.\n"
                "Kliknij numer telefonu, aby go skopiować.\n"
                "(Działa w oknie głównym i w Historii)\n"
                "Wersja 0.732 [23.02]"
                )

        if not self.is_admin:
            desc += f"\n\nWyświetla połączenia skierowane na: {self.assigned_ag or 'Brak'}"
        tk.Label(h, text=desc, justify="center").pack(pady=5)

        # NOWE: Przycisk dodawania użytkowników (tylko dla admina)
        if self.is_admin:
            tk.Button(h, text="➕ Dodaj użytkowników", bg="#17a2b8", fg="white", font=("Arial", 9, "bold"),
                      command=lambda: self.open_add_user_window(h)).pack(pady=10)

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

    def open_add_user_window(self, help_window):
        # Zamknięcie okna pomocy
        help_window.destroy()

        # Tworzenie nowego okna
        add_win = tk.Toplevel(self.root)
        add_win.title("Dodaj nowego użytkownika")
        add_win.geometry("350x250")
        add_win.grab_set()  # Blokuje interakcję z głównym oknem do czasu zamknięcia tego

        # Pola formularza
        tk.Label(add_win, text="Nazwa użytkownika (login):", font=("Arial", 10)).pack(pady=(15, 2))
        username_entry = tk.Entry(add_win, width=35)
        username_entry.pack(pady=5)

        tk.Label(add_win, text="Numery/Grupy (oddzielone przecinkiem):", font=("Arial", 10)).pack(pady=(10, 2))
        assigned_ag_entry = tk.Entry(add_win, width=35)
        assigned_ag_entry.pack(pady=5)

        def submit_new_user():
            username = username_entry.get().strip()
            ag_raw = assigned_ag_entry.get().strip()

            if not username or not ag_raw:
                messagebox.showwarning("Braki w danych", "Proszę wypełnić wszystkie pola!", parent=add_win)
                return

            # Parsowanie wpisanych numerów/grup do listy, pomijając puste spacje
            assigned_ag_list = [item.strip() for item in ag_raw.split(',') if item.strip()]

            # Budowanie payloadu zgodnie ze specyfikacją
            payload = {
                "username": username,
                "assigned_ag": assigned_ag_list,
                "is_admin": False
            }

            try:
                # UWAGA: Podmień "/users" na prawidłowy endpoint w Twoim API
                endpoint_url = f"{config.API_URL}/users"

                # Używamy JSON do automatycznego ustawienia nagłówka Content-Type na application/json
                #stats_response = requests.get(f"{config.API_URL}/stats-data", params={"token": config.TOKEN}, timeout=5)
                response = requests.post(endpoint_url, json=payload, params={"token": config.TOKEN}, timeout=5)

                if response.status_code in (200, 201):
                    messagebox.showinfo("Sukces", f"Pomyślnie dodano użytkownika: {username}", parent=add_win)
                    add_win.destroy()
                else:
                    messagebox.showerror("Błąd API",
                                         f"Nie udało się dodać użytkownika.\nStatus: {response.status_code}\nOdpowiedź: {response.text}",
                                         parent=add_win)

            except requests.exceptions.RequestException as e:
                messagebox.showerror("Błąd połączenia", f"Brak komunikacji z serwerem:\n{e}", parent=add_win)

        # Przycisk zatwierdzający
        tk.Button(add_win, text="Zapisz użytkownika", bg="#28a745", fg="white", font=("Arial", 10, "bold"),
                  command=submit_new_user).pack(pady=20)

    def on_closing(self):
        if messagebox.askyesno("Zamykanie", "Zamknąć monitor?"): self.root.destroy()
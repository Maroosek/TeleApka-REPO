# history_window.py
import tkinter as tk
from tkinter import ttk
import requests
from datetime import datetime
import config
import utils


class HistoryWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Historia Połączeń")
        self.geometry("700x500")

        self.showing_all = False
        self.history_data_cache = []
        self.current_sort_col = "date"
        self.current_sort_reverse = False

        self.setup_ui()
        self.fetch_data()

    def setup_ui(self):
        style = ttk.Style()
        style.configure("Treeview", font=("Arial", 10), rowheight=25)

        tree_frame = tk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.columns = ("phone", "date", "timediff", "menu")
        self.tree = ttk.Treeview(tree_frame, columns=self.columns, show="headings")

        self.tree.tag_configure("normal", foreground="black")
        self.tree.tag_configure("green", foreground="#28a745", font=("Arial", 10, "bold"))
        self.tree.tag_configure("orange", foreground="orange", font=("Arial", 10, "bold"))
        self.tree.tag_configure("red", foreground="red", font=("Arial", 10, "bold"))

        self.tree.heading("phone", text="Numer Telefonu", command=lambda: self.on_header_click("phone"))
        self.tree.heading("date", text="Ostatnie Połączenie", command=lambda: self.on_header_click("date"))
        self.tree.heading("timediff", text="Różnica", command=lambda: self.on_header_click("timediff"))
        self.tree.heading("menu", text="Źródło / Menu", command=lambda: self.on_header_click("menu"))

        self.tree.column("phone", width=120, anchor=tk.CENTER)
        self.tree.column("date", width=130, anchor=tk.CENTER)
        self.tree.column("timediff", width=120, anchor=tk.CENTER)
        self.tree.column("menu", width=250, anchor=tk.W)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<Button-1>", self.on_tree_click)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=5, padx=10)

        self.btn_toggle = tk.Button(btn_frame, text="Pokaż wszystko", command=self.toggle_view, bg="#e1e1e1", width=20)
        self.btn_toggle.pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Zamknij", command=self.destroy, bg="#ffdddd", width=15).pack(side=tk.RIGHT, padx=5)

    def fetch_data(self):
        if not self.winfo_exists(): return
        try:
            # --- 1. POBRANIE DANYCH ZE STATYSTYK DO UZUPEŁNIANIA BRAKÓW (FAILSAFE) ---
            stats_lookup = {}
            try:
                stats_response = requests.get(f"{config.API_URL}/stats-data", params={"token": config.TOKEN}, timeout=5)
                if stats_response.status_code == 200:
                    stats_json = stats_response.json()

                    # Pobieramy listę z klucza 'data' (zgodnie z Twoim przykładem JSON)
                    data_list = stats_json.get('data', [])

                    # Iterujemy po liście (zazwyczaj jest tam jeden główny słownik)
                    for data_item in data_list:
                        if isinstance(data_item, dict):
                            for company, groups in data_item.items():
                                # company np. 'beton polska'
                                if isinstance(groups, dict):
                                    for group_name, phones in groups.items():
                                        # group_name np. 'wizytówki'
                                        if isinstance(phones, list):
                                            for phone in phones:
                                                # Normalizacja numeru (usuwamy spacje, +, prefix 48)
                                                p_str = str(phone).strip().replace(" ", "").replace("+", "")
                                                p_base = p_str[2:] if p_str.startswith("48") else p_str

                                                # Tworzymy opis z mapy
                                                formatted_menu = f"{company} - {group_name}"
                                                stats_lookup[p_base] = formatted_menu
            except Exception as e:
                print(f"Błąd pobierania /stats-data: {e}")

            # --- 2. POBRANIE I GRUPOWANIE HISTORII POŁĄCZEŃ ---
            response = requests.get(f"{config.API_URL}/history", params={"token": config.TOKEN}, timeout=5)
            if response.status_code == 200:
                logs = response.json().get("logs", [])
                now = datetime.now()

                unique_logs = {}
                for log in logs:
                    # Normalizacja numeru z historii
                    raw_phone = str(log.get("phone_number", "")).strip().replace(" ", "").replace("+", "")
                    base_phone = raw_phone[2:] if raw_phone.startswith("48") else raw_phone

                    # Logika grupowania (najnowsze połączenie dla danego numeru)
                    if base_phone not in unique_logs:
                        unique_logs[base_phone] = log
                    else:
                        existing_date = unique_logs[base_phone].get("last_call", "")
                        new_date = log.get("last_call", "")
                        if new_date > existing_date:
                            unique_logs[base_phone] = log

                # --- 3. PRZETWARZANIE UNIKALNYCH LOGÓW ---
                new_cache = []
                count_displayed = 0

                for log in unique_logs.values():
                    raw_phone_display = str(log.get("phone_number", ""))

                    # Normalizacja do wyszukiwania w mapie (failsafe)
                    clean_for_lookup = raw_phone_display.strip().replace(" ", "").replace("+", "")
                    base_phone = clean_for_lookup[2:] if clean_for_lookup.startswith("48") else clean_for_lookup

                    fmt_phone = utils.format_phone_number(raw_phone_display)
                    raw_date = log.get("last_call", "").replace("T", " ").split(".")[0]

                    # --- LOGIKA FAILSAFE ---
                    menu_raw = log.get("last_menu_full")
                    menu_str = str(menu_raw).strip() if menu_raw is not None else ""

                    # WARUNEK: Jeśli brak opisu LUB opis krótszy niż 3 znaki -> użyj mapy
                    if len(menu_str) < 3 or menu_str.lower() in ("none", "null", "-"):
                        # Pobierz z mapy, jeśli nie ma w mapie -> wstaw "-"
                        menu = stats_lookup.get(base_phone, "-")
                    else:
                        # Jeśli API zwróciło poprawny opis, użyj go
                        menu = menu_str

                    try:
                        call_time = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
                        diff = now - call_time
                        diff_minutes = diff.total_seconds() / 60
                        diff_hours = diff_minutes / 60

                        if not self.showing_all and diff_hours > 48:
                            continue

                        fmt_timediff = str(diff).split(".")[0]

                        # Kolorowanie wierszy
                        row_tag = "normal"
                        if diff_minutes < 15:
                            row_tag = "green"
                        elif diff_minutes < 30:
                            row_tag = "orange"
                        elif diff_minutes < 60:
                            row_tag = "red"

                        new_cache.append({
                            'phone': fmt_phone,
                            'date': raw_date,
                            'timediff': fmt_timediff,
                            'menu': menu,
                            'tags': (row_tag,)
                        })
                        count_displayed += 1
                    except Exception:
                        if self.showing_all:
                            new_cache.append({
                                'phone': fmt_phone,
                                'date': raw_date,
                                'timediff': "???",
                                'menu': menu,
                                'tags': ()
                            })

                self.history_data_cache = new_cache
                self.refresh_tree_view()
                self.title(f"Historia Połączeń (Wyświetlono: {count_displayed})")
        except Exception as e:
            print(f"Błąd pobierania historii: {e}")

        self.after(60000, self.fetch_data)

    def refresh_tree_view(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        sort_key = self.current_sort_col
        if sort_key == "timediff": sort_key = "date"
        try:
            self.history_data_cache.sort(key=lambda x: x[sort_key], reverse=self.current_sort_reverse)
        except:
            pass
        for row in self.history_data_cache:
            self.tree.insert("", tk.END, values=(row['phone'], row['date'], row['timediff'], row['menu']),
                             tags=row['tags'])

    def on_header_click(self, col):
        if self.current_sort_col == col:
            self.current_sort_reverse = not self.current_sort_reverse
        else:
            self.current_sort_col = col
            self.current_sort_reverse = True if col in ["date", "timediff"] else False

        for c in self.columns:
            text = self.tree.heading(c, "text").replace(" ▲", "").replace(" ▼", "")
            self.tree.heading(c, text=text)
        arrow = " ▼" if self.current_sort_reverse else " ▲"
        self.tree.heading(col, text=self.tree.heading(col, "text") + arrow)
        self.refresh_tree_view()

    def on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell": return
        col_id = self.tree.identify_column(event.x)
        if col_id == "#1":
            item_id = self.tree.identify_row(event.y)
            if item_id:
                vals = self.tree.item(item_id, "values")
                if vals:
                    self.clipboard_clear()
                    self.clipboard_append(vals[0])
                    self.update()

    def toggle_view(self):
        self.showing_all = not self.showing_all
        self.btn_toggle.config(text="Pokaż tylko < 48h" if self.showing_all else "Pokaż wszystko")
        self.fetch_data()
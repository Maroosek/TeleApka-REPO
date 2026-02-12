import tkinter as tk
from tkinter import messagebox, filedialog
from tkinter import ttk
import requests
import threading
import time
import copy
import csv
import re
import os
from datetime import datetime, timedelta
from collections import defaultdict

# --- ZMIANA 1: Import biblioteki dźwiękowej (Windows) ---
try:
    import winsound
except ImportError:
    winsound = None  # Fallback dla systemów innych niż Windows

# Konfiguracja
# API_URL = "http://192.168.18.8:8020"
API_URL = "http://64.225.111.62:8020"
POLL_INTERVAL = 1
Token = "H4d98da91ji9DSAXm11"


class LoginWindow(tk.Toplevel):
    def __init__(self, parent, on_login_success):
        super().__init__(parent)
        self.on_login_success = on_login_success
        self.title("Logowanie - Monitor połączeń JET")
        self.geometry("300x200")
        self.resizable(False, False)

        # Centrowanie okna
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')

        # Elementy GUI
        tk.Label(self, text="Zaloguj się", font=("Arial", 12, "bold")).pack(pady=10)

        tk.Label(self, text="Użytkownik:").pack(pady=2)
        self.entry_user = tk.Entry(self)
        self.entry_user.pack(pady=2)

        self.btn_login = tk.Button(self, text="Wejdź", bg="#007bff", fg="white",
                                   width=15, command=self.attempt_login)
        self.btn_login.pack(pady=15)

        self.bind('<Return>', lambda event: self.attempt_login())
        self.protocol("WM_DELETE_WINDOW", parent.destroy)

    def attempt_login(self):
        username = self.entry_user.get().strip()
        token = Token

        if not username or not token:
            messagebox.showwarning("Błąd", "Podaj prawidłowy login")
            return

        try:
            payload = {"username": username, "token": token}
            response = requests.post(f"{API_URL}/login", json=payload, timeout=5)

            if response.status_code == 200:
                data = response.json()
                print(f"LOGIN SUCCESSFUL: {data}")
                self.destroy()
                # Przekazujemy dane do funkcji startującej główną aplikację
                self.on_login_success(
                    data.get("username"),
                    data.get("is_admin", False),
                    data.get("assigned_ag")
                )
            elif response.status_code == 404:
                messagebox.showerror("Błąd", "Użytkownik nie istnieje.")
            elif response.status_code == 401:
                messagebox.showerror("Błąd", "Błędny kod dostępu.")
            else:
                messagebox.showerror("Błąd", f"Błąd serwera: {response.status_code}")

        except requests.exceptions.ConnectionError:
            messagebox.showerror("Krytyczny błąd", "Nie można połączyć się z API.")
        except Exception as e:
            messagebox.showerror("Błąd", f"Wystąpił błąd: {e}")


class AlertClient:
    def __init__(self, root, username, is_admin, assigned_ag):
        self.root = root
        self.username = username
        self.is_admin = is_admin
        self.assigned_ag = assigned_ag

        # Tytuł okna
        if isinstance(self.assigned_ag, list):
            ag_title_str = ", ".join(map(str, self.assigned_ag))
        else:
            ag_title_str = str(self.assigned_ag) if self.assigned_ag else "Brak"

        role_info = "ADMIN" if self.is_admin else f"Stanowisko: {ag_title_str}"
        self.root.title(f"Monitor połączeń JET - {self.username} [{role_info}]")

        try:
            img = tk.PhotoImage(file="app.png")
            self.root.iconphoto(False, img)
        except Exception:
            try:
                self.root.iconbitmap("app.ico")
            except Exception:
                pass

        self.root.geometry("500x450")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Stan
        self.api_connected = True
        self.active_alerts = []
        self.seen_alert_ids = set()
        self.last_data_snapshot = None
        self.is_sound_playing = False

        # --- NOWE: Zmienna do śledzenia czasu dla logiki opóźnienia dźwięku ---
        self.moh_start_time = None

        # --- GUI ---
        self.bottom_bar = tk.Frame(root, bd=1, relief=tk.SUNKEN)
        self.bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_label = tk.Label(self.bottom_bar, text="Uruchamianie...", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.help_btn = tk.Button(self.bottom_bar, text="?", font=("Arial", 8, "bold"),
                                  bg="#e0e0e0", width=3, bd=1,
                                  command=self.show_help_window)
        self.help_btn.pack(side=tk.RIGHT, padx=2, pady=1)

        default_sound_state = False if self.is_admin else True
        self.sound_enabled = tk.BooleanVar(value=default_sound_state)

        # --- Sekcja przycisków Admina ---
        if self.is_admin:
            self.admin_btn_frame = tk.Frame(self.bottom_bar)
            self.admin_btn_frame.pack(side=tk.LEFT, padx=2, pady=1)

            self.history_btn = tk.Button(self.admin_btn_frame, text="Historia", font=("Arial", 8, "bold"),
                                         bg="#e0e0e0", width=8, bd=1,
                                         command=self.show_history_window)
            self.history_btn.pack(side=tk.LEFT, padx=1)

            # ZMIANA: Przycisk nazywa się teraz "Raport" i otwiera menu wyboru
            self.report_btn = tk.Button(self.admin_btn_frame, text="Raport", font=("Arial", 8, "bold"),
                                        bg="#d1ecf1", width=8, bd=1,
                                        command=self.open_report_selection_window)
            self.report_btn.pack(side=tk.LEFT, padx=1)

        self.idle_frame = tk.Frame(root, bg="#f0f0f0")

        # --- Lista w pionie dla ekranu oczekiwania ---
        wait_msg = "System czuwa."
        if not self.is_admin and self.assigned_ag:
            if isinstance(self.assigned_ag, list):
                ag_vertical = "\n".join(map(str, self.assigned_ag))
            else:
                ag_vertical = str(self.assigned_ag)

            wait_msg += f"\n\nOczekiwanie na połączenia dla:\n{ag_vertical}"
        elif self.is_admin:
            wait_msg += "\nTryb Administratora (Wszystkie połączenia)"

        tk.Label(self.idle_frame, text=wait_msg,
                 font=("Arial", 14), fg="#888888", bg="#f0f0f0").pack(expand=True)

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

        self.thread = threading.Thread(target=self.network_loop, daemon=True)
        self.thread.start()

        self.root.after(500, self.update_gui)

    def on_closing(self):
        if messagebox.askyesno("Zamykanie", "Czy na pewno chcesz zamknąć monitor połączeń?"):
            self.root.destroy()

    # --- NOWA METODA: OKNO WYBORU RAPORTU ---
    def open_report_selection_window(self):
        sel_win = tk.Toplevel(self.root)
        sel_win.title("Wybór Raportu")
        sel_win.geometry("300x150")
        sel_win.resizable(False, False)

        # Centrowanie
        sel_win.update_idletasks()
        w = sel_win.winfo_width()
        h = sel_win.winfo_height()
        x = (sel_win.winfo_screenwidth() // 2) - (w // 2)
        y = (sel_win.winfo_screenheight() // 2) - (h // 2)
        sel_win.geometry(f'{w}x{h}+{x}+{y}')

        tk.Label(sel_win, text="Wybierz typ raportu:", font=("Arial", 12)).pack(pady=10)

        btn_daily = tk.Button(sel_win, text="Raport dzienny", bg="#007bff", fg="white", width=20,
                              command=lambda: [sel_win.destroy(), self.show_report_window()])
        btn_daily.pack(pady=5)

        btn_stats = tk.Button(sel_win, text="Raport z wykresami (Porównanie)", bg="#17a2b8", fg="white", width=20,
                              command=lambda: [sel_win.destroy(), self.show_stats_comparison_window()])
        btn_stats.pack(pady=5)

    # --- ZMODYFIKOWANA METODA: OKNO PORÓWNAWCZE Z ZAPISEM CSV ---
    def show_stats_comparison_window(self):
        comp_win = tk.Toplevel(self.root)
        comp_win.title("Analiza Trendów - Porównanie")
        comp_win.geometry("1100x700")

        # Zmienne do przechowywania danych dla eksportu CSV
        current_matrix = None
        current_dates = []
        current_keys = []

        # Pasek kontrolny
        ctrl_frame = tk.Frame(comp_win, pady=10, padx=10, bg="#f8f9fa")
        ctrl_frame.pack(fill=tk.X)

        today = datetime.now()
        start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")

        tk.Label(ctrl_frame, text="Od:", bg="#f8f9fa").pack(side=tk.LEFT, padx=5)
        ent_from = tk.Entry(ctrl_frame, width=12)
        ent_from.insert(0, start_date)
        ent_from.pack(side=tk.LEFT, padx=5)

        tk.Label(ctrl_frame, text="Do:", bg="#f8f9fa").pack(side=tk.LEFT, padx=5)
        ent_to = tk.Entry(ctrl_frame, width=12)
        ent_to.insert(0, end_date)
        ent_to.pack(side=tk.LEFT, padx=5)

        # Kontener na przewijaną tabelę (Canvas + Scrollbary)
        table_container = tk.Frame(comp_win, bg="white", bd=1, relief=tk.SUNKEN)
        table_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        canvas = tk.Canvas(table_container, bg="white")
        scroll_y = tk.Scrollbar(table_container, orient="vertical", command=canvas.yview)
        scroll_x = tk.Scrollbar(table_container, orient="horizontal", command=canvas.xview)

        # Ramka wewnątrz canvasa
        scrollable_frame = tk.Frame(canvas, bg="white")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def _on_mousewheel_table(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel_table)

        def _on_close_comp():
            canvas.unbind_all("<MouseWheel>")
            comp_win.destroy()

        comp_win.protocol("WM_DELETE_WINDOW", _on_close_comp)

        status_lbl = tk.Label(comp_win, text="Gotowy", anchor=tk.W, relief=tk.SUNKEN, bd=1)
        status_lbl.pack(side=tk.BOTTOM, fill=tk.X)

        # --- FUNKCJA POBIERANIA DANYCH ---
        def fetch_comparison():
            nonlocal current_matrix, current_dates, current_keys

            d_from = ent_from.get().strip()
            d_to = ent_to.get().strip()

            if not d_from or not d_to:
                messagebox.showwarning("Błąd", "Podaj zakres dat")
                return

            status_lbl.config(text="Pobieranie danych historycznych...", fg="blue")
            comp_win.update()

            for widget in scrollable_frame.winfo_children():
                widget.destroy()

            try:
                params = {
                    "date_from": d_from,
                    "date_to": d_to,
                    "token": Token
                }
                response = requests.get(f"{API_URL}/stats", params=params, timeout=10)

                if response.status_code != 200:
                    status_lbl.config(text=f"Błąd API: {response.status_code}", fg="red")
                    return

                data_json = response.json()
                records = data_json.get("data", [])

                if not records:
                    status_lbl.config(text="Brak danych w wybranym okresie.", fg="orange")
                    current_matrix = None  # Reset danych przy braku wyników
                    return

                # Przetwarzanie danych
                unique_dates = set()
                all_keys = set()
                matrix = defaultdict(dict)

                for rec in records:
                    r_date = rec.get("date")
                    if not r_date: continue
                    unique_dates.add(r_date)

                    for k, v in rec.items():
                        if k in ["_id", "id", "date", "received_at", "_created_at"]:
                            continue
                        all_keys.add(k)
                        matrix[k][r_date] = v

                sorted_dates = sorted(list(unique_dates))
                sorted_keys = sorted(list(all_keys))

                # Zapisujemy do zmiennych dostępnych dla save_csv
                current_matrix = matrix
                current_dates = sorted_dates
                current_keys = sorted_keys

                # --- RYSOWANIE TABELI ---
                # Nagłówek
                tk.Label(scrollable_frame, text="Firma / Źródło", font=("Arial", 9, "bold"),
                         bg="#e9ecef", borderwidth=1, relief="solid", width=35, anchor="w", padx=5, pady=5).grid(
                    row=0, column=0, sticky="nsew")

                for i, d in enumerate(sorted_dates):
                    tk.Label(scrollable_frame, text=d, font=("Arial", 9, "bold"),
                             bg="#e9ecef", borderwidth=1, relief="solid", width=12, padx=5, pady=5).grid(row=0,
                                                                                                         column=i + 1,
                                                                                                         sticky="nsew")

                # Wiersze z danymi
                for r_idx, key in enumerate(sorted_keys):
                    row_num = r_idx + 1

                    tk.Label(scrollable_frame, text=key, font=("Arial", 9),
                             bg="white", borderwidth=1, relief="solid", anchor="w", padx=5).grid(row=row_num,
                                                                                                 column=0,
                                                                                                 sticky="nsew")

                    prev_val = None

                    for c_idx, d in enumerate(sorted_dates):
                        val_raw = matrix[key].get(d, 0)
                        try:
                            val = int(val_raw)
                        except:
                            val = 0

                        fg_color = "black"
                        val_text = str(val)

                        if prev_val is not None:
                            if val > prev_val:
                                fg_color = "#28a745"
                                val_text += " ▲"
                            elif val < prev_val:
                                fg_color = "#dc3545"
                                val_text += " ▼"

                        lbl = tk.Label(scrollable_frame, text=val_text, font=("Arial", 9), fg=fg_color,
                                       bg="white", borderwidth=1, relief="solid")
                        lbl.grid(row=row_num, column=c_idx + 1, sticky="nsew")

                        prev_val = val

                status_lbl.config(text=f"Załadowano dane: {len(sorted_keys)} wierszy, {len(sorted_dates)} dni.",
                                  fg="green")

            except Exception as e:
                status_lbl.config(text=f"Błąd przetwarzania: {e}", fg="red")
                print(e)

        # --- FUNKCJA ZAPISU CSV ---
        def save_comparison_csv():
            if not current_matrix or not current_dates:
                messagebox.showwarning("Brak danych", "Najpierw pobierz dane, aby je zapisać.")
                return

            d_from = ent_from.get().strip()
            d_to = ent_to.get().strip()
            filename = f"Analiza_{d_from}_do_{d_to}"

            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                initialfile=filename,
                filetypes=[("Plik CSV", "*.csv")]
            )
            if not path: return

            try:
                with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f, delimiter=';')

                    # Nagłówek: Źródło, Data1, Data2...
                    header = ["Firma / Źródło"] + current_dates
                    writer.writerow(header)

                    # Dane
                    for key in current_keys:
                        row = [key]
                        for d in current_dates:
                            # Pobieramy czystą wartość (bez strzałek)
                            val = current_matrix[key].get(d, 0)
                            row.append(val)
                        writer.writerow(row)

                status_lbl.config(text=f"Zapisano plik: {path}", fg="green")
                messagebox.showinfo("Sukces", "Plik CSV został zapisany pomyślnie.")
            except Exception as e:
                messagebox.showerror("Błąd zapisu", f"Nie udało się zapisać pliku:\n{e}")

        # Przyciski
        btn_fetch = tk.Button(ctrl_frame, text="Pobierz i Porównaj", bg="#28a745", fg="white",
                              command=fetch_comparison)
        btn_fetch.pack(side=tk.LEFT, padx=15)

        # Nowy przycisk zapisu
        btn_csv = tk.Button(ctrl_frame, text="Zapisz do CSV", bg="#17a2b8", fg="white",
                            command=save_comparison_csv)
        btn_csv.pack(side=tk.LEFT, padx=5)

    def show_report_window(self):
        rep_win = tk.Toplevel(self.root)
        rep_win.title("Wykaz dzienny - Grupowanie wg Branż + Anulowane")
        rep_win.geometry("900x600")

        # --- ZMIENNE STANU ---
        current_report_data = None
        var_expand_groups = tk.BooleanVar(value=False)

        # Zmienne do sortowania
        self.sort_col = "#0"
        self.sort_reverse = False

        # Pasek górny
        top_frame = tk.Frame(rep_win, pady=10, padx=10, bg="#f8f9fa")
        top_frame.pack(fill=tk.X)

        tk.Label(top_frame, text="Wybierz dzień (YYYY-MM-DD):", bg="#f8f9fa").pack(side=tk.LEFT, padx=5)

        today_str = datetime.now().strftime("%Y-%m-%d")
        entry_date = tk.Entry(top_frame, width=12)
        entry_date.insert(0, today_str)
        entry_date.pack(side=tk.LEFT, padx=5)

        # Kontener na drzewo
        tree_frame = tk.Frame(rep_win)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Definicja wszystkich kolumn danych
        columns = ("typ", "total", "cancelled")
        tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings")

        # Konfiguracja kolumn
        tree.heading("#0", text="Firma / Źródło", anchor=tk.W, command=lambda: sort_tree("#0"))
        tree.column("#0", width=300, anchor=tk.W)

        tree.heading("typ", text="Pełna nazwa / Numer", anchor=tk.W, command=lambda: sort_tree("typ"))
        tree.column("typ", width=250, anchor=tk.W)

        tree.heading("total", text="Wszystkie", anchor=tk.CENTER, command=lambda: sort_tree("total"))
        tree.column("total", width=80, anchor=tk.CENTER)

        tree.heading("cancelled", text="Anulowane (Klient <15s)", anchor=tk.CENTER,
                     command=lambda: sort_tree("cancelled"))
        tree.column("cancelled", width=150, anchor=tk.CENTER)

        # Domyślnie ukrywamy kolumnę cancelled (bo checkbox jest False na start)
        tree["displaycolumns"] = ("typ", "total")

        # Style wierszy
        tree.tag_configure("company_row", font=("Arial", 11, "bold"), background="#e1e1e1")
        tree.tag_configure("group_row", font=("Arial", 10, "bold"), background="#f4f4f4")
        tree.tag_configure("detail_row", font=("Arial", 9), background="white")

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        status_lbl = tk.Label(rep_win, text="Gotowy", anchor=tk.W, relief=tk.SUNKEN, bd=1)
        status_lbl.pack(side=tk.BOTTOM, fill=tk.X)

        # --- FUNKCJA SORTUJĄCA ---
        def sort_tree(col):
            if self.sort_col == col:
                self.sort_reverse = not self.sort_reverse
            else:
                self.sort_col = col
                self.sort_reverse = False

            # Reset nagłówków
            for c in ["#0", "typ", "total", "cancelled"]:
                clean_text = tree.heading(c, "text").replace(" ▲", "").replace(" ▼", "")
                tree.heading(c, text=clean_text)

            arrow = " ▼" if self.sort_reverse else " ▲"
            current_text = tree.heading(col, "text")
            tree.heading(col, text=current_text + arrow)
            render_tree()

        # --- FUNKCJA RENDERUJĄCA ---
        def render_tree():
            # 1. Zarządzanie widocznością kolumn
            show_details = var_expand_groups.get()
            if show_details:
                tree["displaycolumns"] = ("typ", "total", "cancelled")
            else:
                tree["displaycolumns"] = ("typ", "total")

            # 2. Czyszczenie drzewa
            for item in tree.get_children():
                tree.delete(item)

            if not current_report_data:
                return

            # Helper do sortowania
            def get_sort_key(item_tuple):
                key, val = item_tuple
                if isinstance(val, dict) and 'total' in val:  # Detal
                    t, c = val['total'], val['cancelled']
                else:  # Grupa/Firma - sumowanie rekurencyjne
                    t, c = 0, 0
                    if isinstance(val, dict):
                        for sub_k, sub_v in val.items():
                            if isinstance(sub_v, dict) and 'total' in sub_v:
                                t += sub_v['total']
                                c += sub_v['cancelled']
                            elif isinstance(sub_v, dict):
                                for d_v in sub_v.values():
                                    t += d_v['total']
                                    c += d_v['cancelled']

                if self.sort_col == "total": return t
                if self.sort_col == "cancelled": return c
                if self.sort_col == "typ": return key
                return key.lower()

            # Budowanie drzewa
            companies_items = list(current_report_data.items())
            companies_items.sort(key=get_sort_key, reverse=self.sort_reverse)

            for company, groups_dict in companies_items:
                comp_total = 0
                comp_cancelled = 0
                for g_details in groups_dict.values():
                    for d_stats in g_details.values():
                        comp_total += d_stats['total']
                        comp_cancelled += d_stats['cancelled']

                company_id = tree.insert(
                    "", tk.END, text=f"{company}",
                    values=("Podsumowanie Firmy", comp_total, comp_cancelled),
                    tags=("company_row",), open=True
                )

                groups_items = list(groups_dict.items())
                groups_items.sort(key=get_sort_key, reverse=self.sort_reverse)

                for group_name, details_dict in groups_items:
                    group_total = 0
                    group_cancelled = 0
                    for d_stats in details_dict.values():
                        group_total += d_stats['total']
                        group_cancelled += d_stats['cancelled']

                    group_id = tree.insert(
                        company_id, tk.END, text=f"  ↳ {group_name}",
                        values=("Zsumowane źródło", group_total, group_cancelled),
                        tags=("group_row",), open=show_details
                    )

                    # Detale
                    details_items = list(details_dict.items())
                    det_sort_col = self.sort_col if self.sort_col != "#0" else "typ"

                    def detail_sort_key(itm):
                        k, v = itm
                        if det_sort_col == "total": return v['total']
                        if det_sort_col == "cancelled": return v['cancelled']
                        return k.lower()

                    details_items.sort(key=detail_sort_key, reverse=self.sort_reverse)

                    for raw_name, stats in details_items:
                        tree.insert(
                            group_id, tk.END, text="",
                            values=(raw_name, stats['total'], stats['cancelled']),
                            tags=("detail_row",)
                        )

        # --- EXPORT DO CSV ---
        def save_to_csv():
            if not current_report_data:
                messagebox.showwarning("Brak danych", "Najpierw pobierz dane.")
                return

            date_str = entry_date.get().strip() or "raport"
            export_details = var_expand_groups.get()
            suffix = "szczegoly" if export_details else "ogolny"

            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                initialfile=f"Raport_{date_str}_{suffix}",
                filetypes=[("Plik CSV", "*.csv")]
            )
            if not path: return

            try:
                with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f, delimiter=';')

                    headers = ["Firma", "Grupa/Branża", "Opis / Numer", "Ilość Całkowita"]
                    if export_details:
                        headers.append("Anulowane (Klient <15s)")

                    writer.writerow(headers)

                    for company in sorted(current_report_data.keys()):
                        groups = current_report_data[company]
                        for group_name in sorted(groups.keys()):
                            details = groups[group_name]

                            g_total = 0
                            g_cancelled = 0
                            for d in details.values():
                                g_total += d['total']
                                g_cancelled += d['cancelled']

                            row_data = [company, group_name, "(SUMA GRUPY)", g_total]
                            if export_details:
                                row_data.append(g_cancelled)

                            writer.writerow(row_data)

                            if export_details:
                                for raw_name, stats in details.items():
                                    writer.writerow(
                                        [company, group_name, raw_name, stats['total'], stats['cancelled']])

                status_lbl.config(text=f"Zapisano: {path}", fg="green")
            except Exception as e:
                messagebox.showerror("Błąd", f"Nie udało się zapisać: {e}")

        # --- FUNKCJA ZAPISU DO BAZY Z WALIDACJĄ DATY ---
        def save_to_db():
            if not current_report_data:
                messagebox.showwarning("Brak danych", "Najpierw pobierz dane.")
                return

            date_val = entry_date.get().strip()
            if not date_val:
                messagebox.showwarning("Błąd", "Brak daty.")
                return

            # 1. SPRAWDZENIE CZY DATA ISTNIEJE W BAZIE
            try:
                status_lbl.config(text="Sprawdzanie duplikatów...", fg="blue")
                rep_win.update()

                check_params = {
                    "date_from": date_val,
                    "date_to": date_val,
                    "token": Token
                }

                check_response = requests.get(f"{API_URL}/stats", params=check_params, timeout=5)

                if check_response.status_code == 200:
                    existing_data = check_response.json()
                    count = existing_data.get("count", 0)

                    if count > 0:
                        msg = (f"W bazie danych znaleziono już wpisy ({count}) dla daty {date_val}.\n\n"
                               "Czy chcesz dodać kolejny raport dla tej daty?")
                        if not messagebox.askyesno("Duplikat daty", msg):
                            status_lbl.config(text="Anulowano przez użytkownika.", fg="orange")
                            return
                else:
                    print(f"Błąd sprawdzania duplikatów: {check_response.status_code}")

            except Exception as e:
                print(f"Błąd połączenia przy sprawdzaniu: {e}")

            # 2. BUDOWANIE PAYLOADU
            payload = {
                "date": date_val
            }

            for company, groups in current_report_data.items():
                for group_name, details in groups.items():
                    group_total = 0
                    for stats in details.values():
                        group_total += stats['total']

                    key = f"{company.capitalize()} - {group_name}"
                    payload[key] = group_total

            # 3. WYSŁANIE DANYCH
            try:
                status_lbl.config(text="Wysyłanie do bazy...", fg="blue")
                rep_win.update()

                response = requests.post(f"{API_URL}/stats", json=payload, timeout=5)

                if response.status_code == 200 or response.status_code == 201:
                    status_lbl.config(text="Zapisano w bazie!", fg="green")
                    messagebox.showinfo("Sukces", "Raport został zapisany w bazie danych.")
                else:
                    status_lbl.config(text=f"Błąd API: {response.status_code}", fg="red")
                    messagebox.showerror("Błąd API",
                                         f"Serwer zwrócił błąd: {response.status_code}\n{response.text}")

            except Exception as e:
                status_lbl.config(text="Błąd połączenia", fg="red")
                messagebox.showerror("Błąd", f"Nie udało się wysłać danych: {e}")

        # --- POPRAWIONA FUNKCJA FETCH REPORT ---
        def fetch_report():
            nonlocal current_report_data
            date_val = entry_date.get().strip()
            if not date_val: return

            status_lbl.config(text="Pobieranie danych i konfiguracji...", fg="blue")
            rep_win.update()

            NAME_CORRECTIONS = {
                "polskikominiarz": "polski kominiarz",
                "liderizolacji": "lider izolacji",
                "betoniarnia-beton": "betoniarnia beton",
                "betoniarnia": "betoniarnia.pl",
                "liderbeton": "lider beton",
                "lider beton strona": "lider beton"
            }

            # Funkcja pomocnicza do czyszczenia numerów
            def normalize_num(n):
                if not n: return ""
                # Usuń wszystko co nie jest cyfrą, usuń +48 z początku
                s = str(n).strip().replace(" ", "").replace("-", "").replace("+", "")
                if s.startswith("48") and len(s) > 9:
                    s = s[2:]
                return s

            try:
                # 1. POBRANIE KONFIGURACJI NUMERÓW (STATS-DATA)
                phone_map = {}  # { "123456789": {"company": "...", "group": "..."} }

                try:
                    stats_resp = requests.get(f"{API_URL}/stats-data", params={"token": Token}, timeout=5)
                    if stats_resp.status_code == 200:
                        json_resp = stats_resp.json()
                        stats_docs = json_resp.get("data", [])

                        # Iterujemy po liście dokumentów (zwykle jest tam jeden główny obiekt)
                        for doc in stats_docs:
                            # Iterujemy po kluczach dokumentu (Nazwy firm: "beton polska", "lider beton" itp.)
                            for key, val in doc.items():
                                if key in ["_id", "id"]: continue  # Pomiń pola systemowe

                                company_key = key.lower().strip()

                                # Sprawdzamy czy wartość to słownik grup (np. { "wizytówki": [...], "olx": [...] })
                                if isinstance(val, dict):
                                    for group_key, numbers_list in val.items():
                                        group_name = group_key.strip()

                                        # Obsługa listy numerów
                                        if isinstance(numbers_list, list):
                                            for raw_num in numbers_list:
                                                clean = normalize_num(raw_num)
                                                if clean:
                                                    phone_map[clean] = {"company": company_key,
                                                                        "group": group_name}

                                        # Obsługa pojedynczego numeru (zabezpieczenie)
                                        elif isinstance(numbers_list, str):
                                            clean = normalize_num(numbers_list)
                                            if clean:
                                                phone_map[clean] = {"company": company_key, "group": group_name}

                        print(f"DEBUG: Załadowano {len(phone_map)} numerów do mapowania.")
                    else:
                        print(f"Błąd stats-data: {stats_resp.status_code}")
                except Exception as e:
                    print(f"Wyjątek przy stats-data: {e}")

                # 2. POBRANIE POŁĄCZEŃ Z TELESTRADY
                url = f"{API_URL}/telestrada/connections"
                response = requests.get(url, params={"date": date_val, "token": Token}, timeout=10)

                if response.status_code == 200:
                    data = response.json()
                    connections = []
                    if isinstance(data, list):
                        connections = data
                    elif isinstance(data, dict):
                        connections = data.get("connections") or next(
                            (v for v in data.values() if isinstance(v, list)), [])

                    if not connections:
                        status_lbl.config(text="Brak danych połączeń.", fg="orange")
                        current_report_data = None
                        render_tree()
                        return

                    stats = defaultdict(
                        lambda: defaultdict(lambda: defaultdict(lambda: {"total": 0, "cancelled": 0})))
                    count_total = 0
                    mapped_count = 0

                    for conn in connections:
                        if not isinstance(conn, dict): continue

                        # Pobieramy dane
                        menu_full = conn.get("element_menu_name") or conn.get("menu_name") or ""
                        ivr_raw = conn.get("ivr_phone_number", "")

                        # Normalizacja numeru z połączenia
                        ivr_clean = normalize_num(ivr_raw)

                        company_name = "nieznane"
                        group_name = "inne"
                        raw_type_name = "brak"

                        matched_in_db = False

                        # KROK A: SPRAWDZENIE W MAPIE NUMERÓW (PRIORYTET)
                        if ivr_clean and ivr_clean in phone_map:
                            mapping = phone_map[ivr_clean]
                            company_name = mapping["company"]
                            group_name = mapping["group"]
                            raw_type_name = ivr_clean  # Jako nazwę szczegółową dajemy numer
                            matched_in_db = True
                            mapped_count += 1

                        # KROK B: PARSOWANIE MENU (JEŚLI NIE ZNALEZIONO W BAZIE)
                        if not matched_in_db:
                            if not menu_full: continue  # Pomiń, jeśli nie ma ani numeru w bazie, ani nazwy menu

                            menu_full = menu_full.strip()

                            if "lider beton" in menu_full.lower() and " - " not in menu_full:
                                menu_full = re.sub(r'(?i)^(lider\s?beton)(\s+)', r'\1 - ', menu_full)

                            if " - " in menu_full:
                                parts = menu_full.split(" - ", 1)
                                company_name = parts[0].strip().lower()
                                raw_type_name = parts[1].strip()
                            else:
                                check = menu_full.replace(" ", "")
                                if len(check) >= 9 and check[-9:].isdigit():
                                    company_name = menu_full[:-9].strip().lower()
                                else:
                                    company_name = menu_full.strip().lower()
                                raw_type_name = f"{menu_full.replace(company_name, '').strip()}" or "Inne"

                            if company_name in NAME_CORRECTIONS: company_name = NAME_CORRECTIONS[company_name]

                            group_name = re.sub(r'[\s]*\d[\d\s-]{5,}\d$', '', raw_type_name).strip().strip(
                                "- ").strip()
                            if group_name.lower() in ["wizytówki w kampanii",
                                                      "wizytówki kampania"]: group_name = "wizytówki kampania"
                            if not group_name: group_name = "Inne / Bezpośrednie"

                        # ZLICZANIE
                        count_total += 1

                        # Zapis do struktury
                        entry = stats[company_name][group_name][raw_type_name]
                        entry['total'] += 1

                        try:
                            raw_billsec = conn.get("billsec")
                            billsec = int(raw_billsec or 0)
                        except (ValueError, TypeError):
                            billsec = 0

                        disc_side = str(conn.get("disconnect_side") or "B").upper()

                        if billsec < 15 and disc_side == 'A':
                            entry['cancelled'] += 1
                        # --------------------------------------

                    current_report_data = stats
                    self.sort_col = "#0"
                    self.sort_reverse = False
                    render_tree()
                    status_lbl.config(text=f"Sukces: {count_total} poł. (zmapowano po IVR: {mapped_count})",
                                      fg="green")
                else:
                    status_lbl.config(text=f"Błąd API: {response.status_code}", fg="red")
            except Exception as e:
                status_lbl.config(text=f"Błąd: {str(e)}", fg="red")
                import traceback
                traceback.print_exc()

        # Przyciski
        btn_fetch = tk.Button(top_frame, text="Pobierz dane", bg="#007bff", fg="white", command=fetch_report)
        btn_fetch.pack(side=tk.LEFT, padx=10)

        btn_save = tk.Button(top_frame, text="Zapisz CSV", bg="#28a745", fg="white", command=save_to_csv)
        btn_save.pack(side=tk.LEFT, padx=10)

        btn_db = tk.Button(top_frame, text="Zapisz do Bazy", bg="#17a2b8", fg="white", command=save_to_db)
        btn_db.pack(side=tk.LEFT, padx=10)

        cb_expand = tk.Checkbutton(top_frame, text="Rozwijaj szczegóły",
                                   variable=var_expand_groups, bg="#f8f9fa", command=render_tree)
        cb_expand.pack(side=tk.LEFT, padx=20)

    def show_history_window(self):
        hist_win = tk.Toplevel(self.root)
        hist_win.title("Historia Połączeń")
        hist_win.geometry("700x500")

        showing_all = False
        self.history_data_cache = []
        current_sort_col = "date"
        current_sort_reverse = False

        style = ttk.Style()
        style.configure("Treeview", font=("Arial", 10), rowheight=25)

        tree_frame = tk.Frame(hist_win)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("phone", "date", "timediff", "menu")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings")

        tree.tag_configure("normal", foreground="black")
        tree.tag_configure("green", foreground="#28a745", font=("Arial", 10, "bold"))
        tree.tag_configure("orange", foreground="orange", font=("Arial", 10, "bold"))
        tree.tag_configure("red", foreground="red", font=("Arial", 10, "bold"))

        def refresh_tree_view():
            for item in tree.get_children():
                tree.delete(item)
            sort_key = current_sort_col
            if sort_key == "timediff": sort_key = "date"
            try:
                self.history_data_cache.sort(key=lambda x: x[sort_key], reverse=current_sort_reverse)
            except:
                pass
            for row in self.history_data_cache:
                tree.insert("", tk.END, values=(row['phone'], row['date'], row['timediff'], row['menu']),
                            tags=row['tags'])

        def on_header_click(col):
            nonlocal current_sort_col, current_sort_reverse
            if current_sort_col == col:
                current_sort_reverse = not current_sort_reverse
            else:
                current_sort_col = col
                current_sort_reverse = True if col in ["date", "timediff"] else False
            for c in columns:
                text = tree.heading(c, "text").replace(" ▲", "").replace(" ▼", "")
                tree.heading(c, text=text)
            arrow = " ▼" if current_sort_reverse else " ▲"
            current_text = tree.heading(col, "text")
            tree.heading(col, text=current_text + arrow)
            refresh_tree_view()

        tree.heading("phone", text="Numer Telefonu", command=lambda: on_header_click("phone"))
        tree.heading("date", text="Ostatnie Połączenie", command=lambda: on_header_click("date"))
        tree.heading("timediff", text="Różnica", command=lambda: on_header_click("timediff"))
        tree.heading("menu", text="Źródło / Menu", command=lambda: on_header_click("menu"))

        tree.column("phone", width=120, anchor=tk.CENTER)
        tree.column("date", width=130, anchor=tk.CENTER)
        tree.column("timediff", width=120, anchor=tk.CENTER)
        tree.column("menu", width=250, anchor=tk.W)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def on_tree_click(event):
            region = tree.identify_region(event.x, event.y)
            if region != "cell": return
            col_id = tree.identify_column(event.x)
            if col_id == "#1":
                item_id = tree.identify_row(event.y)
                if item_id:
                    vals = tree.item(item_id, "values")
                    if vals: self.copy_number(vals[0])

        tree.bind("<Button-1>", on_tree_click)

        btn_frame = tk.Frame(hist_win)
        btn_frame.pack(fill=tk.X, pady=5, padx=10)

        def fetch_data():
            if not hist_win.winfo_exists(): return
            try:
                response = requests.get(f"{API_URL}/history", params={"token": Token}, timeout=5)
                if response.status_code == 200:
                    logs = response.json().get("logs", [])
                    now = datetime.now()
                    new_cache = []
                    count_displayed = 0
                    for log in logs:
                        raw_phone = log.get("phone_number", "")
                        fmt_phone = self.format_phone_number(raw_phone)
                        raw_date = log.get("last_call", "").replace("T", " ").split(".")[0]
                        try:
                            call_time = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
                            diff = now - call_time
                            diff_minutes = diff.total_seconds() / 60
                            diff_hours = diff_minutes / 60
                            if not showing_all and diff_hours > 48: continue
                            fmt_timediff = str(diff).split(".")[0]
                            row_tag = "normal"
                            if diff_minutes < 15:
                                row_tag = "green"
                            elif diff_minutes < 30:
                                row_tag = "orange"
                            elif diff_minutes < 60:
                                row_tag = "red"
                            new_cache.append({
                                'phone': fmt_phone, 'date': raw_date,
                                'timediff': fmt_timediff, 'menu': log.get("last_menu_full", "-"),
                                'tags': (row_tag,)
                            })
                            count_displayed += 1
                        except:
                            if showing_all:
                                new_cache.append({
                                    'phone': fmt_phone, 'date': raw_date,
                                    'timediff': "???", 'menu': log.get("last_menu_full", "-"), 'tags': ()
                                })
                    self.history_data_cache = new_cache
                    refresh_tree_view()
                    hist_win.title(f"Historia Połączeń (Wyświetlono: {count_displayed})")
            except Exception as e:
                print(f"Błąd pobierania historii: {e}")
            hist_win.after(60000, fetch_data)

        def toggle_view():
            nonlocal showing_all
            showing_all = not showing_all
            btn_toggle.config(text="Pokaż tylko < 48h" if showing_all else "Pokaż wszystko")
            fetch_data()

        btn_toggle = tk.Button(btn_frame, text="Pokaż wszystko", command=toggle_view, bg="#e1e1e1", width=20)
        btn_toggle.pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Zamknij", command=hist_win.destroy, bg="#ffdddd", width=15).pack(side=tk.RIGHT,
                                                                                                    padx=5)
        fetch_data()

    def show_help_window(self):
        help_win = tk.Toplevel(self.root)
        help_win.title("Pomoc / Legenda")
        help_win.geometry("350x400")
        help_win.resizable(False, False)
        help_win.attributes("-topmost", True)

        tk.Label(help_win, text="O Aplikacji", font=("Arial", 12, "bold")).pack(pady=(10, 5))
        desc = ("Aplikacja monitoruje system telefoniczny Telestrada.\n"
                "Kliknij numer telefonu, aby go skopiować.\n"
                "(Działa w oknie głównym i w Historii)\n"
                "Wersja 0.65 [11.02]"
                )

        if not self.is_admin:
            desc += f"\n\nWyświetla połączenia skierowane na: {self.assigned_ag or 'Brak'}"
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

        add_legend_row("#007bff", "Niebieski - Dzwoni")
        add_legend_row("#28a745", "Zielony - Odebrane")
        tk.Button(help_win, text="Zamknij", command=help_win.destroy, width=15).pack(side=tk.BOTTOM, pady=20)

    def on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        if self.canvas_frame.winfo_ismapped():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def filter_alerts(self, all_alerts):
        if self.is_admin: return all_alerts
        if not self.assigned_ag: return []
        allowed_sources = self.assigned_ag
        if not isinstance(allowed_sources, list):
            allowed_sources = [str(allowed_sources)]
        allowed_sources = [str(s) for s in allowed_sources]
        filtered = []
        for alert in all_alerts:
            if str(alert.get("source")) in allowed_sources:
                filtered.append(alert)
        return filtered

    def network_loop(self):
        while True:
            try:
                response = requests.get(f"{API_URL}/status", timeout=2)
                data = response.json()
                self.api_connected = True
                raw_alerts = data.get("alerts", [])
                self.active_alerts = self.filter_alerts(raw_alerts)
            except Exception:
                self.api_connected = False
            time.sleep(POLL_INTERVAL)

    def update_gui(self):
        if not self.api_connected:
            self.status_label.config(text="⚠ Brak połączenia z serwerem", fg="red")
        else:
            msg = f"Zalogowano: {self.username}"
            if self.is_admin: msg += " (ADMIN)"
            msg += f" | Widoczne rozmowy: {len(self.active_alerts)}"
            # Nie nadpisuj komunikatu o skopiowaniu
            if "Skopiowano" not in self.status_label.cget("text"):
                self.status_label.config(text=msg, fg="black")

        current_ids = {alert['id'] for alert in self.active_alerts}
        new_alerts = current_ids - self.seen_alert_ids

        if new_alerts:
            self.force_window_to_front()

        # --- LOGIKA DŹWIĘKU (ZMODYFIKOWANA) ---

        # 1. Sprawdzamy, czy jest jakiś alert, który spełnia warunki:
        #    - status to MOH
        #    - agent_name NIE jest puste
        #    (Jeśli agent_name jest puste, to znaczy że w kolejce -> brak dźwięku)

        moh_with_agent_condition = False

        for alert in self.active_alerts:
            status = alert.get("status")
            agent_name = alert.get("agent_name")

            # Sprawdzamy czy agent_name jest "truthy" (nie None i nie pusty string)
            if status == "MOH" and agent_name:
                moh_with_agent_condition = True
                break

        should_play_sound = False

        if moh_with_agent_condition:
            if self.moh_start_time is None:
                # Warunek dopiero wystąpił, startujemy licznik
                self.moh_start_time = time.time()
            else:
                # Warunek trwa, sprawdzamy ile czasu minęło
                elapsed = time.time() - self.moh_start_time
                if elapsed >= 3:
                    should_play_sound = True
        else:
            # Warunek zniknął (rozłączono lub status inny), reset licznika
            self.moh_start_time = None
            should_play_sound = False

        # Obsługa samego odtwarzania
        if self.sound_enabled.get() and winsound:
            if should_play_sound and not self.is_sound_playing:
                sound_file = "sound.wav"
                try:
                    if os.path.exists(sound_file):
                        winsound.PlaySound(sound_file, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
                    else:
                        winsound.PlaySound("SystemHand", winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_LOOP)
                    self.is_sound_playing = True
                except Exception as e:
                    print(f"Błąd startu dźwięku: {e}")

            elif not should_play_sound and self.is_sound_playing:
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    self.is_sound_playing = False
                except Exception as e:
                    print(f"Błąd zatrzymania dźwięku: {e}")

        # Jeśli użytkownik wyłączył dźwięk w checkboxie w trakcie dzwonienia, też ucisz
        if not self.sound_enabled.get() and self.is_sound_playing:
            winsound.PlaySound(None, winsound.SND_PURGE)
            self.is_sound_playing = False

        self.seen_alert_ids = current_ids

        # Aktualizacja listy w GUI (tylko jeśli dane się zmieniły)
        if self.active_alerts != self.last_data_snapshot:
            if len(self.active_alerts) > 0:
                self.idle_frame.pack_forget()
                self.canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
                self.refresh_alerts_list()
            else:
                self.canvas_frame.pack_forget()
                self.idle_frame.pack(fill=tk.BOTH, expand=True)

            self.last_data_snapshot = copy.deepcopy(self.active_alerts)

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

    def format_phone_number(self, phone):
        if not phone: return "Nieznany"
        p = str(phone).strip().replace(" ", "").replace("+", "")
        if len(p) == 11 and p.startswith("48"):
            return f"+{p[:2]} {p[2:5]} {p[5:8]} {p[8:]}"
        if len(p) == 9 and p.isdigit():
            return f"{p[:3]} {p[3:6]} {p[6:]}"
        return phone

    def copy_number(self, number):
        if not number: return
        self.root.clipboard_clear()
        self.root.clipboard_append(number)
        self.root.update()
        original_bg = self.status_label.cget("bg")
        self.status_label.config(text=f"✅ Skopiowano do schowka: {number}", bg="#d4edda")
        self.root.after(3000, lambda: self.status_label.config(bg=original_bg))

    def create_alert_widget(self, alert_data):
        frame = tk.Frame(self.scrollable_frame, bg="white", bd=2, relief=tk.GROOVE)
        frame.pack(fill=tk.X, pady=4)

        raw_caller = alert_data.get('caller', 'Nieznany')
        caller = self.format_phone_number(raw_caller)
        target = alert_data.get('agent_name') or alert_data.get('source', 'Infolinia')
        menu = alert_data.get('menu_name')
        status = alert_data.get('status') or "Dzwoni..."

        if not self.is_admin:
            target = re.sub(r'\d+', '', str(target)).strip()
            target = target.lstrip('- ').strip()
            if menu:
                menu = re.sub(r'\d+', '', str(menu)).strip()
                menu = menu.lstrip('- ').strip()

        status_color = "#007bff"
        if status in ["ANSWERED", "Odebrane"]:
            status_color = "#28a745"
        elif status in ["BUSY", "Zajęte", "Rozłączono"]:
            status_color = "#dc3545"

        tk.Frame(frame, bg=status_color, height=5).pack(fill=tk.X)
        content = tk.Frame(frame, bg="white", padx=10, pady=5)
        content.pack(fill=tk.BOTH, expand=True)
        header_frame = tk.Frame(content, bg="white")
        header_frame.pack(anchor="w", fill=tk.X)

        lbl_phone = tk.Label(header_frame, text=f"📞 {caller}", font=("Arial", 14, "bold"),
                             bg="white", fg="black", cursor="hand2")
        lbl_phone.pack(side=tk.LEFT)
        lbl_phone.bind("<Button-1>", lambda e: self.copy_number(caller))
        tk.Label(header_frame, text=f" ➔ {target}", font=("Arial", 14, "bold"),
                 bg="white", fg="#555").pack(side=tk.LEFT)
        if menu:
            tk.Label(content, text=f"📂 {menu}", font=("Arial", 12, "bold"), fg="#0056b3", bg="white").pack(anchor="w")


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()


    def start_main_app(username, is_admin, assigned_ag):
        root.deiconify()
        app = AlertClient(root, username, is_admin, assigned_ag)


    LoginWindow(root, start_main_app)
    root.mainloop()
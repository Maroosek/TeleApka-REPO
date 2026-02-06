import tkinter as tk
from tkinter import messagebox, filedialog
from tkinter import ttk
import requests
import threading
import time
import copy
import csv
import re
from datetime import datetime
from collections import defaultdict

# Konfiguracja
API_URL = "http://192.168.18.8:8020"
#API_URL = "http://64.225.111.62:8020"
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

        # Tytuł okna (tutaj zostawiamy po przecinku, żeby nie rozciągać paska tytułu)
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

        # --- GUI ---
        self.bottom_bar = tk.Frame(root, bd=1, relief=tk.SUNKEN)
        self.bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_label = tk.Label(self.bottom_bar, text="Uruchamianie...", anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.help_btn = tk.Button(self.bottom_bar, text="?", font=("Arial", 8, "bold"),
                                  bg="#e0e0e0", width=3, bd=1,
                                  command=self.show_help_window)
        self.help_btn.pack(side=tk.RIGHT, padx=2, pady=1)

        # --- Sekcja przycisków Admina ---
        if self.is_admin:
            self.admin_btn_frame = tk.Frame(self.bottom_bar)
            self.admin_btn_frame.pack(side=tk.LEFT, padx=2, pady=1)

            self.history_btn = tk.Button(self.admin_btn_frame, text="Historia", font=("Arial", 8, "bold"),
                                         bg="#e0e0e0", width=8, bd=1,
                                         command=self.show_history_window)
            self.history_btn.pack(side=tk.LEFT, padx=1)

            self.report_btn = tk.Button(self.admin_btn_frame, text="Wykaz", font=("Arial", 8, "bold"),
                                        bg="#d1ecf1", width=8, bd=1,
                                        command=self.show_report_window)
            self.report_btn.pack(side=tk.LEFT, padx=1)

        self.idle_frame = tk.Frame(root, bg="#f0f0f0")

        # --- ZMIANA: Lista w pionie dla ekranu oczekiwania ---
        wait_msg = "System czuwa."
        if not self.is_admin and self.assigned_ag:
            # Tworzymy string z nowymi liniami (\n) dla każdego numeru
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

    # --- NOWA FUNKCJA: OKNO WYKAZU (RAPORTU) ---
    def show_report_window(self):
        rep_win = tk.Toplevel(self.root)
        rep_win.title("Wykaz dzienny - Podsumowanie")
        rep_win.geometry("750x500")

        # --- ZMIENNE STANU ---
        current_report_data = None
        var_detailed = tk.BooleanVar(value=True)  # Domyślnie szczegółowy
        current_sort_col = "firma"  # Domyślne sortowanie po nazwie
        current_sort_reverse = False  # Domyślnie A-Z (rosnąco)

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

        columns = ("firma", "typ", "ilosc")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings")

        tree.column("firma", width=250)
        tree.column("typ", width=250)
        tree.column("ilosc", width=100, anchor=tk.CENTER)

        tree.tag_configure("total_row", font=("Arial", 10, "bold"), background="#e8e8e8")
        tree.tag_configure("normal_row", font=("Arial", 10))

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        status_lbl = tk.Label(rep_win, text="Gotowy", anchor=tk.W, relief=tk.SUNKEN, bd=1)
        status_lbl.pack(side=tk.BOTTOM, fill=tk.X)

        # --- FUNKCJA RENDERUJĄCA (z sortowaniem) ---
        def render_tree():
            # Czyścimy obecny widok
            for item in tree.get_children():
                tree.delete(item)

            if not current_report_data:
                return

            show_details = var_detailed.get()

            # Pobieramy listę firm (kluczy)
            companies = list(current_report_data.keys())

            # Logika sortowania listy firm
            if current_sort_col == "firma":
                companies.sort(reverse=current_sort_reverse)
            elif current_sort_col == "ilosc":
                # Sortujemy po wartości "Wszystkie" dla danej firmy
                companies.sort(key=lambda x: current_report_data[x]["Wszystkie"], reverse=current_sort_reverse)

            for company in companies:
                types = current_report_data[company]

                # 1. Wiersze szczegółowe (jeśli włączone)
                if show_details:
                    # Sortujemy podtypy, ale "Wszystkie" pomijamy tutaj
                    subtypes = sorted([t for t in types.keys() if t != "Wszystkie"])
                    for t_name in subtypes:
                        count = types[t_name]
                        tree.insert("", tk.END, values=(company, t_name, count), tags=("normal_row",))

                # 2. Podsumowanie (zawsze)
                total_count = types["Wszystkie"]
                tree.insert("", tk.END, values=(company, "Wszystkie", total_count), tags=("total_row",))

        # --- OBSŁUGA KLIKNIĘCIA W NAGŁÓWEK ---
        def on_header_click(col):
            nonlocal current_sort_col, current_sort_reverse

            # Jeśli kliknięto w to samo co ostatnio -> odwróć kolejność
            if current_sort_col == col:
                current_sort_reverse = not current_sort_reverse
            else:
                # Nowa kolumna -> domyślnie rosnąco (chyba że ilość, to logiczniej malejąco na start, ale trzymajmy standard)
                current_sort_col = col
                current_sort_reverse = True if col == "ilosc" else False

                # Aktualizacja strzałek w nagłówkach
            headers = {"firma": "Firma (Słowo kluczowe)", "typ": "Typ", "ilosc": "Ilość Połączeń"}

            for key, val in headers.items():
                tree.heading(key, text=val)  # Reset nazw

            arrow = " ▼" if current_sort_reverse else " ▲"
            current_text = headers.get(col, col)
            tree.heading(col, text=current_text + arrow)

            render_tree()

        # Konfiguracja nagłówków z komendami
        tree.heading("firma", text="Firma (Słowo kluczowe)", command=lambda: on_header_click("firma"))
        tree.heading("typ", text="Typ")  # Typu nie sortujemy globalnie, bo jest zależny od firmy
        tree.heading("ilosc", text="Ilość Połączeń", command=lambda: on_header_click("ilosc"))

        # Checkbox i reszta UI
        cb_detailed = tk.Checkbutton(top_frame, text="Raport szczegółowy",
                                     variable=var_detailed, bg="#f8f9fa",
                                     command=render_tree)
        cb_detailed.pack(side=tk.LEFT, padx=15)

        def save_to_csv():
            if not tree.get_children():
                messagebox.showwarning("Brak danych", "Najpierw pobierz dane.")
                return

            date_str = entry_date.get().strip() or "nieznana-data"
            mode_str = "szczegolowy" if var_detailed.get() else "ogolny"
            default_filename = f"Wykaz {mode_str} {date_str}"

            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                initialfile=default_filename,
                filetypes=[("Plik CSV", "*.csv"), ("Wszystkie pliki", "*.*")],
                title="Zapisz raport jako"
            )

            if not path:
                return

            try:
                with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f, delimiter=';')
                    writer.writerow(["Firma", "Typ", "Ilość"])
                    for child in tree.get_children():
                        values = tree.item(child)["values"]
                        writer.writerow(values)
                status_lbl.config(text=f"Zapisano: {path}", fg="green")
            except Exception as e:
                messagebox.showerror("Błąd", f"Nie udało się zapisać: {e}")

        def fetch_report():
            nonlocal current_report_data
            date_val = entry_date.get().strip()
            if not date_val:
                messagebox.showwarning("Błąd", "Wpisz datę.")
                return

            status_lbl.config(text="Pobieranie...", fg="blue")
            rep_win.update()

            NAME_CORRECTIONS = {
                "polskikominiarz": "polski kominiarz",
                "liderizolacji": "lider izolacji",
                "betoniarnia-beton": "betoniarnia beton",
                "betoniarnia": "betoniarnia.pl",
                "liderbeton": "lider beton",
                "lider beton strona": "lider beton"
            }

            try:
                url = f"{API_URL}/telestrada/connections"
                response = requests.get(f"{API_URL}/telestrada/connections", params={"date": date_val, "token": Token})

                if response.status_code == 200:
                    data = response.json()
                    connections = []

                    if isinstance(data, list):
                        connections = data
                    elif isinstance(data, dict):
                        if "connections" in data:
                            connections = data["connections"]
                        else:
                            for key, val in data.items():
                                if isinstance(val, list) and len(val) > 0:
                                    connections = val
                                    break
                            if not connections:
                                connections = list(data.values())

                    if not connections:
                        status_lbl.config(text="Brak danych.", fg="orange")
                        current_report_data = None
                        render_tree()
                        return

                    stats = defaultdict(lambda: defaultdict(int))
                    count_total = 0

                    for conn in connections:
                        if not isinstance(conn, dict): continue
                        menu_full = conn.get("element_menu_name") or conn.get("menu_name") or ""
                        if not menu_full: continue

                        count_total += 1
                        menu_full = menu_full.strip()

                        try:
                            duration = int(conn.get("billsec", 0))
                        except:
                            duration = 0

                        company_name = ""
                        type_name = ""

                        if " - " in menu_full:
                            parts = menu_full.split(" - ", 1)
                            company_name = parts[0].strip().lower()
                            type_name = parts[1].strip()
                        else:
                            check_digits = menu_full.replace(" ", "")
                            if len(check_digits) >= 9 and check_digits[-9:].isdigit():
                                company_name = menu_full[:-9].strip().lower()
                            else:
                                company_name = menu_full.strip().lower()
                            ivr_number = conn.get("ivr_phone_number", "brak_ivr")
                            type_name = f"Inne/Brak - {ivr_number}"

                        if company_name in NAME_CORRECTIONS:
                            company_name = NAME_CORRECTIONS[company_name]
                        if not company_name:
                            company_name = "nieznana firma"

                        if duration < 7:
                            type_name = f"{type_name} (Mniej niż 7s)"

                        stats[company_name][type_name] += 1
                        stats[company_name]["Wszystkie"] += 1

                    current_report_data = stats

                    # Domyślny reset sortowania przy nowym pobraniu (opcjonalne)
                    # current_sort_col = "firma"
                    # current_sort_reverse = False

                    render_tree()
                    status_lbl.config(text=f"Sukces: {count_total} połączeń.", fg="green")
                else:
                    status_lbl.config(text=f"Błąd API: {response.status_code}", fg="red")

            except Exception as e:
                status_lbl.config(text=f"Błąd: {str(e)}", fg="red")

        btn_fetch = tk.Button(top_frame, text="Pobierz dane", bg="#007bff", fg="white", command=fetch_report)
        btn_fetch.pack(side=tk.LEFT, padx=10)

        btn_save = tk.Button(top_frame, text="Zapisz dane (.csv)", bg="#28a745", fg="white", command=save_to_csv)
        btn_save.pack(side=tk.LEFT, padx=10)

    # --- KONIEC NOWEJ FUNKCJI ---

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
            if sort_key == "timediff":
                sort_key = "date"

            try:
                self.history_data_cache.sort(key=lambda x: x[sort_key], reverse=current_sort_reverse)
            except Exception:
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
            if region != "cell":
                return
            col_id = tree.identify_column(event.x)
            if col_id == "#1":
                item_id = tree.identify_row(event.y)
                if item_id:
                    vals = tree.item(item_id, "values")
                    if vals:
                        phone_num = vals[0]
                        self.copy_number(phone_num)

        tree.bind("<Button-1>", on_tree_click)

        btn_frame = tk.Frame(hist_win)
        btn_frame.pack(fill=tk.X, pady=5, padx=10)

        def fetch_data():
            if not hist_win.winfo_exists():
                return
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
                            if not showing_all and diff_hours > 48:
                                continue
                            fmt_timediff = str(diff).split(".")[0]
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
                                'menu': log.get("last_menu_full", "-"),
                                'tags': (row_tag,)
                            })
                            count_displayed += 1
                        except Exception:
                            if showing_all:
                                new_cache.append({
                                    'phone': fmt_phone,
                                    'date': raw_date,
                                    'timediff': "???",
                                    'menu': log.get("last_menu_full", "-"),
                                    'tags': ()
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
            if showing_all:
                btn_toggle.config(text="Pokaż tylko < 48h")
            else:
                btn_toggle.config(text="Pokaż wszystko")
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
                "(Opis do uzupełnienia, to nie pisze AI :v)\n\n"
                "Wersja 0.6 [06.02]"
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
        # Admin widzi wszystko
        if self.is_admin:
            return all_alerts

        # Jeśli użytkownik nie ma przypisanych żadnych AG, nie widzi nic
        if not self.assigned_ag:
            return []

        # Zabezpieczenie: upewniamy się, że assigned_ag to lista stringów
        # (API powinno to gwarantować, ale dla bezpieczeństwa w kliencie):
        allowed_sources = self.assigned_ag
        if not isinstance(allowed_sources, list):
            allowed_sources = [str(allowed_sources)]

        # Konwersja na stringi dla pewności porównania
        allowed_sources = [str(s) for s in allowed_sources]

        filtered = []
        for alert in all_alerts:
            # SPRAWDZENIE: Czy źródło (alert source) znajduje się na liście dozwolonych (allowed_sources)
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
            # Zachowujemy status skopiowania
            if "Skopiowano" not in self.status_label.cget("text"):
                self.status_label.config(text=msg, fg="black")

        current_ids = {alert['id'] for alert in self.active_alerts}
        new_alerts = current_ids - self.seen_alert_ids

        if new_alerts:
            self.force_window_to_front()

        self.seen_alert_ids = current_ids

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

    # --- KOPIOWANIE DO SCHOWKA ---
    def copy_number(self, number):
        if not number:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(number)
        self.root.update()

        # Potwierdzenie na pasku statusu
        original_bg = self.status_label.cget("bg")
        self.status_label.config(text=f"✅ Skopiowano do schowka: {number}", bg="#d4edda")

        self.root.after(3000, lambda: self.status_label.config(bg=original_bg))

    def create_alert_widget(self, alert_data):
        frame = tk.Frame(self.scrollable_frame, bg="white", bd=2, relief=tk.GROOVE)
        frame.pack(fill=tk.X, pady=4)

        raw_caller = alert_data.get('caller', 'Nieznany')
        caller = self.format_phone_number(raw_caller)

        # Pobieramy dane
        target = alert_data.get('agent_name') or alert_data.get('source', 'Infolinia')
        menu = alert_data.get('menu_name')
        status = alert_data.get('status') or "Dzwoni..."

        # --- ZMIANA: Usuwanie cyfr dla zwykłego użytkownika ---
        if not self.is_admin:
            # Usuwamy cyfry z nazwy Agenta/Targetu
            target = re.sub(r'\d+', '', str(target)).strip()
            # Usuwamy ewentualne myślniki na początku (np. z "805 - Jan" zostaje "- Jan")
            target = target.lstrip('- ').strip()

            if menu:
                # Usuwamy cyfry z Menu
                menu = re.sub(r'\d+', '', str(menu)).strip()
                # Kosmetyka menu (np. z "1 - Sprzedaż" zostaje "- Sprzedaż")
                menu = menu.lstrip('- ').strip()
        # -----------------------------------------------------

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

        # Klikalny numer w oknie głównym
        lbl_phone = tk.Label(header_frame, text=f"📞 {caller}", font=("Arial", 14, "bold"),
                             bg="white", fg="black", cursor="hand2")
        lbl_phone.pack(side=tk.LEFT)
        lbl_phone.bind("<Button-1>", lambda e: self.copy_number(caller))

        tk.Label(header_frame, text=f" ➔ {target}", font=("Arial", 14, "bold"),
                 bg="white", fg="#555").pack(side=tk.LEFT)

        if menu:
            tk.Label(content, text=f"📂 {menu}", font=("Arial", 12, "bold"), fg="#0056b3", bg="white").pack(
                anchor="w")


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()


    def start_main_app(username, is_admin, assigned_ag):
        root.deiconify()
        app = AlertClient(root, username, is_admin, assigned_ag)


    LoginWindow(root, start_main_app)
    root.mainloop()
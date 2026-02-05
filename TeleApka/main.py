import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
import requests
import threading
import time
import copy
from datetime import datetime

# Konfiguracja
# API_URL = "http://192.168.18.8:8020"
API_URL = "http://64.225.111.62:8020"
POLL_INTERVAL = 1
Token = "admin123"


class LoginWindow(tk.Toplevel):
    def __init__(self, parent, on_login_success):
        super().__init__(parent)
        self.on_login_success = on_login_success
        self.title("Logowanie - Monitor połączeń JET")
        self.geometry("300x200")
        self.resizable(False, False)

        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')

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

        role_info = "ADMIN" if self.is_admin else f"Stanowisko: {self.assigned_ag or 'Brak'}"
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

        if self.is_admin:
            self.history_btn = tk.Button(self.bottom_bar, text="Historia", font=("Arial", 8, "bold"),
                                         bg="#e0e0e0", width=8, bd=1,
                                         command=self.show_history_window)
            self.history_btn.pack(side=tk.LEFT, padx=2, pady=1)

        self.idle_frame = tk.Frame(root, bg="#f0f0f0")

        wait_msg = "System czuwa."
        if not self.is_admin and self.assigned_ag:
            wait_msg += f"\nOczekiwanie na połączenia dla: {self.assigned_ag}"
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

    def show_history_window(self):
        hist_win = tk.Toplevel(self.root)
        hist_win.title("Historia Połączeń")
        hist_win.geometry("700x500")

        # Stan lokalny okna historii
        showing_all = False
        self.history_data_cache = []  # Przechowujemy dane, żeby móc je sortować lokalnie
        current_sort_col = "date"  # Domyślne sortowanie
        current_sort_reverse = False  # Najnowsze na górze

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

        # --- FUNKCJA PRZELICZAJĄCA I WYŚWIETLAJĄCA DANE W TABELI ---
        def refresh_tree_view():
            # Wyczyść tabelę
            for item in tree.get_children():
                tree.delete(item)

            # Sortowanie
            # Jeśli sortujemy po "timediff", to de facto sortujemy po "date" (bo różnica wynika z daty)
            sort_key = current_sort_col
            if sort_key == "timediff":
                sort_key = "date"

            # Wykonaj sortowanie na liście słowników
            # Klucze w history_data_cache: 'phone', 'date', 'timediff', 'menu', 'tags'
            try:
                self.history_data_cache.sort(key=lambda x: x[sort_key], reverse=current_sort_reverse)
            except Exception:
                pass  # Ignoruj błędy sortowania

            # Wstaw do tabeli
            for row in self.history_data_cache:
                tree.insert("", tk.END, values=(row['phone'], row['date'], row['timediff'], row['menu']),
                            tags=row['tags'])

        # --- KLIKNIĘCIE W NAGŁÓWEK (SORTOWANIE) ---
        def on_header_click(col):
            nonlocal current_sort_col, current_sort_reverse

            # Jeśli kliknięto tę samą kolumnę, odwróć kolejność
            if current_sort_col == col:
                current_sort_reverse = not current_sort_reverse
            else:
                current_sort_col = col
                # Domyślny kierunek dla nowej kolumny
                # Dla daty/czasu chcemy najnowsze (reverse=True), dla tekstu alfabetycznie (reverse=False)
                current_sort_reverse = True if col in ["date", "timediff"] else False

            # Dodaj strzałkę do nagłówka (wizualny bajer - opcjonalne)
            for c in columns:
                text = tree.heading(c, "text").replace(" ▲", "").replace(" ▼", "")
                tree.heading(c, text=text)

            arrow = " ▼" if current_sort_reverse else " ▲"
            current_text = tree.heading(col, "text")
            tree.heading(col, text=current_text + arrow)

            refresh_tree_view()

        # Konfiguracja nagłówków
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

        # --- KLIKNIĘCIE W WIERSZ (KOPIOWANIE) ---
        def on_tree_click(event):
            # Sprawdź, w co kliknięto
            region = tree.identify_region(event.x, event.y)
            if region != "cell":
                return

            # Pobierz ID kolumny (np. #1 to pierwsza kolumna danych - phone)
            col_id = tree.identify_column(event.x)

            # Wg definicji columns=("phone", "date", ...), "phone" to kolumna #1
            if col_id == "#1":
                item_id = tree.identify_row(event.y)
                if item_id:
                    vals = tree.item(item_id, "values")
                    if vals:
                        phone_num = vals[0]  # Pierwsza wartość
                        self.copy_number(phone_num)

        tree.bind("<Button-1>", on_tree_click)

        btn_frame = tk.Frame(hist_win)
        btn_frame.pack(fill=tk.X, pady=5, padx=10)

        def fetch_data():
            if not hist_win.winfo_exists():
                return

            try:
                response = requests.get(f"{API_URL}/history", timeout=5)
                if response.status_code == 200:
                    logs = response.json().get("logs", [])
                    now = datetime.now()

                    new_cache = []
                    count_displayed = 0

                    for log in logs:  # API zwraca posortowane, ale my i tak sortujemy lokalnie
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

                            # Dodajemy do tymczasowej listy słowników
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

                    # Aktualizujemy główny cache i odświeżamy widok
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
                "(Działa w oknie głównym i w Historii)\n\n"
                "Wersja 0.6"
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
        if self.is_admin:
            return all_alerts

        if not self.assigned_ag:
            return []

        filtered = []
        for alert in all_alerts:
            if str(alert.get("source")) == str(self.assigned_ag):
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
        target = alert_data.get('agent_name') or alert_data.get('source', 'Infolinia')
        menu = alert_data.get('menu_name')
        status = alert_data.get('status') or "Dzwoni..."

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
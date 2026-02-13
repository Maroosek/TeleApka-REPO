# report_windows.py
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import csv
import re
from datetime import datetime, timedelta
from collections import defaultdict
import config
import utils


class StatsComparisonWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Analiza Trendów - Porównanie")
        self.geometry("1100x700")

        self.current_matrix = None
        self.current_dates = []
        self.current_keys = []

        self.setup_ui()

    def setup_ui(self):
        ctrl_frame = tk.Frame(self, pady=10, padx=10, bg="#f8f9fa")
        ctrl_frame.pack(fill=tk.X)

        today = datetime.now()
        start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")

        tk.Label(ctrl_frame, text="Od:", bg="#f8f9fa").pack(side=tk.LEFT, padx=5)
        self.ent_from = tk.Entry(ctrl_frame, width=12)
        self.ent_from.insert(0, start_date)
        self.ent_from.pack(side=tk.LEFT, padx=5)

        tk.Label(ctrl_frame, text="Do:", bg="#f8f9fa").pack(side=tk.LEFT, padx=5)
        self.ent_to = tk.Entry(ctrl_frame, width=12)
        self.ent_to.insert(0, end_date)
        self.ent_to.pack(side=tk.LEFT, padx=5)

        table_container = tk.Frame(self, bg="white", bd=1, relief=tk.SUNKEN)
        table_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        canvas = tk.Canvas(table_container, bg="white")
        scroll_y = tk.Scrollbar(table_container, orient="vertical", command=canvas.yview)
        scroll_x = tk.Scrollbar(table_container, orient="horizontal", command=canvas.xview)

        self.scrollable_frame = tk.Frame(canvas, bg="white")
        self.scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        self.protocol("WM_DELETE_WINDOW", lambda: [canvas.unbind_all("<MouseWheel>"), self.destroy()])

        self.status_lbl = tk.Label(self, text="Gotowy", anchor=tk.W, relief=tk.SUNKEN, bd=1)
        self.status_lbl.pack(side=tk.BOTTOM, fill=tk.X)

        tk.Button(ctrl_frame, text="Pobierz i Porównaj", bg="#28a745", fg="white", command=self.fetch_comparison).pack(
            side=tk.LEFT, padx=15)
        tk.Button(ctrl_frame, text="Zapisz do CSV", bg="#17a2b8", fg="white", command=self.save_comparison_csv).pack(
            side=tk.LEFT, padx=5)

    def fetch_comparison(self):
        d_from = self.ent_from.get().strip()
        d_to = self.ent_to.get().strip()
        if not d_from or not d_to:
            messagebox.showwarning("Błąd", "Podaj zakres dat")
            return

        self.status_lbl.config(text="Pobieranie danych historycznych...", fg="blue")
        self.update()

        for widget in self.scrollable_frame.winfo_children(): widget.destroy()

        try:
            params = {"date_from": d_from, "date_to": d_to, "token": config.TOKEN}
            response = requests.get(f"{config.API_URL}/stats", params=params, timeout=10)
            if response.status_code != 200:
                self.status_lbl.config(text=f"Błąd API: {response.status_code}", fg="red")
                return

            data_json = response.json()
            records = data_json.get("data", [])
            if not records:
                self.status_lbl.config(text="Brak danych w wybranym okresie.", fg="orange")
                self.current_matrix = None
                return

            unique_dates = set()
            all_keys = set()
            matrix = defaultdict(dict)

            for rec in records:
                r_date = rec.get("date")
                if not r_date: continue
                unique_dates.add(r_date)
                for k, v in rec.items():
                    if k in ["_id", "id", "date", "received_at", "_created_at"]: continue
                    all_keys.add(k)
                    matrix[k][r_date] = v

            sorted_dates = sorted(list(unique_dates))
            sorted_keys = sorted(list(all_keys))

            self.current_matrix = matrix
            self.current_dates = sorted_dates
            self.current_keys = sorted_keys

            self.render_table(sorted_dates, sorted_keys, matrix)
            self.status_lbl.config(text=f"Załadowano dane: {len(sorted_keys)} wierszy, {len(sorted_dates)} dni.",
                                   fg="green")

        except Exception as e:
            self.status_lbl.config(text=f"Błąd przetwarzania: {e}", fg="red")
            print(e)

    def render_table(self, sorted_dates, sorted_keys, matrix):
        tk.Label(self.scrollable_frame, text="Firma / Źródło", font=("Arial", 9, "bold"),
                 bg="#e9ecef", borderwidth=1, relief="solid", width=35, anchor="w", padx=5, pady=5).grid(row=0,
                                                                                                         column=0,
                                                                                                         sticky="nsew")

        for i, d in enumerate(sorted_dates):
            tk.Label(self.scrollable_frame, text=d, font=("Arial", 9, "bold"),
                     bg="#e9ecef", borderwidth=1, relief="solid", width=12, padx=5, pady=5).grid(row=0, column=i + 1,
                                                                                                 sticky="nsew")

        for r_idx, key in enumerate(sorted_keys):
            row_num = r_idx + 1
            tk.Label(self.scrollable_frame, text=key, font=("Arial", 9),
                     bg="white", borderwidth=1, relief="solid", anchor="w", padx=5).grid(row=row_num, column=0,
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
                        fg_color = "#28a745";
                        val_text += " ▲"
                    elif val < prev_val:
                        fg_color = "#dc3545";
                        val_text += " ▼"

                tk.Label(self.scrollable_frame, text=val_text, font=("Arial", 9), fg=fg_color,
                         bg="white", borderwidth=1, relief="solid").grid(row=row_num, column=c_idx + 1, sticky="nsew")
                prev_val = val

    def save_comparison_csv(self):
        if not self.current_matrix or not self.current_dates:
            messagebox.showwarning("Brak danych", "Najpierw pobierz dane.")
            return
        filename = f"Analiza_{self.ent_from.get()}_do_{self.ent_to.get()}"
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=filename,
                                            filetypes=[("Plik CSV", "*.csv")])
        if not path: return
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(["Firma / Źródło"] + self.current_dates)
                for key in self.current_keys:
                    row = [key] + [self.current_matrix[key].get(d, 0) for d in self.current_dates]
                    writer.writerow(row)
            messagebox.showinfo("Sukces", "Zapisano plik CSV.")
        except Exception as e:
            messagebox.showerror("Błąd", f"Nie udało się zapisać: {e}")


class DailyReportWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Wykaz dzienny")
        self.geometry("900x600")

        self.current_report_data = None
        self.var_expand_groups = tk.BooleanVar(value=False)
        self.sort_col = "#0"
        self.sort_reverse = False

        self.setup_ui()

    def setup_ui(self):
        top_frame = tk.Frame(self, pady=10, padx=10, bg="#f8f9fa")
        top_frame.pack(fill=tk.X)

        tk.Label(top_frame, text="Wybierz dzień (YYYY-MM-DD):", bg="#f8f9fa").pack(side=tk.LEFT, padx=5)
        self.entry_date = tk.Entry(top_frame, width=12)
        self.entry_date.insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.entry_date.pack(side=tk.LEFT, padx=5)

        tree_frame = tk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("typ", "total", "cancelled")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings")

        self.tree.heading("#0", text="Firma / Źródło", anchor=tk.W, command=lambda: self.sort_tree("#0"))
        self.tree.column("#0", width=300, anchor=tk.W)
        self.tree.heading("typ", text="Pełna nazwa / Numer", anchor=tk.W, command=lambda: self.sort_tree("typ"))
        self.tree.column("typ", width=250, anchor=tk.W)
        self.tree.heading("total", text="Wszystkie", anchor=tk.CENTER, command=lambda: self.sort_tree("total"))
        self.tree.column("total", width=80, anchor=tk.CENTER)
        self.tree.heading("cancelled", text="Anulowane (<15s)", anchor=tk.CENTER,
                          command=lambda: self.sort_tree("cancelled"))
        self.tree.column("cancelled", width=150, anchor=tk.CENTER)

        self.tree["displaycolumns"] = ("typ", "total")

        self.tree.tag_configure("company_row", font=("Arial", 11, "bold"), background="#e1e1e1")
        self.tree.tag_configure("group_row", font=("Arial", 10, "bold"), background="#f4f4f4")
        self.tree.tag_configure("detail_row", font=("Arial", 9), background="white")

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.status_lbl = tk.Label(self, text="Gotowy", anchor=tk.W, relief=tk.SUNKEN, bd=1)
        self.status_lbl.pack(side=tk.BOTTOM, fill=tk.X)

        tk.Button(top_frame, text="Pobierz dane", bg="#007bff", fg="white", command=self.fetch_report).pack(
            side=tk.LEFT, padx=10)
        tk.Button(top_frame, text="Zapisz CSV", bg="#28a745", fg="white", command=self.save_to_csv).pack(side=tk.LEFT,
                                                                                                         padx=10)
        tk.Button(top_frame, text="Zapisz do Bazy", bg="#17a2b8", fg="white", command=self.save_to_db).pack(
            side=tk.LEFT, padx=10)
        tk.Checkbutton(top_frame, text="Rozwijaj szczegóły", variable=self.var_expand_groups, bg="#f8f9fa",
                       command=self.render_tree).pack(side=tk.LEFT, padx=20)

    def fetch_report(self):
        date_val = self.entry_date.get().strip()
        if not date_val:
            messagebox.showwarning("Błąd", "Wprowadź datę.")
            return

        self.status_lbl.config(text="Pobieranie...", fg="blue")
        self.update()

        NAME_CORRECTIONS = {
            "polskikominiarz": "polski kominiarz", "liderizolacji": "lider izolacji",
            "betoniarnia-beton": "betoniarnia beton", "betoniarnia": "betoniarnia.pl",
            "liderbeton": "lider beton", "lider beton strona": "lider beton"
        }

        try:
            phone_map = {}
            try:
                print("DEBUG: Pobieranie stats-data...")
                stats_resp = requests.get(f"{config.API_URL}/stats-data", params={"token": config.TOKEN}, timeout=5)
                if stats_resp.status_code == 200:
                    json_data = stats_resp.json()
                    raw_data = json_data if isinstance(json_data, list) else json_data.get("data", [])
                    for doc in raw_data:
                        for key, val in doc.items():
                            if key in ["_id", "id"]: continue
                            if isinstance(val, dict):
                                for g_key, n_list in val.items():
                                    nums = n_list if isinstance(n_list, list) else [n_list]
                                    for n in nums:
                                        clean = utils.normalize_num(n)
                                        if clean: phone_map[clean] = {"company": key.lower().strip(),
                                                                      "group": g_key.strip()}
                print(f"DEBUG: Zmapowano {len(phone_map)} numerów.")
            except Exception as e:
                print(f"DEBUG ERROR stats-data: {e}")

            url = f"{config.API_URL}/telestrada/connections"
            print(f"DEBUG: Pobieranie połączeń z {url} dla daty {date_val}")

            resp = requests.get(url, params={"date": date_val, "token": config.TOKEN}, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                connections = []
                if isinstance(data, list):
                    connections = data
                elif isinstance(data, dict):
                    if "connections" in data:
                        connections = data["connections"]
                    elif "data" in data and isinstance(data["data"], list):
                        connections = data["data"]
                    else:
                        connections = next((v for v in data.values() if isinstance(v, list)), [])

                print(f"DEBUG: Znaleziono {len(connections)} połączeń.")

                if not connections:
                    self.status_lbl.config(text="Brak danych (lista pusta).", fg="orange")
                    self.current_report_data = None
                    self.render_tree()
                    return

                stats = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"total": 0, "cancelled": 0})))
                count_total = 0

                for conn in connections:
                    if not isinstance(conn, dict): continue

                    menu_full = conn.get("element_menu_name") or conn.get("menu_name") or ""
                    ivr_raw = conn.get("ivr_phone_number", "")
                    ivr_clean = utils.normalize_num(ivr_raw)

                    company, group, raw_type = "nieznane", "inne", "brak"

                    if ivr_clean and ivr_clean in phone_map:
                        mapping = phone_map[ivr_clean]
                        company = mapping["company"]
                        group = mapping["group"]
                        raw_type = ivr_clean
                    elif menu_full:
                        menu_full = menu_full.strip()
                        if "lider beton" in menu_full.lower() and " - " not in menu_full:
                            menu_full = re.sub(r'(?i)^(lider\s?beton)(\s+)', r'\1 - ', menu_full)
                        if " - " in menu_full:
                            parts = menu_full.split(" - ", 1)
                            company = parts[0].strip().lower()
                            raw_type = parts[1].strip()
                        else:
                            check = menu_full.replace(" ", "")
                            if len(check) >= 9 and check[-9:].isdigit():
                                company = menu_full[:-9].strip().lower()
                            else:
                                company = menu_full.strip().lower()
                            raw_type = menu_full.replace(company, "").strip() or "Inne"

                        if company in NAME_CORRECTIONS: company = NAME_CORRECTIONS[company]
                        group = re.sub(r'[\s]*\d[\d\s-]{5,}\d$', '', raw_type).strip().strip("- ").strip()
                        if not group: group = "Inne / Bezpośrednie"
                        if group.lower() in ["wizytówki w kampanii", "wizytówki kampania"]: group = "wizytówki kampania"

                    stats[company][group][raw_type]['total'] += 1
                    count_total += 1

                    try:
                        billsec = int(conn.get("billsec") or 0)
                    except:
                        billsec = 0
                    disc = str(conn.get("disconnect_side") or "B").upper()
                    if billsec < 15 and disc == 'A':
                        stats[company][group][raw_type]['cancelled'] += 1

                self.current_report_data = stats
                self.render_tree()
                self.status_lbl.config(text=f"Sukces: {count_total} poł.", fg="green")
            else:
                self.status_lbl.config(text=f"Błąd API: {resp.status_code}", fg="red")
        except Exception as e:
            self.status_lbl.config(text=f"Błąd krytyczny: {e}", fg="red")
            import traceback
            traceback.print_exc()

    def sort_tree(self, col):
        if self.sort_col == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col = col;
            self.sort_reverse = False

        for c in ["#0", "typ", "total", "cancelled"]:
            text = self.tree.heading(c, "text").replace(" ▲", "").replace(" ▼", "")
            self.tree.heading(c, text=text)
        arrow = " ▼" if self.sort_reverse else " ▲"
        self.tree.heading(col, text=self.tree.heading(col, "text") + arrow)
        self.render_tree()

    def render_tree(self):
        if self.var_expand_groups.get():
            self.tree["displaycolumns"] = ("typ", "total", "cancelled")
        else:
            self.tree["displaycolumns"] = ("typ", "total")

        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.current_report_data:
            return

        def get_sort_key(item_tuple):
            key, val = item_tuple
            total_sum = 0
            cancelled_sum = 0

            if isinstance(val, dict) and 'total' in val:
                total_sum = val['total']
                cancelled_sum = val['cancelled']
            elif isinstance(val, dict):
                for sub_key, sub_val in val.items():
                    if isinstance(sub_val, dict) and 'total' in sub_val:
                        total_sum += sub_val['total']
                        cancelled_sum += sub_val['cancelled']
                    elif isinstance(sub_val, dict):
                        for d_val in sub_val.values():
                            total_sum += d_val['total']
                            cancelled_sum += d_val['cancelled']

            if self.sort_col == "total":
                return total_sum
            elif self.sort_col == "cancelled":
                return cancelled_sum
            elif self.sort_col == "typ" or self.sort_col == "#0":
                return key.lower()
            return key.lower()

        companies_list = list(self.current_report_data.items())
        companies_list.sort(key=get_sort_key, reverse=self.sort_reverse)

        for company, groups in companies_list:
            comp_tot = sum(d['total'] for g in groups.values() for d in g.values())
            comp_canc = sum(d['cancelled'] for g in groups.values() for d in g.values())

            c_id = self.tree.insert("", tk.END, text=company,
                                    values=("Podsumowanie Firmy", comp_tot, comp_canc),
                                    tags=("company_row",), open=True)

            groups_list = list(groups.items())
            groups_list.sort(key=get_sort_key, reverse=self.sort_reverse)

            for grp_name, details in groups_list:
                g_tot = sum(d['total'] for d in details.values())
                g_canc = sum(d['cancelled'] for d in details.values())

                g_id = self.tree.insert(c_id, tk.END, text=f"  ↳ {grp_name}",
                                        values=("Grupa", g_tot, g_canc),
                                        tags=("group_row",), open=self.var_expand_groups.get())

                details_list = list(details.items())
                det_sort_col = self.sort_col if self.sort_col != "#0" else "typ"

                def detail_key(itm):
                    k, v = itm
                    if det_sort_col == "total": return v['total']
                    if det_sort_col == "cancelled": return v['cancelled']
                    return k.lower()

                details_list.sort(key=detail_key, reverse=self.sort_reverse)

                for raw_name, stats in details_list:
                    self.tree.insert(g_id, tk.END, text="",
                                     values=(raw_name, stats['total'], stats['cancelled']),
                                     tags=("detail_row",))

    def save_to_csv(self):
        if not self.current_report_data:
            messagebox.showwarning("Brak danych", "Najpierw pobierz dane.")
            return

        date_str = self.entry_date.get().strip() or "raport"
        export_details = self.var_expand_groups.get()
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
                if export_details: headers.append("Anulowane (Klient <15s)")
                writer.writerow(headers)

                for company in sorted(self.current_report_data.keys()):
                    groups = self.current_report_data[company]
                    for group_name in sorted(groups.keys()):
                        details = groups[group_name]

                        g_total = sum(d['total'] for d in details.values())
                        g_cancelled = sum(d['cancelled'] for d in details.values())

                        row_data = [company, group_name, "(SUMA GRUPY)", g_total]
                        if export_details: row_data.append(g_cancelled)
                        writer.writerow(row_data)

                        if export_details:
                            for raw_name, stats in details.items():
                                writer.writerow([company, group_name, raw_name, stats['total'], stats['cancelled']])

            self.status_lbl.config(text=f"Zapisano: {path}", fg="green")
            messagebox.showinfo("Sukces", "Plik CSV został zapisany.")
        except Exception as e:
            messagebox.showerror("Błąd", f"Nie udało się zapisać: {e}")

    def save_to_db(self):
        if not self.current_report_data:
            messagebox.showwarning("Brak danych", "Najpierw pobierz dane.")
            return

        date_val = self.entry_date.get().strip()
        if not date_val:
            messagebox.showwarning("Błąd", "Brak daty.")
            return

        try:
            self.status_lbl.config(text="Sprawdzanie duplikatów...", fg="blue")
            self.update()

            check_response = requests.get(f"{config.API_URL}/stats",
                                          params={"date_from": date_val, "date_to": date_val, "token": config.TOKEN},
                                          timeout=5)

            if check_response.status_code == 200:
                count = check_response.json().get("count", 0)
                if count > 0:
                    if not messagebox.askyesno("Duplikat",
                                               f"Istnieją już dane ({count}) dla daty {date_val}. Dodać mimo to?"):
                        self.status_lbl.config(text="Anulowano.", fg="orange")
                        return
        except Exception as e:
            print(f"Błąd sprawdzania duplikatów: {e}")

        payload = {"date": date_val}
        for company, groups in self.current_report_data.items():
            for group_name, details in groups.items():
                group_total = sum(d['total'] for d in details.values())
                key = f"{company.capitalize()} - {group_name}"
                payload[key] = group_total

        try:
            self.status_lbl.config(text="Wysyłanie do bazy...", fg="blue")
            self.update()
            response = requests.post(f"{config.API_URL}/stats", json=payload, timeout=5)

            if response.status_code in [200, 201]:
                self.status_lbl.config(text="Zapisano w bazie!", fg="green")
                messagebox.showinfo("Sukces", "Raport zapisany w bazie danych.")
            else:
                self.status_lbl.config(text=f"Błąd API: {response.status_code}", fg="red")
                messagebox.showerror("Błąd API", f"Kod: {response.status_code}\n{response.text}")
        except Exception as e:
            self.status_lbl.config(text="Błąd połączenia", fg="red")
            messagebox.showerror("Błąd", f"Nie udało się wysłać: {e}")


def show_report_selection(parent):
    sel_win = tk.Toplevel(parent)
    sel_win.title("Wybór Raportu")
    sel_win.geometry("300x250")

    tk.Label(sel_win, text="Wybierz typ raportu:", font=("Arial", 12)).pack(pady=10)
    tk.Button(sel_win, text="Raport dzienny", bg="#007bff", fg="white", width=20,
              command=lambda: [sel_win.destroy(), DailyReportWindow(parent)]).pack(pady=5)
    tk.Button(sel_win, text="Raport z wykresami", bg="#17a2b8", fg="white", width=20,
              command=lambda: [sel_win.destroy(), StatsComparisonWindow(parent)]).pack(pady=5)
    tk.Label(sel_win, text="Wybierz typ konfiguracji", font=("Arial", 12)).pack(pady=10)
    tk.Button(sel_win, text="Numery", bg="#007bff", fg="white", width=20,
              command=lambda: [sel_win.destroy(), NumbersConfigWindow(parent)]).pack(pady=5)
    tk.Button(sel_win, text="Okres kampanii", bg="#17a2b8", fg="white", width=20,
              command=lambda: [sel_win.destroy(), StatsComparisonWindow(parent)]).pack(pady=5)

    class NumbersConfigWindow(tk.Toplevel):
        def __init__(self, parent):
            super().__init__(parent)
            self.title("Konfiguracja Numerów - Import CSV")
            self.geometry("500x300")
            self.selected_path = tk.StringVar(value="Nie wybrano pliku")

            self.setup_ui()

        def setup_ui(self):
            main_frame = tk.Frame(self, padx=20, pady=20)
            main_frame.pack(fill=tk.BOTH, expand=True)

            tk.Label(main_frame, text="Import mapowania numerów z pliku CSV", font=("Arial", 12, "bold")).pack(pady=10)

            # Sekcja wyboru pliku
            file_frame = tk.LabelFrame(main_frame, text="Plik źródłowy", padx=10, pady=10)
            file_frame.pack(fill=tk.X, pady=10)

            tk.Label(file_frame, textvariable=self.selected_path, wraplength=400, fg="gray").pack(side=tk.LEFT,
                                                                                                  fill=tk.X,
                                                                                                  expand=True)
            tk.Button(file_frame, text="Wybierz plik", command=self.browse_file).pack(side=tk.RIGHT, padx=5)

            # Przycisk wysyłania
            self.btn_send = tk.Button(
                main_frame,
                text="Przetwórz i wyślij do bazy",
                bg="#28a745",
                fg="white",
                font=("Arial", 10, "bold"),
                height=2,
                command=self.process_and_upload
            )
            self.btn_send.pack(fill=tk.X, pady=20)

            self.status_lbl = tk.Label(self, text="Gotowy", anchor=tk.W, relief=tk.SUNKEN, bd=1)
            self.status_lbl.pack(side=tk.BOTTOM, fill=tk.X)

        def browse_file(self):
            path = filedialog.askopenfilename(filetypes=[("Plik CSV", "*.csv")])
            if path:
                self.selected_path.set(path)

        def process_and_upload(self):
            path = self.selected_path.get()
            if path == "Nie wybrano pliku":
                messagebox.showwarning("Błąd", "Najpierw wybierz plik CSV!")
                return

            self.status_lbl.config(text="Przetwarzanie pliku...", fg="blue")
            self.update()

            # Logika filtrowania (Twój skrypt)
            mapa_danych = defaultdict(lambda: defaultdict(list))
            try:
                with open(path, mode='r', encoding='utf-8-sig') as plik:
                    reader = csv.DictReader(plik, delimiter=',')  # Możesz dodać wykrywanie separatora

                    wymagane_kolumny = ['NUMERY GŁÓWNE', 'Element Menu']
                    if not all(col in reader.fieldnames for col in wymagane_kolumny):
                        messagebox.showerror("Błąd struktury", f"Plik musi zawierać kolumny: {wymagane_kolumny}")
                        return

                    for rzad in reader:
                        numer = rzad['NUMERY GŁÓWNE'].strip()
                        element_menu = rzad['Element Menu']

                        if '-' in element_menu:
                            czesci = element_menu.split('-', 1)
                            branza = czesci[0].strip().lower()
                            zrodlo = czesci[1].strip().lower()
                            mapa_danych[branza][zrodlo].append(numer)

                if not mapa_danych:
                    messagebox.showwarning("Pusto",
                                           "Nie wyciągnięto żadnych danych z pliku (sprawdź format 'Element Menu').")
                    return

                # Wysyłka do API
                self.status_lbl.config(text="Wysyłanie do API...", fg="orange")
                self.update()

                # Payload to po prostu mapa_danych skonwertowana na zwykły dict
                payload = {k: dict(v) for k, v in mapa_danych.items()}

                response = requests.post(
                    f"{config.API_URL}/stats-data",
                    json=payload,
                    params={"token": config.TOKEN},
                    timeout=10
                )

                if response.status_code in [200, 201]:
                    messagebox.showinfo("Sukces", f"Dane zostały zapisane!\nOtrzymano ID: {response.json().get('id')}")
                    self.status_lbl.config(text="Zakończono pomyślnie", fg="green")
                else:
                    messagebox.showerror("Błąd API", f"Serwer zwrócił błąd {response.status_code}:\n{response.text}")
                    self.status_lbl.config(text="Błąd wysyłania", fg="red")

            except Exception as e:
                messagebox.showerror("Błąd krytyczny", f"Wystąpił nieoczekiwany błąd:\n{str(e)}")
                self.status_lbl.config(text="Błąd krytyczny", fg="red")
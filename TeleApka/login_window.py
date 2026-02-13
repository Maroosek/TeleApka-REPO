# login_window.py
import tkinter as tk
from tkinter import messagebox
import requests
import config

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
        token = config.TOKEN

        if not username or not token:
            messagebox.showwarning("Błąd", "Podaj prawidłowy login")
            return

        try:
            payload = {"username": username, "token": token}
            response = requests.post(f"{config.API_URL}/login", json=payload, timeout=5)

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
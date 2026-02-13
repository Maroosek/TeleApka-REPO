# run.py
import tkinter as tk
from login_window import LoginWindow
from main_window import AlertClient

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()

    def start_main_app(username, is_admin, assigned_ag):
        root.deiconify()
        AlertClient(root, username, is_admin, assigned_ag)

    LoginWindow(root, start_main_app)
    root.mainloop()
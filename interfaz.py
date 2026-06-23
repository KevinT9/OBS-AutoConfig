"""
Interfaz gráfica de OBS Auto-Configurator.

Separada de la lógica (que vive en escanear.py). La detección de hardware,
el cálculo de la configuración y la lectura/escritura de OBS se importan desde
ese módulo; aquí solo está la ventana y la interacción.

Ejecuta:  python interfaz.py   (o se lanza desde escanear.py)
"""

import threading
import tkinter as tk
from tkinter import Tk, ttk, messagebox, StringVar, scrolledtext

from escanear import (
    get_cpu_info, get_ram_gb, get_gpu_info, get_screen_resolution,
    measure_upload_speed, calculate_obs_settings, read_obs_current_config,
    build_improvement_list, format_settings_text, apply_obs_config,
    read_obs_service, fetch_twitch_ingests, apply_twitch_service,
    measure_ingest_latency, get_obs_version,
    list_obs_profiles, set_active_profile,
)

# Paleta
BG = '#1a1a2e'
CARD = '#16213e'
ACCENT = '#0f3460'
ACCENT_HOVER = '#16498c'
GREEN = '#4ecca3'
GREEN_HOVER = '#6fe3bd'
YELLOW = '#ffd166'
TEXT = '#e0e0e0'
SUBTEXT = '#a0a0b0'


def center_window(win, w, h, y_factor=3):
    """Centra una ventana en la pantalla (un poco por encima del centro)."""
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // y_factor)
    win.geometry(f"{w}x{h}+{x}+{y}")


def bring_to_front(win):
    """Trae la ventana al frente al abrir, sin dejarla siempre-encima
    (lo que haría que los diálogos aparecieran por detrás)."""
    win.lift()
    win.attributes('-topmost', True)
    win.after(400, lambda: win.attributes('-topmost', False))
    win.focus_force()


def add_hover(button, normal, hover):
    """Efecto hover simple para un botón."""
    button.bind('<Enter>', lambda e: button.configure(bg=hover) if str(button['state']) != 'disabled' else None)
    button.bind('<Leave>', lambda e: button.configure(bg=normal) if str(button['state']) != 'disabled' else None)


class OBSConfigurator:
    def __init__(self):
        self.root = Tk()
        self.root.title("OBS Auto-Configurator para Twitch")
        self.root.configure(bg=BG)
        self.root.minsize(580, 540)
        center_window(self.root, 680, 640)
        bring_to_front(self.root)

        self.status_var = StringVar(value="Listo para analizar tu PC")
        self.settings = None
        self.cpu_info = None
        self.gpu_info = None
        self.current_obs = None
        self.improvements = None
        self.profiles = []

        self._build_ui()
        self.root.mainloop()

    # ── Construcción de la interfaz ──
    def _build_ui(self):
        header = tk.Frame(self.root, bg=ACCENT, height=70)
        header.pack(fill='x')
        header.pack_propagate(False)
        tk.Label(header, text="⚙  OBS AUTO-CONFIGURATOR",
                 font=('Consolas', 16, 'bold'), bg=ACCENT, fg=GREEN).pack(side='left', padx=20, pady=18)
        tk.Label(header, text="para Twitch",
                 font=('Consolas', 10), bg=ACCENT, fg=SUBTEXT).pack(side='left', pady=22)

        self.btn_optimize = tk.Button(
            header, text="🚀 Optimizar PC", font=('Consolas', 9, 'bold'),
            bg=GREEN, fg='#0a0a1a', relief='flat', borderwidth=0,
            padx=12, pady=6, cursor='hand2', command=self._open_optimizer
        )
        self.btn_optimize.pack(side='right', padx=(0, 16))
        add_hover(self.btn_optimize, GREEN, GREEN_HOVER)

        self.btn_twitch = tk.Button(
            header, text="🎮 Twitch", font=('Consolas', 9, 'bold'),
            bg=ACCENT, fg=TEXT, relief='flat', borderwidth=0,
            padx=12, pady=6, cursor='hand2', command=self._open_twitch
        )
        self.btn_twitch.pack(side='right', padx=(0, 8))
        add_hover(self.btn_twitch, ACCENT, ACCENT_HOVER)

        # Selector de perfil de OBS
        prof_frame = tk.Frame(self.root, bg=BG, padx=16)
        prof_frame.pack(fill='x', pady=(10, 2))
        tk.Label(prof_frame, text="Perfil de OBS:", font=('Consolas', 9),
                 bg=BG, fg=SUBTEXT).pack(side='left')
        self.profile_box = ttk.Combobox(prof_frame, state='readonly', font=('Consolas', 9), width=34)
        self.profile_box.pack(side='left', padx=8)
        self.profile_box.bind('<<ComboboxSelected>>', self._on_profile_select)
        self._load_profiles()

        status_frame = tk.Frame(self.root, bg=CARD, height=36)
        status_frame.pack(fill='x')
        status_frame.pack_propagate(False)
        self.status_label = tk.Label(status_frame, textvariable=self.status_var,
                                     font=('Consolas', 9), bg=CARD, fg=SUBTEXT, anchor='w', padx=16)
        self.status_label.pack(fill='both', expand=True)

        self.progress = ttk.Progressbar(self.root, mode='indeterminate')
        style = ttk.Style()
        style.theme_use('default')
        style.configure('TProgressbar', background=GREEN, troughcolor=CARD, thickness=4)
        self.progress.pack(fill='x')

        text_frame = tk.Frame(self.root, bg=BG, padx=16, pady=12)
        text_frame.pack(fill='both', expand=True)
        self.output = scrolledtext.ScrolledText(
            text_frame, font=('Consolas', 9), bg=CARD, fg=TEXT,
            insertbackground=GREEN, selectbackground=ACCENT,
            relief='flat', borderwidth=0, wrap='word', state='disabled', height=18
        )
        self.output.pack(fill='both', expand=True)

        btn_frame = tk.Frame(self.root, bg=BG, pady=12)
        btn_frame.pack(fill='x', padx=16)
        btn_style = {'font': ('Consolas', 10, 'bold'), 'relief': 'flat',
                     'borderwidth': 0, 'padx': 20, 'pady': 10, 'cursor': 'hand2'}

        self.btn_analyze = tk.Button(btn_frame, text="▶  ANALIZAR Y CONFIGURAR",
                                     bg=GREEN, fg='#0a0a1a', command=self._start_analysis, **btn_style)
        self.btn_analyze.pack(side='left', padx=(0, 8))
        add_hover(self.btn_analyze, GREEN, GREEN_HOVER)

        self.btn_copy = tk.Button(btn_frame, text="⎘  COPIAR", bg=ACCENT, fg=TEXT,
                                  command=self._copy_to_clipboard, state='disabled', **btn_style)
        self.btn_copy.pack(side='left', padx=(0, 8))
        add_hover(self.btn_copy, ACCENT, ACCENT_HOVER)

        self.btn_apply = tk.Button(btn_frame, text="✓  APLICAR A OBS", bg='#1a3a2a', fg=GREEN,
                                   command=self._apply_to_obs, state='disabled', **btn_style)
        self.btn_apply.pack(side='left')
        add_hover(self.btn_apply, '#1a3a2a', '#244e3a')

        tk.Label(btn_frame, text="OBS debe estar cerrado para aplicar",
                 font=('Consolas', 8), bg=BG, fg=SUBTEXT).pack(side='right', padx=4)

    # ── Helpers de la GUI ──
    def _write(self, text):
        self.output.configure(state='normal')
        self.output.insert('end', text + '\n')
        self.output.see('end')
        self.output.configure(state='disabled')

    def _clear(self):
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.configure(state='disabled')

    def _set_status(self, text):
        self.status_var.set(text)
        self.root.update_idletasks()

    def _load_profiles(self):
        """Carga la lista de perfiles de OBS en el selector."""
        try:
            self.profiles = list_obs_profiles()  # [(visible, carpeta)]
        except Exception:
            self.profiles = []
        if self.profiles:
            self.profile_box.configure(values=[d for d, _ in self.profiles], state='readonly')
            # Preseleccionar el que OBS usaría por defecto (untitled/default)
            idx = 0
            for i, (_d, folder) in enumerate(self.profiles):
                if 'untitled' in folder.lower() or 'default' in folder.lower():
                    idx = i
                    break
            self.profile_box.current(idx)
            set_active_profile(self.profiles[idx][1])
        else:
            self.profile_box.configure(values=["(OBS no encontrado)"], state='disabled')
            self.profile_box.set("(OBS no encontrado)")

    def _on_profile_select(self, event=None):
        i = self.profile_box.current()
        if 0 <= i < len(self.profiles):
            set_active_profile(self.profiles[i][1])
            self._set_status(f"Perfil activo: {self.profiles[i][0]}. Vuelve a analizar para ver su configuración.")

    def _open_optimizer(self):
        """Abre el módulo de optimización de PC en una ventana aparte."""
        try:
            from optimizar import OptimizerWindow
            OptimizerWindow(self.root)
        except Exception as e:
            messagebox.showerror("Optimizador", f"No se pudo abrir el optimizador:\n{e}", parent=self.root)

    def _open_twitch(self):
        """Abre el diálogo para configurar el servicio de Twitch en OBS."""
        try:
            TwitchDialog(self.root)
        except Exception as e:
            messagebox.showerror("Twitch", f"No se pudo abrir la configuración de Twitch:\n{e}", parent=self.root)

    # ── Análisis ──
    def _start_analysis(self):
        self.btn_analyze.configure(state='disabled')
        self.btn_copy.configure(state='disabled')
        self.btn_apply.configure(state='disabled')
        self._clear()
        self.progress.start(10)
        threading.Thread(target=self._run_analysis, daemon=True).start()

    def _run_analysis(self):
        try:
            self.current_obs = None
            self.improvements = None

            self._set_status("Detectando CPU...")
            self._write("Detectando CPU...")
            self.cpu_info = get_cpu_info()
            self._write(f"  ✓ {self.cpu_info['name']} ({self.cpu_info['threads']} hilos)")

            self._set_status("Detectando RAM...")
            self._write("Detectando RAM...")
            ram = get_ram_gb()
            self._write(f"  ✓ {ram} GB")

            self._set_status("Detectando GPU...")
            self._write("Detectando GPU...")
            self.gpu_info = get_gpu_info()
            if self.gpu_info['names']:
                self._write(f"  ✓ {', '.join(self.gpu_info['names'])}")
                if self.gpu_info['has_hw_encoder']:
                    self._write(f"    → Encoder por hardware disponible ({self.gpu_info['vendor'].upper()})")
            else:
                self._write("  • No se detectó GPU dedicada (se usará x264 por CPU)")

            self._set_status("Detectando resolución del monitor...")
            self._write("Detectando resolución del monitor...")
            screen_res = get_screen_resolution()
            self._write(f"  ✓ {screen_res[0]}x{screen_res[1]}")

            self._set_status("Detectando versión de OBS...")
            self._write("Detectando versión de OBS...")
            obs_ver_str, obs_ver_tuple = get_obs_version()
            self._write(f"  ✓ OBS {obs_ver_str}" if obs_ver_str else "  • Versión de OBS no detectada")

            self._set_status("Midiendo velocidad de upload (puede tardar ~15s)...")
            self._write("Midiendo velocidad de upload...")
            upload = measure_upload_speed(self._set_status)
            self._write(f"  ✓ {upload} Mbps de upload")

            self._set_status("Calculando configuración óptima...")
            self._write("\nCalculando configuración óptima...")
            self.settings = calculate_obs_settings(
                self.cpu_info, ram, upload,
                gpu_info=self.gpu_info, screen_res=screen_res, target_res='auto',
                obs_version=obs_ver_tuple
            )

            self._set_status("Leyendo configuración actual de OBS...")
            self._write("Leyendo configuración actual de OBS...")
            self.current_obs, obs_error = read_obs_current_config()
            if obs_error:
                self._write(f"  • No se pudo leer configuración actual: {obs_error}")
            else:
                self._write("  ✓ Configuración actual detectada")
                self.improvements = build_improvement_list(self.current_obs, self.settings)

            result_text = format_settings_text(
                self.settings, self.cpu_info, self.current_obs, self.improvements
            )
            self._write("\n" + result_text)
            self._set_status("Análisis completo. Puedes copiar o aplicar la configuración.")

        except Exception as e:
            self._write(f"\n✗ Error durante el análisis: {str(e)}")
            self._set_status("Error durante el análisis.")
        finally:
            self.progress.stop()
            self.btn_analyze.configure(state='normal')
            if self.settings:
                self.btn_copy.configure(state='normal')
                self.btn_apply.configure(state='normal')

    def _copy_to_clipboard(self):
        if not self.settings:
            return
        text = format_settings_text(self.settings, self.cpu_info, self.current_obs, self.improvements)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("✓ Configuración copiada al portapapeles.")

    def _apply_to_obs(self):
        if not self.settings:
            return
        confirm = messagebox.askyesno(
            "Aplicar configuración",
            "Esto modificará los archivos de configuración de OBS.\n\n"
            "Se creará un backup automático (.ini.bak)\n\n"
            "¿OBS está cerrado?\n\n¿Continuar?",
            icon='warning', parent=self.root
        )
        if not confirm:
            return

        self._set_status("Aplicando configuración a OBS...")
        success, message = apply_obs_config(self.settings)
        if success:
            self._write(f"\n✓ {message}")
            self._set_status("✓ Configuración aplicada. Abre OBS para verificar.")
            messagebox.showinfo(
                "Configuración aplicada",
                f"✓ Configuración aplicada correctamente.\n\n{message}\n\n"
                "Abre OBS y verifica en:\n"
                "Configuración → Salida y Configuración → Video",
                parent=self.root
            )
        else:
            self._write(f"\n✗ No se pudo aplicar automáticamente:\n  {message}")
            self._set_status("No se pudo aplicar. Aplica manualmente con los valores mostrados.")
            messagebox.showwarning(
                "Aplicación manual requerida",
                f"No se pudo aplicar automáticamente:\n\n{message}\n\n"
                "Usa los valores mostrados en pantalla para configurar OBS manualmente.",
                parent=self.root
            )


class TwitchDialog:
    """Diálogo para configurar el servicio de Twitch (service.json) en OBS."""

    def __init__(self, parent):
        self.parent = parent
        self.win = tk.Toplevel(parent)
        self.win.title("Configurar Twitch en OBS")
        self.win.configure(bg=BG)
        self.win.resizable(False, False)

        self.servers = [('Auto (recomendado)', 'auto')]  # (nombre, url)
        self.show_key = tk.BooleanVar(value=False)

        self._build_ui()

        self.win.update_idletasks()
        w, h = 520, 420
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        self.win.transient(parent)
        self.win.lift()
        self.win.focus_force()

        threading.Thread(target=self._load_state, daemon=True).start()

    def _build_ui(self):
        header = tk.Frame(self.win, bg=ACCENT, height=56)
        header.pack(fill='x')
        header.pack_propagate(False)
        tk.Label(header, text="🎮 CONFIGURAR TWITCH", font=('Consolas', 13, 'bold'),
                 bg=ACCENT, fg=GREEN).pack(side='left', padx=16, pady=14)

        body = tk.Frame(self.win, bg=BG, padx=18, pady=14)
        body.pack(fill='both', expand=True)

        self.current_var = StringVar(value="Leyendo configuración actual…")
        tk.Label(body, textvariable=self.current_var, font=('Consolas', 8),
                 bg=BG, fg=SUBTEXT, justify='left', anchor='w', wraplength=480).pack(fill='x', pady=(0, 10))

        tk.Label(body, text="Servidor de ingest:", font=('Consolas', 9, 'bold'),
                 bg=BG, fg=TEXT, anchor='w').pack(fill='x')
        self.server_box = ttk.Combobox(body, state='readonly', font=('Consolas', 9),
                                       values=['Auto (recomendado)'])
        self.server_box.current(0)
        self.server_box.pack(fill='x', pady=(2, 4))

        self.btn_latency = tk.Button(
            body, text="🔎 Medir latencia y elegir el mejor", font=('Consolas', 8, 'bold'),
            bg=ACCENT, fg=TEXT, relief='flat', borderwidth=0, padx=10, pady=5,
            cursor='hand2', command=self._start_latency
        )
        self.btn_latency.pack(anchor='w', pady=(0, 12))
        add_hover(self.btn_latency, ACCENT, ACCENT_HOVER)

        tk.Label(body, text="Stream key (opcional — si la dejas vacía se conserva la actual):",
                 font=('Consolas', 9, 'bold'), bg=BG, fg=TEXT, anchor='w', wraplength=480,
                 justify='left').pack(fill='x')
        self.key_entry = tk.Entry(body, show='*', font=('Consolas', 9), bg=CARD, fg=TEXT,
                                  insertbackground=GREEN, relief='flat')
        self.key_entry.pack(fill='x', ipady=4, pady=(2, 2))
        tk.Checkbutton(body, text="Mostrar clave", variable=self.show_key, command=self._toggle_key,
                       font=('Consolas', 8), bg=BG, fg=SUBTEXT, selectcolor=ACCENT,
                       activebackground=BG, activeforeground=GREEN, anchor='w').pack(fill='x')

        tk.Label(body, text="Obtén tu clave en: dashboard.twitch.tv → Ajustes → Transmisión",
                 font=('Consolas', 8), bg=BG, fg=SUBTEXT, anchor='w',
                 wraplength=480, justify='left').pack(fill='x', pady=(8, 0))
        tk.Label(body, text="⚠ Cierra OBS antes de aplicar (si está abierto, sobrescribirá esto al cerrarse).",
                 font=('Consolas', 8), bg=BG, fg=YELLOW, anchor='w',
                 wraplength=480, justify='left').pack(fill='x', pady=(2, 0))

        self.status_var = StringVar(value="")
        tk.Label(self.win, textvariable=self.status_var, font=('Consolas', 8),
                 bg=CARD, fg=SUBTEXT, anchor='w', padx=16).pack(fill='x')

        btns = tk.Frame(self.win, bg=BG, pady=10)
        btns.pack(fill='x', padx=16)
        bstyle = {'font': ('Consolas', 10, 'bold'), 'relief': 'flat', 'borderwidth': 0,
                  'padx': 16, 'pady': 8, 'cursor': 'hand2'}
        self.btn_apply = tk.Button(btns, text="✓ APLICAR", bg=GREEN, fg='#0a0a1a',
                                   command=self._apply, **bstyle)
        self.btn_apply.pack(side='left', padx=(0, 8))
        add_hover(self.btn_apply, GREEN, GREEN_HOVER)
        btn_close = tk.Button(btns, text="Cerrar", bg=ACCENT, fg=TEXT,
                              command=self.win.destroy, **bstyle)
        btn_close.pack(side='left')
        add_hover(btn_close, ACCENT, ACCENT_HOVER)

    def _toggle_key(self):
        self.key_entry.configure(show='' if self.show_key.get() else '*')

    def _load_state(self):
        # Config actual
        current, err = read_obs_service()
        if err:
            self.current_var.set(f"⚠ {err}")
        elif current:
            if current['exists']:
                key_txt = "configurada ✓" if current['has_key'] else "no configurada"
                self.current_var.set(
                    f"Actual → servicio: {current.get('service') or '—'} | "
                    f"servidor: {current.get('server') or '—'} | clave: {key_txt}"
                )
            else:
                self.current_var.set("Aún no hay service.json (se creará al aplicar).")

        # Lista de ingests
        self.status_var.set("Obteniendo servidores de Twitch…")
        self.servers = fetch_twitch_ingests()
        names = [n for n, _ in self.servers]
        self.server_box.configure(values=names)
        self.server_box.current(0)
        extra = "" if len(self.servers) > 1 else " (sin conexión: solo Auto)"
        self.status_var.set(f"{len(self.servers)} servidores disponibles{extra}.")

    def _start_latency(self):
        if len(self.servers) <= 1:
            self.status_var.set("Aún no hay lista de servidores (¿sin conexión?).")
            return
        self.btn_latency.configure(state='disabled')
        self.status_var.set("Midiendo latencia a los servidores de Twitch…")
        threading.Thread(target=self._measure_latency, daemon=True).start()

    def _measure_latency(self):
        try:
            results = measure_ingest_latency(self.servers)
            reachable = [(n, u, ms) for n, u, ms in results if ms is not None]
            if not reachable:
                self.status_var.set("No se pudo medir latencia (firewall/red).")
                return
            best_name, best_url, best_ms = reachable[0]
            # Seleccionar el mejor en el combobox
            for i, (n, u) in enumerate(self.servers):
                if u == best_url:
                    self.server_box.current(i)
                    break
            top = " | ".join(f"{n.split('(')[0].strip()}: {ms}ms" for n, u, ms in reachable[:3])
            self.status_var.set(f"Mejor: {best_name} ({best_ms} ms). Top: {top}")
        except Exception as e:
            self.status_var.set(f"Error midiendo latencia: {e}")
        finally:
            self.btn_latency.configure(state='normal')

    def _apply(self):
        idx = self.server_box.current()
        idx = idx if idx >= 0 else 0
        name, server_url = self.servers[idx]
        key = self.key_entry.get().strip() or None

        self.btn_apply.configure(state='disabled')
        self.status_var.set("Aplicando…")
        ok, msg = apply_twitch_service(server=server_url, stream_key=key)
        self.btn_apply.configure(state='normal')

        if ok:
            self.status_var.set("✓ Twitch configurado.")
            messagebox.showinfo("Twitch", msg + f"\n\nServidor: {name}", parent=self.win)
        else:
            self.status_var.set("No se pudo aplicar.")
            messagebox.showerror("Twitch", msg, parent=self.win)


def main():
    print("=" * 60)
    print("  OBS Auto-Configurator para Twitch")
    print("  Detecta specs → Lee config actual → Recomienda → Aplica")
    print("=" * 60)
    OBSConfigurator()


if __name__ == '__main__':
    main()

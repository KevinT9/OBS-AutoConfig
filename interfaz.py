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
)

# Paleta
BG = '#1a1a2e'
CARD = '#16213e'
ACCENT = '#0f3460'
ACCENT_HOVER = '#16498c'
GREEN = '#4ecca3'
GREEN_HOVER = '#6fe3bd'
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
        self.btn_optimize.pack(side='right', padx=16)
        add_hover(self.btn_optimize, GREEN, GREEN_HOVER)

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

    def _open_optimizer(self):
        """Abre el módulo de optimización de PC en una ventana aparte."""
        try:
            from optimizar import OptimizerWindow
            OptimizerWindow(self.root)
        except Exception as e:
            messagebox.showerror("Optimizador", f"No se pudo abrir el optimizador:\n{e}", parent=self.root)

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

            self._set_status("Midiendo velocidad de upload (puede tardar ~15s)...")
            self._write("Midiendo velocidad de upload...")
            upload = measure_upload_speed(self._set_status)
            self._write(f"  ✓ {upload} Mbps de upload")

            self._set_status("Calculando configuración óptima...")
            self._write("\nCalculando configuración óptima...")
            self.settings = calculate_obs_settings(
                self.cpu_info, ram, upload,
                gpu_info=self.gpu_info, screen_res=screen_res, target_res='auto'
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


def main():
    print("=" * 60)
    print("  OBS Auto-Configurator para Twitch")
    print("  Detecta specs → Lee config actual → Recomienda → Aplica")
    print("=" * 60)
    OBSConfigurator()


if __name__ == '__main__':
    main()

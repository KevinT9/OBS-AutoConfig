"""
Optimizador de PC para streaming (módulo complementario de OBS Auto-Configurator).

Detecta el estado del sistema y aplica optimizaciones SEGURAS y REVERSIBLES
para tener un directo estable:
  • Recursos (RAM/CPU): cerrar apps pesadas en segundo plano, prioridad de OBS.
  • Rendimiento de Windows: plan de energía Alto rendimiento, Modo Juego, HAGS.
  • Sin interrupciones: silenciar notificaciones (toasts) durante el stream.
  • Red y disco: revisar descargas/sincronización activas y espacio libre.

Cada cambio guarda el valor original en un backup JSON; el botón "Restaurar"
deja el sistema como estaba. No borra archivos ni desactiva servicios.

Se puede ejecutar solo (`python optimizar.py`) o abrir desde la app principal.
"""

import os
import json
import shutil
import platform
import subprocess
import threading
from pathlib import Path
from tkinter import Tk, Toplevel, ttk, messagebox, BooleanVar, StringVar, scrolledtext
import tkinter as tk

try:
    import ctypes
    import winreg
except Exception:  # pragma: no cover - solo Windows
    ctypes = None
    winreg = None


_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

BACKUP_DIR = Path.home() / 'AppData' / 'Local' / 'obs-autoconfig'
BACKUP_FILE = BACKUP_DIR / 'optimizer_backup.json'

# GUID del plan de energía "Alto rendimiento" (estándar en Windows)
POWER_HIGH_GUID = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'

# Apps que conviene cerrar antes de streamear (consumen RAM/CPU/red).
# Clave = nombre de proceso SIN extensión (como lo devuelve Get-Process).
HEAVY_APPS = {
    'chrome': 'Google Chrome',
    'msedge': 'Microsoft Edge',
    'firefox': 'Firefox',
    'opera': 'Opera',
    'brave': 'Brave',
    'discord': 'Discord',
    'spotify': 'Spotify',
    'steam': 'Steam',
    'epicgameslauncher': 'Epic Games Launcher',
    'slack': 'Slack',
    'teams': 'Microsoft Teams',
    'ms-teams': 'Microsoft Teams',
    'telegram': 'Telegram',
    'whatsapp': 'WhatsApp',
}

# Apps de red/sincronización (solo se reportan; consumen ancho de banda).
NETWORK_HOGS = {
    'onedrive': 'OneDrive',
    'dropbox': 'Dropbox',
    'googledrivefs': 'Google Drive',
    'steam': 'Steam (descargas)',
    'epicgameslauncher': 'Epic Games (descargas)',
    'backblaze': 'Backblaze',
}


# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────

def _run_powershell(script, timeout=10):
    try:
        proc = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return ''


def _parse_json(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def is_admin():
    """True si el proceso corre con privilegios de administrador."""
    if not ctypes:
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _load_backup():
    try:
        return json.loads(BACKUP_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_backup(data):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


def _remember(key, value):
    """Guarda el valor ORIGINAL una sola vez (para poder restaurar)."""
    data = _load_backup()
    if key not in data:
        data[key] = value
        _save_backup(data)


# ─────────────────────────────────────────────
# DETECCIÓN DE ESTADO
# ─────────────────────────────────────────────

class _MEMORYSTATUSEX(ctypes.Structure if ctypes else object):
    _fields_ = [
        ('dwLength', ctypes.c_ulong if ctypes else None),
        ('dwMemoryLoad', ctypes.c_ulong if ctypes else None),
        ('ullTotalPhys', ctypes.c_ulonglong if ctypes else None),
        ('ullAvailPhys', ctypes.c_ulonglong if ctypes else None),
        ('ullTotalPageFile', ctypes.c_ulonglong if ctypes else None),
        ('ullAvailPageFile', ctypes.c_ulonglong if ctypes else None),
        ('ullTotalVirtual', ctypes.c_ulonglong if ctypes else None),
        ('ullAvailVirtual', ctypes.c_ulonglong if ctypes else None),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong if ctypes else None),
    ] if ctypes else []


def get_memory_status():
    """RAM total, disponible y % en uso."""
    if not ctypes:
        return {'percent': None, 'total_gb': None, 'avail_gb': None}
    try:
        m = _MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return {
            'percent': m.dwMemoryLoad,
            'total_gb': round(m.ullTotalPhys / 1024 ** 3, 1),
            'avail_gb': round(m.ullAvailPhys / 1024 ** 3, 1),
        }
    except Exception:
        return {'percent': None, 'total_gb': None, 'avail_gb': None}


def _running_apps_with_mem():
    """Devuelve {nombre: {'count','mem_mb'}} de procesos en ejecución."""
    out = _run_powershell(
        "Get-Process | Group-Object ProcessName | "
        "Select-Object Name,Count,"
        "@{n='Mem';e={[math]::Round((($_.Group|Measure-Object WorkingSet64 -Sum).Sum)/1MB)}} | "
        "ConvertTo-Json -Compress"
    )
    data = _parse_json(out) or []
    if isinstance(data, dict):
        data = [data]
    result = {}
    for d in data:
        name = (d.get('Name') or '').lower()
        if name:
            result[name] = {'count': d.get('Count', 1), 'mem_mb': d.get('Mem', 0)}
    return result


def get_heavy_processes():
    """Apps pesadas en ejecución, ordenadas por uso de RAM."""
    running = _running_apps_with_mem()
    found = []
    for proc_name, display in HEAVY_APPS.items():
        if proc_name in running:
            found.append({
                'name': proc_name,
                'display': display,
                'count': running[proc_name]['count'],
                'mem_mb': running[proc_name]['mem_mb'],
            })
    found.sort(key=lambda x: -x['mem_mb'])
    return found


def get_network_hogs():
    """Apps de red/sincronización en ejecución + estado de Windows Update."""
    running = _running_apps_with_mem()
    apps = [display for proc_name, display in NETWORK_HOGS.items() if proc_name in running]
    wu = _run_powershell("(Get-Service wuauserv -ErrorAction SilentlyContinue).Status")
    return {'apps': sorted(set(apps)), 'windows_update': wu or 'Desconocido'}


def get_disk_space(path='C:\\'):
    try:
        usage = shutil.disk_usage(path)
        return {'free_gb': round(usage.free / 1024 ** 3, 1),
                'total_gb': round(usage.total / 1024 ** 3, 1)}
    except Exception:
        return {'free_gb': None, 'total_gb': None}


def get_obs_priority():
    """PriorityClass del proceso obs64 si está en ejecución, si no None."""
    out = _run_powershell(
        "Get-Process obs64 -ErrorAction SilentlyContinue | "
        "Select-Object -First 1 -ExpandProperty PriorityClass"
    )
    return out or None


def get_power_plan():
    """(guid, nombre) del plan de energía activo."""
    out = _run_powershell("powercfg /getactivescheme")
    import re
    guid = None
    name = None
    m = re.search(r'([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})', out)
    if m:
        guid = m.group(1)
    n = re.search(r'\(([^)]+)\)', out)
    if n:
        name = n.group(1)
    return guid, name


def _reg_get(hive, path, name):
    if not winreg:
        return None
    try:
        k = winreg.OpenKey(hive, path)
        val, _ = winreg.QueryValueEx(k, name)
        winreg.CloseKey(k)
        return val
    except Exception:
        return None


def _reg_set(hive, path, name, value):
    k = winreg.CreateKey(hive, path)
    winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)
    winreg.CloseKey(k)


def _reg_delete(hive, path, name):
    try:
        k = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(k, name)
        winreg.CloseKey(k)
    except Exception:
        pass


def get_game_mode():
    return _reg_get(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\GameBar', 'AutoGameModeEnabled') if winreg else None


def get_hags():
    return _reg_get(winreg.HKEY_LOCAL_MACHINE,
                    r'SYSTEM\CurrentControlSet\Control\GraphicsDrivers', 'HwSchMode') if winreg else None


def get_notifications_enabled():
    val = _reg_get(winreg.HKEY_CURRENT_USER,
                   r'Software\Microsoft\Windows\CurrentVersion\PushNotifications', 'ToastEnabled') if winreg else None
    return 1 if val is None else val  # por defecto están activadas


# ─────────────────────────────────────────────
# APLICAR OPTIMIZACIONES (reversibles)
# ─────────────────────────────────────────────

def close_processes(proc_names):
    """Cierra (de forma ordenada, sin /F) los procesos indicados."""
    results = []
    for n in proc_names:
        exe = n if n.lower().endswith('.exe') else n + '.exe'
        try:
            proc = subprocess.run(['taskkill', '/IM', exe], capture_output=True,
                                  text=True, creationflags=_NO_WINDOW)
            results.append((n, proc.returncode == 0))
        except Exception:
            results.append((n, False))
    return results


def set_high_performance():
    guid, _name = get_power_plan()
    if guid:
        _remember('power_guid', guid)
    out = _run_powershell(f"powercfg /setactive {POWER_HIGH_GUID}")
    new_guid, new_name = get_power_plan()
    if new_guid and new_guid.lower() == POWER_HIGH_GUID.lower():
        return True, "Plan de energía → Alto rendimiento."
    return False, "No se pudo cambiar el plan (puede no existir 'Alto rendimiento' en este equipo)."


def set_game_mode(on=True):
    if not winreg:
        return False, "No disponible."
    _remember('game_mode', get_game_mode())
    _reg_set(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\GameBar', 'AutoGameModeEnabled', 1 if on else 0)
    return True, "Modo Juego de Windows activado."


def set_hags(on=True):
    if not winreg:
        return False, "No disponible."
    if not is_admin():
        return False, "HAGS requiere ejecutar como administrador (clic derecho → Ejecutar como administrador)."
    _remember('hags', get_hags())
    try:
        _reg_set(winreg.HKEY_LOCAL_MACHINE,
                 r'SYSTEM\CurrentControlSet\Control\GraphicsDrivers', 'HwSchMode', 2 if on else 1)
        return True, "GPU scheduling (HAGS) activado — REINICIA para que tenga efecto."
    except Exception as e:
        return False, f"No se pudo aplicar HAGS: {e}"


def set_notifications(enabled):
    if not winreg:
        return False, "No disponible."
    _remember('toast', get_notifications_enabled())
    _reg_set(winreg.HKEY_CURRENT_USER,
             r'Software\Microsoft\Windows\CurrentVersion\PushNotifications', 'ToastEnabled', 1 if enabled else 0)
    estado = "activadas" if enabled else "silenciadas"
    return True, f"Notificaciones {estado}."


def set_obs_priority(level='AboveNormal'):
    cur = get_obs_priority()
    if not cur:
        return False, "OBS no está en ejecución (ábrelo y reintenta)."
    _remember('obs_priority', cur)
    _run_powershell(f"Get-Process obs64 | ForEach-Object {{ $_.PriorityClass = '{level}' }}")
    return True, f"Prioridad de OBS → {level}."


def revert_all():
    """Restaura todos los valores guardados en el backup."""
    data = _load_backup()
    if not data:
        return ["No hay cambios que restaurar."]
    msgs = []

    if 'power_guid' in data and data['power_guid']:
        _run_powershell(f"powercfg /setactive {data['power_guid']}")
        msgs.append("Plan de energía restaurado.")

    if 'game_mode' in data and winreg:
        if data['game_mode'] is None:
            _reg_delete(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\GameBar', 'AutoGameModeEnabled')
        else:
            _reg_set(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\GameBar', 'AutoGameModeEnabled', data['game_mode'])
        msgs.append("Modo Juego restaurado.")

    if 'hags' in data and winreg:
        if is_admin():
            if data['hags'] is None:
                _reg_delete(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\GraphicsDrivers', 'HwSchMode')
            else:
                _reg_set(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\GraphicsDrivers', 'HwSchMode', data['hags'])
            msgs.append("HAGS restaurado (reinicia para aplicar).")
        else:
            msgs.append("HAGS no restaurado: requiere administrador.")

    if 'toast' in data and winreg:
        _reg_set(winreg.HKEY_CURRENT_USER,
                 r'Software\Microsoft\Windows\CurrentVersion\PushNotifications', 'ToastEnabled', data['toast'])
        msgs.append("Notificaciones restauradas.")

    if 'obs_priority' in data:
        if get_obs_priority():
            _run_powershell(f"Get-Process obs64 | ForEach-Object {{ $_.PriorityClass = '{data['obs_priority']}' }}")
            msgs.append("Prioridad de OBS restaurada.")

    try:
        BACKUP_FILE.unlink()
    except Exception:
        pass

    return msgs


# ─────────────────────────────────────────────
# INTERFAZ
# ─────────────────────────────────────────────

BG = '#1a1a2e'
CARD = '#16213e'
ACCENT = '#0f3460'
GREEN = '#4ecca3'
YELLOW = '#ffd166'
TEXT = '#e0e0e0'
SUBTEXT = '#a0a0b0'


class OptimizerWindow:
    """Ventana del optimizador. Funciona sola o como hija de la app principal."""

    def __init__(self, parent=None):
        self.parent = parent
        self.win = Toplevel(parent) if parent else Tk()
        self.win.title("Optimizar PC para streaming")
        self.win.geometry("640x640")
        self.win.resizable(False, False)
        self.win.configure(bg=BG)

        self.status_var = StringVar(value="Pulsa 'Analizar estado' para revisar tu PC")
        self.heavy = []   # procesos pesados detectados
        self._build_ui()

        if not parent:
            self.win.mainloop()

    def _build_ui(self):
        header = tk.Frame(self.win, bg=ACCENT, height=64)
        header.pack(fill='x')
        header.pack_propagate(False)
        tk.Label(header, text="🚀 OPTIMIZAR PC PARA STREAMING",
                 font=('Consolas', 14, 'bold'), bg=ACCENT, fg=GREEN).pack(side='left', padx=18, pady=16)
        admin_txt = "Admin ✓" if is_admin() else "Sin admin (HAGS no disponible)"
        tk.Label(header, text=admin_txt, font=('Consolas', 8),
                 bg=ACCENT, fg=(GREEN if is_admin() else YELLOW)).pack(side='right', padx=14)

        # Checkboxes
        opts = tk.Frame(self.win, bg=BG, padx=16, pady=10)
        opts.pack(fill='x')

        self.var_apps = BooleanVar(value=True)
        self.var_obs = BooleanVar(value=True)
        self.var_power = BooleanVar(value=True)
        self.var_game = BooleanVar(value=True)
        self.var_hags = BooleanVar(value=False)
        self.var_notif = BooleanVar(value=True)

        def chk(parent, text, var):
            c = tk.Checkbutton(parent, text=text, variable=var, font=('Consolas', 9),
                               bg=BG, fg=TEXT, selectcolor=ACCENT, activebackground=BG,
                               activeforeground=GREEN, anchor='w')
            c.pack(fill='x', anchor='w')
            return c

        chk(opts, "Cerrar apps pesadas en segundo plano (navegador, Discord, launchers…)", self.var_apps)
        chk(opts, "Prioridad alta para OBS (si está abierto)", self.var_obs)
        chk(opts, "Plan de energía: Alto rendimiento", self.var_power)
        chk(opts, "Activar Modo Juego de Windows", self.var_game)
        self._hags_chk = chk(opts, "Activar GPU scheduling / HAGS (requiere admin + reinicio)", self.var_hags)
        chk(opts, "Silenciar notificaciones durante el stream", self.var_notif)
        if not is_admin():
            self._hags_chk.configure(state='disabled', fg=SUBTEXT)

        # Log
        log_frame = tk.Frame(self.win, bg=BG, padx=16, pady=6)
        log_frame.pack(fill='both', expand=True)
        self.output = scrolledtext.ScrolledText(
            log_frame, font=('Consolas', 9), bg=CARD, fg=TEXT, insertbackground=GREEN,
            relief='flat', borderwidth=0, wrap='word', state='disabled', height=16)
        self.output.pack(fill='both', expand=True)

        # Status
        self.status_label = tk.Label(self.win, textvariable=self.status_var, font=('Consolas', 9),
                                     bg=CARD, fg=SUBTEXT, anchor='w', padx=16)
        self.status_label.pack(fill='x')

        # Botones
        btns = tk.Frame(self.win, bg=BG, pady=10)
        btns.pack(fill='x', padx=16)
        bstyle = {'font': ('Consolas', 10, 'bold'), 'relief': 'flat', 'borderwidth': 0,
                  'padx': 14, 'pady': 9, 'cursor': 'hand2'}

        self.btn_scan = tk.Button(btns, text="🔍 ANALIZAR ESTADO", bg=ACCENT, fg=TEXT,
                                  command=self._start_scan, **bstyle)
        self.btn_scan.pack(side='left', padx=(0, 6))
        self.btn_apply = tk.Button(btns, text="✓ APLICAR", bg=GREEN, fg='#0a0a1a',
                                   command=self._start_apply, **bstyle)
        self.btn_apply.pack(side='left', padx=(0, 6))
        self.btn_revert = tk.Button(btns, text="↩ RESTAURAR", bg='#3a2a1a', fg=YELLOW,
                                    command=self._start_revert, **bstyle)
        self.btn_revert.pack(side='left')

    # ── helpers GUI ──
    def _write(self, text):
        self.output.configure(state='normal')
        self.output.insert('end', text + '\n')
        self.output.see('end')
        self.output.configure(state='disabled')

    def _clear(self):
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.configure(state='disabled')

    def _status(self, t):
        self.status_var.set(t)
        self.win.update_idletasks()

    def _busy(self, busy):
        state = 'disabled' if busy else 'normal'
        for b in (self.btn_scan, self.btn_apply, self.btn_revert):
            b.configure(state=state)

    # ── Analizar ──
    def _start_scan(self):
        self._clear()
        self._busy(True)
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        try:
            self._status("Analizando estado del sistema...")
            self._write("── RECURSOS ──")
            mem = get_memory_status()
            if mem['percent'] is not None:
                self._write(f"  RAM: {mem['avail_gb']} GB libres de {mem['total_gb']} GB "
                            f"({mem['percent']}% en uso)")
            self.heavy = get_heavy_processes()
            if self.heavy:
                self._write("  Apps pesadas en ejecución:")
                for h in self.heavy:
                    self._write(f"    • {h['display']}: {h['mem_mb']} MB ({h['count']} procesos)")
            else:
                self._write("  ✓ No hay apps pesadas conocidas en ejecución.")
            obs = get_obs_priority()
            self._write(f"  OBS: {'prioridad ' + obs if obs else 'no está abierto'}")

            self._write("\n── RENDIMIENTO DE WINDOWS ──")
            _guid, pname = get_power_plan()
            self._write(f"  Plan de energía: {pname or 'desconocido'}")
            gm = get_game_mode()
            self._write(f"  Modo Juego: {'activado' if gm == 1 else 'desactivado'}")
            hags = get_hags()
            hags_txt = {2: 'activado', 1: 'desactivado'}.get(hags, 'no soportado/desconocido')
            self._write(f"  HAGS (GPU scheduling): {hags_txt}")

            self._write("\n── SIN INTERRUPCIONES ──")
            notif = get_notifications_enabled()
            self._write(f"  Notificaciones: {'activadas' if notif == 1 else 'silenciadas'}")

            self._write("\n── RED Y DISCO ──")
            net = get_network_hogs()
            if net['apps']:
                self._write(f"  ⚠ Apps de red/sincronización activas: {', '.join(net['apps'])}")
            else:
                self._write("  ✓ Sin apps de sincronización conocidas activas.")
            self._write(f"  Windows Update (servicio): {net['windows_update']}")
            disk = get_disk_space()
            if disk['free_gb'] is not None:
                low = " ⚠ poco espacio" if disk['free_gb'] < 15 else ""
                self._write(f"  Disco C: {disk['free_gb']} GB libres de {disk['total_gb']} GB{low}")

            self._write("\nMarca las casillas que quieras y pulsa APLICAR.")
            self._status("Análisis completo.")
        except Exception as e:
            self._write(f"\n✗ Error al analizar: {e}")
            self._status("Error al analizar.")
        finally:
            self._busy(False)

    # ── Aplicar ──
    def _start_apply(self):
        # Confirmar cierre de apps si corresponde
        if self.var_apps.get():
            if not self.heavy:
                self.heavy = get_heavy_processes()
            if self.heavy:
                nombres = "\n".join(f"• {h['display']}" for h in self.heavy)
                ok = messagebox.askyesno(
                    "Cerrar aplicaciones",
                    "Se cerrarán estas apps para liberar recursos.\n"
                    "GUARDA tu trabajo antes de continuar:\n\n" + nombres + "\n\n¿Continuar?",
                    icon='warning', parent=self.win)
                if not ok:
                    return
        self._busy(True)
        threading.Thread(target=self._apply, daemon=True).start()

    def _apply(self):
        try:
            self._write("\n══ APLICANDO OPTIMIZACIONES ══")

            if self.var_apps.get() and self.heavy:
                self._status("Cerrando apps pesadas...")
                res = close_processes([h['name'] for h in self.heavy])
                for name, ok in res:
                    disp = HEAVY_APPS.get(name, name)
                    self._write(f"  {'✓' if ok else '•'} {disp}: {'cerrado' if ok else 'no se pudo cerrar'}")

            if self.var_obs.get():
                ok, msg = set_obs_priority()
                self._write(f"  {'✓' if ok else '•'} {msg}")

            if self.var_power.get():
                ok, msg = set_high_performance()
                self._write(f"  {'✓' if ok else '•'} {msg}")

            if self.var_game.get():
                ok, msg = set_game_mode(True)
                self._write(f"  {'✓' if ok else '•'} {msg}")

            if self.var_hags.get():
                ok, msg = set_hags(True)
                self._write(f"  {'✓' if ok else '⚠'} {msg}")

            if self.var_notif.get():
                ok, msg = set_notifications(False)
                self._write(f"  {'✓' if ok else '•'} {msg}")

            self._write("\nListo. Usa ↩ RESTAURAR para revertir todo cuando termines de streamear.")
            self._status("Optimizaciones aplicadas.")
            messagebox.showinfo("Optimización aplicada",
                                "Optimizaciones aplicadas.\n\nRecuerda pulsar 'Restaurar' al terminar el stream.",
                                parent=self.win)
        except Exception as e:
            self._write(f"\n✗ Error al aplicar: {e}")
            self._status("Error al aplicar.")
        finally:
            self._busy(False)

    # ── Restaurar ──
    def _start_revert(self):
        ok = messagebox.askyesno("Restaurar", "¿Revertir todos los cambios aplicados?", parent=self.win)
        if not ok:
            return
        self._busy(True)
        threading.Thread(target=self._revert, daemon=True).start()

    def _revert(self):
        try:
            self._write("\n══ RESTAURANDO ══")
            for m in revert_all():
                self._write(f"  ✓ {m}")
            self._status("Restauración completa.")
        except Exception as e:
            self._write(f"\n✗ Error al restaurar: {e}")
            self._status("Error al restaurar.")
        finally:
            self._busy(False)


if __name__ == '__main__':
    if platform.system() != 'Windows':
        print("Este optimizador solo funciona en Windows.")
    else:
        OptimizerWindow()

"""
OBS Auto-Configurator para Twitch
Detecta specs del PC (CPU, RAM, GPU), mide upload, lee la configuración
actual de OBS, indica qué mejorar y aplica la configuración óptima.

Mejoras clave:
  • Detecta la GPU y recomienda el encoder por HARDWARE (NVENC/AMF/QuickSync)
    cuando está disponible — libera el CPU y da streams más estables que x264.
  • Si NO hay gráfica usable, cae automáticamente en x264 por CPU.
  • Detección de hardware vía PowerShell/CIM (funciona en Windows 10 y 11;
    wmic queda solo como respaldo porque está obsoleto en Win11 reciente).
  • Lee la configuración actual de OBS y la compara con la recomendada.

Compatibilidad: Windows 10 y 11, con o sin tarjeta gráfica dedicada.

Este módulo contiene SOLO la lógica (detección, cálculo y lectura/escritura
de OBS). La interfaz gráfica vive en interfaz.py.
"""

import os
import json
import platform
import subprocess
import urllib.request
import time
import shutil
from pathlib import Path


# Evita que parpadeen ventanas de consola al lanzar subprocesos desde la GUI
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Mapeo de IDs de encoder de OBS → nombre legible
ENCODER_LABELS = {
    'x264': 'x264 (CPU)',
    'obs_x264': 'x264 (CPU)',
    'amd': 'AMD AMF (hardware)',
    'amd_hevc': 'AMD AMF HEVC (hardware)',
    'h264_texture_amf': 'AMD AMF (hardware)',
    'amd_amf_h264': 'AMD AMF (hardware)',
    'nvenc': 'NVENC (hardware)',
    'jim_nvenc': 'NVENC (hardware)',
    'ffmpeg_nvenc': 'NVENC (hardware)',
    'qsv': 'QuickSync (hardware)',
    'obs_qsv11': 'QuickSync (hardware)',
}


# ─────────────────────────────────────────────
# UTILIDADES DE SISTEMA
# ─────────────────────────────────────────────

def _run_powershell(script, timeout=8):
    """Ejecuta un comando de PowerShell y devuelve stdout (o '' si falla)."""
    try:
        proc = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_NO_WINDOW
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


def get_windows_label():
    """Devuelve una etiqueta legible del SO (distingue Win10 de Win11 por build)."""
    if platform.system() != 'Windows':
        return f"{platform.system()} {platform.release()}"
    try:
        build = int(platform.version().split('.')[-1])
        name = 'Windows 11' if build >= 22000 else 'Windows 10'
        return f"{name} (build {build})"
    except Exception:
        return f"Windows {platform.release()}"


# ─────────────────────────────────────────────
# DETECCIÓN DE HARDWARE
# ─────────────────────────────────────────────

def get_cpu_info():
    """Obtiene nombre y núcleos del CPU (CIM con fallback a wmic)."""
    info = {'name': 'Desconocido', 'cores': os.cpu_count() or 2, 'threads': os.cpu_count() or 2}
    if platform.system() != 'Windows':
        return info

    # Preferido: PowerShell/CIM (wmic está obsoleto)
    data = _parse_json(_run_powershell(
        "Get-CimInstance Win32_Processor | "
        "Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | "
        "ConvertTo-Json -Compress"
    ))
    if data:
        if isinstance(data, list):
            data = data[0]
        name = (data.get('Name') or '').strip()
        if name:
            info['name'] = name
        try:
            info['cores'] = int(data.get('NumberOfCores') or info['cores'])
            info['threads'] = int(data.get('NumberOfLogicalProcessors') or info['threads'])
        except Exception:
            pass
        return info

    # Fallback: wmic (sistemas antiguos)
    try:
        result = subprocess.run(
            ['wmic', 'cpu', 'get', 'Name,NumberOfCores,NumberOfLogicalProcessors', '/format:csv'],
            capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line or 'Name' in line and 'Node' in line:
                continue
            parts = line.split(',')
            if len(parts) >= 4 and parts[2].strip().isdigit():
                info['name'] = parts[1].strip()
                info['cores'] = int(parts[2].strip())
                info['threads'] = int(parts[3].strip())
                break
    except Exception:
        pass
    return info


def get_ram_gb():
    """Obtiene RAM total en GB (CIM con fallback a wmic)."""
    if platform.system() != 'Windows':
        return 8.0

    out = _run_powershell("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
    try:
        if out:
            return round(int(out.strip()) / (1024 ** 3), 1)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ['wmic', 'computersystem', 'get', 'TotalPhysicalMemory', '/format:csv'],
            capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line and 'TotalPhysicalMemory' not in line and 'Node' not in line:
                parts = line.split(',')
                if len(parts) >= 2 and parts[-1].strip().isdigit():
                    return round(int(parts[-1].strip()) / (1024 ** 3), 1)
    except Exception:
        pass
    return 8.0


def get_gpu_info():
    """
    Detecta las GPUs y determina el fabricante para elegir encoder por hardware.
    Filtra adaptadores virtuales (Parsec, Virtual, Basic Display, etc.).
    """
    result = {'names': [], 'all': [], 'vendor': 'none', 'has_hw_encoder': False}
    if platform.system() != 'Windows':
        return result

    data = _parse_json(_run_powershell(
        "Get-CimInstance Win32_VideoController | Select-Object Name | ConvertTo-Json -Compress"
    ))
    if isinstance(data, dict):
        data = [data]

    names = []
    if isinstance(data, list):
        for d in data:
            n = (d.get('Name') or '').strip()
            if n:
                names.append(n)

    result['all'] = names[:]

    # Descartar adaptadores virtuales o sin driver real (no sirven para codificar).
    # En una PC sin gráfica suele quedar solo "Microsoft Basic Display Adapter".
    skip_kw = ['parsec', 'virtual', 'basic display', 'basic render', 'remote',
               'idd', 'meta ', 'oray', 'sunshine', 'citrix', 'hyper-v', 'displaylink']
    real = [n for n in names if not any(v in n.lower() for v in skip_kw)]
    result['names'] = real  # solo GPUs reales/usables (vacío = sin gráfica → x264 CPU)

    joined = ' '.join(real).lower()
    if any(k in joined for k in ['nvidia', 'geforce', 'rtx', 'gtx', 'quadro']):
        result['vendor'] = 'nvidia'
        result['has_hw_encoder'] = True
    elif any(k in joined for k in ['radeon', 'amd', ' rx ']):
        result['vendor'] = 'amd'
        result['has_hw_encoder'] = True
    elif 'intel' in joined:
        result['vendor'] = 'intel'
        result['has_hw_encoder'] = True
    # Sin coincidencias → vendor='none' → se usará x264 por CPU (PC sin gráfica)

    return result


def get_screen_resolution():
    """
    Resolución nativa del monitor principal (ancho, alto). Usa la resolución
    real en píxeles (no la escalada por DPI). Fallback: 1920x1080.
    """
    if platform.system() == 'Windows':
        data = _parse_json(_run_powershell(
            "Get-CimInstance Win32_VideoController | "
            "Where-Object { $_.CurrentHorizontalResolution -and $_.CurrentVerticalResolution } | "
            "Select-Object CurrentHorizontalResolution,CurrentVerticalResolution | ConvertTo-Json -Compress"
        ))
        if isinstance(data, dict):
            data = [data]
        best = None
        if isinstance(data, list):
            for d in data:
                try:
                    w = int(d.get('CurrentHorizontalResolution'))
                    h = int(d.get('CurrentVerticalResolution'))
                except (TypeError, ValueError):
                    continue
                # El monitor principal suele ser el de mayor área
                if w > 0 and h > 0 and (best is None or w * h > best[0] * best[1]):
                    best = (w, h)
        if best:
            return best
    return (1920, 1080)


def _even(n):
    """Redondea a entero par (los encoders de video requieren dimensiones pares)."""
    n = int(round(n))
    return n - (n % 2)


def scaled_output(native_w, native_h, desired_h):
    """
    Escala la salida a una altura estándar (≤ nativa, sin upscaling),
    conservando la relación de aspecto del monitor.
    """
    aspect = native_w / native_h if native_h else (16 / 9)
    cap = min(desired_h, native_h)
    out_h = next((s for s in (1080, 900, 720, 480, 360) if s <= cap), cap)
    return _even(out_h * aspect), _even(out_h)


def measure_upload_speed(status_callback=None):
    """
    Mide la velocidad de subida. Intenta speedtest-cli; si no está, sube
    datos a un endpoint de Cloudflare; como último recurso estima desde la bajada.
    """
    if status_callback:
        status_callback("Midiendo velocidad de upload...")

    try:
        result = subprocess.run(
            ['speedtest-cli', '--simple', '--no-download'],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if 'Upload' in line:
                    val = float(line.split(':')[1].strip().split()[0])
                    return round(val, 1)
    except Exception:
        pass

    # Subida real a Cloudflare
    try:
        data = b'0' * (2 * 1024 * 1024)  # 2 MB
        start = time.time()
        req = urllib.request.Request(
            'https://speed.cloudflare.com/__up',
            data=data, method='POST',
            headers={'Content-Type': 'application/octet-stream', 'Content-Length': str(len(data))}
        )
        urllib.request.urlopen(req, timeout=15)
        elapsed = time.time() - start
        if elapsed > 0:
            return round((len(data) * 8) / (elapsed * 1_000_000), 1)
    except Exception:
        pass

    # Estimación desde la bajada (conexiones asimétricas: ~40%)
    try:
        start = time.time()
        urllib.request.urlopen('https://speed.cloudflare.com/__down?bytes=2000000', timeout=15).read()
        elapsed = time.time() - start
        down = (2_000_000 * 8) / (elapsed * 1_000_000)
        return round(down * 0.4, 1)
    except Exception:
        pass

    return 5.0  # Valor conservador por defecto


# ─────────────────────────────────────────────
# LÓGICA DE CONFIGURACIÓN OBS
# ─────────────────────────────────────────────

def select_encoder(gpu_info, threads):
    """
    Elige el encoder según la GPU disponible. Prioriza hardware (libera el CPU).
    Devuelve los IDs correctos para modo Simple y Avanzado de OBS.
    """
    vendor = (gpu_info or {}).get('vendor', 'none')

    if vendor == 'nvidia':
        return {
            'type': 'hardware', 'vendor': 'nvidia',
            'label': 'NVENC (hardware NVIDIA)',
            'simple_value': 'nvenc', 'adv_value': 'jim_nvenc',
            'preset_simple_key': 'NVENCPreset2', 'preset_value': 'p5', 'preset_human': 'P5 (calidad)',
            'reason': 'GPU NVIDIA detectada — NVENC descarga la codificación del CPU y mejora la calidad frente a x264.',
        }
    if vendor == 'amd':
        return {
            'type': 'hardware', 'vendor': 'amd',
            'label': 'AMD AMF (hardware)',
            'simple_value': 'amd', 'adv_value': 'h264_texture_amf',
            'preset_simple_key': 'AMDPreset', 'preset_value': 'quality', 'preset_human': 'Calidad',
            'reason': 'GPU AMD detectada — el encoder AMF (hardware) libera el CPU y mantiene el stream estable.',
        }
    if vendor == 'intel':
        return {
            'type': 'hardware', 'vendor': 'intel',
            'label': 'QuickSync (hardware Intel)',
            'simple_value': 'qsv', 'adv_value': 'obs_qsv11',
            'preset_simple_key': 'QSVPreset', 'preset_value': 'quality', 'preset_human': 'Calidad',
            'reason': 'iGPU Intel detectada — QuickSync codifica por hardware y libera el CPU.',
        }

    # Sin GPU con encoder: x264 según hilos
    if threads >= 16:
        p = 'fast'
    elif threads >= 8:
        p = 'veryfast'
    elif threads >= 4:
        p = 'superfast'
    else:
        p = 'ultrafast'
    return {
        'type': 'software', 'vendor': 'cpu',
        'label': 'x264 (CPU/software)',
        'simple_value': 'x264', 'adv_value': 'obs_x264',
        'preset_simple_key': 'Preset', 'preset_value': p, 'preset_human': p,
        'reason': f'Sin encoder por hardware — x264 con preset "{p}" según {threads} hilos.',
    }


def calculate_obs_settings(cpu_info, ram_gb, upload_mbps, gpu_info=None,
                           screen_res=None, target_res='auto'):
    """
    Calcula la configuración óptima de OBS. Prioriza estabilidad.

    El lienzo base se ajusta a la resolución nativa del monitor y la salida
    se escala según el monitor, el upload y el encoder:
      target_res='auto'  → elige la mejor salida sostenible (por defecto)
      target_res='1080p' / '720p' → fuerza esa altura (igual limitada a lo nativo)
    """
    threads = cpu_info['threads']
    encoder = select_encoder(gpu_info, threads)
    is_hw = encoder['type'] == 'hardware'

    # ── Lienzo base = resolución nativa del monitor ──
    base_width, base_height = screen_res or (1920, 1080)

    # ── Altura de salida deseada ──
    if target_res == '1080p':
        desired_h = 1080
    elif target_res == '720p':
        desired_h = 720
    else:  # auto: según upload (y encoder por hardware para 1080p)
        if upload_mbps >= 6 and is_hw:
            desired_h = 1080
        elif upload_mbps >= 4.5:
            desired_h = 900
        elif upload_mbps >= 3:
            desired_h = 720
        else:
            desired_h = 480

    out_width, out_height = scaled_output(base_width, base_height, desired_h)

    # ── FPS adaptativo (30/60) ──
    # 60 fps necesita más cómputo y ~30% más de bitrate. Solo se recomienda con
    # margen de subida, y con x264 (CPU) además con un procesador potente.
    can_60 = upload_mbps >= 4.5
    if is_hw:
        fps = 60 if can_60 else 30
    else:
        fps = 60 if (can_60 and threads >= 12) else 30
    if out_height >= 1080 and upload_mbps < 6:
        fps = 30  # 1080p60 exige bastante upload

    # ── Bitrate de video según resolución y FPS ──
    # (Twitch recomienda máx 6000; usamos ~80% del upload como techo)
    max_bitrate = min(int(upload_mbps * 1000 * 0.80), 6000)
    if out_height >= 1080:
        low, high = 3500, 6000
    elif out_height >= 900:
        low, high = 3000, 5000
    elif out_height >= 720:
        low, high = 2000, 3500
    else:
        low, high = 800, 2000
    if fps == 60:  # 60 fps pide más bitrate para la misma calidad
        low = int(low * 1.2)
        high = min(int(high * 1.3), 6000)
    recommended_bitrate = max(low, min(max_bitrate, high))

    audio_bitrate = 160          # buena calidad, bajo consumo
    audio_sample_rate = 48000    # Twitch usa 48 kHz
    profile = 'main'             # compatible con todos los dispositivos
    scale_type = 'lanczos'       # mejor nitidez al reescalar el lienzo a la salida

    res_reason = (
        f"Monitor {base_width}x{base_height} → salida {out_width}x{out_height} @ {fps} FPS "
        f"(según upload {upload_mbps} Mbps y encoder {'hardware' if is_hw else 'CPU'})."
    )

    # ── Advertencias / sugerencias ──
    warnings = []
    if upload_mbps < 3:
        warnings.append("⚠ Upload bajo (<3 Mbps): posible pixelado. Se recomienda resolución reducida.")
    if encoder['type'] == 'software' and threads < 4:
        warnings.append("⚠ CPU con pocos hilos y sin GPU para codificar: usa ultrafast y cierra todo antes de streamear.")
    if ram_gb < 6:
        warnings.append("⚠ Poca RAM (<6 GB): cierra el navegador y otras apps durante el stream.")
    if recommended_bitrate < 2000:
        warnings.append("⚠ Bitrate muy bajo: la calidad será limitada pero estable.")
    if out_height < base_height:
        warnings.append(
            f"ℹ Tu monitor es {base_width}x{base_height}; se transmitirá escalado a "
            f"{out_width}x{out_height} para ahorrar ancho de banda y CPU/GPU."
        )
    if fps == 60:
        warnings.append("ℹ 60 FPS recomendado: tienes margen de upload (y CPU/GPU) suficiente.")
    elif not is_hw and threads < 12:
        warnings.append("ℹ 30 FPS por estabilidad: a 60 FPS x264 saturaría tu CPU.")

    return {
        'base_width': base_width,
        'base_height': base_height,
        'output_width': out_width,
        'output_height': out_height,
        'fps': fps,
        'video_bitrate': recommended_bitrate,
        'audio_bitrate': audio_bitrate,
        'audio_sample_rate': audio_sample_rate,
        'encoder': encoder,
        'profile': profile,
        'scale_type': scale_type,
        'rate_control': 'CBR',          # requerido por Twitch
        'keyframe_interval': 2,         # requerido por Twitch
        'resolution_reason': res_reason,
        'warnings': warnings,
        'upload_mbps': upload_mbps,
        'cpu_threads': threads,
        'ram_gb': ram_gb,
        'gpu_names': (gpu_info or {}).get('names', []),
        'gpu_vendor': (gpu_info or {}).get('vendor', 'none'),
    }


# ─────────────────────────────────────────────
# LEER / APLICAR CONFIGURACIÓN DE OBS
# ─────────────────────────────────────────────

def find_obs_config_path():
    """Busca la carpeta de configuración de OBS en Windows."""
    candidates = [
        Path.home() / 'AppData' / 'Roaming' / 'obs-studio',
        Path('C:/Program Files/obs-studio/config/obs-studio'),
        Path('C:/Program Files (x86)/obs-studio/config/obs-studio'),
    ]
    for path in candidates:
        if path.exists():
            return path

    roaming = Path.home() / 'AppData' / 'Roaming'
    found = list(roaming.glob('**/obs-studio'))
    return found[0] if found else None


def get_obs_basic_ini_path():
    """Resuelve la ruta al basic.ini del perfil de OBS más probable."""
    obs_path = find_obs_config_path()
    if not obs_path:
        return None, "No se encontró la carpeta de configuración de OBS."

    profiles_path = obs_path / 'basic' / 'profiles'
    if not profiles_path.exists():
        return None, f"No se encontró la carpeta de perfiles de OBS en:\n{profiles_path}"

    profiles = [p for p in profiles_path.iterdir() if p.is_dir()]
    if not profiles:
        return None, "No hay perfiles de OBS creados. Abre OBS primero."

    profile_path = None
    for p in profiles:
        if 'untitled' in p.name.lower() or 'default' in p.name.lower():
            profile_path = p
            break
    if not profile_path:
        profile_path = profiles[0]

    basic_ini = profile_path / 'basic.ini'
    if not basic_ini.exists():
        return None, f"No se encontró basic.ini en:\n{profile_path}"

    return basic_ini, None


def parse_ini_to_sections(text):
    """Parsea texto INI a un diccionario por secciones."""
    sections = {}
    current_section = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(';') or line.startswith('#'):
            continue
        if line.startswith('[') and line.endswith(']'):
            current_section = line[1:-1].strip()
            sections.setdefault(current_section, {})
            continue
        if '=' in line and current_section:
            key, value = line.split('=', 1)
            sections[current_section][key.strip()] = value.strip()
    return sections


def read_obs_current_config():
    """Lee la configuración actual de OBS desde basic.ini."""
    basic_ini, error = get_obs_basic_ini_path()
    if error:
        return None, error

    try:
        with open(basic_ini, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return None, f"No se pudo leer basic.ini: {e}"

    sections = parse_ini_to_sections(content)
    output = sections.get('Output', {})
    video = sections.get('Video', {})
    audio = sections.get('Audio', {})
    simple = sections.get('SimpleOutput', {})
    advout = sections.get('AdvOut', {})

    def to_int(value):
        try:
            return int(str(value).strip())
        except Exception:
            return None

    mode = (output.get('Mode') or 'Simple').strip()
    is_advanced = mode.lower().startswith('adv')

    if is_advanced:
        encoder_id = advout.get('Encoder')
        # En modo Avanzado el bitrate del stream vive en otro archivo, no en basic.ini
        video_bitrate = None
        preset = advout.get('Preset') or advout.get('AMDPreset') or advout.get('NVENCPreset')
    else:
        encoder_id = simple.get('StreamEncoder')
        video_bitrate = to_int(simple.get('VBitrate'))
        preset = (simple.get('Preset') or simple.get('AMDPreset')
                  or simple.get('NVENCPreset2') or simple.get('NVENCPreset')
                  or simple.get('QSVPreset'))

    audio_bitrate = to_int(simple.get('ABitrate')) or to_int(advout.get('FFABitrate'))

    current = {
        'source_file': str(basic_ini),
        'mode': mode,
        'base_width': to_int(video.get('BaseCX')),
        'base_height': to_int(video.get('BaseCY')),
        'output_width': to_int(video.get('OutputCX')),
        'output_height': to_int(video.get('OutputCY')),
        'fps': to_int(video.get('FPSCommon')),
        'video_bitrate': video_bitrate,
        'audio_bitrate': audio_bitrate,
        'encoder_id': encoder_id,
        'encoder_label': ENCODER_LABELS.get((encoder_id or '').strip(), encoder_id),
        'preset': preset,
        'audio_sample_rate': to_int(audio.get('SampleRate')),
        'scale_type': (video.get('ScaleType') or '').strip().lower() or None,
    }
    return current, None


def _encoder_family(enc_id):
    """Normaliza un ID de encoder a su 'familia' legible para comparar."""
    e = (enc_id or '').lower()
    if 'amf' in e or e == 'amd' or e.startswith('amd_'):
        return 'AMD AMF (hardware)'
    if 'nvenc' in e:
        return 'NVENC (hardware)'
    if 'qsv' in e:
        return 'QuickSync (hardware)'
    if 'x264' in e:
        return 'x264 (CPU)'
    return enc_id or 'desconocido'


def build_improvement_list(current_obs, recommended):
    """Compara configuración actual vs recomendada y sugiere mejoras."""
    if not current_obs:
        return ["No se detectó configuración actual de OBS para comparar."]

    improvements = []
    enc = recommended['encoder']

    cur_base = (current_obs.get('base_width'), current_obs.get('base_height'))
    rec_base = (recommended['base_width'], recommended['base_height'])
    if None not in cur_base and cur_base != rec_base:
        improvements.append(
            f"Lienzo base: {cur_base[0]}x{cur_base[1]} → {rec_base[0]}x{rec_base[1]} "
            f"(igualar a la resolución de tu monitor)."
        )

    cur_out = (current_obs.get('output_width'), current_obs.get('output_height'))
    rec_out = (recommended['output_width'], recommended['output_height'])
    if None not in cur_out and cur_out != rec_out:
        improvements.append(
            f"Resolución de salida: {cur_out[0]}x{cur_out[1]} → {rec_out[0]}x{rec_out[1]}."
        )

    if current_obs.get('fps') and current_obs.get('fps') != recommended['fps']:
        improvements.append(f"FPS: {current_obs.get('fps')} → {recommended['fps']}.")

    cur_br = current_obs.get('video_bitrate')
    if cur_br is not None and abs(cur_br - recommended['video_bitrate']) > 150:
        verbo = "subir" if cur_br < recommended['video_bitrate'] else "bajar"
        improvements.append(
            f"Bitrate de video: {verbo} de {cur_br} → {recommended['video_bitrate']} kb/s."
        )

    cur_ab = current_obs.get('audio_bitrate')
    if cur_ab is not None and cur_ab != recommended['audio_bitrate']:
        improvements.append(f"Bitrate de audio: {cur_ab} → {recommended['audio_bitrate']} kb/s.")

    cur_sr = current_obs.get('audio_sample_rate')
    if cur_sr is not None and cur_sr != recommended['audio_sample_rate']:
        improvements.append(
            f"Frecuencia de audio: {cur_sr} Hz → {recommended['audio_sample_rate']} Hz (Twitch usa 48 kHz)."
        )

    cur_scale = current_obs.get('scale_type')
    if cur_scale is not None and cur_scale != recommended['scale_type']:
        improvements.append(
            f"Filtro de reescalado: {cur_scale} → {recommended['scale_type']} (más nitidez)."
        )

    cur_fam = _encoder_family(current_obs.get('encoder_id'))
    rec_fam = _encoder_family(enc['simple_value'])
    if cur_fam != rec_fam:
        extra = " (¡aprovecha tu GPU!)" if enc['type'] == 'hardware' else ""
        improvements.append(f"Encoder: {cur_fam} → {rec_fam}{extra}.")

    if (current_obs.get('mode') or '').lower().startswith('adv'):
        improvements.append(
            "Estás en modo Salida 'Avanzado': verifica que el Control de tasa sea CBR "
            "y el intervalo de keyframe = 2 s (requisitos de Twitch)."
        )

    if not improvements:
        improvements.append("Tu configuración actual ya coincide con la recomendada para este equipo. ✓")

    return improvements


def apply_obs_config(settings):
    """Aplica la configuración a basic.ini de OBS (con backup previo)."""
    basic_ini, error = get_obs_basic_ini_path()
    if error:
        return False, f"{error}\nAsegúrate de haber abierto OBS al menos una vez."

    backup_path = basic_ini.with_suffix('.ini.bak')
    shutil.copy2(basic_ini, backup_path)

    with open(basic_ini, 'r', encoding='utf-8') as f:
        content = f.read()

    def set_ini_value(text, section, key, value):
        import re
        section_pattern = re.compile(rf'^\[{re.escape(section)}\]', re.MULTILINE)
        match = section_pattern.search(text)
        if match:
            key_pattern = re.compile(rf'^{re.escape(key)}=.*$', re.MULTILINE)
            section_start = match.end()
            next_section = re.search(r'^\[', text[section_start:], re.MULTILINE)
            section_end = section_start + next_section.start() if next_section else len(text)
            section_body = text[section_start:section_end]
            if key_pattern.search(section_body):
                new_body = key_pattern.sub(f'{key}={value}', section_body)
                return text[:section_start] + new_body + text[section_end:]
            insert_pos = section_start + len(section_body.rstrip('\n'))
            return text[:insert_pos] + f'\n{key}={value}' + text[insert_pos:]
        return text + f'\n[{section}]\n{key}={value}\n'

    enc = settings['encoder']
    changes = {
        ('Output', 'Mode'): 'Simple',
        ('Video', 'BaseCX'): str(settings['base_width']),
        ('Video', 'BaseCY'): str(settings['base_height']),
        ('Video', 'OutputCX'): str(settings['output_width']),
        ('Video', 'OutputCY'): str(settings['output_height']),
        ('Video', 'FPSType'): '0',
        ('Video', 'FPSCommon'): str(settings['fps']),
        ('Video', 'ScaleType'): settings['scale_type'],
        ('Audio', 'SampleRate'): str(settings['audio_sample_rate']),
        ('SimpleOutput', 'VBitrate'): str(settings['video_bitrate']),
        ('SimpleOutput', 'ABitrate'): str(settings['audio_bitrate']),
        ('SimpleOutput', 'StreamEncoder'): enc['simple_value'],
        ('SimpleOutput', enc['preset_simple_key']): enc['preset_value'],
    }

    for (section, key), value in changes.items():
        content = set_ini_value(content, section, key, value)

    with open(basic_ini, 'w', encoding='utf-8') as f:
        f.write(content)

    return True, f"Configuración aplicada en:\n{basic_ini}\nBackup guardado en:\n{backup_path}"


# ─────────────────────────────────────────────
# SERVICIO TWITCH (service.json)
# ─────────────────────────────────────────────

def get_obs_service_path():
    """Ruta al service.json del perfil de OBS (junto a basic.ini)."""
    basic_ini, error = get_obs_basic_ini_path()
    if error:
        return None, error
    return basic_ini.parent / 'service.json', None


def read_obs_service():
    """Lee la configuración de servicio (Twitch) actual de OBS."""
    service_path, error = get_obs_service_path()
    if error:
        return None, error

    if not service_path.exists():
        return {'exists': False, 'service': None, 'server': None,
                'has_key': False, 'path': str(service_path)}, None

    try:
        data = json.loads(service_path.read_text(encoding='utf-8'))
    except Exception as e:
        return None, f"No se pudo leer service.json: {e}"

    settings = data.get('settings', {}) or {}
    return {
        'exists': True,
        'service': settings.get('service'),
        'server': settings.get('server'),
        'has_key': bool(settings.get('key')),
        'path': str(service_path),
    }, None


def fetch_twitch_ingests(timeout=8):
    """
    Lista de servidores de ingest de Twitch como [(nombre, server_url)].
    Siempre incluye 'Auto' primero. Si falla la red, solo devuelve Auto.
    """
    servers = [('Auto (recomendado)', 'auto')]
    try:
        with urllib.request.urlopen('https://ingest.twitch.tv/ingests', timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
        for ing in data.get('ingests', []):
            name = ing.get('name')
            tmpl = ing.get('url_template') or ''
            url = tmpl.split('/{stream_key}')[0] if tmpl else None
            if name and url:
                servers.append((name, url))
    except Exception:
        pass
    return servers


def apply_twitch_service(server='auto', stream_key=None):
    """
    Configura OBS para transmitir a Twitch (service.json), con backup.
    Si stream_key es None/vacío, conserva la clave existente.
    """
    service_path, error = get_obs_service_path()
    if error:
        return False, error

    data = {}
    if service_path.exists():
        try:
            data = json.loads(service_path.read_text(encoding='utf-8'))
        except Exception:
            data = {}
        try:
            shutil.copy2(service_path, service_path.with_suffix('.json.bak'))
        except Exception:
            pass

    data['type'] = 'rtmp_common'
    settings = data.get('settings', {}) or {}
    settings['service'] = 'Twitch'
    settings['server'] = server or 'auto'
    settings.setdefault('bwtest', False)
    if stream_key:
        settings['key'] = stream_key
    data['settings'] = settings

    try:
        service_path.write_text(json.dumps(data, indent=4), encoding='utf-8')
    except Exception as e:
        return False, f"No se pudo escribir service.json: {e}"

    if stream_key:
        key_msg = "clave actualizada"
    elif settings.get('key'):
        key_msg = "clave existente conservada"
    else:
        key_msg = "sin clave (pégala en OBS → Ajustes → Emisión)"
    return True, f"Twitch configurado (servidor: {server}, {key_msg})."


# ─────────────────────────────────────────────
# FORMATEAR CONFIGURACIÓN PARA MOSTRAR
# ─────────────────────────────────────────────

def format_settings_text(settings, cpu_info, current_obs=None, improvements=None):
    enc = settings['encoder']
    if settings['gpu_names']:
        gpu_text = ', '.join(settings['gpu_names'])
    else:
        gpu_text = 'Ninguna usable → codificación por CPU (x264)'

    lines = [
        "╔══════════════════════════════════════════════════════╗",
        "║        CONFIGURACIÓN ÓPTIMA DE OBS PARA TWITCH       ║",
        "╚══════════════════════════════════════════════════════╝",
        "",
        "── SPECS DETECTADAS ──────────────────────────────────",
        f"  SO:      {get_windows_label()}",
        f"  CPU:     {cpu_info['name']}",
        f"  Hilos:   {settings['cpu_threads']}",
        f"  RAM:     {settings['ram_gb']} GB",
        f"  GPU:     {gpu_text}",
        f"  Monitor: {settings['base_width']}x{settings['base_height']}",
        f"  Upload:  {settings['upload_mbps']} Mbps",
        "",
        "── CONFIGURACIÓN DE VIDEO ────────────────────────────",
        f"  Resolución base:    {settings['base_width']}x{settings['base_height']} (lienzo = monitor)",
        f"  Resolución salida:  {settings['output_width']}x{settings['output_height']}",
        f"  Motivo resolución:  {settings['resolution_reason']}",
        f"  FPS:                {settings['fps']}",
        f"  Bitrate video:      {settings['video_bitrate']} kb/s",
        f"  Control de tasa:    {settings['rate_control']} (requerido Twitch)",
        f"  Filtro reescalado:  {settings['scale_type'].capitalize()} (mejor nitidez)",
        "",
        "── ENCODING ──────────────────────────────────────────",
        f"  Encoder:            {enc['label']}",
        f"  Preset:             {enc['preset_human']}",
        f"  Razón:              {enc['reason']}",
        f"  Perfil:             {settings['profile']}",
        f"  Keyframe interval:  {settings['keyframe_interval']}s (requerido Twitch)",
        "",
        "── AUDIO ─────────────────────────────────────────────",
        f"  Bitrate audio:      {settings['audio_bitrate']} kb/s",
        f"  Frecuencia:         {settings['audio_sample_rate']} Hz (Twitch usa 48 kHz)",
        f"  Codec:              AAC",
        "",
        "── CÓMO APLICAR EN OBS (si no se aplicó automático) ──",
        "  Configuración → Salida → Modo Simple:",
        f"    • Bitrate de video: {settings['video_bitrate']}",
        f"    • Encoder: {enc['label']}",
        f"    • Preset/Calidad: {enc['preset_human']}",
        "  Configuración → Video:",
        f"    • Resolución base (lienzo): {settings['base_width']}x{settings['base_height']}",
        f"    • Resolución de salida: {settings['output_width']}x{settings['output_height']}",
        f"    • FPS: {settings['fps']}",
        "  Configuración → Audio:",
        f"    • Frecuencia de muestreo: {settings['audio_sample_rate']} Hz",
        f"    • Bitrate (pista 1): {settings['audio_bitrate']}",
    ]

    if current_obs:
        br = current_obs.get('video_bitrate')
        br_text = f"{br} kb/s" if br is not None else "(no visible en modo Avanzado)"
        lines += [
            "",
            "── CONFIGURACIÓN ACTUAL DETECTADA EN OBS ─────────────",
            f"  Archivo:            {current_obs.get('source_file')}",
            f"  Modo de salida:     {current_obs.get('mode')}",
            f"  Resolución base:    {current_obs.get('base_width')}x{current_obs.get('base_height')}",
            f"  Resolución salida:  {current_obs.get('output_width')}x{current_obs.get('output_height')}",
            f"  FPS:                {current_obs.get('fps')}",
            f"  Bitrate video:      {br_text}",
            f"  Bitrate audio:      {current_obs.get('audio_bitrate')} kb/s",
            f"  Frecuencia audio:   {current_obs.get('audio_sample_rate')} Hz",
            f"  Filtro reescalado:  {current_obs.get('scale_type')}",
            f"  Encoder:            {current_obs.get('encoder_label')}",
            f"  Preset:             {current_obs.get('preset')}",
        ]

    if improvements:
        lines += ["", "── QUÉ DEBES MEJORAR ─────────────────────────────────"]
        for item in improvements:
            lines.append(f"  • {item}")

    if settings['warnings']:
        lines += ["", "── ADVERTENCIAS / NOTAS ──────────────────────────────"]
        for w in settings['warnings']:
            lines.append(f"  {w}")

    lines += ["", "═" * 56]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
# La interfaz gráfica está en interfaz.py. Ejecutar este archivo lanza la GUI
# por comodidad; también puedes ejecutar directamente `python interfaz.py`.

if __name__ == '__main__':
    from interfaz import main
    main()

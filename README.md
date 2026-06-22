# OBS Auto-Configurator para Twitch

Aplicación de escritorio que **detecta el hardware de tu PC, mide tu velocidad de subida y calcula la configuración óptima de OBS para transmitir en Twitch** — y opcionalmente la aplica por ti. Pensada para que cualquiera tenga un stream estable sin saber de bitrates, presets ni encoders.

## ⬇️ Descarga rápida (no necesita Python)

**[Descargar OBS-AutoConfig.exe](https://github.com/KevinT9/OBS-AutoConfig/releases/latest/download/OBS-AutoConfig.exe)**

Un único `.exe` autocontenido. Compatible con **Windows 10 y 11 (64 bits)**. No requiere instalar Python ni permisos de administrador.

> Al no estar firmado digitalmente, Windows SmartScreen puede mostrar *"Windows protegió tu PC"*. Pulsa **Más información → Ejecutar de todas formas**. Si prefieres, puedes [compilarlo tú mismo](#-compilar-desde-el-código) desde el código fuente.

## ✨ Características

- 🔎 **Detecta tu hardware** (CPU, RAM y GPU) vía PowerShell/CIM — funciona en Windows 10 y 11.
- 🖼️ **Detecta la resolución de tu monitor** y adapta la salida automáticamente (sin upscaling y respetando el aspecto).
- 🎮 **Elige el mejor encoder automáticamente**: usa el **encoder por hardware** de tu GPU (NVENC/AMF/QuickSync) cuando está disponible, o **x264 por CPU** si no hay gráfica.
- 📶 **Mide tu velocidad de subida** real y ajusta el bitrate sin pasarte del límite de Twitch.
- 📋 **Lee tu configuración actual de OBS** y te dice exactamente **qué deberías mejorar**.
- ⚙️ **Aplica la configuración por ti** (con backup automático de `basic.ini`), o te da los valores para ponerlos a mano.
- 🎮 **Configura Twitch en OBS**: deja el servicio listo (servidor de ingest + stream key opcional) escribiendo `service.json`, con backup.
- 🚀 **Optimiza la PC para streaming** (módulo aparte): cierra apps pesadas, plan de energía Alto rendimiento, Modo Juego, HAGS, silenciar notificaciones y prioridad de OBS — todo **reversible** con un botón.
- 🖥️ Interfaz simple: un botón para analizar, copiar o aplicar.

## 🚀 Uso

1. Descarga y ejecuta `OBS-AutoConfig.exe`.
2. Pulsa **▶ ANALIZAR Y CONFIGURAR**. Verás tus specs, la configuración recomendada y qué mejorar.
3. (Opcional) Pulsa **✓ APLICAR A OBS** para escribirla directamente. *OBS debe estar cerrado.*
   - También puedes pulsar **⎘ COPIAR** y ajustar los valores manualmente en OBS.

> Al aplicar, se crea un respaldo `basic.ini.bak` en tu perfil de OBS por si quieres revertir.

## 🎮 Configurar Twitch en OBS

Pulsa **🎮 Twitch** (arriba a la derecha) para dejar OBS listo para transmitir:

- **Servidor de ingest**: elige *Auto* (recomendado) o un servidor concreto de la lista oficial de Twitch.
- **Stream key** (opcional): pégala para configurarla de una vez; si la dejas vacía, se conserva la que ya tuvieras.

Se escribe en el `service.json` del perfil de OBS (con backup `.json.bak`). **Cierra OBS antes de aplicar.** Tu clave la obtienes en `dashboard.twitch.tv → Ajustes → Transmisión`.

## 🚀 Optimizar la PC para streaming

Pulsa **🚀 Optimizar PC** (arriba a la derecha) para abrir el optimizador. Pulsa **Analizar estado** para ver un diagnóstico y marca lo que quieras aplicar:

| Optimización | Qué hace |
|---|---|
| **Cerrar apps pesadas** | Detecta navegador, Discord, launchers… y los cierra para liberar RAM/CPU |
| **Prioridad de OBS** | Sube la prioridad del proceso de OBS si está abierto |
| **Plan de energía** | Activa *Alto rendimiento* para que la CPU no haga *throttling* |
| **Modo Juego** | Activa el Modo Juego de Windows |
| **HAGS** | Programación de GPU acelerada por hardware (requiere admin + reinicio) |
| **Silenciar notificaciones** | Evita popups durante el directo |

Todos los cambios se guardan y puedes deshacerlos con **↩ Restaurar** al terminar el stream. No borra archivos ni desactiva servicios. También puedes ejecutar el optimizador por separado con `python optimizar.py`.

## 🧠 Cómo decide el encoder

| GPU detectada | Encoder recomendado | Por qué |
|---|---|---|
| NVIDIA (GeForce/RTX/GTX) | **NVENC** (hardware) | Libera el CPU, mejor calidad que x264 |
| AMD (Radeon) | **AMF** (hardware) | Codifica en la GPU, stream más estable |
| Intel (iGPU) | **QuickSync** (hardware) | Codifica en la iGPU, libera el CPU |
| Sin gráfica usable | **x264** (CPU) | Preset según los hilos del procesador |

## 🖼️ Cómo decide la resolución

El **lienzo base** de OBS se ajusta a la resolución nativa de tu monitor, y la **resolución de salida** se escala según tu monitor, tu velocidad de subida y el encoder — siempre conservando la relación de aspecto y **sin nunca hacer upscaling**:

| Velocidad de subida | Salida (auto) | Notas |
|---|---|---|
| ≥ 6 Mbps + encoder por hardware | **1080p** | Con x264 (CPU) se limita a 900p para no saturar |
| ≥ 4.5 Mbps | **900p** | |
| ≥ 3 Mbps | **720p** | |
| < 3 Mbps | **480p** | Prioriza estabilidad |

La salida nunca supera la resolución de tu monitor (p. ej. un portátil 1366×768 transmite a 720p, no se fuerza a más). El **bitrate** se calcula a partir de la subida medida (≈80% del upload, tope de 6000 kb/s como recomienda Twitch) y se adapta al nivel de resolución elegido. Se prioriza la **estabilidad** sobre la calidad máxima, con **30 FPS** por defecto.

## 🛠️ Compilar desde el código

Si prefieres generar el `.exe` tú mismo (o modificar el programa), consulta **[README_BUILD.md](README_BUILD.md)**. En resumen:

```powershell
python -m pip install -r requirements.txt
python -m PyInstaller --onefile --windowed --name OBS-AutoConfig --clean --noconfirm --hidden-import interfaz --hidden-import optimizar escanear.py
# Resultado: dist\OBS-AutoConfig.exe
```

También puedes ejecutarlo directamente con Python sin compilar:

```powershell
python interfaz.py   # (equivalente: python escanear.py)
```

## 📁 Estructura del proyecto

| Archivo | Descripción |
|---|---|
| `escanear.py` | Lógica: detección de hardware, cálculo y lectura/escritura de OBS |
| `interfaz.py` | Interfaz gráfica (GUI) principal |
| `optimizar.py` | Módulo de optimización de PC para streaming |
| `requirements.txt` | Dependencias para **construir** el `.exe` (PyInstaller) |
| `build.bat` / `build.ps1` | Scripts para generar el ejecutable |
| `README_BUILD.md` | Guía detallada de compilación y distribución |

## ⚠️ Notas

- El programa **modifica los archivos de configuración de OBS** solo cuando pulsas *Aplicar*, y siempre crea un backup antes.
- La medición de subida usa `speedtest-cli` si está instalado; si no, recurre a endpoints de Cloudflare. Si no hay red, usa un valor conservador.
- Requiere **PowerShell** (incluido en todo Windows 10/11) para detectar el hardware.

---

Hecho para simplificar la configuración de OBS. Si encuentras un problema o tienes una sugerencia, abre un *issue*.

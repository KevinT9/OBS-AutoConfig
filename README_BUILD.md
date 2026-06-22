# OBS Auto-Configurator — Cómo generar el .exe

El programa se empaqueta con **PyInstaller** en modo `--onefile`: se obtiene un
único `OBS-AutoConfig.exe` con todas las dependencias dentro. Ese archivo corre
en **Windows 10 y 11 sin instalar Python** ni nada más.

## 1. Construir (en un PC que SÍ tenga Python)

Necesitas Python instalado **solo en el PC donde construyes**, no en el de destino.

Elige una opción:

- **Doble clic** en `build.bat`, o
- En PowerShell:
  ```powershell
  ./build.ps1
  ```
- Manualmente:
  ```powershell
  python -m pip install -r requirements.txt
  python -m PyInstaller --onefile --windowed --name OBS-AutoConfig --clean --noconfirm escanear.py
  ```

Al terminar tendrás:

```
dist\OBS-AutoConfig.exe   <-- este es el que copias
```

## 2. Usar en el otro PC (Windows 10)

1. Copia **solo** `dist\OBS-AutoConfig.exe` (por USB, red, etc.).
2. Doble clic. Se abre la interfaz.
3. Pulsa **ANALIZAR Y CONFIGURAR**. No requiere Python ni permisos de admin.

## Notas importantes

- **Arquitectura**: el .exe hereda la arquitectura del Python con que se
  construye. Con Python de 64 bits genera un .exe de 64 bits (lo normal). Si el
  PC destino fuera de 32 bits, construye con un Python de 32 bits.
- **Compatibilidad**: construir en Windows 11 produce un .exe que funciona en
  Windows 10. Python 3.x soporta Windows 10. (No empaquetes pensando en Win 7/8.)
- **Antivirus / SmartScreen**: los .exe de PyInstaller `--onefile` a veces se
  marcan como sospechosos por ser ejecutables nuevos sin firma. Si Windows
  SmartScreen lo bloquea: *Más información → Ejecutar de todas formas*. Si tu
  antivirus lo borra, añádelo a la lista de excepciones.
- **Primer arranque algo lento**: en modo `--onefile` el .exe se descomprime en
  una carpeta temporal cada vez que se abre. Es normal que tarde unos segundos.
- **Ventana de consola**: se usa `--windowed` para que NO aparezca consola
  (es una app gráfica). Si necesitas ver errores de arranque para depurar,
  quita `--windowed` del comando y reconstruye.
- **PowerShell en el destino**: la detección de hardware usa PowerShell/CIM, que
  viene incluido en todo Windows 10/11. No hay que instalar nada.

## Archivos que genera la construcción (se pueden borrar)

- `build/` — temporales de PyInstaller.
- `OBS-AutoConfig.spec` — receta autogenerada (sirve para reconstruir con
  `python -m PyInstaller OBS-AutoConfig.spec`).
- `dist/` — aquí queda el `.exe` final.

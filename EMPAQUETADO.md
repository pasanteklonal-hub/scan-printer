# Empaquetado

La aplicación se distribuye como `onedir` con PyInstaller. La carpeta generada contiene el ejecutable y sus recursos iniciales.

## Linux

Desde Linux, con Python instalado:

```bash
chmod +x build_linux.sh
./build_linux.sh
```

Resultado: `dist/scanPrinter/scanPrinter`.

## Windows

El ejecutable Windows debe construirse en Windows:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build_windows.ps1
```

Resultado: `dist\scanPrinter\scanPrinter.exe`.

No se deben mezclar los artefactos entre sistemas operativos. Para distribuir la aplicación, copiar la carpeta completa `dist/scanPrinter`, no solo el ejecutable.

Los datos modificables (`estado-impresoras`, `scanner.db` y la planilla generada) se guardan junto al ejecutable. `impresoras.xlsx` y los snapshots incluidos se usan como datos iniciales si todavía no existen en esa carpeta.

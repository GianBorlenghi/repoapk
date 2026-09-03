# Pergamino Precios - APK

App Android sencilla y amigable para comparar precios en MasOnline, VEA y Carrefour (Pergamino).

## Features
- **Comparar precios**: Buscá "coca cola 2.25", "leche serenisima", etc. Muestra precio regular, precio efectivo c/u con promo (2x1, 3x2, 2do 50%), y link a tienda.
- **Más barato inteligente**: Indica más barato x1 y más barato llevando promo (ej: $4.425 c/u llevando 2 en Carrefour).
- **Promos Bancarias**: Apartado dedicado por supermercado (MasOnline, VEA, Carrefour) con promos de pago detectadas dinámicamente (Cencopay, CSI, cuotas, bancos) + estáticas.

## Estructura
- `core.py` - Lógica compartida (búsqueda VTEX, promos, precio efectivo) - mismo que `comparador_precios.py`
- `main.py` - App KivyMD con 2 pestañas
- `buildozer.spec` - Config para compilar APK

## Probar en PC (Windows)
```bash
pip install kivy kivymd requests
python main.py
```

## Compilar APK (requiere Linux/WSL)
### Opción A: WSL (Ubuntu) local
```bash
wsl
sudo apt update && sudo apt install -y python3-pip openjdk-17-jdk zip unzip
pip install buildozer cython
cd /mnt/c/Users/giaan/OneDrive/Documentos/Escritorio/pycom/app
buildozer android debug
# APK queda en bin/pergaminoprecios-1.0-debug.apk
```

### Opción B: GitHub Actions (recomendado, no instala nada local)
1. Subí la carpeta `app/` a un repo GitHub
2. Creá `.github/workflows/build.yml` con:
```yaml
name: Build APK
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with: {python-version: '3.10'}
      - run: pip install buildozer cython
      - run: cd app && buildozer android debug
      - uses: actions/upload-artifact@v3
        with: {name: APK, path: app/bin/*.apk}
```
3. Hacé push → descargá el APK desde Actions → Artifacts

## Promos Bancarias
El apartado "Promos Bancarias" combina:
- **Estáticas** (`PROMOS_BANCARIAS_ESTATICAS` en `core.py`) - editables
- **Dinámicas** detectadas en `productClusters` que contienen `cencopay`, `csi`, `cuotas`, `banco` al buscar "leche"

Podés editar el diccionario en `core.py` para agregar tu banco.

"""
Comparador de precios de supermercados - Pergamino
Busca un producto en MasOnline, VEA y Carrefour usando sus APIs VTEX directas.

Requisitos:
    py -m pip install requests
Uso:
    py comparador_precios.py                  # modo interactivo
    py comparador_precios.py "coca cola 2.25" # búsqueda directa
"""

import os
import re
import sys
import unicodedata
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

# Evitar UnicodeEncodeError en consolas Windows cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Habilitar ANSI en Windows 10+
if os.name == "nt":
    try:
        os.system("")
    except Exception:
        pass

# ── Colores ANSI ──────────────────────────────
VERDE    = "\033[92m"
AMARILLO = "\033[93m"
CIAN     = "\033[96m"
ROJO     = "\033[91m"
RESET    = "\033[0m"
NEGRITA  = "\033[1m"
DIM      = "\033[2m"

SUPERMERCADOS = {
    "MasOnline": "https://www.masonline.com.ar/api/catalog_system/pub/products/search?ft={query}&_from=0&_to=49",
    "VEA":       "https://www.vea.com.ar/api/catalog_system/pub/products/search?ft={query}&_from=0&_to=49",
    "Carrefour": "https://www.carrefour.com.ar/api/catalog_system/pub/products/search?ft={query}&_from=0&_to=49",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

MAX_POR_SUPER = 8
PRECIO_MINIMO = 500    # bajado: algunos productos <1000 existen (ej. chicles)
RATIO_MAX     = 3.0    # si ListPrice > 3x Price → dato corrupto (bug VEA)

PALABRAS_PACK = ["pack", "combo", "fardo", "bulto", "multipack"]


# ── Normalización ─────────────────────────────

def normalizar(texto: str) -> str:
    """
    Minúsculas, sin tildes, coma decimal → punto, número pegado a unidad.
    Ej: "2,25 Lts" → "2.25lts"  |  "Niñas" → "ninas"
    """
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"(\d),(\d)", r"\1.\2", texto)           # 2,25 → 2.25
    # pega número con unidad: "2.25 lts" → "2.25lts" ; "500 ml" → "500ml"
    texto = re.sub(r"(\d\.?\d*)\s*(l|lt|lts|ml|cc|kg|k|g|gr|grs)\b", r"\1\2", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def terminos_de(query: str) -> list[str]:
    return [t for t in normalizar(query).split() if len(t) >= 2]


# ── Validación de precio ──────────────────────

def precio_valido(precio_final, precio_original) -> bool:
    if precio_final is None:
        return False
    try:
        pf = float(precio_final)
    except (TypeError, ValueError):
        return False
    if pf < PRECIO_MINIMO:
        return False
    if precio_original is not None:
        try:
            po = float(precio_original)
            if po > 0 and (po / pf) > RATIO_MAX:
                # No invalida el producto, solo indica ListPrice corrupto
                # (lo corregimos arriba). Pero si no corregimos, sí es inválido
                return False
        except (TypeError, ValueError):
            pass
    return True


def sanear_precio_original(precio_final, precio_original):
    """Si ListPrice está corrupto (>RATIO_MAX), devolver Price."""
    if precio_original is None or precio_final is None:
        return precio_original
    try:
        if float(precio_original) / float(precio_final) > RATIO_MAX:
            return precio_final
    except Exception:
        pass
    return precio_original


# ── Filtro de relevancia ──────────────────────

def es_pack(nombre: str, query: str) -> bool:
    nombre_n = normalizar(nombre)
    query_n  = normalizar(query)
    for palabra in PALABRAS_PACK:
        if palabra in nombre_n and palabra not in query_n:
            return True
    # Combos tipo "Coca + Sprite 2.25L" → tiene " + "
    if " + " in nombre and " + " not in query:
        # solo filtrar si parece combo de gaseosas/productos distintos
        if "+" in nombre_n and "+" not in query_n:
            return True
    # Detectar "x4", "x6" etc en el nombre pero NO en la query
    packs_re = re.findall(r"\bx\d+\b", nombre_n)
    query_packs = re.findall(r"\bx\d+\b", query_n)
    for p in packs_re:
        if p not in query_packs:
            return True
    # "x 6" con espacio
    if re.search(r"\bx\s*\d+\b", nombre_n) and not re.search(r"\bx\s*\d+\b", query_n):
        # evitar falso positivo con "2.25 lts" ya normalizado
        if re.search(r"\bx\s*[2-9]\b", nombre_n):
            return True
    return False


def es_relevante(nombre_producto: str, terminos: list[str]) -> bool:
    """
    1. Todos los términos deben aparecer en el nombre normalizado.
    2. Al menos uno de los términos debe estar entre las primeras 6 palabras
       (los nombres VTEX varían: MasOnline "Gaseosa Coca Cola..." vs
        VEA "Gaseosa Cola Sabor Original 2.25 Lts Coca Cola").
    3. Filtra variantes no pedidas: polvo, chocolatada, crema, etc.
    4. Filtra dulce de leche si se busca leche sola.
    """
    if not terminos:
        return False
    nombre_n = normalizar(nombre_producto)
    query_n  = " ".join(terminos)

    # 1. Todos los términos presentes (subcadena)
    if not all(t in nombre_n for t in terminos):
        return False

    # 2. Al menos un término en las primeras 6 palabras (antes era 3 → bug)
    palabras = nombre_n.split()
    primeras = " ".join(palabras[:6])
    # si el primer término es muy genérico (ej. "coca") y el título empieza
    # con categoría ("gaseosa coca cola...") debe pasar con ventana 6
    # También aceptamos que CUALQUIERA de los términos esté entre las primeras 6
    # para casos como "serenisima" en "leche la serenisima..."
    if not any(t in primeras for t in terminos):
        # fallback estricto: si ningún término está al inicio, es irrelevante
        # Ej: "yerba" no debe matchear "mate cocido yerba..."? pero eso es raro
        return False

    # 3. Exclusiones: variantes que el usuario no pidió
    exclusiones = {
        "polvo":       "polvo",
        "chocolatada": "chocolatada",
        "crema":       "crema",
        "saborizad":   "saborizad",
        "condensad":   "condensad",
        # Dulce de leche vs leche líquida
        "dulce de leche": "dulce",
        "dulce de leche": "dulce",
    }
    # chequeo especial para dulce de leche y abreviaciones: "dulce", "d.leche", "dulce de leche"
    if "dulce" not in query_n and "d.leche" not in query_n:
        if "dulce" in nombre_n or "d.leche" in nombre_n or "d leche" in nombre_n:
            return False

    for palabra_prod, palabra_query in exclusiones.items():
        if palabra_prod in nombre_n and palabra_query not in query_n:
            # caso "dulce de leche" ya manejado arriba
            if palabra_prod == "dulce de leche":
                continue
            return False

    return True


# ── Promociones ───────────────────────────────

def _limpiar_nombre_promo(nombre: str) -> str:
    """Limpia nombres crudos tipo PROMO-...-Reg-..."""
    nombre = nombre.strip()
    # quitar prefijo PROMO-
    nombre = re.sub(r"^PROMO[\s\-–]*", "", nombre, flags=re.I).strip()
    # cortar sufijo técnico Reg-...
    nombre = re.split(r"\s*[-–]\s*Reg[\s\-].*", nombre, flags=re.I)[0].strip()
    nombre = re.split(r"\s+Reg[\s\-].*", nombre, flags=re.I)[0].strip()
    # normalizar espacios
    nombre = re.sub(r"\s+", " ", nombre).strip()
    # capitalizar primera letra
    if nombre:
        nombre = nombre[0].upper() + nombre[1:]
    return nombre


def _precio_efectivo_por_promo(precio_final: float, nombre_promo: str) -> str | None:
    """Calcula precio efectivo c/u para promos tipo 2do al X%, 3x2, 2x1, segunda unidad."""
    if not precio_final or not nombre_promo:
        return None
    low = nombre_promo.lower()
    try:
        pf = float(precio_final)
    except Exception:
        return None

    # 2do al X%  / segunda unidad al X%  (ej: 2do al 50%, segunda unidad 50%, 50% en segunda unidad)
    for pat in [r"2do\s+al\s+(\d+)\s*%", r"segunda\s+unidad.*?(\d+)\s*%", r"2da\s+unidad.*?(\d+)\s*%", r"(\d+)\s*%.*?segunda\s+unidad"]:
        m = re.search(pat, low)
        if m:
            try:
                pct = int(m.group(1))
                if 1 <= pct <= 90:
                    total = pf + pf * (1 - pct / 100)
                    efectivo = total / 2
                    return formatear_precio(efectivo)
            except Exception:
                pass

    # 3ra unidad al X%  (ej: 3ra unidad al 33% → total = P + P + P*0.67 /3)
    m = re.search(r"3ra\s+unidad.*?(\d+)\s*%", low)
    if m:
        try:
            pct = int(m.group(1))
            if 1 <= pct <= 90:
                total = pf + pf + pf * (1 - pct / 100)
                efectivo = total / 3
                return formatear_precio(efectivo)
        except Exception:
            pass

    # 3x2, 2x1, 4x3, etc
    m = re.search(r"(\d+)\s*x\s*(\d+)\b", low)
    if m:
        try:
            lleva = int(m.group(1))
            paga = int(m.group(2))
            if 2 <= lleva <= 6 and 1 <= paga < lleva:
                efectivo = pf * paga / lleva
                return formatear_precio(efectivo)
        except Exception:
            pass

    if "3x2" in low:
        return formatear_precio(pf * 2 / 3)
    if "2x1" in low:
        return formatear_precio(pf / 2)

    return None


def interpretar_promociones(oferta: dict, prod: dict | None = None, terminos: list[str] | None = None) -> list[str]:
    promos: list[str] = []
    seen: set[str] = set()

    def _norm_key(texto: str) -> str:
        # normaliza para deduplicar: sin emojis, minúsculas, sin espacios extra, sin "iguales"
        t = re.sub(r"^[🎁🏷️]+\s*", "", texto.lower())
        t = re.sub(r"\biguales\b", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        # extrae núcleo promo (ej: 2do al 50% vs 2do al 50% iguales -> mismo key)
        m = re.search(r"(2do\s+al\s+\d+\s*%|3x2|2x1|\d+\s*%\s*off)", t)
        if m:
            return m.group(1)
        return t

    def add_promo(texto: str, precio_final=None):
        if not texto or not isinstance(texto, str):
            return
        texto = texto.strip()
        if len(texto) < 3 or len(texto) > 90:
            return
        # deduplicación inteligente
        key = _norm_key(texto)
        if key in seen:
            return
        # si ya existe promo que contiene este key o viceversa, no duplicar
        for k in seen:
            if key in k or k in key:
                # si uno es substring del otro, quedarnos con el más corto/específico ya existente
                return
        seen.add(key)

        # calcular precio efectivo c/u si aplica
        efectivo = None
        if precio_final is not None:
            try:
                efectivo = _precio_efectivo_por_promo(float(precio_final), texto)
            except Exception:
                efectivo = None
        if efectivo:
            # extraer cantidad para mensaje
            low = texto.lower()
            qty_msg = "2"
            if "3x2" in low:
                qty_msg = "3"
            elif "2x1" in low:
                qty_msg = "2"
            elif "2do" in low:
                qty_msg = "2"
            texto = f"{texto} → {efectivo} c/u llevando {qty_msg}"
        promos.append(texto)

    teasers = oferta.get("PromotionTeasers") or oferta.get("teasers") or []
    if isinstance(teasers, dict):
        teasers = [teasers]
    precio_final    = oferta.get("Price")
    precio_original = oferta.get("ListPrice")
    precio_sin_desc = oferta.get("PriceWithoutDiscount")

    # 1) Teasers / PromotionTeasers (VTEX con y sin BackingField) — PRIORITARIOS
    teasers_promos_inicial = len(promos)
    for t in teasers:
        if not isinstance(t, dict):
            continue
        nombre = (
            t.get("Name") or t.get("name") or
            t.get("<Name>k__BackingField", "")
        )
        if not isinstance(nombre, str):
            nombre = str(nombre)
        nombre = nombre.strip()
        nombre_limpio = _limpiar_nombre_promo(nombre) if nombre else ""

        condiciones = (
            t.get("Conditions") or t.get("conditions") or
            t.get("<Conditions>k__BackingField") or {}
        )
        if not isinstance(condiciones, dict):
            condiciones = {}
        min_qty = (
            condiciones.get("MinimumQuantity") or
            condiciones.get("minimumQuantity") or
            condiciones.get("<MinimumQuantity>k__BackingField") or 0
        )
        try:
            min_qty = int(min_qty)
        except Exception:
            min_qty = 0

        efectos = (
            t.get("Effects") or t.get("effects") or
            t.get("<Effects>k__BackingField") or {}
        )
        if not isinstance(efectos, dict):
            efectos = {}
        parametros = (
            efectos.get("Parameters") or efectos.get("parameters") or
            efectos.get("<Parameters>k__BackingField") or []
        )
        if not isinstance(parametros, list):
            parametros = []

        pct = None
        for p in parametros:
            if not isinstance(p, dict):
                continue
            pnom = p.get("Name") or p.get("name") or p.get("<Name>k__BackingField", "")
            pval = p.get("Value") or p.get("value") or p.get("<Value>k__BackingField", "")
            if pnom == "PercentualDiscount":
                try:
                    pct = int(round(abs(float(pval))))
                except (ValueError, TypeError):
                    pass

        if pct is not None:
            if   min_qty == 2 and pct == 100: add_promo("🎁 2x1 (llevás 2, pagás 1)", precio_final)
            elif min_qty == 3 and pct == 100: add_promo("🎁 3x2 (llevás 3, pagás 2)", precio_final)
            elif min_qty == 2 and pct == 50:  add_promo("🎁 2da unidad al 50%", precio_final)
            elif min_qty == 3 and pct == 67:  add_promo("🎁 3ra unidad al 33%", precio_final)
            elif min_qty >= 2 and 0 < pct <= 70:
                add_promo(f"🎁 {pct}% OFF llevando {min_qty} unidades", precio_final)
            elif min_qty <= 1 and 0 < pct <= 70:
                label = f" — {nombre_limpio}" if nombre_limpio else ""
                add_promo(f"🏷️  {pct}% OFF{label}", precio_final)
        elif nombre_limpio and 2 < len(nombre_limpio) < 70:
            emoji = "🎁" if re.search(r"(2do|3x2|2x1|lleva)", nombre_limpio, re.I) else "🏷️ "
            if nombre_limpio.startswith("🎁") or nombre_limpio.startswith("🏷"):
                add_promo(nombre_limpio, precio_final)
            else:
                add_promo(f"{emoji} {nombre_limpio}", precio_final)

    tiene_teaser_real = len(promos) > teasers_promos_inicial

    # 2) DiscountHighLight (solo si no hay teaser real, para no duplicar)
    if not tiene_teaser_real:
        dhl = oferta.get("DiscountHighLight") or oferta.get("discountHighLight") or []
        if isinstance(dhl, dict):
            dhl = [dhl]
        if isinstance(dhl, list):
            for item in dhl:
                if isinstance(item, dict):
                    val = item.get("name") or item.get("Name") or str(item)
                else:
                    val = str(item)
                val = val.strip()
                if val and val != "0" and len(val) < 30:
                    if re.match(r"^\d+(\.\d+)?$", val):
                        try:
                            if 0 < float(val) <= 70:
                                add_promo(f"🏷️  {int(round(float(val)))}% OFF", precio_final)
                                continue
                        except Exception:
                            pass
                    if re.search(r"(%|off)", val, re.I):
                        add_promo(f"🏷️  {val}", precio_final)

    # 3) Clusters del producto — SOLO si NO hay teaser real (VEA/MasOnline)
    #    Si hay teaser (Carrefour), los clusters son ruido genérico ("Hasta 35% off...")
    #    IMPORTANTE: solo mostrar cluster si es relevante para el producto buscado
    #    (contiene marca/termino buscado, o es promo de cantidad, o es promo de pago)
    if not tiene_teaser_real and prod:
        clusters: dict = {}
        clusters.update(prod.get("productClusters") or {})
        clusters.update(prod.get("clusterHighlights") or {})
        # para relevancia: nombre del producto normalizado y terminos buscados
        nombre_prod_n = normalizar(prod.get("productName", "")) if prod.get("productName") else ""
        terminos_n = [t for t in (terminos or [])]
        for cid, cname in clusters.items():
            if not isinstance(cname, str):
                cname = str(cname)
            cname_s = cname.strip()
            if not cname_s or len(cname_s) < 4 or len(cname_s) > 85:
                continue
            low = cname_s.lower()
            if any(x in low for x in [
                "colection_test", "coleccion_test", "coleccion prueba",
                "coleccion fija", "changomania", "cucarda", "leydegondolas",
                "productos dest", "mas vendidos",
                "n2", "generico", "food completo", "canasta",
                "exceptos", "excluidos", "exclusiones", "campana total",
                "arbol n2", "marcas exclusivas", "primer pedido",
                "coleccion automatica", "promos de integracion"
            ]):
                continue
            if low in ("almacen", "almacén", "pastas", "generico", "n2", "almacen - op", "destacados"):
                continue
            if not re.search(r"(\d+\s*%|%\s*off|2do\s+al|3x2|2x1|descuento|csi|cencopay)", low):
                continue
            # ── Filtro de relevancia: ¿la promo es para este producto? ──
            # Solo mostrar promo de cluster si es realmente para este producto.
            # Reglas:
            # - 3x2 / 2x1 siempre son del mismo producto → keep
            # - 2do solo si dice "iguales" o menciona marca/termino (si no, es genérico de categoría)
            # - Pago (cencopay/csi) siempre keep (es forma de pago)
            # - Descuento % solo si menciona marca/termino
            es_3x2_2x1 = bool(re.search(r"(3x2|2x1)", low))
            es_2do = bool(re.search(r"2do", low))
            es_2do_iguales = bool(re.search(r"2do.*iguales", low))
            es_pago = bool(re.search(r"(cencopay|csi|cuotas)", low))
            menciona_producto = False
            if terminos_n:
                if any(t in low for t in terminos_n):
                    menciona_producto = True
                marca = nombre_prod_n.split()[0] if nombre_prod_n else ""
                if marca and len(marca) >= 3 and marca in low:
                    menciona_producto = True
                # también si promo menciona "paty", "coca", etc. aunque sea parte de promo genérica?
            # Decidir si mantener
            mantener = False
            if es_3x2_2x1:
                mantener = True  # 3x2/2x1 siempre es del mismo prod.
            elif es_2do:
                # 2do solo si es iguales o menciona producto
                if es_2do_iguales or menciona_producto:
                    mantener = True
            elif es_pago:
                mantener = True
            elif menciona_producto:
                mantener = True
            # Si es genérica "Hasta 30% en bebidas..." sin cantidad ni marca ni pago → filtrar
            if not mantener:
                continue
            cname_s = _limpiar_nombre_promo(cname_s)
            if re.search(r"(2do|3x2|2x1|lleva|3x)", low):
                add_promo(f"🎁 {cname_s}", precio_final)
            else:
                add_promo(f"🏷️  {cname_s}", precio_final)
            if len(promos) >= 2:  # limitar clusters a 2 para no spamear
                break

    # 4) Fallback por diferencia de precios (SIEMPRE mostrar si hay descuento y no hay promo de cantidad)
    #    Evitar duplicar si ya hay promo con % similar
    if not any("c/u llevando" in p for p in promos):
        for ref_price in (precio_original, precio_sin_desc):
            if ref_price and precio_final and ref_price > precio_final:
                try:
                    pf = float(precio_final)
                    po = float(ref_price)
                    if po > pf and (po / pf) <= RATIO_MAX:
                        pct = round((1 - pf / po) * 100)
                        if 0 < pct <= 70:
                            # no duplicar si ya hay % similar
                            if not any(str(pct) + "%" in p for p in promos):
                                add_promo(f"🏷️  {pct}% OFF", precio_final)
                            break
                except Exception:
                    pass

    return promos[:3]  # máximo 3 promos relevantes (antes 6 → spam)


# ── Scraping ──────────────────────────────────

def formatear_precio(valor: float) -> str:
    return f"${valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _calcular_precio_efectivo(precio_final: float, promociones: list[str], solo_iguales: bool = False, qty: int | None = None) -> tuple[float, str | None]:
    """Devuelve (precio_efectivo_min, promo_que_lo_genera) parseando '→ $X c/u'.
    Si solo_iguales=True, solo considera promos de mismo producto (iguales/3x2/2x1).
    Si qty=2 o 3, filtra solo promos que requieren esa cantidad."""
    efectivo = float(precio_final)
    promo_efectiva = None
    for promo in promociones:
        if solo_iguales:
            low = promo.lower()
            if not re.search(r"(iguales|3x2|2x1|\bllev[aá]s\s+\d+)", low):
                continue
        if qty is not None:
            # filtrar por cantidad requerida
            if f"llevando {qty}" not in promo:
                # también chequear "3x2" -> lleva 3, "2x1" -> lleva 2
                if qty == 3 and "3x2" not in promo.lower():
                    continue
                if qty == 2 and "llevando 2" not in promo and "2x1" not in promo.lower() and "2do" not in promo.lower():
                    continue
        m = re.search(r"→\s*\$\s*([\d\.\,]+)\s*c/u", promo)
        if m:
            raw = m.group(1).strip()
            try:
                val = float(raw.replace(".", "").replace(",", "."))
                if val < efectivo - 0.01:
                    efectivo = val
                    promo_efectiva = promo
            except Exception:
                continue
    return efectivo, promo_efectiva


def _mejor_oferta_de_producto(prod: dict):
    """
    Itera todos los items/sellers y devuelve la mejor oferta válida
    (precio más bajo, disponible). Soporta ambos spellings VTEX.
    """
    items = prod.get("items", [])
    mejor = None
    for item in items:
        sellers = item.get("sellers", [])
        for seller in sellers:
            # VTEX escribe mal "commertialOffer" pero por si acaso chequeamos ambos
            oferta = seller.get("commertialOffer") or seller.get("commercialOffer") or {}
            if not oferta:
                continue
            # disponibilidad
            available = oferta.get("AvailableQuantity")
            is_available = seller.get("commertialOffer", {}).get("AvailableQuantity", 1)
            # VTEX a veces usa IsAvailable en el item
            if available is not None:
                try:
                    if int(available) <= 0:
                        continue
                except Exception:
                    pass
            # chequear Price
            precio_final = oferta.get("Price")
            precio_original = oferta.get("ListPrice")
            if precio_final is None:
                continue
            try:
                pf = float(precio_final)
            except Exception:
                continue
            if pf <= 0:
                continue
            # sanear ListPrice corrupto antes de validar
            precio_original = sanear_precio_original(pf, precio_original)
            if not precio_valido(pf, precio_original):
                continue
            # quedarnos con el más barato
            if mejor is None or pf < mejor[0]:
                mejor = (pf, precio_original, oferta)
    return mejor


def buscar_en_super(nombre_super: str, url_template: str, query: str, terminos: list[str]) -> list[dict]:
    resultados = []
    if not terminos:
        return resultados
    url = url_template.format(query=quote(query))

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        productos = resp.json()
        if not isinstance(productos, list):
            return resultados

        for prod in productos:
            try:
                nombre_prod = (prod.get("productName") or "").strip()
                if not nombre_prod:
                    continue
                if not es_relevante(nombre_prod, terminos):
                    continue
                if es_pack(nombre_prod, query):
                    continue

                link = prod.get("link") or prod.get("linkText") or ""
                # link puede ser sin dominio
                if link and link.startswith("/"):
                    base = url_template.split("/api")[0]
                    link = base + link
                elif link and not link.startswith("http"):
                    link = "https://" + link

                mejor = _mejor_oferta_de_producto(prod)
                if mejor is None:
                    continue
                pf, po, oferta = mejor
                promociones = interpretar_promociones(oferta, prod, terminos)
                precio_efectivo, promo_efectiva = _calcular_precio_efectivo(float(pf), promociones)
                # para ranking "más barato llevando promo" solo usa promos de mismo producto
                precio_efectivo_iguales, promo_iguales = _calcular_precio_efectivo(float(pf), promociones, solo_iguales=True)
                if promo_iguales is None:
                    precio_efectivo_iguales = float(pf)
                # efectivos por cantidad específica para ranking x2 vs x3
                precio_efectivo_x2, promo_x2 = _calcular_precio_efectivo(float(pf), promociones, qty=2)
                if promo_x2 is None:
                    precio_efectivo_x2 = float(pf)
                precio_efectivo_x3, promo_x3 = _calcular_precio_efectivo(float(pf), promociones, qty=3)
                if promo_x3 is None:
                    precio_efectivo_x3 = float(pf)

                resultados.append({
                    "supermercado":    nombre_super,
                    "nombre":          nombre_prod,
                    "precio_final":    float(pf),
                    "precio_original": float(po) if po is not None else None,
                    "precio_str":      formatear_precio(float(pf)),
                    "precio_efectivo": float(precio_efectivo),
                    "promo_efectiva":  promo_efectiva,
                    "precio_efectivo_str": formatear_precio(float(precio_efectivo)) if precio_efectivo < float(pf) - 0.01 else None,
                    "precio_efectivo_iguales": float(precio_efectivo_iguales),
                    "promo_iguales": promo_iguales,
                    "precio_efectivo_iguales_str": formatear_precio(float(precio_efectivo_iguales)) if precio_efectivo_iguales < float(pf) - 0.01 else None,
                    "promociones":     promociones,
                    "url":             link,
                })

                if len(resultados) >= MAX_POR_SUPER:
                    break
            except Exception:
                # error en un producto puntual → ignorar y seguir
                continue

        # ordenar por precio dentro del super
        resultados.sort(key=lambda x: x["precio_final"])

    except requests.exceptions.Timeout:
        print(f"  {ROJO}❌ [{nombre_super}] Tiempo de espera agotado.{RESET}")
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        print(f"  {ROJO}❌ [{nombre_super}] Error HTTP: {code}{RESET}")
    except Exception as e:
        print(f"  {ROJO}❌ [{nombre_super}] Error: {e}{RESET}")

    return resultados


def buscar_en_todos(query: str) -> list[dict]:
    terminos = terminos_de(query)
    if not terminos:
        return []
    todos = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futuros = {
            executor.submit(buscar_en_super, nombre, url, query, terminos): nombre
            for nombre, url in SUPERMERCADOS.items()
        }
        for futuro in as_completed(futuros):
            try:
                todos.extend(futuro.result())
            except Exception as e:
                print(f"  {ROJO}❌ [{futuros[futuro]}] Error interno: {e}{RESET}")
    # ordenar global por precio
    todos.sort(key=lambda x: x["precio_final"])
    return todos


# ── Presentación ──────────────────────────────

def mostrar_resultados(resultados: list[dict], query: str):
    print()
    print("═" * 65)
    print(f"  Resultados para: \"{query}\"  |  Zona: Pergamino")
    print("═" * 65)

    if not resultados:
        print()
        print("  Sin resultados. Probá con menos palabras.")
        print("  Ej: 'coca cola 2.25'  /  'leche serenisima'  /  'arroz largo'")
        print()
        print("═" * 65)
        return

    precio_min = min(r["precio_final"] for r in resultados)
    # precio efectivo: si hay promo por cantidad, es el precio c/u llevando promo
    # para el ranking "llevando promo" usamos solo promos de mismo producto (iguales/3x2)
    # así VEA genérico "Hasta 2do al 70% en Almacén" no compite con Carrefour "2do al 50% Iguales"
    precio_min_efectivo = min(r.get("precio_efectivo", r["precio_final"]) for r in resultados)
    precio_min_iguales = min(r.get("precio_efectivo_iguales", r["precio_final"]) for r in resultados)

    por_super: dict[str, list] = {}
    for r in resultados:
        por_super.setdefault(r["supermercado"], []).append(r)

    # Mantener orden MasOnline, VEA, Carrefour si existen
    orden = ["MasOnline", "VEA", "Carrefour"]
    supers_ordenados = sorted(por_super.keys(), key=lambda k: orden.index(k) if k in orden else 99)

    for nombre_super in supers_ordenados:
        items = por_super[nombre_super]
        print(f"\n  🛒  {NEGRITA}{nombre_super}{RESET} {DIM}({len(items)} productos){RESET}")
        print("  " + "─" * 55)

        for i, item in enumerate(items, 1):
            nombre = item["nombre"]
            if len(nombre) > 52:
                nombre = nombre[:52] + "…"

            pf = item["precio_final"]
            pe = item.get("precio_efectivo", pf)
            pe_ig = item.get("precio_efectivo_iguales", pf)
            es_min = abs(pf - precio_min) < 0.01
            es_min_efectivo = abs(pe - precio_min_efectivo) < 0.01 and pe < pf - 0.01
            es_min_iguales = abs(pe_ig - precio_min_iguales) < 0.01 and pe_ig < pf - 0.01

            if es_min and es_min_iguales:
                precio_txt = f"{VERDE}{NEGRITA}{item['precio_str']}  ◄ MÁS BARATO (1u y promo){RESET}"
            elif es_min:
                precio_txt = f"{VERDE}{NEGRITA}{item['precio_str']}  ◄ MÁS BARATO x1{RESET}"
            elif es_min_iguales:
                precio_txt = f"{item['precio_str']}"
            else:
                precio_txt = item["precio_str"]

            precio_antes = ""
            po = item["precio_original"]
            if po and po > pf:
                try:
                    if (float(po) / float(pf)) <= RATIO_MAX:
                        precio_antes = f"  {AMARILLO}antes {formatear_precio(float(po))}{RESET}"
                except Exception:
                    pass

            print(f"  {i}. {nombre}")
            print(f"     💲 {precio_txt}{precio_antes}")

            for promo in item["promociones"]:
                # resaltar promo que genera el precio efectivo más barato (mismo producto)
                if es_min_iguales and promo == item.get("promo_iguales"):
                    print(f"     {VERDE}{NEGRITA}{promo}  ◄ MÁS BARATO c/u{RESET}")
                elif es_min_efectivo and promo == item.get("promo_efectiva"):
                    print(f"     {VERDE}{promo}  ◄ MÁS BARATO c/u{RESET}")
                else:
                    print(f"     {AMARILLO}{promo}{RESET}")

            # Si el producto no es el más barato x1 pero sí tiene promo iguales que lo hace barato c/u, mostrar hint
            if not es_min and es_min_iguales:
                print(f"     {VERDE}   → Efectivo {item['precio_efectivo_iguales_str']} c/u (¡más barato llevando promo!){RESET}")

            if item["url"]:
                url = item["url"]
                url_corta = url[:68] + "…" if len(url) > 68 else url
                print(f"     🔗 {DIM}{url_corta}{RESET}")

    mejor = next(r for r in resultados if abs(r["precio_final"] - precio_min) < 0.01)
    # para el ranking "llevando promo" usamos solo promos iguales (mismo producto)
    mejor_iguales = next(r for r in resultados if abs(r.get("precio_efectivo_iguales", r["precio_final"]) - precio_min_iguales) < 0.01)
    mejor_efectivo = next(r for r in resultados if abs(r.get("precio_efectivo", r["precio_final"]) - precio_min_efectivo) < 0.01)
    nombre_corto = mejor["nombre"][:44] + "…" if len(mejor["nombre"]) > 44 else mejor["nombre"]
    nombre_corto_ig = mejor_iguales["nombre"][:44] + "…" if len(mejor_iguales["nombre"]) > 44 else mejor_iguales["nombre"]

    print()
    print("═" * 65)
    print(f"  {VERDE}{NEGRITA}⭐ Más barato (1 unidad): {mejor['precio_str']} en {mejor['supermercado']}{RESET}")
    print(f"     {nombre_corto}")
    # Mostrar también el más barato llevando promo (mismo producto) si es distinto o con efectivo menor
    if mejor_iguales.get("precio_efectivo_iguales_str"):
        # si el más barato por promo es distinto al más barato por 1 unidad, o si hay ahorro
        if abs(precio_min_iguales - precio_min) > 0.01 or mejor["supermercado"] != mejor_iguales["supermercado"] or mejor["nombre"] != mejor_iguales["nombre"]:
            ahorro = ""
            try:
                ahorro_val = float(mejor["precio_final"]) - float(mejor_iguales["precio_efectivo_iguales"])
                if ahorro_val > 0:
                    ahorro = f"  (ahorrás {formatear_precio(ahorro_val)} vs llevar 1 en {mejor['supermercado']})"
            except Exception:
                pass
            promo_txt = ""
            if mejor_iguales.get("promo_iguales"):
                promo_txt = mejor_iguales["promo_iguales"].split("→")[0].strip()
            print(f"  {VERDE}{NEGRITA}⭐ Más barato llevando promo (mismo prod.): {mejor_iguales['precio_efectivo_iguales_str']} c/u en {mejor_iguales['supermercado']}{RESET}")
            if promo_txt:
                print(f"     {promo_txt} — {nombre_corto_ig}{ahorro}")
            else:
                print(f"     {nombre_corto_ig}{ahorro}")
            # si hay un efectivo genérico aún más barato (ej. VEA genérico 2do 70%), mostrar info adicional
            if mejor_efectivo.get("precio_efectivo_str") and abs(mejor_efectivo["precio_efectivo"] - mejor_iguales["precio_efectivo_iguales"]) > 0.01 and mejor_efectivo["precio_efectivo"] < mejor_iguales["precio_efectivo_iguales"]:
                print(f"  {DIM}   Nota: con promo genérica {mejor_efectivo['supermercado']} llegaría a {mejor_efectivo['precio_efectivo_str']} c/u ({mejor_efectivo['promo_efectiva'].split('→')[0].strip()}){RESET}")
    print("═" * 65)
    print()


# ── Main ──────────────────────────────────────

def main():
    print()
    try:
        print("╔═══════════════════════════════════════════════════════════╗")
        print("║  Comparador de Precios - Supermercados de Pergamino       ║")
        print("║  MasOnline  •  VEA  •  Carrefour                          ║")
        print("╚═══════════════════════════════════════════════════════════╝")
    except UnicodeEncodeError:
        print("=============================================================")
        print("  Comparador de Precios - Supermercados de Pergamino")
        print("  MasOnline  •  VEA  •  Carrefour")
        print("=============================================================")
    print()
    print("  Ejemplos: 'coca cola 2.25'  /  'leche serenisima descremada'")
    print("            'arroz largo fino'  /  'aceite girasol 1.5'")
    print("  (Escribí 'salir' para terminar)\n")

    # Soporte búsqueda directa por argumento: py comparador_precios.py "yerba 1kg"
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:]).strip()
        if query.lower() not in ("-h", "--help"):
            print(f"  Buscando \"{query}\"...\n")
            resultados = buscar_en_todos(query)
            mostrar_resultados(resultados, query)
            return

    while True:
        try:
            query = input("  Producto: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  ¡Hasta luego!")
            break

        if not query:
            continue
        if query.lower() in ("salir", "exit", "q", "quit"):
            print("\n  ¡Hasta luego!")
            break

        print(f"\n  Buscando \"{query}\"...\n")
        resultados = buscar_en_todos(query)
        mostrar_resultados(resultados, query)


if __name__ == "__main__":
    main()

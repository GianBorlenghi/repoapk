"""
core.py - Lógica compartida entre CLI y APK
Extraído de comparador_precios.py para reutilizar en Kivy.
"""
import re
import unicodedata
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

SUPERMERCADOS = {
    "MasOnline": "https://www.masonline.com.ar/api/catalog_system/pub/products/search?ft={query}&_from=0&_to=49",
    "VEA":       "https://www.vea.com.ar/api/catalog_system/pub/products/search?ft={query}&_from=0&_to=49",
    "Carrefour": "https://www.carrefour.com.ar/api/catalog_system/pub/products/search?ft={query}&_from=0&_to=49",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}
MAX_POR_SUPER = 8
PRECIO_MINIMO = 500
RATIO_MAX = 3.0
PALABRAS_PACK = ["pack", "combo", "fardo", "bulto", "multipack"]

def normalizar(texto: str) -> str:
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"(\d),(\d)", r"\1.\2", texto)
    texto = re.sub(r"(\d\.?\d*)\s*(l|lt|lts|ml|cc|kg|k|g|gr|grs)\b", r"\1\2", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto

def terminos_de(query: str) -> list[str]:
    return [t for t in normalizar(query).split() if len(t) >= 2]

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
                return False
        except (TypeError, ValueError):
            pass
    return True

def sanear_precio_original(precio_final, precio_original):
    if precio_original is None or precio_final is None:
        return precio_original
    try:
        if float(precio_original) / float(precio_final) > RATIO_MAX:
            return precio_final
    except Exception:
        pass
    return precio_original

def es_pack(nombre: str, query: str) -> bool:
    nombre_n = normalizar(nombre)
    query_n  = normalizar(query)
    for palabra in PALABRAS_PACK:
        if palabra in nombre_n and palabra not in query_n:
            return True
    if " + " in nombre and " + " not in query:
        if "+" in nombre_n and "+" not in query_n:
            return True
    packs_re = re.findall(r"\bx\d+\b", nombre_n)
    query_packs = re.findall(r"\bx\d+\b", query_n)
    for p in packs_re:
        if p not in query_packs:
            return True
    if re.search(r"\bx\s*\d+\b", nombre_n) and not re.search(r"\bx\s*\d+\b", query_n):
        if re.search(r"\bx\s*[2-9]\b", nombre_n):
            return True
    return False

def es_relevante(nombre_producto: str, terminos: list[str]) -> bool:
    if not terminos:
        return False
    nombre_n = normalizar(nombre_producto)
    query_n  = " ".join(terminos)
    if not all(t in nombre_n for t in terminos):
        return False
    palabras = nombre_n.split()
    primeras = " ".join(palabras[:6])
    if not any(t in primeras for t in terminos):
        return False
    exclusiones = {
        "polvo": "polvo", "chocolatada": "chocolatada", "crema": "crema",
        "saborizad": "saborizad", "condensad": "condensad", "dulce de leche": "dulce",
    }
    if "dulce" not in query_n and "d.leche" not in query_n:
        if "dulce" in nombre_n or "d.leche" in nombre_n or "d leche" in nombre_n:
            return False
    for palabra_prod, palabra_query in exclusiones.items():
        if palabra_prod in nombre_n and palabra_query not in query_n:
            if palabra_prod == "dulce de leche":
                continue
            return False
    return True

def formatear_precio(valor: float) -> str:
    return f"${valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def _limpiar_nombre_promo(nombre: str) -> str:
    nombre = nombre.strip()
    nombre = re.sub(r"^PROMO[\s\-–]*", "", nombre, flags=re.I).strip()
    nombre = re.split(r"\s*[-–]\s*Reg[\s\-].*", nombre, flags=re.I)[0].strip()
    nombre = re.split(r"\s+Reg[\s\-].*", nombre, flags=re.I)[0].strip()
    nombre = re.sub(r"\s+", " ", nombre).strip()
    if nombre:
        nombre = nombre[0].upper() + nombre[1:]
    return nombre

def _precio_efectivo_por_promo(precio_final: float, nombre_promo: str) -> str | None:
    if not precio_final or not nombre_promo:
        return None
    low = nombre_promo.lower()
    try:
        pf = float(precio_final)
    except Exception:
        return None
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
    m = re.search(r"(\d+)\s*x\s*(\d+)\b", low)
    if m:
        try:
            lleva = int(m.group(1)); paga = int(m.group(2))
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
        t = re.sub(r"^[🎁🏷️]+\s*", "", texto.lower())
        t = re.sub(r"\biguales\b", "", t)
        t = re.sub(r"\s+", " ", t).strip()
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
        key = _norm_key(texto)
        if key in seen:
            return
        for k in seen:
            if key in k or k in key:
                return
        seen.add(key)
        efectivo = None
        if precio_final is not None:
            try:
                efectivo = _precio_efectivo_por_promo(float(precio_final), texto)
            except Exception:
                efectivo = None
        if efectivo:
            low = texto.lower()
            qty_msg = "2"
            if "3x2" in low:
                qty_msg = "3"
            elif "2x1" in low:
                qty_msg = "2"
            elif "2do" in low or "segunda" in low:
                qty_msg = "2"
            texto = f"{texto} → {efectivo} c/u llevando {qty_msg}"
        promos.append(texto)

    teasers = oferta.get("PromotionTeasers") or oferta.get("teasers") or []
    if isinstance(teasers, dict):
        teasers = [teasers]
    precio_final    = oferta.get("Price")
    precio_original = oferta.get("ListPrice")
    precio_sin_desc = oferta.get("PriceWithoutDiscount")
    teasers_promos_inicial = len(promos)
    for t in teasers:
        if not isinstance(t, dict):
            continue
        nombre = (t.get("Name") or t.get("name") or t.get("<Name>k__BackingField", "")).strip()
        nombre_limpio = _limpiar_nombre_promo(nombre) if isinstance(nombre, str) else ""
        condiciones = (t.get("Conditions") or t.get("conditions") or t.get("<Conditions>k__BackingField") or {})
        if not isinstance(condiciones, dict):
            condiciones = {}
        min_qty = (condiciones.get("MinimumQuantity") or condiciones.get("minimumQuantity") or condiciones.get("<MinimumQuantity>k__BackingField") or 0)
        try:
            min_qty = int(min_qty)
        except Exception:
            min_qty = 0
        efectos = (t.get("Effects") or t.get("effects") or t.get("<Effects>k__BackingField") or {})
        if not isinstance(efectos, dict):
            efectos = {}
        parametros = (efectos.get("Parameters") or efectos.get("parameters") or efectos.get("<Parameters>k__BackingField") or [])
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
            emoji = "🎁" if re.search(r"(2do|3x2|2x1|lleva|segunda)", nombre_limpio, re.I) else "🏷️ "
            if nombre_limpio.startswith("🎁") or nombre_limpio.startswith("🏷"):
                add_promo(nombre_limpio, precio_final)
            else:
                add_promo(f"{emoji} {nombre_limpio}", precio_final)
    tiene_teaser_real = len(promos) > teasers_promos_inicial
    if not tiene_teaser_real:
        dhl = oferta.get("DiscountHighLight") or oferta.get("discountHighLight") or []
        if isinstance(dhl, dict):
            dhl = [dhl]
        if isinstance(dhl, list):
            for item in dhl:
                val = item.get("name") or item.get("Name") or str(item) if isinstance(item, dict) else str(item)
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
    if not tiene_teaser_real and prod:
        clusters: dict = {}
        clusters.update(prod.get("productClusters") or {})
        clusters.update(prod.get("clusterHighlights") or {})
        nombre_prod_n = normalizar(prod.get("productName", "")) if prod.get("productName") else ""
        terminos_n = [t for t in (terminos or [])]
        for cid, cname in clusters.items():
            if not isinstance(cname, str):
                cname = str(cname)
            cname_s = cname.strip()
            if not cname_s or len(cname_s) < 4 or len(cname_s) > 85:
                continue
            low = cname_s.lower()
            if any(x in low for x in ["colection_test","coleccion_test","coleccion prueba","coleccion fija","changomania","cucarda","leydegondolas","productos dest","mas vendidos","n2","generico","food completo","canasta","exceptos","excluidos","exclusiones","campana total","arbol n2","marcas exclusivas","primer pedido","coleccion automatica","promos de integracion"]):
                continue
            if low in ("almacen","almacén","pastas","generico","n2","almacen - op","destacados"):
                continue
            if not re.search(r"(\d+\s*%|%\s*off|2do\s+al|3x2|2x1|descuento|csi|cencopay)", low):
                continue
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
            mantener = False
            if es_3x2_2x1:
                mantener = True
            elif es_2do:
                if es_2do_iguales or menciona_producto:
                    mantener = True
            elif es_pago:
                mantener = True
            elif menciona_producto:
                mantener = True
            if not mantener:
                continue
            cname_s = _limpiar_nombre_promo(cname_s)
            if re.search(r"(2do|3x2|2x1|lleva|3x|segunda)", low):
                add_promo(f"🎁 {cname_s}", precio_final)
            else:
                add_promo(f"🏷️  {cname_s}", precio_final)
            if len(promos) >= 2:
                break
    if not any("c/u llevando" in p for p in promos):
        for ref_price in (precio_original, precio_sin_desc):
            if ref_price and precio_final and ref_price > precio_final:
                try:
                    pf = float(precio_final); po = float(ref_price)
                    if po > pf and (po / pf) <= RATIO_MAX:
                        pct = round((1 - pf / po) * 100)
                        if 0 < pct <= 70:
                            if not any(str(pct) + "%" in p for p in promos):
                                add_promo(f"🏷️  {pct}% OFF", precio_final)
                            break
                except Exception:
                    pass
    return promos[:3]

def _calcular_precio_efectivo(precio_final: float, promociones: list[str], solo_iguales: bool = False, qty: int | None = None) -> tuple[float, str | None]:
    efectivo = float(precio_final)
    promo_efectiva = None
    for promo in promociones:
        if solo_iguales:
            low = promo.lower()
            if not re.search(r"(iguales|3x2|2x1|\bllev[aá]s\s+\d+)", low):
                continue
        if qty is not None:
            if f"llevando {qty}" not in promo:
                if qty == 3 and "3x2" not in promo.lower():
                    continue
                if qty == 2 and "llevando 2" not in promo and "2x1" not in promo.lower() and "2do" not in promo.lower() and "segunda" not in promo.lower():
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
    items = prod.get("items", [])
    mejor = None
    for item in items:
        sellers = item.get("sellers", [])
        for seller in sellers:
            oferta = seller.get("commertialOffer") or seller.get("commercialOffer") or {}
            if not oferta:
                continue
            available = oferta.get("AvailableQuantity")
            if available is not None:
                try:
                    if int(available) <= 0:
                        continue
                except Exception:
                    pass
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
            precio_original = sanear_precio_original(pf, precio_original)
            if not precio_valido(pf, precio_original):
                continue
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
                precio_efectivo_iguales, promo_iguales = _calcular_precio_efectivo(float(pf), promociones, solo_iguales=True)
                if promo_iguales is None:
                    precio_efectivo_iguales = float(pf)
                precio_efectivo_x2, promo_x2 = _calcular_precio_efectivo(float(pf), promociones, qty=2)
                if promo_x2 is None:
                    precio_efectivo_x2 = float(pf)
                precio_efectivo_x3, promo_x3 = _calcular_precio_efectivo(float(pf), promociones, qty=3)
                if promo_x3 is None:
                    precio_efectivo_x3 = float(pf)
                resultados.append({
                    "supermercado": nombre_super, "nombre": nombre_prod,
                    "precio_final": float(pf), "precio_original": float(po) if po is not None else None,
                    "precio_str": formatear_precio(float(pf)),
                    "precio_efectivo": float(precio_efectivo), "promo_efectiva": promo_efectiva,
                    "precio_efectivo_str": formatear_precio(float(precio_efectivo)) if precio_efectivo < float(pf)-0.01 else None,
                    "precio_efectivo_iguales": float(precio_efectivo_iguales), "promo_iguales": promo_iguales,
                    "precio_efectivo_iguales_str": formatear_precio(float(precio_efectivo_iguales)) if precio_efectivo_iguales < float(pf)-0.01 else None,
                    "precio_efectivo_x2": float(precio_efectivo_x2), "promo_x2": promo_x2,
                    "precio_efectivo_x3": float(precio_efectivo_x3), "promo_x3": promo_x3,
                    "promociones": promociones, "url": link,
                })
                if len(resultados) >= MAX_POR_SUPER:
                    break
            except Exception:
                continue
        resultados.sort(key=lambda x: x["precio_final"])
    except Exception as e:
        print(f"[{nombre_super}] Error: {e}")
    return resultados

def buscar_en_todos(query: str) -> list[dict]:
    terminos = terminos_de(query)
    if not terminos:
        return []
    todos = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futuros = {executor.submit(buscar_en_super, nombre, url, query, terminos): nombre for nombre, url in SUPERMERCADOS.items()}
        for futuro in as_completed(futuros):
            try:
                todos.extend(futuro.result())
            except Exception as e:
                print(f"[{futuros[futuro]}] Error: {e}")
    todos.sort(key=lambda x: x["precio_final"])
    return todos

# ── Promociones Bancarias ──
PROMOS_BANCARIAS_ESTATICAS = {
    "MasOnline": [
        {"banco": "Naranja X", "promo": "3 cuotas sin interés", "detalle": "En compras superiores a $40.000"},
        {"banco": "Cencopay", "promo": "25% + 3 CSI", "detalle": "Con Cencopay - Tope $8.000"},
        {"banco": "Banco Nación", "promo": "30% OFF", "detalle": "Miércoles con BNA - Tope $10.000"},
    ],
    "VEA": [
        {"banco": "Cencopay", "promo": "25% + 3 cuotas sin interés", "detalle": "Todos los días con Cencopay"},
        {"banco": "Banco Galicia", "promo": "20% OFF", "detalle": "Jueves con Galicia - Tope $7.000"},
        {"banco": "BBVA", "promo": "15% OFF + 3 CSI", "detalle": "Viernes con BBVA"},
    ],
    "Carrefour": [
        {"banco": "Mi Carrefour Crédito", "promo": "15% OFF + 3 CSI", "detalle": "Todos los días con Mi Carrefour"},
        {"banco": "Banco Nación", "promo": "30% OFF", "detalle": "Martes con BNA - Tope $12.000"},
        {"banco": "Banco Galicia", "promo": "25% OFF", "detalle": "Miércoles con Galicia - Carrefour Maxi"},
    ],
}

def obtener_promos_bancarias(supermercado: str | None = None, query: str = "leche") -> dict:
    """
    Combina promos estáticas + promos de pago extraídas dinámicamente de la API
    (clusters con cencopay/csi/cuotas).
    """
    resultado = {}
    supers = [supermercado] if supermercado else list(SUPERMERCADOS.keys())
    # primero estáticas
    for s in supers:
        resultado[s] = list(PROMOS_BANCARIAS_ESTATICAS.get(s, []))
    # luego dinámicas: buscar promos de pago en un query genérico
    try:
        terminos = terminos_de(query)
        for s in supers:
            url = SUPERMERCADOS[s].format(query=quote(query))
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code != 200:
                continue
            prods = resp.json()
            dinamicas = set()
            for prod in prods[:10]:
                for cname in list((prod.get("productClusters") or {}).values()) + list((prod.get("clusterHighlights") or {}).values()):
                    low = cname.lower()
                    if any(k in low for k in ["cencopay","csi","cuotas","cuota","banco","tarjeta","naranja","galicia","bbva","santander","nacion"]):
                        # limpiar y deduplicar
                        if len(cname) < 80 and "colection" not in low:
                            dinamicas.add(cname.strip())
            for d in list(dinamicas)[:4]:
                # evitar duplicar estáticas
                if not any(d.lower() in p["promo"].lower() for p in resultado[s]):
                    resultado[s].append({"banco": "Promo detectada", "promo": d, "detalle": "Detectada en productos"})
    except Exception:
        pass
    if supermercado:
        return resultado.get(supermercado, [])
    return resultado

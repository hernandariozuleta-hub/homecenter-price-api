#!/usr/bin/env python3
"""
homecenter_price.py

Busca el precio de un producto en Homecenter Colombia (homecenter.com.co).

Estrategia:
1. Intenta la API pública de catálogo de VTEX (la plataforma e-commerce que usa
   homecenter.com.co). Este endpoint no requiere API key porque es el mismo que
   usa el buscador del sitio web.
2. Si la API de VTEX no responde JSON válido (bloqueada, movida, etc.), cae a
   scraping del HTML de resultados de búsqueda como respaldo.
3. Si el HTML crudo tampoco trae productos (porque el sitio los renderiza con
   JavaScript/React, o hay un challenge anti-bot), cae a un tercer nivel:
   abrir la página con un navegador real headless (Playwright), esperar a que
   cargue el JavaScript, y extraer los productos ya renderizados.
4. Con los resultados obtenidos:
   - Si hay un producto cuyo nombre coincide (casi) exactamente con lo buscado,
     devuelve ese precio.
   - Si no hay coincidencia exacta, elige el producto "más parecido" usando
     similitud de texto (difflib) sobre el nombre.
   - Además siempre calcula el precio promedio de todos los resultados
     encontrados, por si prefieres usar ese dato en vez de un único producto.

Uso:
    python3 homecenter_price.py "taladro percutor bosch"
    python3 homecenter_price.py "nevera lg 300 litros" --top 15 --json

Requisitos:
    pip install requests beautifulsoup4 --break-system-packages
    # Opcional pero recomendado, para el tercer nivel de respaldo con navegador real:
    pip install playwright --break-system-packages
    python3 -m playwright install chromium

IMPORTANTE:
- No existe una API oficial documentada de Homecenter para esto; este script
  usa el endpoint público de VTEX (la plataforma sobre la que corre el sitio),
  y como respaldo, scraping del HTML público (con o sin navegador real).
- Revisa los Términos de Uso de homecenter.com.co antes de usar esto de forma
  intensiva o automatizada; usa esto de forma responsable (no hagas demasiadas
  solicitudes por segundo).
- Este script no pudo probarse en vivo desde el entorno donde fue generado
  porque ese entorno no tiene salida de red hacia homecenter.com.co. Pruébalo
  tú mismo antes de confiar en él para producción.
"""

import argparse
import difflib
import json
import re
import sys
import time
from statistics import mean

try:
    import requests
except ImportError:
    print("Falta 'requests'. Instala con: pip install requests beautifulsoup4 --break-system-packages")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

BASE_URL = "https://www.homecenter.com.co"
VTEX_SEARCH_URL = BASE_URL + "/api/catalog_system/pub/products/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9",
}


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9áéíóúñ\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def parse_price(value) -> float:
    """
    Convierte distintos formatos de precio (string o número) a float,
    interpretando correctamente el formato colombiano: punto como separador
    de miles y coma como separador decimal (ej: "$310.000" = 310000,
    "$1.250.500,50" = 1250500.50).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    s = re.sub(r"[^\d,.]", "", str(value))
    if not s:
        return None

    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        # El separador que aparece último es el decimal; el otro son miles.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        # Solo coma: si el último grupo tiene 3 dígitos, es separador de
        # miles (poco común en COP pero por si acaso); si no, es decimal.
        last_group = s.split(",")[-1]
        s = s.replace(",", "") if len(last_group) == 3 else s.replace(",", ".")
    elif has_dot:
        # Solo punto: en formato colombiano casi siempre es separador de
        # miles (ej: 310.000). Solo lo tratamos como decimal si el último
        # grupo tiene 1 o 2 dígitos (ej: 45.90).
        last_group = s.split(".")[-1]
        if len(last_group) == 3:
            s = s.replace(".", "")
        # si tiene 1-2 dígitos, se deja el punto como decimal tal cual

    try:
        return float(s)
    except ValueError:
        return None


def parse_vtex_products(data):
    """Convierte la respuesta JSON cruda de VTEX en una lista de dicts uniformes."""
    results = []
    for product in data:
        name = product.get("productName") or product.get("productTitle")
        link = product.get("link") or product.get("linkText")
        product_id = product.get("productId")
        items = product.get("items") or []
        price = None
        ref_ids = set()
        for item in items:
            ref = item.get("referenceId") or []
            for r in ref:
                value = r.get("Value") if isinstance(r, dict) else None
                if value:
                    ref_ids.add(str(value))
            sellers = item.get("sellers") or []
            for seller in sellers:
                offer = seller.get("commertialOffer") or {}
                p = offer.get("Price") or offer.get("ListPrice")
                if p:
                    price = float(p)
                    break
            if price:
                break
        if name and price:
            results.append({
                "name": name,
                "price": price,
                "url": link or "",
                "reference_id": next(iter(ref_ids), None),
                "product_id": str(product_id) if product_id else None,
            })
    return results


def search_vtex_api(query: str, limit: int = 20, timeout: int = 12):
    """
    Intenta usar la API pública de catálogo de VTEX (búsqueda por texto libre).
    Devuelve una lista de dicts: {"name","price","url","reference_id","product_id"}
    o None si el endpoint no responde JSON válido.
    """
    params = {
        "ft": query,
        "_from": 0,
        "_to": max(limit - 1, 0),
    }
    try:
        resp = requests.get(VTEX_SEARCH_URL, params=params, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        print(f"[VTEX API] Error de conexión: {e}", file=sys.stderr)
        return None

    if resp.status_code != 200:
        print(f"[VTEX API] Respuesta HTTP {resp.status_code}, probablemente bloqueado.", file=sys.stderr)
        return None

    try:
        data = resp.json()
    except ValueError:
        print("[VTEX API] La respuesta no es JSON (probablemente cayó a una página HTML/captcha).", file=sys.stderr)
        return None

    if not isinstance(data, list):
        return None

    return parse_vtex_products(data)


def search_vtex_by_reference(ref: str, timeout: int = 12):
    """
    Búsqueda DIRECTA por referencia/SKU exacto contra la API de VTEX, usando
    el filtro fq (en vez de búsqueda de texto libre ft). Esto es mucho más
    confiable que el matching por similitud cuando el usuario ya conoce el
    código exacto del producto (referencia de fábrica o SKU de Homecenter).

    Intenta, en este orden:
      1. fq=alternateIds_RefId:<ref>   (referencia de fábrica / modelo)
      2. fq=productId:<ref>            (solo si ref es numérico: ID interno VTEX)

    Devuelve la lista de productos encontrados (puede tener 0, 1 o varios
    ítems) o None si hubo error de conexión / respuesta inválida en ambos
    intentos.
    """
    attempts = [f"alternateIds_RefId:{ref}"]
    if ref.strip().isdigit():
        attempts.append(f"productId:{ref.strip()}")

    for fq in attempts:
        params = {"fq": fq, "_from": 0, "_to": 9}
        try:
            resp = requests.get(VTEX_SEARCH_URL, params=params, headers=HEADERS, timeout=timeout)
        except requests.RequestException as e:
            print(f"[VTEX API - referencia] Error de conexión: {e}", file=sys.stderr)
            continue
        if resp.status_code != 200:
            continue
        try:
            data = resp.json()
        except ValueError:
            continue
        if isinstance(data, list) and data:
            parsed = parse_vtex_products(data)
            if parsed:
                return parsed
    return []


def normalize_ref(text: str) -> str:
    """Normaliza un código/referencia para comparar sin importar espacios,
    guiones, puntos o mayúsculas: 'GSB-13 RE' == 'gsb13re'."""
    return re.sub(r"[\s\-\._/]", "", text or "").upper()


def looks_like_reference(query: str) -> bool:
    """
    Heurística para adivinar si el usuario escribió un código/referencia
    exacta (ej: 'GSB-13RE', '618871', 'DUAF-10-LA') en vez de una
    descripción libre (ej: 'taladro percutor bosch'). No es perfecta: es
    solo para decidir si vale la pena intentar la búsqueda directa por
    referencia antes de la búsqueda de texto libre.
    """
    q = query.strip()
    words = q.split()
    if len(words) > 3:
        return False
    digit_count = sum(c.isdigit() for c in q)
    letter_count = sum(c.isalpha() for c in q)
    if digit_count == 0:
        return False
    # Un solo token que mezcla letras y números (GSB13RE, DUAF-10-LA) o un
    # código puramente numérico de al menos 4 dígitos (618871).
    if any(any(c.isdigit() for c in w) and any(c.isalpha() for c in w) for w in words):
        return True
    if letter_count == 0 and digit_count >= 4:
        return True
    return False


def find_exact_reference_in_results(query: str, results):
    """
    Revisa una lista de resultados (de cualquier fuente: API, HTML o
    Playwright) buscando una coincidencia EXACTA de referencia/código,
    ya sea porque el campo reference_id/product_id coincide literalmente
    con la consulta, o porque el código aparece tal cual dentro del nombre
    del producto (normalizado). Devuelve el producto si lo encuentra, o
    None si no hay coincidencia exacta confiable.
    """
    nq = normalize_ref(query)
    if len(nq) < 3:
        return None  # muy corto para confiar en un match por substring

    for r in results:
        for field in ("reference_id", "product_id"):
            val = r.get(field)
            if val and normalize_ref(str(val)) == nq:
                return r

    for r in results:
        if nq in normalize_ref(r["name"]):
            return r

    return None


def search_html_fallback(query: str, limit: int = 20, timeout: int = 15):
    """
    Respaldo: hace scraping simple de la página de resultados de búsqueda.
    Requiere beautifulsoup4. Devuelve lista de dicts {"name","price","url"}
    o None si no se pudo extraer nada útil (la estructura del sitio puede
    cambiar; esta parte es más frágil por naturaleza).
    """
    if not HAS_BS4:
        print("[HTML fallback] Falta beautifulsoup4. Instala con: pip install beautifulsoup4 --break-system-packages", file=sys.stderr)
        return None

    search_url = f"{BASE_URL}/homecenter-co/search?Ntt={requests.utils.quote(query)}"
    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        print(f"[HTML fallback] Error de conexión: {e}", file=sys.stderr)
        return None

    if resp.status_code != 200:
        print(f"[HTML fallback] Respuesta HTTP {resp.status_code}.", file=sys.stderr)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    # Estos selectores son aproximados: sitios VTEX suelen renderizar resultados
    # vía JavaScript (React), así que el HTML crudo puede no traer los productos.
    # Si esto devuelve vacío, probablemente necesites un navegador headless
    # (ej. Playwright/Selenium) en vez de requests+BeautifulSoup.
    candidates = soup.select("[class*='product'], [data-testid*='product']")
    for card in candidates[:limit * 3]:
        text = card.get_text(" ", strip=True)
        price_match = re.search(r"\$\s?[\d\.,]+", text)
        if not price_match:
            continue
        price = parse_price(price_match.group(0))
        if not price:
            continue
        # nombre: heurística simple, toma el texto antes del precio
        name_part = text[: price_match.start()].strip()
        if len(name_part) < 3:
            continue
        link_tag = card.find("a", href=True)
        url = BASE_URL + link_tag["href"] if link_tag and link_tag["href"].startswith("/") else (link_tag["href"] if link_tag else "")
        results.append({"name": name_part, "price": price, "url": url})
        if len(results) >= limit:
            break

    return results if results else None


def search_playwright_fallback(query: str, limit: int = 20, timeout_ms: int = 25000, headless: bool = True):
    """
    Tercer nivel de respaldo: abre la página de resultados con un navegador
    real (Chromium vía Playwright), espera a que el JavaScript renderice los
    productos, y extrae nombre/precio/url del DOM ya cargado.

    Esto es más lento y pesado que las opciones anteriores, pero funciona
    aunque el sitio use React/Cloudflare-challenge, porque ejecuta JS de
    verdad en vez de solo leer el HTML crudo.

    Devuelve lista de dicts {"name","price","url"} o None si no se pudo.
    """
    if not HAS_PLAYWRIGHT:
        print(
            "[Playwright fallback] Falta playwright. Instala con:\n"
            "  pip install playwright --break-system-packages\n"
            "  python3 -m playwright install chromium",
            file=sys.stderr,
        )
        return None

    search_url = f"{BASE_URL}/homecenter-co/search?Ntt={requests.utils.quote(query)}"
    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="es-CO",
                viewport={"width": 1366, "height": 900},
            )
            page = context.new_page()
            page.goto(search_url, timeout=timeout_ms, wait_until="domcontentloaded")

            # Espera a que aparezca al menos un elemento que "huela" a tarjeta
            # de producto. Si el sitio cambia sus clases, este selector puede
            # necesitar ajuste (usa el inspector del navegador para revisarlo).
            try:
                page.wait_for_selector(
                    "[class*='product'], [data-testid*='product'], a[href*='/p']",
                    timeout=timeout_ms,
                )
            except Exception:
                pass  # seguimos igual e intentamos extraer lo que haya

            # Pequeño scroll para disparar carga perezosa (lazy load) de más productos
            for _ in range(3):
                page.mouse.wheel(0, 1500)
                page.wait_for_timeout(500)

            html = page.content()
            browser.close()
    except Exception as e:
        print(f"[Playwright fallback] Error: {e}", file=sys.stderr)
        return None

    if not HAS_BS4:
        print("[Playwright fallback] Falta beautifulsoup4 para parsear el HTML renderizado.", file=sys.stderr)
        return None

    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.select("[class*='product'], [data-testid*='product']")
    seen_names = set()

    for card in candidates:
        text = card.get_text(" ", strip=True)
        price_match = re.search(r"\$\s?[\d\.,]+", text)
        if not price_match:
            continue
        price = parse_price(price_match.group(0))
        if not price or price <= 0:
            continue
        name_part = text[: price_match.start()].strip()
        if len(name_part) < 3 or name_part in seen_names:
            continue
        seen_names.add(name_part)
        link_tag = card.find("a", href=True)
        url = ""
        if link_tag:
            href = link_tag["href"]
            url = BASE_URL + href if href.startswith("/") else href
        results.append({"name": name_part, "price": price, "url": url})
        if len(results) >= limit:
            break

    return results if results else None


def find_best_match(query: str, results):
    """Devuelve (mejor_resultado, score) usando similitud de texto sobre el nombre."""
    q_norm = normalize(query)
    best = None
    best_score = -1.0
    for r in results:
        r_norm = normalize(r["name"])
        score = difflib.SequenceMatcher(None, q_norm, r_norm).ratio()
        if score > best_score:
            best_score = score
            best = r
    return best, best_score


def main():
    parser = argparse.ArgumentParser(description="Busca el precio de un producto en Homecenter Colombia.")
    parser.add_argument("query", help="Nombre, descripción o referencia/SKU exacta del producto a buscar")
    parser.add_argument("--top", type=int, default=20, help="Cantidad máxima de resultados a considerar (default: 20)")
    parser.add_argument("--exact-threshold", type=float, default=0.92,
                         help="Umbral de similitud (0-1) para considerar un match por descripción como 'exacto' (default: 0.92)")
    parser.add_argument("--json", action="store_true", help="Imprime el resultado en formato JSON")
    parser.add_argument("--show-results", action="store_true", help="Muestra todos los productos encontrados")
    parser.add_argument("--no-playwright", action="store_true",
                         help="No usar el navegador headless como último respaldo, aunque esté instalado")
    parser.add_argument("--show-browser", action="store_true",
                         help="Si se usa Playwright, muestra la ventana del navegador (útil para depurar)")
    parser.add_argument("--ref", action="store_true",
                         help="Fuerza a tratar la consulta como una referencia/SKU exacta, "
                              "aunque la heurística automática no la detecte como tal")
    parser.add_argument("--no-ref-lookup", action="store_true",
                         help="Desactiva por completo la búsqueda directa por referencia/SKU "
                              "y el atajo de 'match exacto sin promedio'; siempre usa similitud+promedio")
    args = parser.parse_args()

    print(f"Buscando '{args.query}' en Homecenter Colombia...", file=sys.stderr)

    treat_as_reference = (not args.no_ref_lookup) and (args.ref or looks_like_reference(args.query))

    # --- Paso 0: si parece una referencia/SKU exacta, intenta el lookup directo ---
    # (fq=alternateIds_RefId / fq=productId), que es mucho más confiable que una
    # búsqueda de texto libre porque no depende de similitud aproximada.
    if treat_as_reference:
        print(f"'{args.query}' parece una referencia/SKU exacta, probando búsqueda directa...", file=sys.stderr)
        ref_results = search_vtex_by_reference(args.query)
        if ref_results:
            exact = find_exact_reference_in_results(args.query, ref_results)
            if exact:
                print_exact_reference_result(args.query, exact, "vtex_api_exact_reference", args.json)
                return

    # --- Cascada normal: API por texto -> HTML -> Playwright ---
    results = search_vtex_api(args.query, limit=args.top)
    source = "vtex_api"

    if not results:
        print("La API de VTEX no devolvió resultados usables, probando scraping HTML...", file=sys.stderr)
        time.sleep(1)
        results = search_html_fallback(args.query, limit=args.top)
        source = "html_fallback"

    if not results and not args.no_playwright:
        print("El scraping HTML simple no encontró nada, probando con navegador headless (Playwright)...", file=sys.stderr)
        time.sleep(1)
        results = search_playwright_fallback(args.query, limit=args.top, headless=not args.show_browser)
        source = "playwright_fallback"

    if not results:
        output = {
            "query": args.query,
            "found": False,
            "message": "No se encontraron productos por ninguno de los métodos disponibles "
                       "(API VTEX, scraping HTML simple, navegador headless). "
                       "Es posible que el sitio esté bloqueando solicitudes automatizadas, "
                       "que la estructura del HTML haya cambiado, o que falte instalar Playwright.",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2) if args.json else output["message"])
        sys.exit(1)

    if args.show_results:
        print("\nResultados encontrados:", file=sys.stderr)
        for r in results:
            print(f"  - {r['name']} : ${r['price']:,.0f}", file=sys.stderr)

    # --- Paso extra: aunque no se haya tratado como referencia desde el inicio,
    # revisa si el código/consulta aparece EXACTO dentro de algún resultado ya
    # obtenido (útil cuando la heurística no detectó que era una referencia,
    # pero el código igual aparece literal en el nombre del producto). ---
    if not args.no_ref_lookup:
        exact = find_exact_reference_in_results(args.query, results)
        if exact:
            print_exact_reference_result(args.query, exact, source, args.json)
            return

    # --- Sin match exacto por referencia: usar similitud + promedio, como antes ---
    best, score = find_best_match(args.query, results)
    prices = [r["price"] for r in results if r.get("price")]
    avg_price = mean(prices) if prices else None
    is_exact = score >= args.exact_threshold

    output = {
        "query": args.query,
        "found": True,
        "source": source,
        "match_type": "exacto por descripción" if is_exact else "más parecido",
        "similarity_score": round(score, 3),
        "matched_product": {
            "name": best["name"],
            "price": best["price"],
            "url": best.get("url", ""),
        },
        "average_price_of_results": round(avg_price, 2) if avg_price else None,
        "num_results_considered": len(results),
    }

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"\nBúsqueda: {args.query}")
        print(f"Fuente de datos: {source}")
        print(f"Coincidencia {'EXACTA POR DESCRIPCIÓN' if is_exact else 'MÁS PARECIDA'} (similitud {score:.0%}):")
        print(f"  {best['name']}")
        print(f"  Precio: ${best['price']:,.0f} COP")
        if best.get("url"):
            print(f"  URL: {best['url']}")
        if avg_price:
            print(f"\nPrecio promedio de los {len(results)} resultados considerados: ${avg_price:,.0f} COP")


def print_exact_reference_result(query: str, product: dict, source: str, as_json: bool):
    """
    Imprime el resultado cuando se encontró una coincidencia EXACTA por
    referencia/SKU. A diferencia del camino normal, aquí NO se calcula ni se
    muestra ningún promedio: el usuario pidió el precio real de esa
    referencia puntual, no un estimado.
    """
    output = {
        "query": query,
        "found": True,
        "source": source,
        "match_type": "referencia exacta",
        "matched_product": {
            "name": product["name"],
            "price": product["price"],
            "url": product.get("url", ""),
            "reference_id": product.get("reference_id"),
        },
    }
    if as_json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"\nBúsqueda: {query}")
        print(f"Fuente de datos: {source}")
        print("Coincidencia por REFERENCIA EXACTA (precio real, sin promediar):")
        print(f"  {product['name']}")
        print(f"  Precio: ${product['price']:,.0f} COP")
        if product.get("reference_id"):
            print(f"  Referencia: {product['reference_id']}")
        if product.get("url"):
            print(f"  URL: {product['url']}")


if __name__ == "__main__":
    main()

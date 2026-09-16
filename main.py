#!/usr/bin/env python3
"""
main.py

API web (FastAPI) que expone homecenter_price.py como un servicio HTTP.
No se modifica homecenter_price.py: este archivo solo lo importa y
reutiliza sus funciones, orquestando la misma cascada de búsqueda
(API de VTEX -> scraping HTML -> Playwright opcional) que ya usa el CLI.

Endpoints:
    GET /              -> info básica del servicio
    GET /health         -> chequeo de salud (para Render / monitoreo)
    GET /price?q=...    -> cotización de un producto
    GET /docs           -> documentación interactiva (Swagger, automática)

Ejecutar localmente:
    uvicorn main:app --reload

Variables de entorno:
    ENABLE_PLAYWRIGHT=true|false   (default: false)
        Si es "true" Y el paquete playwright + chromium están instalados,
        habilita el tercer nivel de respaldo (navegador headless). Se deja
        apagado por defecto porque en despliegues simples (sin Docker)
        Playwright normalmente no está disponible. Ver README.md.
"""

import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parent))
import homecenter_price as hc  # noqa: E402  (import después de ajustar sys.path)

ENABLE_PLAYWRIGHT = os.getenv("ENABLE_PLAYWRIGHT", "false").lower() == "true"

app = FastAPI(
    title="Homecenter Price API",
    description=(
        "API no oficial para consultar precios reales de productos en "
        "Homecenter Colombia (homecenter.com.co), usando el endpoint público "
        "de catálogo de VTEX y, como respaldo, scraping del sitio."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _formato_referencia_exacta(query: str, product: dict, source: str) -> dict:
    return {
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


def buscar_precio(query: str, top: int = 15, exact_threshold: float = 0.92) -> dict:
    """
    Réplica, como función que devuelve un dict (en vez de imprimir y hacer
    sys.exit), de la misma lógica que ya tiene main() en homecenter_price.py:
    referencia exacta -> API VTEX por texto -> HTML -> Playwright (opcional)
    -> similitud + promedio.
    """
    treat_as_reference = hc.looks_like_reference(query)

    # Paso 0: lookup directo por referencia/SKU (más confiable que texto libre)
    if treat_as_reference:
        ref_results = hc.search_vtex_by_reference(query)
        if ref_results:
            exact = hc.find_exact_reference_in_results(query, ref_results)
            if exact:
                return _formato_referencia_exacta(query, exact, "vtex_api_exact_reference")

    # Cascada normal
    results = hc.search_vtex_api(query, limit=top)
    source = "vtex_api"

    if not results:
        results = hc.search_html_fallback(query, limit=top)
        source = "html_fallback"

    if not results and ENABLE_PLAYWRIGHT and hc.HAS_PLAYWRIGHT:
        results = hc.search_playwright_fallback(query, limit=top, headless=True)
        source = "playwright_fallback"

    if not results:
        return {
            "query": query,
            "found": False,
            "message": (
                "No se encontraron productos por ninguno de los métodos disponibles. "
                "Es posible que el sitio esté bloqueando la solicitud, que la "
                "estructura del HTML haya cambiado, o que Playwright esté "
                "deshabilitado en este despliegue."
            ),
        }

    # Coincidencia exacta por código, aunque no se haya detectado desde el inicio
    exact = hc.find_exact_reference_in_results(query, results)
    if exact:
        return _formato_referencia_exacta(query, exact, source)

    best, score = hc.find_best_match(query, results)
    prices = [r["price"] for r in results if r.get("price")]
    avg_price = sum(prices) / len(prices) if prices else None
    is_exact = score >= exact_threshold

    return {
        "query": query,
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


@app.get("/")
def root():
    return {
        "service": "Homecenter Price API",
        "status": "ok",
        "playwright_enabled": ENABLE_PLAYWRIGHT and hc.HAS_PLAYWRIGHT,
        "docs": "/docs",
        "example": "/price?q=taladro percutor bosch",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/price")
def price(
    q: str = Query(..., min_length=1, description="Nombre, descripción o referencia/SKU del producto"),
    top: int = Query(15, ge=1, le=50, description="Cantidad de resultados a considerar"),
):
    try:
        return buscar_precio(q, top=top)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error consultando Homecenter: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)

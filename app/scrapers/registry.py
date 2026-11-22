from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, Optional

from app.models import ProductOffer, StoreInfo, StoreSearchStatus
from app.scrapers.alkosto import AlkostoScraper
from app.scrapers.base import BaseStoreScraper
from app.scrapers.exito import ExitoScraper
from app.scrapers.falabella import FalabellaScraper
from app.scrapers.homecenter import HomecenterScraper
from app.scrapers.jumbo import JumboScraper
from app.scrapers.mercadolibre import MercadoLibreScraper
from app.scrapers.olimpica import OlimpicaScraper
from app.services.firecrawl_service import firecrawl_service

ALL_SCRAPERS: list[BaseStoreScraper] = [
    ExitoScraper(),
    AlkostoScraper(),
    FalabellaScraper(),
    JumboScraper(),
    OlimpicaScraper(),
    HomecenterScraper(),
    MercadoLibreScraper(),
]

SCRAPER_MAP: dict[str, BaseStoreScraper] = {s.store.id: s for s in ALL_SCRAPERS}


def list_stores() -> list[StoreInfo]:
    return [s.store for s in ALL_SCRAPERS]


def get_selected_scrapers(store_ids: Optional[list[str]] = None) -> list[BaseStoreScraper]:
    if not store_ids:
        return list(ALL_SCRAPERS)
    selected = [SCRAPER_MAP[sid] for sid in store_ids if sid in SCRAPER_MAP]
    return selected or list(ALL_SCRAPERS)


async def stream_store_searches(
    query: str,
    store_ids: Optional[list[str]] = None,
    max_results: int = 10,
    use_playwright: bool = True,
    timeout: float = 25.0,
) -> AsyncIterator[tuple[list[ProductOffer], StoreSearchStatus]]:
    """Ejecuta todos los scrapers en paralelo y entrega los resultados a medida que cada tienda responde."""
    scrapers = get_selected_scrapers(store_ids)

    async def _run_one(scraper: BaseStoreScraper):
        # 1) Si Firecrawl está en modo principal, consultar vía Firecrawl primero
        if firecrawl_service.is_primary_mode():
            t0 = time.perf_counter()
            try:
                fc_offers = await asyncio.wait_for(
                    firecrawl_service.scrape_store_offers(
                        store=scraper.store,
                        query=query,
                        max_results=max_results,
                        timeout=max(timeout, 30.0),
                    ),
                    timeout=max(timeout, 30.0) + 5.0,
                )
                if fc_offers:
                    elapsed = int((time.perf_counter() - t0) * 1000)
                    return fc_offers[:max_results], StoreSearchStatus(
                        store_id=scraper.store.id,
                        store_name=scraper.store.name,
                        store_color=scraper.store.color,
                        status="ok",
                        count=len(fc_offers[:max_results]),
                        elapsed_ms=elapsed,
                        method="firecrawl",
                    )
            except Exception:
                pass

        # 2) Scraper local especializado (API / HTML / Playwright)
        try:
            offers, status = await asyncio.wait_for(
                scraper.safe_search(
                    query=query,
                    max_results=max_results,
                    use_playwright=use_playwright,
                    timeout=timeout,
                ),
                timeout=timeout + 5.0,
            )
        except asyncio.TimeoutError:
            offers, status = [], StoreSearchStatus(
                store_id=scraper.store.id,
                store_name=scraper.store.name,
                store_color=scraper.store.color,
                status="timeout",
                count=0,
                elapsed_ms=int(timeout * 1000),
                method="timeout",
                error_message="Tiempo de espera agotado.",
            )

        # 3) Respaldo automático con Firecrawl si la tienda falló o devolvió 0 ofertas
        if not offers and firecrawl_service.is_enabled_for_fallback() and not firecrawl_service.is_primary_mode():
            t_fc = time.perf_counter()
            try:
                fc_offers = await asyncio.wait_for(
                    firecrawl_service.scrape_store_offers(
                        store=scraper.store,
                        query=query,
                        max_results=max_results,
                        timeout=max(timeout, 30.0),
                    ),
                    timeout=max(timeout, 30.0) + 5.0,
                )
                if fc_offers:
                    elapsed = status.elapsed_ms + int((time.perf_counter() - t_fc) * 1000)
                    return fc_offers[:max_results], StoreSearchStatus(
                        store_id=scraper.store.id,
                        store_name=scraper.store.name,
                        store_color=scraper.store.color,
                        status="ok",
                        count=len(fc_offers[:max_results]),
                        elapsed_ms=elapsed,
                        method="firecrawl-fallback",
                    )
            except Exception:
                pass

        return offers, status

    tasks = [asyncio.create_task(_run_one(s)) for s in scrapers]
    for coro in asyncio.as_completed(tasks):
        offers, status = await coro
        yield offers, status

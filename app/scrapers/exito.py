from __future__ import annotations

from app.models import StoreInfo
from app.scrapers.vtex import VTEXStoreScraper


class ExitoScraper(VTEXStoreScraper):
    store = StoreInfo(
        id="exito",
        name="Éxito",
        domain="www.exito.com",
        color="#eab308",
        badge_bg="rgba(234, 179, 8, 0.16)",
        card_name="Tarjeta Éxito",
    )
    base_url = "https://www.exito.com"
    search_page_template = "https://www.exito.com/s?q={q}&sort=score_desc"
    card_keywords = ("tarjeta exito", "tarjeta éxito", "tuya", "puntos colombia")

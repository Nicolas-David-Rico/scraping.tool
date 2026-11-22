from __future__ import annotations

from app.models import StoreInfo
from app.scrapers.vtex import VTEXStoreScraper


class OlimpicaScraper(VTEXStoreScraper):
    store = StoreInfo(
        id="olimpica",
        name="Olímpica",
        domain="www.olimpica.com",
        color="#dc2626",
        badge_bg="rgba(220, 38, 38, 0.16)",
        card_name="Tarjeta Plata",
    )
    base_url = "https://www.olimpica.com"
    search_page_template = "https://www.olimpica.com/{q}?_q={q}&map=ft"
    card_keywords = ("tarjeta plata", "plata", "olimpica", "olímpica")

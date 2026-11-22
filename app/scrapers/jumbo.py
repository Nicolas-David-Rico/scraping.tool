from __future__ import annotations

from app.models import StoreInfo
from app.scrapers.vtex import VTEXStoreScraper


class JumboScraper(VTEXStoreScraper):
    store = StoreInfo(
        id="jumbo",
        name="Jumbo",
        domain="www.jumbocolombia.com",
        color="#16a34a",
        badge_bg="rgba(22, 163, 74, 0.16)",
        card_name="Tarjeta Cencosud",
    )
    base_url = "https://www.jumbocolombia.com"
    search_page_template = "https://www.jumbocolombia.com/{q}?_q={q}&map=ft"
    card_keywords = ("cencosud", "prime", "tarjeta")

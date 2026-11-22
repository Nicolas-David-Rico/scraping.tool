from __future__ import annotations

import hashlib
import re
import time
from abc import ABC, abstractmethod
from typing import Optional
import httpx

from app.models import ProductOffer, StoreInfo, StoreSearchStatus

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, application/xhtml+xml, */*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9,en-US;q=0.7,en;q=0.6",
    "Cache-Control": "no-cache",
}


def make_offer_id(store_id: str, url: str, title: str) -> str:
    raw = f"{store_id}|{url}|{title}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:14]


def parse_cop_price(value: object) -> Optional[float]:
    """Convierte precios en formato colombiano ('$ 1.499.900', '1.499.900,00', 1499900) a float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        val = float(value)
        return val if val > 0 else None

    s = str(value).strip()
    if not s:
        return None

    # Keep digits, dots, commas
    m = re.search(r"[\d\.,]+", s)
    if not m:
        return None
    num_str = m.group(0)

    # Colombian COP typically uses '.' for thousands and ',' for decimals (rarely decimals at all)
    if "," in num_str and "." in num_str:
        if num_str.rfind(",") > num_str.rfind("."):
            # 1.299.900,00
            num_str = num_str.replace(".", "").split(",")[0]
        else:
            # 1,299,900.00
            num_str = num_str.replace(",", "").split(".")[0]
    elif "." in num_str:
        parts = num_str.split(".")
        if len(parts[-1]) == 2 and len(parts) == 2 and len(parts[0]) >= 4:
            # e.g. 1499900.00
            num_str = parts[0]
        else:
            # e.g. 1.499.900
            num_str = num_str.replace(".", "")
    elif "," in num_str:
        parts = num_str.split(",")
        if len(parts[-1]) == 2 and len(parts) == 2:
            num_str = parts[0]
        else:
            num_str = num_str.replace(",", "")

    digits = re.sub(r"[^\d]", "", num_str)
    if not digits:
        return None
    val = float(digits)
    return val if val >= 500 else None  # Avoid capturing tiny garbage numbers as COP prices


def calc_discount(price: float, list_price: Optional[float]) -> Optional[int]:
    if not list_price or list_price <= price or list_price <= 0:
        return None
    pct = round((1.0 - (price / list_price)) * 100)
    return pct if 1 <= pct <= 95 else None


class BaseStoreScraper(ABC):
    store: StoreInfo

    def create_http_client(self, timeout: float = 18.0, extra_headers: Optional[dict[str, str]] = None) -> httpx.AsyncClient:
        headers = dict(DEFAULT_HEADERS)
        if extra_headers:
            headers.update(extra_headers)
        return httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
            http2=False,
        )

    async def safe_search(
        self,
        query: str,
        max_results: int = 10,
        use_playwright: bool = True,
        timeout: float = 25.0,
    ) -> tuple[list[ProductOffer], StoreSearchStatus]:
        start = time.perf_counter()
        try:
            offers, method = await self.search(
                query=query,
                max_results=max_results,
                use_playwright=use_playwright,
                timeout=timeout,
            )
            elapsed = int((time.perf_counter() - start) * 1000)
            status = "ok" if offers else "empty"
            return offers[:max_results], StoreSearchStatus(
                store_id=self.store.id,
                store_name=self.store.name,
                store_color=self.store.color,
                status=status,
                count=len(offers[:max_results]),
                elapsed_ms=elapsed,
                method=method,
            )
        except TimeoutError:
            elapsed = int((time.perf_counter() - start) * 1000)
            return [], StoreSearchStatus(
                store_id=self.store.id,
                store_name=self.store.name,
                store_color=self.store.color,
                status="timeout",
                count=0,
                elapsed_ms=elapsed,
                method="timeout",
                error_message="Tiempo de espera agotado consultando la tienda.",
            )
        except Exception as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            msg = str(exc)[:180]
            status_kind = "blocked" if any(k in msg.lower() for k in ("403", "cloudflare", "captcha", "akamai", "blocked")) else "error"
            return [], StoreSearchStatus(
                store_id=self.store.id,
                store_name=self.store.name,
                store_color=self.store.color,
                status=status_kind,
                count=0,
                elapsed_ms=elapsed,
                method="error",
                error_message=msg,
            )

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 10,
        use_playwright: bool = True,
        timeout: float = 25.0,
    ) -> tuple[list[ProductOffer], str]:
        """Debe devolver (ofertas, metodo_usado)."""
        raise NotImplementedError

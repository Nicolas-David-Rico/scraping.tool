from __future__ import annotations

import math
import re
import statistics
import unicodedata
from typing import Optional
from app.models import PriceStats, ProductOffer, QueryIntent

STOP_WORDS = {
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "con", "para", "por", "en", "y", "o", "a", "al", "que", "es", "su",
    "busco", "quiero", "necesito", "comprar", "oferta", "ofertas", "precio",
    "precios", "barato", "barata", "economico", "economica", "mejor", "mejores",
    "promocion", "promociones", "descuento", "descuentos", "colombia", "tienda",
    "tiendas", "referencia", "ref", "producto", "comparar", "buscar",
}

ACCESSORY_KEYWORDS = {
    "funda", "estuche", "carcasa", "protector", "vidrio templado", "mica",
    "cable", "cargador", "adaptador", "soporte", "base", "control remoto",
    "repuesto", "correa", "pulso", "manilla", "tapa", "bateria", "skin",
    "sticker", "forro", "pelicula", "hidrogel", "audifono", "bolso", "maletin",
    "morral", "mousepad", "teclado", "limpiador", "kit de limpieza",
}


def normalize_text(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s\.\-\"]+", " ", without_accents).strip()


def tokenize(text: str) -> list[str]:
    norm = normalize_text(text)
    tokens = re.findall(r"[a-z0-9]+", norm)
    return [t for t in tokens if len(t) >= 2 and t not in STOP_WORDS]


def looks_like_model_code(token: str) -> bool:
    """Detecta referencias tipo SM-S928B, UN55CU7000, RTX4060, WH1000XM5."""
    return bool(re.search(r"[a-z]", token) and re.search(r"\d", token) and len(token) >= 4)


def compute_relevance(offer: ProductOffer, intent: QueryIntent) -> float:
    query_tokens = tokenize(intent.search_query)
    if not query_tokens:
        return 1.0

    title_norm = normalize_text(f"{offer.title} {offer.brand} {offer.sku or ''}")
    title_tokens = set(re.findall(r"[a-z0-9]+", title_norm))

    # Exclude terms explicitly requested
    for ex in intent.exclude_terms:
        ex_norm = normalize_text(ex)
        if ex_norm and ex_norm in title_norm:
            return 0.0

    # Check if user is searching for an accessory; if NOT, heavily penalize accessories
    query_norm = normalize_text(intent.search_query)
    user_wants_accessory = any(acc in query_norm for acc in ACCESSORY_KEYWORDS)
    if not user_wants_accessory:
        for acc in ACCESSORY_KEYWORDS:
            # Match whole word/phrase in title
            if re.search(rf"\b{re.escape(acc)}\b", title_norm):
                return 0.05

    matched = 0.0
    total_weight = 0.0
    missing_numeric_spec = False
    for tok in query_tokens:
        is_spec = looks_like_model_code(tok) or tok.isdigit()
        weight = 2.6 if looks_like_model_code(tok) else (2.2 if tok.isdigit() else 1.0)
        total_weight += weight
        if tok in title_tokens:
            matched += weight
        elif len(tok) >= 4 and tok in title_norm:
            matched += weight * 0.85
        elif tok.isdigit() and re.search(rf"(?<!\d){re.escape(tok)}(?!\d)", title_norm):
            matched += weight * 0.95
        elif is_spec:
            missing_numeric_spec = True

    score = matched / max(total_weight, 1.0)
    if missing_numeric_spec:
        score *= 0.65

    # Bonus for must_include terms
    if intent.must_include:
        must_hits = sum(1 for m in intent.must_include if normalize_text(m) in title_norm)
        must_ratio = must_hits / len(intent.must_include)
        score = score * 0.7 + must_ratio * 0.3

    return round(min(score, 1.0), 3)


def filter_and_rank_offers(
    offers: list[ProductOffer],
    intent: QueryIntent,
) -> tuple[list[ProductOffer], PriceStats, Optional[ProductOffer], Optional[ProductOffer], Optional[ProductOffer]]:
    """Filtra resultados irrelevantes, deduplica, calcula estadísticas y ordena por mejor precio."""
    # Deduplicate by (store_id, normalized url or title+price)
    seen: set[str] = set()
    cleaned: list[ProductOffer] = []

    for offer in offers:
        if offer.price <= 0:
            continue
        if intent.min_price and offer.price < intent.min_price:
            continue
        if intent.max_price and offer.price > intent.max_price:
            continue

        dedup_key = f"{offer.store_id}|{offer.url.split('?')[0].lower()}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        offer.relevance_score = compute_relevance(offer, intent)
        cleaned.append(offer)

    # Keep relevant offers (adaptive threshold: prefer exact spec matches when >= 3 exist)
    exact_rel = [o for o in cleaned if o.relevance_score >= 0.70]
    high_rel = [o for o in cleaned if o.relevance_score >= 0.45]
    if len(exact_rel) >= 3:
        filtered = exact_rel
    elif len(high_rel) >= 3:
        filtered = high_rel
    else:
        med_rel = [o for o in cleaned if o.relevance_score >= 0.25]
        filtered = med_rel if med_rel else [o for o in cleaned if o.relevance_score > 0.08]

    # Remove extreme low-price outliers (likely leftover accessories) if we have enough results
    if len(filtered) >= 5:
        prices_sorted = sorted(o.price for o in filtered)
        median_p = statistics.median(prices_sorted)
        # Drop items cheaper than 18% of median price unless user explicitly searched low price
        filtered = [o for o in filtered if o.price >= median_p * 0.18] or filtered

    # Sort primarily by effective price (lowest first), keeping higher relevance first among close ties
    filtered.sort(key=lambda o: (0 if o.in_stock else 1, o.price, -o.relevance_score))

    if not filtered:
        return [], PriceStats(), None, None, None

    prices = [o.price for o in filtered]
    min_p = min(prices)
    max_p = max(prices)
    avg_p = round(sum(prices) / len(prices))
    med_p = round(statistics.median(prices))
    stores_with_results = len({o.store_id for o in filtered})

    discounts = [o.discount_percent for o in filtered if o.discount_percent and o.discount_percent > 0]
    max_disc = max(discounts) if discounts else None

    stats = PriceStats(
        total_offers=len(filtered),
        stores_with_results=stores_with_results,
        min_price=min_p,
        max_price=max_p,
        avg_price=avg_p,
        median_price=med_p,
        savings_vs_max=round(max_p - min_p) if max_p > min_p else 0,
        savings_vs_avg=round(avg_p - min_p) if avg_p > min_p else 0,
        max_discount_percent=max_disc,
    )

    # Best overall offer: lowest price among in-stock & decent relevance
    in_stock = [o for o in filtered if o.in_stock] or filtered
    best_offer = min(in_stock, key=lambda o: o.price)

    # Best card offer (if any store offers a lower price with store card)
    card_candidates = [o for o in in_stock if o.card_price and o.card_price > 0]
    best_card_offer = min(card_candidates, key=lambda o: o.card_price or math.inf) if card_candidates else None

    # Best discount offer
    disc_candidates = [o for o in in_stock if o.discount_percent and o.discount_percent > 0]
    best_discount_offer = (
        max(disc_candidates, key=lambda o: (o.discount_percent or 0, -o.price))
        if disc_candidates
        else None
    )

    return filtered, stats, best_offer, best_card_offer, best_discount_offer


def format_cop(amount: Optional[float]) -> str:
    if amount is None:
        return "N/A"
    return f"${int(round(amount)):,}".replace(",", ".")

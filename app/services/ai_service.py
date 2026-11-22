from __future__ import annotations

import json
import re
from typing import Any, Optional
import httpx

from app.config import settings_manager
from app.models import PriceStats, ProductOffer, QueryIntent, StoreSearchStatus
from app.services.comparator import format_cop


class AIService:
    """Servicio de IA compatible con Google Gemini y OpenAI (vía REST, con respaldo local)."""

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
        provider_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Retorna (texto, provider_usado, error)."""
        if provider_override and api_key_override:
            provider = provider_override
            api_key = api_key_override
            model = model_override or ("gemini-2.5-flash" if provider == "gemini" else "gpt-4o-mini")
        else:
            provider, api_key, model = settings_manager.get_active_ai_config()

        if not provider or not api_key:
            return None, None, None

        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                if provider == "gemini":
                    url = (
                        f"https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{model}:generateContent?key={api_key}"
                    )
                    payload: dict[str, Any] = {
                        "system_instruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                        "generationConfig": {
                            "temperature": temperature,
                            "maxOutputTokens": 1400,
                        },
                    }
                    if json_mode:
                        payload["generationConfig"]["responseMimeType"] = "application/json"

                    resp = await client.post(url, json=payload)
                    if resp.status_code != 200:
                        detail = resp.text[:300]
                        return None, provider, f"Gemini HTTP {resp.status_code}: {detail}"
                    data = resp.json()
                    candidates = data.get("candidates") or []
                    if not candidates:
                        return None, provider, "Gemini no devolvió candidatos de respuesta."
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    text = "".join(p.get("text", "") for p in parts).strip()
                    return text, provider, None

                elif provider == "openai":
                    url = "https://api.openai.com/v1/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": model,
                        "temperature": temperature,
                        "max_tokens": 1400,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    }
                    if json_mode:
                        payload["response_format"] = {"type": "json_object"}

                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code != 200:
                        detail = resp.text[:300]
                        return None, provider, f"OpenAI HTTP {resp.status_code}: {detail}"
                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        return None, provider, "OpenAI no devolvió respuesta."
                    text = ((choices[0].get("message") or {}).get("content") or "").strip()
                    return text, provider, None

                return None, provider, f"Proveedor no soportado: {provider}"
        except Exception as exc:
            return None, provider, f"Error conectando con {provider}: {exc}"

    async def test_connection(
        self,
        provider: str,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        cfg = settings_manager.get_all()
        if not api_key or api_key.startswith("•••"):
            api_key = cfg.get("gemini_api_key" if provider == "gemini" else "openai_api_key", "")
        if not model:
            model = cfg.get("gemini_model" if provider == "gemini" else "openai_model")
        if not api_key:
            return {"ok": False, "message": f"No hay API Key configurada para {provider}."}

        text, used, err = await self._call_llm(
            "Responde brevemente en español.",
            "Confirma que estás activo diciendo 'Conexión exitosa'.",
            provider_override=provider,
            api_key_override=api_key,
            model_override=model,
        )
        if err or not text:
            return {"ok": False, "message": err or "Respuesta vacía del proveedor."}
        return {"ok": True, "provider": used, "model": model, "message": f"Conexión exitosa con {used} ({model})."}

    async def parse_query_intent(self, raw_message: str) -> QueryIntent:
        """Interpreta el mensaje del chat en una búsqueda optimizada para tiendas colombianas."""
        local_intent = self._parse_query_locally(raw_message)

        system_prompt = (
            "Eres un asistente experto en comercio electrónico y compras en grandes superficies de Colombia "
            "(Éxito, Alkosto, Falabella, Jumbo, Olímpica, Homecenter, Mercado Libre).\n"
            "Tu tarea es analizar la solicitud del usuario y extraer una consulta de búsqueda limpia y efectiva "
            "para buscadores de tiendas.\n"
            "Devuelve SOLO un objeto JSON con las claves:\n"
            "- search_query (string): término corto y preciso para buscar en tiendas (conserva marcas y códigos de referencia exactos).\n"
            "- must_include (array de strings): 1 a 4 palabras clave obligatorias (marca, modelo, tamaño).\n"
            "- exclude_terms (array de strings): términos a excluir si el usuario los rechazó.\n"
            "- min_price (number o null): precio mínimo en pesos colombianos (COP) si se menciona.\n"
            "- max_price (number o null): precio máximo en pesos colombianos (COP) si se menciona (ej. '2 millones' -> 2000000, '500 mil' -> 500000).\n"
            "- category_hint (string o null): categoría breve (ej. 'Televisores', 'Celulares', 'Electrodomésticos').\n"
            "- intent_summary (string): resumen de una línea en español de lo que se va a buscar."
        )

        text, _, err = await self._call_llm(
            system_prompt,
            f"Solicitud del usuario: {raw_message}",
            json_mode=True,
            temperature=0.1,
        )
        if not text or err:
            return local_intent

        try:
            parsed = json.loads(self._extract_json_block(text))
            sq = str(parsed.get("search_query") or local_intent.search_query).strip()
            return QueryIntent(
                raw_message=raw_message,
                search_query=sq or raw_message.strip(),
                must_include=[str(x).strip() for x in (parsed.get("must_include") or []) if str(x).strip()],
                exclude_terms=[str(x).strip() for x in (parsed.get("exclude_terms") or []) if str(x).strip()],
                min_price=self._to_float(parsed.get("min_price")),
                max_price=self._to_float(parsed.get("max_price")),
                category_hint=parsed.get("category_hint"),
                intent_summary=str(parsed.get("intent_summary") or f"Buscando mejores ofertas para «{sq}»"),
                used_ai=True,
            )
        except Exception:
            return local_intent

    async def generate_comparison_analysis(
        self,
        intent: QueryIntent,
        offers: list[ProductOffer],
        stats: PriceStats,
        store_statuses: list[StoreSearchStatus],
    ) -> tuple[str, bool, Optional[str], Optional[str]]:
        """Genera la comparativa y recomendación en español (con IA si hay API key, o motor local)."""
        if not offers:
            failed = [s.store_name for s in store_statuses if s.status != "ok"]
            msg = (
                f"No se encontraron ofertas relevantes para **«{intent.search_query}»** "
                "con los filtros actuales.\n\n"
                "**Sugerencias:**\n"
                "- Prueba con una referencia más corta (por ejemplo, marca + modelo sin características extra).\n"
                "- Verifica si pusiste un límite de precio muy bajo."
            )
            if failed:
                msg += f"\n- Tiendas sin resultados o con bloqueo temporal: {', '.join(failed)}."
            return msg, False, None, None

        # Build compact summary of top offers (up to 18) for LLM context
        top_offers = offers[:18]
        offers_payload = []
        for idx, o in enumerate(top_offers, start=1):
            offers_payload.append(
                {
                    "rank": idx,
                    "tienda": o.store_name,
                    "titulo": o.title,
                    "marca": o.brand,
                    "precio_cop": int(round(o.price)),
                    "precio_lista_cop": int(round(o.list_price)) if o.list_price else None,
                    "descuento_pct": o.discount_percent,
                    "precio_tarjeta_cop": int(round(o.card_price)) if o.card_price else None,
                    "promo_tarjeta": o.card_promo_text,
                    "promos": o.promo_badges,
                    "envio": o.shipping_info,
                    "vendedor": o.seller,
                    "url": o.url,
                }
            )

        system_prompt = (
            "Eres un experto asesor de compras en Colombia especializado en grandes superficies "
            "(Éxito, Alkosto, Falabella, Jumbo, Olímpica, Homecenter y Mercado Libre).\n"
            "Analiza las ofertas REALES proporcionadas y entrega una comparativa clara, honesta y accionable en español.\n"
            "Reglas estrictas:\n"
            "- Usa ÚNICAMENTE los precios, tiendas y datos del JSON entregado. Nunca inventes precios ni enlaces.\n"
            "- Formatea todos los precios en pesos colombianos con punto de miles (ej. $1.499.900).\n"
            "- Si notas que alguna oferta barata parece ser un modelo inferior, reacondicionado, diferente capacidad o accesorio, adviértelo.\n"
            "- Destaca si existe un mejor precio pagando con tarjeta propia de la tienda (Tarjeta Éxito, CMR, Cencosud, etc.).\n"
            "- Estructura la respuesta en Markdown limpio con:\n"
            "  1. **Mejor opción recomendada** (tienda, precio y razón)\n"
            "  2. **Promociones y descuentos destacados**\n"
            "  3. **Comparativa entre tiendas** (diferencia de precios y ahorro potencial)\n"
            "  4. **Recomendación práctica antes de comprar**\n"
            "- Sé conciso (máximo ~260 palabras)."
        )

        user_prompt = json.dumps(
            {
                "consulta_usuario": intent.raw_message,
                "busqueda_normalizada": intent.search_query,
                "estadisticas": stats.model_dump(),
                "tiendas_consultadas": [
                    {"tienda": s.store_name, "estado": s.status, "encontrados": s.count}
                    for s in store_statuses
                ],
                "ofertas_ordenadas_por_precio": offers_payload,
            },
            ensure_ascii=False,
        )

        text, provider, err = await self._call_llm(system_prompt, user_prompt, temperature=0.3)
        if text:
            return text, True, provider, None

        # Local deterministic fallback analysis
        fallback = self._build_local_analysis(intent, offers, stats, store_statuses)
        return fallback, False, provider, err

    async def answer_followup(
        self,
        question: str,
        offers: list[ProductOffer],
        intent: QueryIntent,
        stats: PriceStats,
        history: list[dict[str, str]],
    ) -> tuple[str, bool, Optional[str], Optional[str]]:
        """Responde preguntas de seguimiento sobre los resultados actuales sin volver a hacer scraping."""
        top_offers = [
            {
                "tienda": o.store_name,
                "titulo": o.title,
                "precio_cop": int(round(o.price)),
                "precio_lista_cop": int(round(o.list_price)) if o.list_price else None,
                "descuento_pct": o.discount_percent,
                "precio_tarjeta_cop": int(round(o.card_price)) if o.card_price else None,
                "promo_tarjeta": o.card_promo_text,
                "promos": o.promo_badges,
                "vendedor": o.seller,
                "envio": o.shipping_info,
                "url": o.url,
            }
            for o in offers[:20]
        ]

        system_prompt = (
            "Eres un asesor de compras en Colombia. Responde la pregunta de seguimiento del usuario "
            "basándote EXCLUSIVAMENTE en los resultados de búsqueda actuales proporcionados en JSON. "
            "Formatea precios en COP (ej. $1.299.900). Incluye links en Markdown [Ver oferta](url) cuando recomiendes productos. "
            "Responde en español, claro y conciso."
        )
        context_blob = json.dumps(
            {
                "busqueda_original": intent.raw_message,
                "estadisticas": stats.model_dump(),
                "ofertas": top_offers,
                "historial_reciente": history[-6:],
            },
            ensure_ascii=False,
        )
        user_prompt = f"Contexto de ofertas:\n{context_blob}\n\nPregunta del usuario: {question}"

        text, provider, err = await self._call_llm(system_prompt, user_prompt, temperature=0.3)
        if text:
            return text, True, provider, None

        # Local fallback if no API key configured
        best = offers[0] if offers else None
        if not best:
            return "No hay ofertas cargadas para responder esta consulta.", False, provider, err

        msg = (
            "Configura tu API Key de **Gemini** u **OpenAI** en el botón de **Configuración IA** "
            "para hacer preguntas conversacionales libres sobre los resultados.\n\n"
            f"Mientras tanto, el precio más bajo encontrado sigue siendo **{best.title}** en "
            f"**{best.store_name}** por **{format_cop(best.price)}** ([ver oferta]({best.url}))."
        )
        return msg, False, provider, err

    @staticmethod
    def _build_local_analysis(
        intent: QueryIntent,
        offers: list[ProductOffer],
        stats: PriceStats,
        store_statuses: list[StoreSearchStatus],
    ) -> str:
        best = offers[0]
        lines = [
            f"### Comparativa rápida para «{intent.search_query}»",
            f"- **Mejor precio encontrado:** **{format_cop(best.price)}** en **{best.store_name}** — [{best.title}]({best.url})",
        ]

        if stats.savings_vs_max and stats.savings_vs_max > 0:
            lines.append(
                f"- **Ahorro potencial:** hasta **{format_cop(stats.savings_vs_max)}** "
                f"frente al precio más alto encontrado ({format_cop(stats.max_price)}). "
                f"Promedio del mercado: **{format_cop(stats.avg_price)}**."
            )

        card_offers = [o for o in offers if o.card_price and o.card_price < o.price]
        if card_offers:
            best_card = min(card_offers, key=lambda o: o.card_price or o.price)
            lines.append(
                f"- **Mejor oferta con tarjeta de tienda:** **{format_cop(best_card.card_price)}** en "
                f"**{best_card.store_name}** ({best_card.card_promo_text or 'Tarjeta aliada'})."
            )

        disc_offers = [o for o in offers if o.discount_percent and o.discount_percent > 0]
        if disc_offers:
            top_disc = max(disc_offers, key=lambda o: o.discount_percent or 0)
            lines.append(
                f"- **Mayor descuento publicado:** **-{top_disc.discount_percent}%** en "
                f"**{top_disc.store_name}** ({format_cop(top_disc.price)}, antes {format_cop(top_disc.list_price)})."
            )

        # Per-store lowest price summary
        by_store: dict[str, ProductOffer] = {}
        for o in offers:
            if o.store_id not in by_store or o.price < by_store[o.store_id].price:
                by_store[o.store_id] = o

        if len(by_store) > 1:
            store_bits = [
                f"**{o.store_name}**: {format_cop(o.price)}"
                for o in sorted(by_store.values(), key=lambda x: x.price)
            ]
            lines.append("- **Mínimo por tienda:** " + " · ".join(store_bits))

        lines.append(
            "\n> *Tip: Agrega tu API Key de **Gemini** u **OpenAI** en **Configuración IA** "
            "para activar análisis inteligente de variantes, detección de ofertas reales y chat interactivo.*"
        )
        return "\n".join(lines)

    @staticmethod
    def _parse_query_locally(raw_message: str) -> QueryIntent:
        text = raw_message.strip()
        max_price: Optional[float] = None
        min_price: Optional[float] = None

        # Parse Colombian budget phrases: "menos de 2 millones", "hasta 1.5 millones", "máximo 800 mil", "bajo 2500000"
        m_max_mil = re.search(
            r"(?:menos de|hasta|maximo|máximo|presupuesto(?: de)?|bajo|menor a)\s*\$?\s*(\d+(?:[\.,]\d+)?)\s*(millon(?:es)?|m\b|mil|k\b)?",
            text,
            re.IGNORECASE,
        )
        if m_max_mil:
            val_str = m_max_mil.group(1)
            unit = (m_max_mil.group(2) or "").lower()
            max_price = AIService._parse_colombian_budget(val_str, unit)
            text = (text[: m_max_mil.start()] + " " + text[m_max_mil.end() :]).strip()

        # Strip conversational filler prefixes
        cleaned = re.sub(
            r"^(?:hola[,!\s]*)?(?:por favor\s+)?(?:busco|quiero|necesito|buscar|compara(?:r)?|cotiza(?:r)?|dame|muestrame|muéstrame|ofertas?\s+(?:de|para)|precios?\s+(?:de|para))\s+(?:un|una|el|la|los|las)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(?:en colombia|en tiendas|grandes superficies|mejor precio|mejores precios|en oferta|barato|barata|economico|económico)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-?¡!")
        if not cleaned:
            cleaned = raw_message.strip()

        summary = f"Buscando ofertas para «{cleaned}»"
        if max_price:
            summary += f" (hasta {format_cop(max_price)})"

        return QueryIntent(
            raw_message=raw_message,
            search_query=cleaned,
            min_price=min_price,
            max_price=max_price,
            intent_summary=summary,
            used_ai=False,
        )

    @staticmethod
    def _parse_colombian_budget(val_str: str, unit: str) -> Optional[float]:
        try:
            if unit.startswith("millon") or unit == "m":
                num = float(val_str.replace(",", "."))
                return num * 1_000_000
            if unit in ("mil", "k"):
                num = float(val_str.replace(",", "."))
                return num * 1_000
            # Plain number e.g. 1.500.000 or 2500000
            digits = re.sub(r"[^\d]", "", val_str)
            return float(digits) if digits else None
        except Exception:
            return None

    @staticmethod
    def _extract_json_block(text: str) -> str:
        text = text.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            return fenced.group(1)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
        return text

    @staticmethod
    def _to_float(val: Any) -> Optional[float]:
        if val is None or val == "":
            return None
        try:
            f = float(val)
            return f if f > 0 else None
        except Exception:
            return None


ai_service = AIService()

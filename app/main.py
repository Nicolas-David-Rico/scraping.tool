from __future__ import annotations

import csv
import io
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import auth
from app.config import BASE_DIR, settings_manager
from app.models import (
    AppSettingsPayload,
    ChatSearchRequest,
    FollowUpRequest,
    ProductOffer,
    SearchComparison,
    StoreSearchStatus,
)
from app.scrapers.browser_pool import browser_pool
from app.scrapers.registry import list_stores, stream_store_searches
from app.services.ai_service import ai_service
from app.services.comparator import filter_and_rank_offers
from app.services.firecrawl_service import firecrawl_service
from app.services.history import history_manager

STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await browser_pool.close()


app = FastAPI(
    title="OfertaCO — Buscador Local de Grandes Superficies en Colombia",
    version="1.0.0",
    lifespan=lifespan,
)

LOCK_SETTINGS = os.getenv("LOCK_SETTINGS", "false").lower() in ("1", "true", "yes")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

auth.bootstrap_from_env()

PUBLIC_PATHS = {
    "/login",
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def _current_user(request: Request) -> str | None:
    token = request.cookies.get(auth.COOKIE_NAME, "")
    return auth.verify_session_token(token) if token else None


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


@app.middleware("http")
async def session_guard(request: Request, call_next):
    """Exige sesión iniciada para todo excepto login/health; añade cabeceras de seguridad."""
    path = request.url.path
    if path not in PUBLIC_PATHS and not _current_user(request):
        if path.startswith("/api/"):
            response: Response = JSONResponse({"detail": "No autenticado"}, status_code=401)
        else:
            response = RedirectResponse("/login", status_code=303)
    else:
        response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers[k] = v
    response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class Credentials(BaseModel):
    username: str
    password: str


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str


def _set_session_cookie(response: Response, username: str) -> None:
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.create_session_token(username),
        max_age=auth.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        path="/",
    )


@app.get("/login")
async def serve_login():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/api/auth/status")
async def auth_status(request: Request):
    user = _current_user(request)
    return {
        "authenticated": bool(user),
        "username": user,
        "setup_required": auth.setup_required(),
    }


@app.post("/api/auth/setup")
async def auth_setup(creds: Credentials):
    err = auth.create_initial_user(creds.username.strip(), creds.password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    response = JSONResponse({"ok": True})
    _set_session_cookie(response, creds.username.strip())
    return response


@app.post("/api/auth/login")
async def auth_login(creds: Credentials, request: Request):
    ip = _client_ip(request)
    remaining = auth.is_locked(ip)
    if remaining:
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados intentos fallidos. Intenta de nuevo en {remaining} s.",
        )
    if not auth.verify_credentials(creds.username.strip(), creds.password):
        auth.register_failure(ip)
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
    auth.clear_failures(ip)
    response = JSONResponse({"ok": True})
    _set_session_cookie(response, creds.username.strip())
    return response


@app.post("/api/auth/logout")
async def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@app.post("/api/auth/change-password")
async def auth_change_password(payload: ChangePasswordPayload, request: Request):
    user = _current_user(request)
    err = auth.change_password(user or "", payload.current_password, payload.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True}


@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "stores": len(list_stores())}


@app.get("/api/stores")
async def get_stores():
    return [s.model_dump() for s in list_stores()]


@app.get("/api/settings")
async def get_settings():
    return settings_manager.get_public()


@app.post("/api/settings")
async def update_settings(payload: AppSettingsPayload):
    if LOCK_SETTINGS:
        raise HTTPException(
            status_code=403,
            detail="Configuración bloqueada en producción: usa variables de entorno en tu hosting.",
        )
    return settings_manager.update(payload.model_dump())


class TestAIPayload(BaseModel):
    provider: str = "gemini"
    api_key: str | None = None
    model: str | None = None


@app.post("/api/settings/test-ai")
async def test_ai(payload: TestAIPayload):
    return await ai_service.test_connection(
        provider=payload.provider,
        api_key=payload.api_key,
        model=payload.model,
    )


class TestFirecrawlPayload(BaseModel):
    api_key: str | None = None


@app.post("/api/settings/test-firecrawl")
async def test_firecrawl(payload: TestFirecrawlPayload):
    return await firecrawl_service.test_connection(api_key=payload.api_key)


@app.get("/api/history")
async def get_history():
    return history_manager.list_summaries()


@app.get("/api/history/{search_id}")
async def get_history_item(search_id: str):
    comp = history_manager.get(search_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Búsqueda no encontrada")
    return comp.model_dump()


@app.delete("/api/history/{search_id}")
async def delete_history_item(search_id: str):
    ok = history_manager.delete(search_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Búsqueda no encontrada")
    return {"deleted": True}


@app.get("/api/export/{search_id}")
async def export_csv(search_id: str):
    comp = history_manager.get(search_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Búsqueda no encontrada")

    output = io.StringIO()
    # UTF-8 BOM for clean Excel compatibility in Spanish Windows
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(
        [
            "Ranking",
            "Tienda",
            "Producto",
            "Marca",
            "Precio Oferta (COP)",
            "Precio Antes (COP)",
            "Descuento (%)",
            "Precio Tarjeta (COP)",
            "Promo Tarjeta",
            "Promociones",
            "Envío",
            "Vendedor",
            "Enlace",
        ]
    )
    for idx, o in enumerate(comp.offers, start=1):
        writer.writerow(
            [
                idx,
                o.store_name,
                o.title,
                o.brand,
                int(round(o.price)),
                int(round(o.list_price)) if o.list_price else "",
                o.discount_percent or "",
                int(round(o.card_price)) if o.card_price else "",
                o.card_promo_text or "",
                " | ".join(o.promo_badges),
                o.shipping_info or "",
                o.seller or "",
                o.url,
            ]
        )

    filename = f"comparativa-{search_id[:8]}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
async def chat_search_stream(req: ChatSearchRequest):
    async def event_generator():
        try:
            yield _sse({"stage": "understanding"})
            intent = await ai_service.parse_query_intent(req.message)
            yield _sse({"stage": "intent_ready", "intent": intent.model_dump()})

            cfg = settings_manager.get_all()
            max_results = req.max_results_per_store or int(cfg.get("max_results_per_store", 10))
            use_pw = bool(cfg.get("use_playwright_fallback", True))
            timeout = float(cfg.get("store_timeout_seconds", 25))

            raw_offers: list[ProductOffer] = []
            statuses: list[StoreSearchStatus] = []

            async for store_offers, status in stream_store_searches(
                query=intent.search_query,
                store_ids=req.stores,
                max_results=max_results,
                use_playwright=use_pw,
                timeout=timeout,
            ):
                raw_offers.extend(store_offers)
                statuses.append(status)
                yield _sse({"stage": "store_done", "store": status.model_dump()})

            yield _sse({"stage": "analyzing", "total_raw": len(raw_offers)})

            ranked_offers, stats, best_offer, best_card, best_disc = filter_and_rank_offers(
                raw_offers, intent
            )

            ai_text, ai_used, ai_provider, ai_error = await ai_service.generate_comparison_analysis(
                intent=intent,
                offers=ranked_offers,
                stats=stats,
                store_statuses=statuses,
            )

            comparison = SearchComparison(
                search_id=uuid.uuid4().hex[:12],
                raw_message=req.message,
                intent=intent,
                best_offer=best_offer,
                best_card_offer=best_card,
                best_discount_offer=best_disc,
                stats=stats,
                offers=ranked_offers,
                store_statuses=statuses,
                ai_analysis=ai_text,
                ai_used=ai_used,
                ai_provider=ai_provider,
                ai_error=ai_error,
            )
            history_manager.add(comparison)
            yield _sse({"stage": "complete", "comparison": comparison.model_dump()})
        except Exception as exc:
            yield _sse({"stage": "error", "message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/followup")
async def chat_followup(req: FollowUpRequest):
    comp = history_manager.get(req.search_id)
    if not comp:
        raise HTTPException(status_code=404, detail="No se encontró el contexto de búsqueda.")

    answer, ai_used, ai_provider, ai_error = await ai_service.answer_followup(
        question=req.question,
        offers=comp.offers,
        intent=comp.intent,
        stats=comp.stats,
        history=req.history,
    )
    return {
        "answer": answer,
        "ai_used": ai_used,
        "ai_provider": ai_provider,
        "ai_error": ai_error,
    }

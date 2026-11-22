# OfertaCO — Buscador y Comparador Local de Ofertas en Colombia

Aplicación web local con interfaz de chat para buscar referencias y productos en tiempo real en las principales grandes superficies de Colombia, comparar precios, detectar promociones (incluyendo descuentos con tarjetas propias) y acceder directamente a los enlaces de compra.

## Tiendas incluidas
- **Éxito** (`exito.com`) — incluye detección de Tarjeta Éxito / promociones
- **Alkosto** (`alkosto.com`)
- **Falabella** (`falabella.com.co`) — incluye detección de precio Tarjeta CMR
- **Jumbo** (`jumbocolombia.com`) — incluye detección de Tarjeta Cencosud
- **Olímpica** (`olimpica.com`) — incluye detección de Tarjeta Plata
- **Homecenter** (`homecenter.com.co`) — incluye detección de Tarjeta CMR
- **Mercado Libre Colombia** (`mercadolibre.com.co`)

## Inicio rápido en Windows
1. Haz doble clic en **`start.bat`** (o ejecuta desde PowerShell):
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
2. Abre tu navegador en **http://127.0.0.1:8000**.
3. (Opcional recomendado) Haz clic en **⚙ Configuración IA** en la aplicación para ingresar:
   - Tu API Key de **Google Gemini** u **OpenAI** (interpretación inteligente de consultas, comparativa redactada y preguntas de seguimiento).
   - Tu API Key de **Firecrawl** (`fc-...`) para usar Firecrawl como respaldo automático ante bloqueos o como motor principal de extracción estructurada.

## Estructura del proyecto
- `app/main.py` — Servidor FastAPI, streaming SSE en tiempo real, exportación CSV y API REST.
- `app/scrapers/` — Scrapers especializados por tienda con respaldo automático en Playwright (Chrome/Edge).
- `app/services/` — Servicio de IA (Gemini / OpenAI), comparador y ranking, e historial local.
- `static/` — Interfaz web moderna de chat, tarjetas de oferta, tabla comparativa y modal de configuración.
- `data/` — Configuración local (`settings.json`) e historial (`history.json`) guardados únicamente en tu PC.

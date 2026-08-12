# Trading bot (paper trading) + noticias

Basado en un screener técnico de medio plazo, ampliado con una señal de
sentimiento de noticias (NewsAPI). **No opera con dinero real**: simula una
cuenta de $10,000 ficticios (`paper_trader.py`).

## Cómo funciona

1. `screener.py` analiza cada ticker con 4 indicadores técnicos (EMA, RSI,
   ATR, ADX) + 1 señal de noticias (`news.py`, via NewsAPI).
2. Si 4 de 5 señales coinciden → propone una entrada con Stop Loss y Take
   Profit calculados con ATR.
3. `paper_trader.py` simula qué pasaría si tomaras esas señales, con
   gestión de riesgo de cartera (máx 6% de riesgo simultáneo, máx 2
   posiciones por sector, filtro de régimen de mercado, stop temporal a
   40 días).
4. Al final de cada corrida, manda un resumen por Telegram (opcional).

## Setup

```bash
pip install -r requirements.txt
```

### 1. API key de NewsAPI (gratis)

1. Regístrate en https://newsapi.org/register
2. Copia tu API key
3. Configúrala como variable de entorno:

```bash
export NEWSAPI_KEY="tu_api_key_aqui"
```

> Nota: el plan gratuito de NewsAPI está pensado para desarrollo/pruebas,
> no para producción 24/7. Para correr el bot 1 vez al día está bien.

### 2. Telegram (opcional, para recibir alertas)

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram y crea un bot
   → te da un `TELEGRAM_TOKEN`
2. Escríbele algo a tu bot, luego visita
   `https://api.telegram.org/bot<TOKEN>/getUpdates` para encontrar tu
   `chat_id`
3. Configura las variables:

```bash
export TELEGRAM_TOKEN="tu_token_aqui"
export TELEGRAM_CHAT_ID="tu_chat_id_aqui"
```

## Cómo correrlo

Solo el screener (ver señales sin simular cuenta):
```bash
python screener.py
```

Paper trading completo (simula la cuenta, guarda estado en `state.json`):
```bash
python paper_trader.py
```

Para que corra solo todos los días, puedes usar el archivo en
`.github/workflows/` (GitHub Actions) o un cron job en tu propia máquina.

## Advertencia

Esto es una herramienta educativa de simulación. No es asesoría financiera.
Las señales técnicas y de noticias no garantizan resultados. Nunca
conectes esto a una cuenta real sin entender bien qué hace cada parte del
código y sin haberlo probado en paper trading durante un buen tiempo.

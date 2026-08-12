"""
=============================================================================
SCREENER DE MEDIO PLAZO - Acciones e Indices (con señal de noticias)
=============================================================================
Vigila una lista de tickers, calcula métricas técnicas basadas en precio y
volatilidad, SUMA una señal de sentimiento de noticias (via news.py /
NewsAPI), evalúa una regla de confluencia y, cuando se cumple, propone
entrada, Stop Loss y Take Profit dimensionados con ATR.

FILOSOFÍA: esto es un ASISTENTE DE DECISIÓN, no un bot de ejecución.
Te avisa. Tú decides y ejecutas. Nunca abre operaciones solo.

Fuente de precios: yfinance (gratis, sin API key).
Fuente de noticias: NewsAPI (gratis, requiere API key - ver news.py).
=============================================================================
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

from news import news_sentiment

# =============================================================================
# 1. CONFIGURACIÓN — lo único que tocas en el día a día
# =============================================================================
CONFIG = {
    "tickers": [
        # --- Tecnología ---
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
        # --- Financieras ---
        "JPM", "BAC", "V",
        # --- Salud ---
        "JNJ", "UNH", "PFE",
        # --- Energía ---
        "XOM", "CVX",
        # --- Consumo básico (defensivas, se mueven distinto) ---
        "PG", "KO", "WMT",
        # --- Industriales ---
        "CAT", "BA",
        # --- Más tecnología ---
        "META", "TSLA", "AMD", "CRM", "ADBE",
        # --- Más financieras ---
        "MA", "GS", "WFC",
        # --- Más salud ---
        "LLY", "ABBV",
        # --- Más consumo ---
        "COST", "MCD",
        # --- Más energía/industriales ---
        "NEE", "HON",
        # --- Índices (referencia de mercado) ---
        "^GSPC", "^NDX", "^DJI", "^RUT",
    ],

    "interval": "1d",
    "lookback": "1y",

    # Parámetros de indicadores (valores clásicos de medio plazo)
    "ema_fast": 20,
    "ema_slow": 50,
    "ema_trend": 200,
    "rsi_period": 14,
    "atr_period": 14,
    "adx_period": 14,

    # Reglas de gestión de riesgo
    "atr_sl_mult": 1.5,
    "risk_reward": 2.0,
    "adx_min": 20,

    # Cuántas señales de categorías distintas deben coincidir para avisar.
    # Ahora son 5 categorías (4 técnicas + 1 de noticias) -> exigimos 4/5.
    "min_confluence": 4,

    # Noticias: si True, se consulta NewsAPI para cada ticker con señal
    # técnica pendiente. Si False, esa señal siempre cuenta como positiva
    # (equivale al comportamiento original sin noticias).
    "use_news": True,
    "news_max_articles": 10,
}

# =============================================================================
# 2. INDICADORES — todos calculados a mano con pandas (sin TA-Lib, sin volumen)
# =============================================================================
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def adx(high, low, close, period=14):
    """Mide fuerza de tendencia (no dirección). >25 tendencia fuerte."""
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(alpha=1/period, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(alpha=1/period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False).mean()


def enrich(df, cfg):
    """Añade todas las columnas de indicadores al DataFrame de precios."""
    c, h, l = df["Close"], df["High"], df["Low"]
    df["ema_fast"] = ema(c, cfg["ema_fast"])
    df["ema_slow"] = ema(c, cfg["ema_slow"])
    df["ema_trend"] = ema(c, cfg["ema_trend"])
    df["rsi"] = rsi(c, cfg["rsi_period"])
    df["atr"] = atr(h, l, c, cfg["atr_period"])
    df["adx"] = adx(h, l, c, cfg["adx_period"])
    return df

# =============================================================================
# 3. REGLA DE CONFLUENCIA — el corazón de tu estrategia (edítala a tu gusto)
# =============================================================================
def evaluate(df, cfg, ticker=None):
    """
    Mira la última vela y cuenta cuántas señales alcistas coinciden,
    incluyendo el sentimiento de noticias si use_news=True.
    Devuelve un dict con el veredicto, o None si no hay datos suficientes.
    """
    if len(df) < cfg["ema_trend"]:
        return None

    last = df.iloc[-1]
    signals = {}

    # --- Señales técnicas (igual que antes) ---
    signals["tendencia_corto"] = last["ema_fast"] > last["ema_slow"]
    signals["tendencia_mayor"] = last["Close"] > last["ema_trend"]
    signals["momentum"] = 45 < last["rsi"] < 70
    signals["fuerza_tendencia"] = last["adx"] > cfg["adx_min"]

    # --- Señal de noticias (nueva) ---
    news_info = {"score": None, "n_articles": 0}
    if cfg.get("use_news"):
        news_info = news_sentiment(ticker, cfg.get("news_max_articles", 10))
        signals["sentimiento_noticias"] = news_info["is_bullish"]
    else:
        signals["sentimiento_noticias"] = True  # no bloquea si está desactivado

    score = sum(signals.values())
    is_signal = score >= cfg["min_confluence"]

    result = {
        "ticker": ticker,
        "price": round(float(last["Close"]), 2),
        "score": int(score),
        "max_score": len(signals),
        "signals": signals,
        "news": news_info,
        "is_signal": bool(is_signal),
        "entry": None, "sl": None, "tp": None, "rr": cfg["risk_reward"],
    }

    if is_signal:
        entry = float(last["Close"])
        atr_val = float(last["atr"])
        sl = entry - cfg["atr_sl_mult"] * atr_val
        tp = entry + cfg["atr_sl_mult"] * atr_val * cfg["risk_reward"]
        result.update({
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "risk_pct": round((entry - sl) / entry * 100, 2),
        })
    return result

# =============================================================================
# 4. DESCARGA DE DATOS
# =============================================================================
def fetch(ticker, cfg):
    """Descarga OHLC vía yfinance. Devuelve DataFrame o None si falla."""
    try:
        df = yf.Ticker(ticker).history(period=cfg["lookback"], interval=cfg["interval"])
        if df.empty:
            print(f" [!] {ticker}: sin datos")
            return None
        return df
    except Exception as e:
        print(f" [!] {ticker}: error de descarga -> {e}")
        return None

# =============================================================================
# 5. SALIDA — tabla limpia lista para leer y trasladar al broker
# =============================================================================
def report(results):
    señales = [r for r in results if r and r["is_signal"]]
    print("\n" + "=" * 70)
    print(f" SCREENER MEDIO PLAZO + NOTICIAS | {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 70)

    if not señales:
        print("\n Sin oportunidades que cumplan la confluencia mínima hoy.")
        print(" (Esto es normal y sano: no operar es una decisión válida.)\n")
        return

    rows = []
    for r in señales:
        rows.append({
            "Ticker": r["ticker"],
            "Precio": r["price"],
            "Señales": f"{r['score']}/{r['max_score']}",
            "Noticias": r["news"]["score"],
            "Entrada": r["entry"],
            "SL": r["sl"],
            "TP": r["tp"],
            "Riesgo %": r["risk_pct"],
            "R:R": f"1:{r['rr']:.0f}",
        })

    tabla = pd.DataFrame(rows)
    print("\n" + tabla.to_string(index=False))
    print("\n Recordatorio: valida el calendario económico y usa <=1-2% de")
    print(" riesgo por operación. Estas son sugerencias, no órdenes.\n")

# =============================================================================
# 6. ORQUESTADOR
# =============================================================================
def run(cfg=CONFIG):
    print(f"Analizando {len(cfg['tickers'])} tickers (técnico + noticias)...")
    results = []
    for t in cfg["tickers"]:
        df = fetch(t, cfg)
        if df is None:
            continue
        df = enrich(df, cfg)
        res = evaluate(df, cfg, ticker=t)
        if res:
            results.append(res)
    report(results)
    return results


if __name__ == "__main__":
    run()

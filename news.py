"""
=============================================================================
MODULO DE NOTICIAS - Sentimiento por ticker via NewsAPI
=============================================================================
Busca noticias recientes de un ticker/empresa y calcula un sentimiento
simple basado en palabras clave (bullish/bearish). No requiere IA de pago:
funciona 100% gratis con el plan free de NewsAPI (newsapi.org).

LIMITACIONES DEL PLAN FREE DE NEWSAPI (léelas antes de usar):
- Solo pensado para desarrollo/pruebas, no para apps en producción 24/7.
  Para un bot que corre 1 vez al día en tu propia máquina, está bien.
- Artículos con ~24h de retraso.
- 100 requests/día. Con ~20 tickers, corriendo 1 vez al día, sobra margen.

Consigue tu API key gratis en: https://newsapi.org/register
=============================================================================
"""

import os
import requests

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
NEWSAPI_URL = "https://newsapi.org/v2/everything"

# Mapa ticker -> nombre de búsqueda (mejora los resultados vs buscar "AAPL" literal)
TICKER_NAMES = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
    "GOOGL": "Google OR Alphabet",
    "AMZN": "Amazon",
    "JPM": "JPMorgan",
    "BAC": "Bank of America",
    "V": "Visa",
    "JNJ": "Johnson & Johnson",
    "UNH": "UnitedHealth",
    "PFE": "Pfizer",
    "XOM": "Exxon",
    "CVX": "Chevron",
    "PG": "Procter & Gamble",
    "KO": "Coca-Cola",
    "WMT": "Walmart",
    "CAT": "Caterpillar",
    "BA": "Boeing",
}

# Palabras clave simples para sentimiento (en inglés, porque NewsAPI trae
# mayormente medios en inglés). Ajusta esta lista a tu gusto con el tiempo.
POSITIVE_WORDS = [
    "surge", "soar", "beat", "beats", "record", "rally", "upgrade",
    "growth", "profit", "gain", "gains", "strong", "outperform",
    "bullish", "breakthrough", "expansion", "raises guidance",
]
NEGATIVE_WORDS = [
    "plunge", "crash", "miss", "misses", "downgrade", "recall",
    "lawsuit", "investigation", "layoffs", "decline", "loss", "losses",
    "weak", "bearish", "warning", "cuts guidance", "scandal", "fraud",
]


def fetch_news(ticker, max_articles=10):
    """Descarga noticias recientes para un ticker. Devuelve lista de dicts
    con 'title' y 'description', o lista vacia si falla / sin API key."""
    if not NEWSAPI_KEY:
        return []

    query = TICKER_NAMES.get(ticker, ticker)
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": max_articles,
        "apiKey": NEWSAPI_KEY,
    }
    try:
        resp = requests.get(NEWSAPI_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("articles", [])
    except Exception as e:
        print(f"  [!] {ticker}: error de NewsAPI -> {e}")
        return []


def score_article(article):
    """Puntua un articulo: +1 por palabra positiva encontrada, -1 por negativa."""
    text = f"{article.get('title', '')} {article.get('description', '')}".lower()
    score = 0
    for word in POSITIVE_WORDS:
        if word in text:
            score += 1
    for word in NEGATIVE_WORDS:
        if word in text:
            score -= 1
    return score


def news_sentiment(ticker, max_articles=10):
    """
    Devuelve un dict con el sentimiento agregado de las noticias recientes
    de un ticker:
      - score: suma normalizada, aprox entre -1 y 1
      - n_articles: cuantos articulos se analizaron
      - is_bullish: True si el sentimiento es net positivo
    Si no hay API key o no hay noticias, is_bullish=True (fail-open, no
    bloquea al screener por falta de datos de noticias).
    """
    articles = fetch_news(ticker, max_articles)
    if not articles:
        return {"score": 0.0, "n_articles": 0, "is_bullish": True}

    scores = [score_article(a) for a in articles]
    total = sum(scores)
    # Normaliza aprox a [-1, 1] asumiendo max ~3 palabras clave por articulo
    norm_score = max(-1.0, min(1.0, total / (len(articles) * 3)))

    return {
        "score": round(norm_score, 3),
        "n_articles": len(articles),
        "is_bullish": norm_score >= 0,
    }


if __name__ == "__main__":
    # Prueba rapida
    for t in ["AAPL", "NVDA", "BA"]:
        r = news_sentiment(t)
        print(f"{t}: score={r['score']} articulos={r['n_articles']} bullish={r['is_bullish']}")

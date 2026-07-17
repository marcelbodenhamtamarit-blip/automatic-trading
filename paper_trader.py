"""
=============================================================================
 PAPER TRADER v2 - Con gestion de riesgo de cartera
=============================================================================
 Mejoras sobre la v1 (decisiones tomadas tras el backtest v3 ampliado):

   1. TOPE DE RIESGO TOTAL: maximo 6% de la cuenta en riesgo simultaneo.
      Con 1% por operacion = maximo ~6 posiciones abiertas a la vez.
   2. TOPE POR SECTOR: maximo 2 posiciones del mismo sector, para que el
      riesgo simultaneo este repartido en apuestas genuinamente distintas.
   3. INTERRUPTOR DE REGIMEN: solo se abren posiciones NUEVAS si el S&P 500
      cotiza sobre su EMA200. Las abiertas se gestionan normalmente.
   4. STOP TEMPORAL: posiciones sin resolver en 40 dias habiles se cierran
      a mercado (capital zombi fuera).
   5. EQUITY A VALOR DE MERCADO: cada dia se guarda la curva de equity real
      (efectivo + posiciones valoradas al precio actual) en equity_history.

 COMPATIBLE con el state.json de la v1: adopta las posiciones existentes.
 La regla de entrada NO se toca: sigue siendo la larga verificada de
 screener.py. Todo esto es gestion de riesgo ALREDEDOR de la estrategia.
=============================================================================
"""

import os
import json
import csv
from datetime import datetime, timezone

import numpy as np
import yfinance as yf
from screener import enrich, evaluate, CONFIG


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

PT_CONFIG = {
    **CONFIG,
    "start_equity": 10000.0,
    "risk_per_trade": 0.01,        # 1% de la cuenta por operacion
    "cost_pct": 0.05,              # coste por lado (%)
    "lookback": "1y",

    # --- Gestion de riesgo de cartera (nuevo) --------------------------------
    "max_total_risk": 0.06,        # 6% de riesgo simultaneo maximo
    "max_per_sector": 2,           # maximo 2 posiciones por sector
    "regime_ticker": "^GSPC",      # el termometro del mercado
    "time_stop_bars": 40,          # dias habiles maximos por posicion
}

# Mapa ticker -> sector. Tickers no listados caen en "otros".
SECTORS = {
    "AAPL": "tech", "MSFT": "tech", "NVDA": "tech", "GOOGL": "tech", "AMZN": "tech",
    "JPM": "financiero", "BAC": "financiero", "V": "financiero",
    "JNJ": "salud", "UNH": "salud", "PFE": "salud",
    "XOM": "energia", "CVX": "energia",
    "PG": "consumo", "KO": "consumo", "WMT": "consumo",
    "CAT": "industrial", "BA": "industrial",
    "^GSPC": "indices", "^NDX": "indices", "^DJI": "indices", "^RUT": "indices",
}

STATE_FILE = "state.json"
TRADES_CSV = "trades_paper.csv"


# =============================================================================
# ESTADO PERSISTENTE (compatible con v1)
# =============================================================================

def load_state(cfg):
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
        # Migracion v1 -> v2: añade campos nuevos si faltan
        state.setdefault("equity_history", [])
        return state
    return {
        "equity": cfg["start_equity"],
        "start_equity": cfg["start_equity"],
        "peak_equity": cfg["start_equity"],
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "last_run": None,
        "open_positions": {},
        "closed_trades": [],
        "equity_history": [],       # [{date, cash_equity, mtm_equity}]
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def append_csv(trade):
    exists = os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "ticker", "entry_date", "exit_date", "entry", "exit",
            "shares", "outcome", "pnl", "equity_after"])
        if not exists:
            w.writeheader()
        w.writerow(trade)


# =============================================================================
# GESTIÓN DE RIESGO DE CARTERA (nuevo)
# =============================================================================

def current_total_risk(state):
    """Suma del riesgo comprometido en posiciones abiertas, como fraccion."""
    total = sum(p.get("risk_amount", 0) for p in state["open_positions"].values())
    return total / state["equity"] if state["equity"] > 0 else 1.0


def sector_count(state, sector):
    return sum(1 for t in state["open_positions"]
               if SECTORS.get(t, "otros") == sector)


def regime_is_bullish(cfg, cache):
    """True si el S&P 500 cotiza sobre su EMA200. Usa cache de descargas."""
    t = cfg["regime_ticker"]
    df = cache.get(t)
    if df is None:
        return True   # sin datos del termometro, no bloqueamos (fail-open)
    ema200 = df["Close"].ewm(span=cfg["ema_trend"], adjust=False).mean()
    return bool(df["Close"].iloc[-1] > ema200.iloc[-1])


def bars_held(entry_date_str, today_str):
    """Dias habiles entre la entrada y hoy."""
    try:
        return int(np.busday_count(entry_date_str, today_str))
    except Exception:
        return 0


# =============================================================================
# CUENTA SIMULADA
# =============================================================================

def open_position(state, ticker, sig, cfg, today):
    equity = state["equity"]
    risk_amount = equity * cfg["risk_per_trade"]
    per_share_risk = sig["entry"] - sig["sl"]
    if per_share_risk <= 0:
        return None
    shares = risk_amount / per_share_risk
    state["open_positions"][ticker] = {
        "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
        "shares": round(shares, 4), "risk_amount": round(risk_amount, 2),
        "entry_date": today,
    }
    return state["open_positions"][ticker]


def close_position(state, ticker, exit_price, outcome, cfg, today):
    pos = state["open_positions"].pop(ticker)
    cost = cfg["cost_pct"] / 100.0
    eff_entry = pos["entry"] * (1 + cost)
    eff_exit  = exit_price * (1 - cost)
    pnl = pos["shares"] * (eff_exit - eff_entry)
    state["equity"] += pnl
    state["peak_equity"] = max(state["peak_equity"], state["equity"])
    trade = {
        "ticker": ticker,
        "entry_date": pos["entry_date"], "exit_date": today,
        "entry": round(pos["entry"], 2), "exit": round(exit_price, 2),
        "shares": pos["shares"], "outcome": outcome,
        "pnl": round(pnl, 2), "equity_after": round(state["equity"], 2),
    }
    state["closed_trades"].append(trade)
    append_csv(trade)
    return trade


def mark_to_market(state, cache):
    """Equity a valor de mercado: efectivo +/- PnL no realizado de abiertas."""
    mtm = state["equity"]
    for t, p in state["open_positions"].items():
        df = cache.get(t)
        if df is not None and not df.empty:
            last_close = float(df["Close"].iloc[-1])
            mtm += p["shares"] * (last_close - p["entry"])
    return round(mtm, 2)


# =============================================================================
# CICLO PRINCIPAL
# =============================================================================

def download_all(cfg):
    """Descarga todos los tickers (+ el termometro de regimen) una sola vez."""
    cache = {}
    tickers = list(dict.fromkeys(cfg["tickers"] + [cfg["regime_ticker"]]))
    for t in tickers:
        try:
            df = yf.Ticker(t).history(period=cfg["lookback"], interval=cfg["interval"])
            cache[t] = df if not df.empty else None
        except Exception:
            cache[t] = None
    return cache


def run(cfg=PT_CONFIG):
    state = load_state(cfg)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if state.get("last_run") == today:
        print(f"Ya se corrio hoy ({today}). Saltando.")
        return state

    events = []
    cache = download_all(cfg)
    bullish = regime_is_bullish(cfg, cache)

    # --- 1. GESTIONAR SALIDAS (siempre, sin importar el regimen) ------------
    for ticker in list(state["open_positions"].keys()):
        df = cache.get(ticker)
        if df is None:
            events.append(f"[!] {ticker}: sin datos hoy, posicion sigue abierta")
            continue
        last = df.iloc[-1]
        pos = state["open_positions"][ticker]

        hit_sl = last["Low"] <= pos["sl"]
        hit_tp = last["High"] >= pos["tp"]
        expired = bars_held(pos["entry_date"], today) >= cfg["time_stop_bars"]

        if hit_sl and hit_tp:
            t = close_position(state, ticker, pos["sl"], "SL (ambiguo)", cfg, today)
            events.append(f"CIERRE {ticker}: SL ambiguo, PnL ${t['pnl']}")
        elif hit_sl:
            t = close_position(state, ticker, pos["sl"], "SL", cfg, today)
            events.append(f"CIERRE {ticker}: STOP LOSS, PnL ${t['pnl']}")
        elif hit_tp:
            t = close_position(state, ticker, pos["tp"], "TP", cfg, today)
            events.append(f"CIERRE {ticker}: TAKE PROFIT, PnL ${t['pnl']}")
        elif expired:
            t = close_position(state, ticker, float(last["Close"]), "TIEMPO", cfg, today)
            events.append(f"CIERRE {ticker}: STOP TEMPORAL (40 dias), PnL ${t['pnl']}")

    # --- 2. BUSCAR ENTRADAS (solo si el regimen lo permite) -----------------
    if not bullish:
        events.append("REGIMEN BAJISTA (S&P bajo EMA200): no se abren posiciones nuevas.")
    else:
        for ticker in cfg["tickers"]:
            if ticker in state["open_positions"]:
                continue
            # Tope de riesgo total
            if current_total_risk(state) + cfg["risk_per_trade"] > cfg["max_total_risk"]:
                events.append("TOPE DE RIESGO (6%) alcanzado: se ignoran señales nuevas.")
                break
            # Tope por sector
            sector = SECTORS.get(ticker, "otros")
            if sector_count(state, sector) >= cfg["max_per_sector"]:
                continue
            df = cache.get(ticker)
            if df is None:
                continue
            df = enrich(df, cfg)
            res = evaluate(df, cfg)
            if res and res["is_signal"]:
                pos = open_position(state, ticker, res, cfg, today)
                if pos:
                    events.append(
                        f"ENTRADA {ticker} ({sector}) @ {res['entry']} | "
                        f"SL {res['sl']} TP {res['tp']} | {res['score']}/4")

    # --- 3. EQUITY A VALOR DE MERCADO ---------------------------------------
    mtm = mark_to_market(state, cache)
    state["equity_history"].append({
        "date": today,
        "cash_equity": round(state["equity"], 2),
        "mtm_equity": mtm,
    })

    state["last_run"] = today
    save_state(state)

    summary = build_summary(state, events, today, mtm, bullish, cfg)
    print(summary)
    send_telegram(summary)
    return state


# =============================================================================
# RESUMEN Y ALERTAS
# =============================================================================

def build_summary(state, events, today, mtm, bullish, cfg):
    eq = state["equity"]
    start = state["start_equity"]
    ret_mtm = (mtm - start) / start * 100
    risk_used = current_total_risk(state) * 100
    n_open = len(state["open_positions"])
    n_closed = len(state["closed_trades"])
    regime = "ALCISTA" if bullish else "BAJISTA"

    lines = [
        f"PAPER TRADING v2 | {today} | Regimen: {regime}",
        f"Cuenta (mercado): ${mtm:,.2f} ({ret_mtm:+.1f}%) | Efectivo: ${eq:,.2f}",
        f"Riesgo en uso: {risk_used:.1f}% de {cfg['max_total_risk']*100:.0f}% | "
        f"Abiertas: {n_open} | Cerradas: {n_closed}",
    ]
    if events:
        lines.append("--- Hoy ---")
        lines += events
    else:
        lines.append("Sin movimientos hoy.")

    if state["open_positions"]:
        lines.append("--- Abiertas ---")
        for t, p in state["open_positions"].items():
            held = bars_held(p["entry_date"], today)
            lines.append(f"  {t}: {p['entry']} | SL {p['sl']} | TP {p['tp']} | {held}d")
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("(Telegram no configurado; omitiendo alerta)")
        return
    try:
        import urllib.request, urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15)
        print("(Alerta enviada a Telegram)")
    except Exception as e:
        print(f"(Fallo al enviar Telegram: {e})")


if __name__ == "__main__":
    run()

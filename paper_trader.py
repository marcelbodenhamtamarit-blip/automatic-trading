"""
=============================================================================
 PAPER TRADER - Simulacion en vivo de la estrategia LARGA verificada
=============================================================================
 Corre una vez al dia (via GitHub Actions). En cada corrida:
   1. Carga su estado del dia anterior (state.json)
   2. Descarga los precios mas recientes
   3. Revisa posiciones abiertas: ¿tocaron SL o TP? -> las cierra
   4. Busca señales nuevas -> abre posiciones simuladas
   5. Actualiza la cuenta (equity sube/baja segun resultados)
   6. Guarda el estado, escribe el CSV y avisa por Telegram

 DINERO FICTICIO. Usa la regla LARGA de screener.py (la verificada).
 No opera cortos (los descartamos por evidencia en el backtest v3).
=============================================================================
"""

import os
import json
import csv
from datetime import datetime, timezone

import yfinance as yf
from screener import enrich, evaluate, CONFIG


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

PT_CONFIG = {
    **CONFIG,
    "start_equity": 10000.0,
    "risk_per_trade": 0.01,      # 1% de la cuenta arriesgado por operacion
    "cost_pct": 0.05,            # coste por lado (%). ~0.10% ida+vuelta
    "lookback": "1y",            # historico para recalcular indicadores
}

STATE_FILE = "state.json"
TRADES_CSV = "trades_paper.csv"


# =============================================================================
# ESTADO PERSISTENTE
# =============================================================================

def load_state(cfg):
    """Carga el estado o crea uno nuevo si es la primera corrida."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "equity": cfg["start_equity"],
        "start_equity": cfg["start_equity"],
        "peak_equity": cfg["start_equity"],
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "last_run": None,
        "open_positions": {},     # ticker -> dict
        "closed_trades": [],      # lista de operaciones cerradas
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def append_csv(trade):
    """Añade una operacion cerrada al CSV legible."""
    exists = os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "ticker", "entry_date", "exit_date", "entry", "exit",
            "shares", "outcome", "pnl", "equity_after"])
        if not exists:
            w.writeheader()
        w.writerow(trade)


# =============================================================================
# SIMULACIÓN DE CUENTA
# =============================================================================

def open_position(state, ticker, sig, cfg, today):
    """Abre una posicion simulada, dimensionada para arriesgar 1% al SL."""
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
    """Cierra una posicion, aplica costes, actualiza equity y registra."""
    pos = state["open_positions"].pop(ticker)
    cost = cfg["cost_pct"] / 100.0
    eff_entry = pos["entry"] * (1 + cost)     # pagaste mas al comprar
    eff_exit  = exit_price * (1 - cost)       # recibes menos al vender
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


# =============================================================================
# CICLO PRINCIPAL
# =============================================================================

def process_ticker(state, ticker, cfg, today, events):
    """Procesa un ticker: primero gestiona salida, luego posible entrada."""
    try:
        df = yf.Ticker(ticker).history(period=cfg["lookback"], interval=cfg["interval"])
        if df.empty:
            return
    except Exception as e:
        events.append(f"[!] {ticker}: error descarga {e}")
        return

    df = enrich(df, cfg)
    last = df.iloc[-1]

    # --- 1. ¿Hay posicion abierta que cerrar? ---
    if ticker in state["open_positions"]:
        pos = state["open_positions"][ticker]
        hit_sl = last["Low"] <= pos["sl"]
        hit_tp = last["High"] >= pos["tp"]
        if hit_sl and hit_tp:
            t = close_position(state, ticker, pos["sl"], "SL (ambiguo)", cfg, today)
            events.append(f"CIERRE {ticker}: SL ambiguo, PnL ${t['pnl']}")
        elif hit_sl:
            t = close_position(state, ticker, pos["sl"], "SL", cfg, today)
            events.append(f"CIERRE {ticker}: STOP LOSS, PnL ${t['pnl']}")
        elif hit_tp:
            t = close_position(state, ticker, pos["tp"], "TP", cfg, today)
            events.append(f"CIERRE {ticker}: TAKE PROFIT, PnL ${t['pnl']}")

    # --- 2. ¿Señal nueva? (solo si no hay ya posicion en este ticker) ---
    if ticker not in state["open_positions"]:
        res = evaluate(df, cfg)
        if res and res["is_signal"]:
            pos = open_position(state, ticker, res, cfg, today)
            if pos:
                events.append(
                    f"ENTRADA {ticker} @ {res['entry']} | SL {res['sl']} "
                    f"TP {res['tp']} | {res['score']}/4 señales")


def run(cfg=PT_CONFIG):
    state = load_state(cfg)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Evita doble procesamiento si la accion corre dos veces el mismo dia
    if state.get("last_run") == today:
        print(f"Ya se corrio hoy ({today}). Saltando.")
        return state

    events = []
    for ticker in cfg["tickers"]:
        process_ticker(state, ticker, cfg, today, events)

    state["last_run"] = today
    save_state(state)

    summary = build_summary(state, events, today, cfg)
    print(summary)
    send_telegram(summary)
    return state


# =============================================================================
# RESUMEN Y ALERTAS
# =============================================================================

def build_summary(state, events, today, cfg):
    eq = state["equity"]
    start = state["start_equity"]
    ret = (eq - start) / start * 100
    dd = (state["peak_equity"] - eq) / state["peak_equity"] * 100
    n_open = len(state["open_positions"])
    n_closed = len(state["closed_trades"])

    lines = [
        f"PAPER TRADING | {today}",
        f"Cuenta: ${eq:,.2f}  ({ret:+.1f}%)  | DD actual: {dd:.1f}%",
        f"Posiciones abiertas: {n_open} | Operaciones cerradas: {n_closed}",
    ]
    if events:
        lines.append("--- Movimientos de hoy ---")
        lines += events
    else:
        lines.append("Sin movimientos hoy.")

    if state["open_positions"]:
        lines.append("--- Abiertas ahora ---")
        for t, p in state["open_positions"].items():
            lines.append(f"  {t}: entrada {p['entry']} | SL {p['sl']} | TP {p['tp']}")
    return "\n".join(lines)


def send_telegram(text):
    """Envia el resumen a Telegram si los secretos estan configurados."""
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

from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import requests, csv, io

app = FastAPI(title="FP&A Mini Backend (Railway + FastAPI)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True, "runtime": "python", "framework": "fastapi"}

# ---------- helpers ----------
def stooq_symbol(sym: str) -> str:
    # Stooq usa .sa em minúsculas para B3
    # ex.: PTBL3.SA -> ptbl3.sa
    s = sym.strip()
    return s.lower()

def stooq_quote(sym: str):
    """Retorna (price, shares, mcap) ou (None, None, None) a partir do Stooq."""
    # Campos: s,d2,t2,o,h,l,c,v,n (c=close)
    url = f"https://stooq.com/q/l/?s={stooq_symbol(sym)}&f=sd2t2ohlcvn"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        # CSV de 1 linha
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if not rows:
            return None, None, None
        row = rows[0]
        price = row.get("c")
        price = float(price) if price not in (None, "", "N/D") else None
        # Stooq não traz shares/mcap; deixamos None
        return price, None, None
    except Exception:
        return None, None, None

def yf_quote(sym: str):
    """Retorna (price, shares, mcap) via yfinance (tolerante)."""
    try:
        tk = yf.Ticker(sym)
        fi = getattr(tk, "fast_info", {}) or {}
        price = fi.get("last_price") or fi.get("lastPrice")
        shares = fi.get("shares")
        mcap = fi.get("market_cap")

        if price is None:
            # fallback leve
            try:
                info = tk.info or {}
                price = info.get("regularMarketPrice") or info.get("previousClose")
                shares = shares or info.get("sharesOutstanding")
                mcap = mcap or info.get("marketCap")
            except Exception:
                pass

        # normaliza tipos
        price = float(price) if price is not None else None
        mcap  = float(mcap)  if mcap  is not None else None
        shares = int(shares) if shares is not None else None
        return price, shares, mcap
    except Exception as e:
        # devolve None e deixa o caller decidir o fallback
        return None, None, None

def stooq_chart(sym: str, interval: str = "d"):
    """Retorna lista de closes via Stooq (interval d/w/m)."""
    url = f"https://stooq.com/q/d/l/?s={stooq_symbol(sym)}&i={interval}"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text))
        closes = []
        for row in reader:
            c = row.get("Close")
            if c and c not in ("N/D",):
                try:
                    closes.append(float(c))
                except Exception:
                    pass
        return closes
    except Exception:
        return []

# ---------- endpoints ----------
@app.get("/quote")
def quote(symbols: str = Query(..., description="Comma-separated tickers, e.g. PTBL3.SA,DXCO3.SA")):
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    out = []
    errors = []
    for t in syms:
        # 1) tenta Yahoo/yfinance
        price, shares, mcap = yf_quote(t)
        source = "yfinance"
        if price is None:
            # 2) fallback Stooq
            p2, s2, m2 = stooq_quote(t)
            if p2 is not None:
                price, shares, mcap = p2, s2, m2
                source = "stooq"
            else:
                errors.append({"symbol": t, "error": "no_data", "source": "yahoo+stooq"})

        out.append({
            "symbol": t,
            "regularMarketPrice": price,
            "marketCap": mcap,
            "sharesOutstanding": shares,
            "source": source
        })
    return {"result": out, "errors": errors}

@app.get("/chart")
def chart(symbol: str, range: str = "ytd", interval: str = "1d"):
    """
    Tenta yfinance; se falhar, usa Stooq (diário).
    Observação: Stooq não filtra 'ytd'; devolvemos a série diária inteira e o front pode calcular YTD.
    """
    # Normaliza intervalo para Stooq: '1d' -> 'd', '1w' -> 'w', etc.
    stooq_int = "d"
    if interval.lower().startswith("w"): stooq_int = "w"
    if interval.lower().startswith("m"): stooq_int = "m"

    # 1) yfinance
    try:
        hist = yf.Ticker(symbol).history(period=range, interval=interval)
        close = [float(x) for x in hist["Close"].dropna().tolist()]
        if close:
            return {"close": close, "source": "yfinance"}
    except Exception:
        pass

    # 2) stooq fallback
    close = stooq_chart(symbol, interval=stooq_int)
    if close:
        return {"close": close, "source": "stooq"}

    return Response(content='{"error":"no_data"}', media_type="application/json", status_code=502)

from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import requests, csv, io, os

app = FastAPI(title="FP&A Mini Backend (Railway + FastAPI)")

# CORS liberado para facilitar testes (Canvas/localhost/etc.)
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
    # Stooq usa minúsculas e .sa para B3 (ex.: PTBL3.SA -> ptbl3.sa)
    return sym.strip().lower()

def brapi_symbol(sym: str) -> str:
    # Brapi usa sem .SA (ex.: PTBL3.SA -> PTBL3)
    s = sym.strip().upper()
    return s.replace(".SA", "")

def stooq_quote(sym: str):
    """Preço no Stooq (market cap / shares não disponíveis)."""
    url = f"https://stooq.com/q/l/?s={stooq_symbol(sym)}&f=sd2t2ohlcvn"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if not rows:
            return None, None, None
        price_str = rows[0].get("c")
        price = float(price_str) if price_str and price_str not in ("N/D",) else None
        return price, None, None
    except Exception:
        return None, None, None

def brapi_quote(sym: str):
    """Preço / shares / mcap via brapi.dev (sem token também funciona)."""
    base = "https://brapi.dev/api/quote"
    t = brapi_symbol(sym)
    token = os.getenv("BRAPI_TOKEN")  # opcional
    url = f"{base}/{t}?range=1d&interval=1d" + (f"&token={token}" if token else "")
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        arr = data.get("results") or []
        if not arr:
            return None, None, None
        q = arr[0]
        price = q.get("regularMarketPrice") or q.get("regularMarketPreviousClose")
        mcap = q.get("marketCap")
        shares = q.get("sharesOutstanding") or q.get("shares")
        price = float(price) if price is not None else None
        mcap = float(mcap) if mcap is not None else None
        shares = int(shares) if shares is not None else None
        return price, shares, mcap
    except Exception:
        return None, None, None

def yf_quote(sym: str):
    """Preço / shares / mcap via yfinance com tolerância a falhas."""
    try:
        tk = yf.Ticker(sym)
        fi = getattr(tk, "fast_info", {}) or {}
        price = fi.get("last_price") or fi.get("lastPrice")
        shares = fi.get("shares")
        mcap = fi.get("market_cap")

        if price is None:
            try:
                info = tk.info or {}
                price = info.get("regularMarketPrice") or info.get("previousClose")
                shares = shares or info.get("sharesOutstanding")
                mcap = mcap or info.get("marketCap")
            except Exception:
                pass

        price = float(price) if price is not None else None
        mcap = float(mcap) if mcap is not None else None
        shares = int(shares) if shares is not None else None
        return price, shares, mcap
    except Exception:
        return None, None, None

def stooq_chart(sym: str, interval: str = "d"):
    """Série de fechamento via Stooq (d/w/m)."""
    url = f"https://stooq.com/q/d/l/?s={stooq_symbol(sym)}&i={interval}"
    try:
        r = requests.get(url, timeout=10)
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

def brapi_chart(sym: str, range_: str = "1y", interval: str = "1d"):
    """Série via brapi.dev (usa historicalDataPrice)."""
    base = "https://brapi.dev/api/quote"
    t = brapi_symbol(sym)
    token = os.getenv("BRAPI_TOKEN")
    url = f"{base}/{t}?range={range_}&interval={interval}" + (f"&token={token}" if token else "")
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        arr = data.get("results") or []
        if not arr:
            return []
        candles = arr[0].get("historicalDataPrice") or []
        closes = []
        for c in candles:
            v = c.get("close")
            if v is not None:
                try:
                    closes.append(float(v))
                except Exception:
                    pass
        return closes
    except Exception:
        return []

# ---------- endpoints ----------
@app.get("/quote")
def quote(symbols: str = Query(..., description="Comma-separated tickers e.g. PTBL3.SA,DXCO3.SA")):
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    out, errors = [], []
    for t in syms:
        source = "yfinance"
        price, shares, mcap = yf_quote(t)
        if price is None:
            source = "stooq"
            price, shares, mcap = stooq_quote(t)
        if price is None:
            source = "brapi"
            price, shares, mcap = brapi_quote(t)
        if price is None:
            errors.append({"symbol": t, "error": "no_data_all_sources"})
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
    # 1) yfinance
    try:
        hist = yf.Ticker(symbol).history(period=range, interval=interval)
        close = [float(x) for x in hist["Close"].dropna().tolist()]
        if close:
            return {"close": close, "source": "yfinance"}
    except Exception:
        pass

    # 2) Stooq
    stooq_int = "d"
    if interval.lower().startswith("w"):
        stooq_int = "w"
    elif interval.lower().startswith("m"):
        stooq_int = "m"
    close = stooq_chart(symbol, interval=stooq_int)
    if close:
        return {"close": close, "source": "stooq"}

    # 3) Brapi
    br_range = "1y" if range.lower() == "ytd" else range
    close = brapi_chart(symbol, range_=br_range, interval=interval)
    if close:
        return {"close": close, "source": "brapi"}

    return Response(content='{"error":"no_data_all_sources"}', media_type="application/json", status_code=502)

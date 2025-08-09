from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
import os, requests, csv, io
import yfinance as yf

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

# ============== Helpers de símbolos =================
def is_b3(sym: str) -> bool:
    return sym.strip().upper().endswith(".SA")

def to_alpha_symbol(sym: str) -> str:
    # Alpha Vantage usa ".SAO" para São Paulo (ex.: PETR4.SAO)
    s = sym.strip().upper().replace(".SA", "")
    return f"{s}.SAO"

def to_twelve_symbol(sym: str) -> str:
    # Twelve Data usa ".BVMF" (ex.: PETR4.BVMF)
    s = sym.strip().upper().replace(".SA", "")
    return f"{s}.BVMF"

def stooq_symbol(sym: str) -> str:
    return sym.strip().lower()  # ex.: petr4.sa

# ============== Providers =================
ALPHA_KEY = os.getenv("T3V35R0OAT8JWOFB", "").strip()
TWELVE_KEY = os.getenv("9f39bdddf7b04ff19b00ecc2136ff8ee", "").strip()

def alpha_quote(sym: str):
    """Alpha Vantage: GLOBAL_QUOTE"""
    if not ALPHA_KEY: return None, None, None
    t = to_alpha_symbol(sym)
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={t}&apikey={ALPHA_KEY}"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        data = r.json().get("Global Quote") or {}
        price = data.get("05. price")
        price = float(price) if price else None
        # Alpha raramente traz shares/mcap no free — devolvemos None
        return price, None, None
    except Exception:
        return None, None, None

def alpha_chart(sym: str, range_: str = "1y"):
    """Alpha Vantage: TIME_SERIES_DAILY_ADJUSTED (limite 100 pontos no free)"""
    if not ALPHA_KEY: return []
    t = to_alpha_symbol(sym)
    url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED&symbol={t}&outputsize=compact&apikey={ALPHA_KEY}"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        js = r.json()
        ts = js.get("Time Series (Daily)") or {}
        closes = [float(v["4. close"]) for k, v in sorted(ts.items())]
        return closes
    except Exception:
        return []

def twelve_quote(sym: str):
    """Twelve Data: /quote (requer TWELVE_KEY)"""
    if not TWELVE_KEY: return None, None, None
    t = to_twelve_symbol(sym)
    url = f"https://api.twelvedata.com/quote?symbol={t}&apikey={TWELVE_KEY}"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        js = r.json()
        if "price" not in js: return None, None, None
        price = float(js.get("price")) if js.get("price") else None
        # Twelve free também não traz shares/mcap por padrão
        return price, None, None
    except Exception:
        return None, None, None

def twelve_chart(sym: str, interval: str = "1day"):
    if not TWELVE_KEY: return []
    t = to_twelve_symbol(sym)
    url = f"https://api.twelvedata.com/time_series?symbol={t}&interval={interval}&outputsize=5000&apikey={TWELVE_KEY}"
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        js = r.json()
        data = js.get("values") or []
        closes = [float(x["close"]) for x in reversed(data)]
        return closes
    except Exception:
        return []

def yf_quote(sym: str):
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
        mcap  = float(mcap)  if mcap  is not None else None
        shares = int(shares) if shares is not None else None
        return price, shares, mcap
    except Exception:
        return None, None, None

def stooq_quote(sym: str):
    url = f"https://stooq.com/q/l/?s={stooq_symbol(sym)}&f=sd2t2ohlcvn"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if not rows: return None, None, None
        price_s = rows[0].get("c")
        price = float(price_s) if price_s and price_s not in ("N/D",) else None
        return price, None, None
    except Exception:
        return None, None, None

def stooq_chart(sym: str, interval: str = "d"):
    url = f"https://stooq.com/q/d/l/?s={stooq_symbol(sym)}&i={interval}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text))
        closes = []
        for row in reader:
            v = row.get("Close")
            if v and v not in ("N/D",):
                try: closes.append(float(v))
                except: pass
        return closes
    except Exception:
        return []

# ============== Endpoints =================
@app.get("/quote")
def quote(symbols: str = Query(..., description="Comma-separated tickers e.g. PTBL3.SA,DXCO3.SA")):
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    out, errors = [], []
    for t in syms:
        price = shares = mcap = None
        source = None

        # Ordem preferencial: Alpha → Twelve → Yahoo → Stooq
        price, shares, mcap = alpha_quote(t)
        source = "alpha"
        if price is None:
            p2, s2, m2 = twelve_quote(t); 
            if p2 is not None:
                price, shares, mcap = p2, s2, m2; source = "twelve"
        if price is None:
            p3, s3, m3 = yf_quote(t); 
            if p3 is not None:
                price, shares, mcap = p3, s3, m3; source = "yfinance"
        if price is None:
            p4, s4, m4 = stooq_quote(t); 
            if p4 is not None:
                price, shares, mcap = p4, s4, m4; source = "stooq"

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
    # Alpha Vantage (diário)
    closes = alpha_chart(symbol, range_="1y" if range.lower()=="ytd" else range)
    if closes:
        return {"close": closes, "source": "alpha"}

    # Twelve Data
    interval12 = "1day" if interval.lower().startswith("1d") else "1week"
    closes = twelve_chart(symbol, interval=interval12)
    if closes:
        return {"close": closes, "source": "twelve"}

    # yfinance
    try:
        hist = yf.Ticker(symbol).history(period=range, interval=interval)
        closes = [float(x) for x in hist["Close"].dropna().tolist()]
        if closes:
            return {"close": closes, "source": "yfinance"}
    except Exception:
        pass

    # Stooq
    stooq_int = "d" if interval.lower().startswith("1d") else ("w" if interval.lower().startswith("1w") else "m")
    closes = stooq_chart(symbol, interval=stooq_int)
    if closes:
        return {"close": closes, "source": "stooq"}

    return Response(content='{"error":"no_data_all_sources"}', media_type="application/json", status_code=502)

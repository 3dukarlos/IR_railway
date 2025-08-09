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

# ========= helpers de símbolo =========
def is_b3(sym: str) -> bool:
    return sym.strip().upper().endswith(".SA")

def to_alpha_symbol(sym: str) -> str:   # Alpha: PETR4.SAO
    return sym.strip().upper().replace(".SA", "") + ".SAO"

def to_twelve_symbol(sym: str) -> str:  # Twelve: PETR4.BVMF
    return sym.strip().upper().replace(".SA", "") + ".BVMF"

def stooq_symbol(sym: str) -> str:      # Stooq: petr4.sa
    return sym.strip().lower()

# ========= providers =========
ALPHA_KEY = os.getenv("ALPHAVANTAGE_KEY", "").strip()
TWELVE_KEY = os.getenv("TWELVE_KEY", "").strip()

def alpha_quote(sym: str):
    if not ALPHA_KEY: return None, None, None
    t = to_alpha_symbol(sym)
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={t}&apikey={ALPHA_KEY}"
    try:
        r = requests.get(url, timeout=12); r.raise_for_status()
        data = r.json().get("Global Quote") or {}
        price = data.get("05. price")
        price = float(price) if price else None
        return price, None, None
    except Exception:
        return None, None, None

def alpha_chart(sym: str):
    if not ALPHA_KEY: return []
    t = to_alpha_symbol(sym)
    url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED&symbol={t}&outputsize=compact&apikey={ALPHA_KEY}"
    try:
        r = requests.get(url, timeout=12); r.raise_for_status()
        ts = (r.json().get("Time Series (Daily)") or {})
        return [float(v["4. close"]) for _, v in sorted(ts.items())]
    except Exception:
        return []

def twelve_quote(sym: str):
    if not TWELVE_KEY: return None, None, None
    t = to_twelve_symbol(sym)
    url = f"https://api.twelvedata.com/quote?symbol={t}&apikey={TWELVE_KEY}"
    try:
        r = requests.get(url, timeout=12); r.raise_for_status()
        js = r.json()
        if "price" not in js: return None, None, None
        price = float(js.get("price")) if js.get("price") else None
        return price, None, None
    except Exception:
        return None, None, None

def twelve_chart(sym: str, interval: str = "1day"):
    if not TWELVE_KEY: return []
    t = to_twelve_symbol(sym)
    url = f"https://api.twelvedata.com/time_series?symbol={t}&interval={interval}&outputsize=5000&apikey={TWELVE_KEY}"
    try:
        r = requests.get(url, timeout=12); r.raise_for_status()
        js = r.json()
        data = js.get("values") or []
        return [float(x["close"]) for x in reversed(data)]
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
        r = requests.get(url, timeout=10); r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if not rows: return None, None, None
        ps = rows[0].get("c")
        price = float(ps) if ps and ps not in ("N/D",) else None
        return price, None, None
    except Exception:
        return None, None, None

def stooq_chart(sym: str, interval: str = "d"):
    url = f"https://stooq.com/q/d/l/?s={stooq_symbol(sym)}&i={interval}"
    try:
        r = requests.get(url, timeout=10); r.raise_for_status()
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

# ========= endpoints =========
@app.get("/quote")
def quote(symbols: str = Query(..., description="Comma-separated tickers e.g. PTBL3.SA,DXCO3.SA")):
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    out, errors = [], []
    for t in syms:
        price = shares = mcap = None
        source = None
        if is_b3(t):
            # B3: Twelve → Alpha → Yahoo → Stooq
            for name, fn in [("twelve", twelve_quote), ("alpha", alpha_quote), ("yfinance", yf_quote), ("stooq", stooq_quote)]:
                p, s, m = fn(t)
                if p is not None:
                    price, shares, mcap = p, s, m; source = name; break
        else:
            # Outros: Alpha → Twelve → Yahoo → Stooq
            for name, fn in [("alpha", alpha_quote), ("twelve", twelve_quote), ("yfinance", yf_quote), ("stooq", stooq_quote)]:
                p, s, m = fn(t)
                if p is not None:
                    price, shares, mcap = p, s, m; source = name; break
        if price is None:
            errors.append({"symbol": t, "error": "no_data_all_sources"})
        out.append({"symbol": t, "regularMarketPrice": price, "marketCap": mcap, "sharesOutstanding": shares, "source": source})
    return {"result": out, "errors": errors}

@app.get("/chart")
def chart(symbol: str, range: str = "ytd", interval: str = "1d"):
    # B3: Twelve → Alpha → Yahoo → Stooq
    if is_b3(symbol):
        closes = twelve_chart(symbol, interval="1day" if interval.lower().startswith("1d") else "1week")
        if closes: return {"close": closes, "source": "twelve"}
        closes = alpha_chart(symbol)
        if closes: return {"close": closes, "source": "alpha"}
        try:
            hist = yf.Ticker(symbol).history(period=range, interval=interval)
            closes = [float(x) for x in hist["Close"].dropna().tolist()]
            if closes: return {"close": closes, "source": "yfinance"}
        except Exception:
            pass
        closes = stooq_chart(symbol, interval=("d" if interval.lower().startswith("1d") else "w"))
        if closes: return {"close": closes, "source": "stooq"}
        return Response(content='{"error":"no_data_all_sources"}', media_type="application/json", status_code=502)
    # Outros: Alpha → Twelve → Yahoo → Stooq
    closes = alpha_chart(symbol)
    if closes: return {"close": closes, "source": "alpha"}
    closes = twelve_chart(symbol, interval="1day" if interval.lower().startswith("1d") else "1week")
    if closes: return {"close": closes, "source": "twelve"}
    try:
        hist = yf.Ticker(symbol).history(period=range, interval=interval)
        closes = [float(x) for x in hist["Close"].dropna().tolist()]
        if closes: return {"close": closes, "source": "yfinance"}
    except Exception:
        pass
    closes = stooq_chart(symbol, interval=("d" if interval.lower().startswith("1d") else "w"))
    if closes: return {"close": closes, "source": "stooq"}
    return Response(content='{"error":"no_data_all_sources"}', media_type="application/json", status_code=502)

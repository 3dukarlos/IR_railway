from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
import os, time, requests, csv, io
import yfinance as yf

app = FastAPI(title="FP&A Mini Backend (Railway + FastAPI)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALPHA_KEY = os.getenv("ALPHAVANTAGE_KEY", "").strip()
ALPHA_BASE = "https://www.alphavantage.co/query"

# cache bobo (memória do processo) para aliviar rate limit
CACHE_TTL = 60  # segundos
_cache = {}

def cache_get(k):
    v = _cache.get(k)
    if not v: return None
    val, ts = v
    if time.time() - ts > CACHE_TTL:
        _cache.pop(k, None)
        return None
    return val

def cache_set(k, val):
    _cache[k] = (val, time.time())

def is_b3(sym: str) -> bool:
    return sym.strip().upper().endswith(".SA")

def to_alpha_symbol(sym: str) -> str:   # Alpha: PETR4.SAO
    return sym.strip().upper().replace(".SA", "") + ".SAO"

def stooq_symbol(sym: str) -> str:      # Stooq: petr4.sa
    return sym.strip().lower()

@app.get("/health")
def health():
    return {"ok": True, "runtime": "python", "framework": "fastapi"}

# ---------- providers ----------
def alpha_quote(sym: str):
    if not ALPHA_KEY: return None, None, None
    t = to_alpha_symbol(sym)
    key = ("alpha_quote", t)
    c = cache_get(key)
    if c is not None: return c
    url = f"{ALPHA_BASE}?function=GLOBAL_QUOTE&symbol={t}&apikey={ALPHA_KEY}"
    try:
        r = requests.get(url, timeout=12); r.raise_for_status()
        data = r.json().get("Global Quote") or {}
        price = data.get("05. price")
        price = float(price) if price else None
        out = (price, None, None)  # Alpha free não traz shares/mcap
        cache_set(key, out)
        return out
    except Exception:
        return None, None, None

def alpha_chart(sym: str):
    # usa TIME_SERIES_DAILY_ADJUSTED (compact = últimos ~100 dias)
    if not ALPHA_KEY: return []
    t = to_alpha_symbol(sym)
    key = ("alpha_chart", t)
    c = cache_get(key)
    if c is not None: return c
    url = f"{ALPHA_BASE}?function=TIME_SERIES_DAILY_ADJUSTED&symbol={t}&outputsize=compact&apikey={ALPHA_KEY}"
    try:
        r = requests.get(url, timeout=12); r.raise_for_status()
        ts = (r.json().get("Time Series (Daily)") or {})
        closes = [float(v["4. close"]) for _, v in sorted(ts.items())]
        cache_set(key, closes)
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

# ===== Alpha Vantage: OVERVIEW + BALANCE_SHEET + GLOBAL_QUOTE =====
def alpha_overview(sym: str):
    """Campos de fundamentals (TTM) via Alpha: MarketCap, EBITDA, EPS, BookValue, SharesOutstanding, P/E, P/B, EV/EBITDA."""
    if not ALPHA_KEY: return {}
    t = to_alpha_symbol(sym)
    key = ("alpha_overview", t)
    c = cache_get(key)
    if c is not None: return c
    url = f"{ALPHA_BASE}?function=OVERVIEW&symbol={t}&apikey={ALPHA_KEY}"
    try:
        r = requests.get(url, timeout=12); r.raise_for_status()
        js = r.json() or {}
        # normaliza numéricos
        def f(x):
            try: return float(x)
            except: return None
        out = {
            "MarketCapitalization": f(js.get("MarketCapitalization")),
            "EBITDA": f(js.get("EBITDA")),
            "DilutedEPSTTM": f(js.get("DilutedEPSTTM")),
            "BookValue": f(js.get("BookValue")),           # por ação
            "SharesOutstanding": (int(float(js.get("SharesOutstanding"))) if js.get("SharesOutstanding") not in (None,"") else None),
            "PERatio": f(js.get("PERatio")) or f(js.get("TrailingPE")),
            "PriceToBookRatio": f(js.get("PriceToBookRatio")),
            "EVToEBITDA": f(js.get("EVToEBITDA")),
        }
        cache_set(key, out); return out
    except Exception:
        return {}

def alpha_balance_latest(sym: str):
    """Busca TotalDebt e Cash do último relatório (annual ou quarterly)."""
    if not ALPHA_KEY: return {}
    t = to_alpha_symbol(sym)
    key = ("alpha_balance", t)
    c = cache_get(key)
    if c is not None: return c
    url = f"{ALPHA_BASE}?function=BALANCE_SHEET&symbol={t}&apikey={ALPHA_KEY}"
    try:
        r = requests.get(url, timeout=12); r.raise_for_status()
        js = r.json() or {}
        rows = (js.get("quarterlyReports") or []) or (js.get("annualReports") or [])
        def f(x):
            try: return float(x)
            except: return None
        if rows:
            last = rows[0]
            out = {
                "TotalDebt": f(last.get("totalDebt")),
                "Cash": f(last.get("cashAndCashEquivalentsAtCarryingValue") or last.get("cashAndCashEquivalents")) ,
            }
        else:
            out = {"TotalDebt": None, "Cash": None}
        cache_set(key, out); return out
    except Exception:
        return {"TotalDebt": None, "Cash": None}

def alpha_price(sym: str):
    """Preço via GLOBAL_QUOTE (com cache)."""
    p,_,_ = alpha_quote(sym)  # já tem cache
    return p


# ---------- endpoints ----------
@app.get("/quote")
def quote(symbols: str = Query(..., description="Comma-separated tickers e.g. PTBL3.SA,DXCO3.SA")):
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    out, errors = [], []
    for t in syms:
        # ordem: Alpha → yfinance → Stooq
        price, shares, mcap = alpha_quote(t)
        source = "alpha"
        if price is None:
            p2, s2, m2 = yf_quote(t)
            if p2 is not None:
                price, shares, mcap = p2, s2, m2; source = "yfinance"
        if price is None:
            p3, s3, m3 = stooq_quote(t)
            if p3 is not None:
                price, shares, mcap = p3, s3, m3; source = "stooq"

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
    # Alpha (diário). Para 'ytd', o front calcula retorno a partir da série.
    closes = alpha_chart(symbol)
    if closes:
        return {"close": closes, "source": "alpha"}
    # fallback yfinance
    try:
        hist = yf.Ticker(symbol).history(period=range, interval=interval)
        closes = [float(x) for x in hist["Close"].dropna().tolist()]
        if closes:
            return {"close": closes, "source": "yfinance"}
    except Exception:
        pass
    # fallback Stooq
    stooq_int = "d" if interval.lower().startswith("1d") else ("w" if interval.lower().startswith("1w") else "m")
    closes = stooq_chart(symbol, interval=stooq_int)
    if closes:
        return {"close": closes, "source": "stooq"}
    return Response(content='{"error":"no_data_all_sources"}', media_type="application/json", status_code=502)

@app.get("/fundamentals")
def fundamentals(ticker: str):
    """
    Retorna básicos para múltiplos:
    - price, sharesOutstanding, marketCap
    - totalDebt, cash, netDebt, EV
    - ebitdaTTM, epsTTM, bookValuePerShare
    - evEbitda, pe, pb
    source: "alpha"
    """
    sym = ticker.strip().upper()
    ov = alpha_overview(sym)
    bal = alpha_balance_latest(sym)
    price = alpha_price(sym)

    shares = ov.get("SharesOutstanding")
    marketCap = ov.get("MarketCapitalization")
    ebitda = ov.get("EBITDA")
    eps = ov.get("DilutedEPSTTM")
    bvps = ov.get("BookValue")
    pe_av = ov.get("PERatio")
    pb_av = ov.get("PriceToBookRatio")
    ev_ebitda_av = ov.get("EVToEBITDA")

    totalDebt = bal.get("TotalDebt")
    cash = bal.get("Cash")
    netDebt = (totalDebt - cash) if (totalDebt is not None and cash is not None) else None

    # EV preferindo marketCap + netDebt; senão deixa None
    ev = (marketCap + netDebt) if (marketCap is not None and netDebt is not None) else None

    # Múltiplos calculados (se possível)
    evEbitda_calc = (ev / ebitda) if (ev is not None and ebitda and ebitda > 0) else None
    pe_calc = (price / eps) if (price is not None and eps and eps != 0) else None
    # Book Value total = bvps * shares (se existir)
    equityBV = (bvps * shares) if (bvps is not None and shares is not None) else None
    pb_calc = (marketCap / equityBV) if (marketCap is not None and equityBV and equityBV > 0) else None

    return {
        "symbol": sym,
        "source": "alpha",
        "price": price,
        "sharesOutstanding": shares,
        "marketCap": marketCap,
        "totalDebt": totalDebt,
        "cash": cash,
        "netDebt": netDebt,
        "ev": ev,
        "ebitdaTTM": ebitda,
        "epsTTM": eps,
        "bookValuePerShare": bvps,
        "equityBookValue": equityBV,
        "multiples": {
            "evEbitda": evEbitda_calc or ev_ebitda_av,
            "pe": pe_calc or pe_av,
            "pb": pb_calc or pb_av
        }
    }


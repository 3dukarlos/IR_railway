from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf

app = FastAPI(title="FP&A Mini Backend (Railway + FastAPI)")

# CORS liberado
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

@app.get("/quote")
def quote(symbols: str = Query(..., description="Comma-separated tickers, e.g. PTBL3.SA,DXCO3.SA")):
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    out = []
    errors = []
    for t in syms:
        try:
            tk = yf.Ticker(t)
            fi = getattr(tk, "fast_info", {}) or {}
            price = fi.get('last_price') or fi.get('lastPrice')
            shares = fi.get('shares')
            mcap = fi.get('market_cap')

            if price is None:
                try:
                    info = tk.info or {}
                    price = info.get('regularMarketPrice') or info.get('previousClose')
                    shares = shares or info.get('sharesOutstanding')
                    mcap = mcap or info.get('marketCap')
                except Exception as e:
                    errors.append({"symbol": t, "stage": "info", "error": str(e)})

            out.append({
                "symbol": t,
                "regularMarketPrice": float(price) if price is not None else None,
                "marketCap": float(mcap) if mcap is not None else None,
                "sharesOutstanding": int(shares) if shares is not None else None,
            })
        except Exception as e:
            errors.append({"symbol": t, "stage": "fast_info", "error": str(e)})
            out.append({"symbol": t, "regularMarketPrice": None, "marketCap": None, "sharesOutstanding": None})
    return {"result": out, "errors": errors}

@app.get("/chart")
def chart(symbol: str, range: str = "ytd", interval: str = "1d"):
    try:
        hist = yf.Ticker(symbol).history(period=range, interval=interval)
        close = [float(x) for x in hist["Close"].dropna().tolist()]
        return {"close": close}
    except Exception as e:
        return Response(content='{"error":"upstream_error","detail":"' + str(e).replace('"','\"') + '"}', media_type="application/json", status_code=502)

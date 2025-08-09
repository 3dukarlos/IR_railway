# FP&A Mini Backend — Railway (FastAPI + yfinance)

Endpoints:

- `GET /health` → status
- `GET /quote?symbols=PTBL3.SA,DXCO3.SA,LJQQ3.SA,^BVSP` → preços/market cap/shares
- `GET /chart?symbol=PTBL3.SA&range=ytd&interval=1d` → série de fechamento

## Deploy (Railway)
1. Crie um novo projeto no [Railway](https://railway.app/).
2. Clique **New → Upload** e suba este zip.
3. Configure Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Habilite porta pública.

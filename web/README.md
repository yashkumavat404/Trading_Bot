# Trading Bot Dashboard

This folder is the Vercel-deployable Next.js dashboard. It is deliberately separate from the Python trading worker.

## Deploy on Vercel

1. Import `yashkumavat404/Trading_Bot`.
2. Set **Root Directory** to `web`.
3. Use the **Next.js** framework preset.
4. Add `NEXT_PUBLIC_BOT_API_URL` only when the separate worker API has been deployed. It should be the worker's HTTPS base URL.
5. Deploy the dashboard.

The page renders safely before a worker is connected. It requests `GET /api/status` from the worker every 15 seconds once `NEXT_PUBLIC_BOT_API_URL` is configured.

## Security

- Never put exchange API keys or secrets in `NEXT_PUBLIC_*` variables. These are bundled for browser use.
- Keep `BYBIT_DEMO_API_KEY` and `BYBIT_DEMO_API_SECRET` only on the private worker service that needs them.
- The UI is monitoring-only; it does not place orders.
- A connected dashboard requires the worker to implement a read-only `GET /api/status` endpoint with CORS restricted to the dashboard origin.

// Read proxy: serves the full session history straight out of Postgres.
//
// The response is byte-for-byte the same shape as data/history.json, so the
// dashboard needs no new rendering path and can fall back to the committed
// JSON whenever this endpoint or the database is unavailable. The difference
// is depth: history.json is trimmed to decisions.history_keep_days, this is
// every session ever written.
//
// The connection string lives only in the server environment and never reaches
// the browser. Use a SELECT-only role for it; this endpoint never writes.

import { neon } from '@neondatabase/serverless';

// Columns are float4 in Postgres, so they come back with single-precision
// noise (0.15 as 0.15000000596...). Round to the precision the pipeline
// actually stores, so the API and the JSON file agree exactly.
const round = (v, places) =>
  v === null || v === undefined ? null : Math.round(Number(v) * 10 ** places) / 10 ** places;

export default async function handler(req, res) {
  const dsn = process.env.DATABASE_URL;
  if (!dsn) {
    res.status(503).json({ error: 'DATABASE_URL is not configured for this deployment' });
    return;
  }

  try {
    const sql = neon(dsn);
    const rows = await sql`
      select to_char(session_date, 'YYYY-MM-DD') as date,
             ticker, skew, pts, ret, chain_ok
        from board_rows
       where skew is not null
       order by session_date, ticker
    `;

    const out = {};
    const dates = new Set();
    for (const r of rows) {
      dates.add(r.date);
      const entry = { date: r.date, skew: round(r.skew, 4), pts: round(r.pts, 2), ok: r.chain_ok };
      if (r.ret !== null && r.ret !== undefined) entry.ret = round(r.ret, 2);
      (out[r.ticker] ??= []).push(entry);
    }
    out._dates = [...dates].sort();

    // The board updates once per session; a five minute edge cache matches the
    // page's own poll interval and keeps idle traffic off the database.
    res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=600');
    res.status(200).json(out);
  } catch (e) {
    // Say what broke. The dashboard falls back to data/history.json on any
    // non-200, so a database problem degrades the Delta column, never the board.
    res.status(502).json({ error: String((e && e.message) || e) });
  }
}

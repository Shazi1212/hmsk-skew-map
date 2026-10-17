// Read proxy for the board itself, the intraday counterpart to api/history.js.
//
// Optional. If you run intraday builds that write only to Postgres, the
// freshest board lives here and data/latest.json is the previous close.
// The dashboard fetches both and keeps
// whichever carries the newer as_of, which means a cold or unreachable database
// costs freshness and never the board.
//
// The stored JSON is exactly what the pipeline wrote, warnings and failures
// included, so this needs no shaping. The connection string lives only in the
// server environment, on a SELECT-only role.

import { neon } from '@neondatabase/serverless';

export default async function handler(req, res) {
  const dsn = process.env.DATABASE_URL;
  if (!dsn) {
    res.status(503).json({ error: 'DATABASE_URL is not configured for this deployment' });
    return;
  }

  try {
    const sql = neon(dsn);
    const rows = await sql`
      select board
        from boards
       order by session_date desc
       limit 1
    `;
    if (!rows.length) {
      res.status(404).json({ error: 'no board has been stored yet' });
      return;
    }

    // The page polls every 5 minutes, so a one minute
    // edge cache keeps the database idle without the board ever looking stale.
    res.setHeader('Cache-Control', 's-maxage=60, stale-while-revalidate=300');
    res.status(200).json(rows[0].board);
  } catch (e) {
    // The dashboard falls back to data/latest.json on any non-200.
    res.status(502).json({ error: String((e && e.message) || e) });
  }
}

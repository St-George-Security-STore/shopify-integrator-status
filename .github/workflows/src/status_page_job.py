"""Statussida: en enkel, offentlig HTML-sida som visar alla ordrars status.

Körs en gång i timmen (se .github/workflows/status.yml), skriver
`public/index.html`, och publiceras sedan via GitHub Pages av samma
workflow. Sidan är read-only och byggs om från grunden varje gång — inget
sparas här, allt kommer från databasen.

VIKTIGT — vad som medvetet INTE finns med på sidan: leveransadress, namn,
telefonnummer, e-post. GitHub Pages från ett privat repo är fortfarande en
OFFENTLIGT nåbar sida (repots privata status skyddar bara källkoden, inte
den publicerade sidan — det är bara med GitHub Enterprise Cloud man kan
hålla en Pages-sida privat). Se docs/OPPNA_FRAGOR.md punkt 11. Sidan visar
därför bara ordernummer, leverantör, status och tider — aldrig
`leveransadress`-fältet eller `ra_payload`.

Om någon senare vill ha full detalj (inklusive kunduppgifter) är Neons
egen inloggningsskyddade tabellvy rätt ställe för det, inte den här sidan.
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

from . import db
from .config import load_config

MAX_ORDRAR = 300

STATUS_LABEL = {
    "pending": "Väntar",
    "sent": "Skickad",
    "confirmed": "Bekräftad",
    "failed": "Misslyckad",
    "cancelled": "Avbruten",
}
STATUS_ORDNING = ["pending", "sent", "confirmed", "failed", "cancelled"]

KORNING_LABEL = {
    "sync": "Synk (Shopify → databas)",
    "export": "Export (databas → leverantör)",
    "reconciliation": "Avstämning och tystnadslarm",
}
KORNING_ORDNING = ["sync", "export", "reconciliation"]


def hamta_senaste_korningar(cur) -> dict[str, dict]:
    cur.execute(
        """
        SELECT DISTINCT ON (jobb) jobb, start, slut, status, antal_ordrar, antal_rader, fel_meddelande
        FROM korning
        ORDER BY jobb, start DESC
        """
    )
    return {rad["jobb"]: rad for rad in cur.fetchall()}


def hamta_summering(cur) -> dict[str, dict[str, int]]:
    cur.execute(
        """
        SELECT leverantor_nyckel, status, COUNT(*) AS antal
        FROM leverantorsorder
        GROUP BY leverantor_nyckel, status
        """
    )
    summering: dict[str, dict[str, int]] = {}
    for rad in cur.fetchall():
        summering.setdefault(rad["leverantor_nyckel"], {})[rad["status"]] = rad["antal"]
    return summering


def hamta_ordrar(cur) -> list[dict]:
    cur.execute(
        """
        SELECT
            o.shopify_order_namn, o.leverantor_nyckel, o.status,
            o.skapad_at, o.skickad_at, o.bekraftad_at,
            (SELECT COUNT(*) FROM leverantorsorderrad r WHERE r.leverantorsorder_id = o.id) AS antal_rader
        FROM leverantorsorder o
        ORDER BY o.skapad_at DESC
        LIMIT %s
        """,
        (MAX_ORDRAR,),
    )
    return cur.fetchall()


def hamta_totalt_antal_ordrar(cur) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM leverantorsorder")
    return cur.fetchone()["n"]


def _fmt_tid(varde) -> str:
    if varde is None:
        return "—"
    return varde.strftime("%Y-%m-%d %H:%M")


def _e(varde) -> str:
    return html.escape(str(varde)) if varde is not None else "—"


def bygg_html(
    genererad: dt.datetime,
    korningar: dict[str, dict],
    summering: dict[str, dict[str, int]],
    ordrar: list[dict],
    totalt_antal_ordrar: int,
    leverantorsnamn: dict[str, str],
) -> str:
    korning_rader = []
    for jobb in KORNING_ORDNING:
        k = korningar.get(jobb)
        if k is None:
            korning_rader.append(
                f"<tr><td>{_e(KORNING_LABEL.get(jobb, jobb))}</td>"
                f"<td colspan='4' class='dimmed'>Har aldrig körts än</td></tr>"
            )
            continue
        status_klass = {"ok": "ok", "fel": "fel"}.get(k["status"], "")
        status_text = {"ok": "OK", "fel": "Fel", "running": "Pågår"}.get(k["status"], k["status"])
        korning_rader.append(
            "<tr>"
            f"<td>{_e(KORNING_LABEL.get(jobb, jobb))}</td>"
            f"<td>{_fmt_tid(k['start'])}</td>"
            f"<td class='{status_klass}'>{_e(status_text)}</td>"
            f"<td>{_e(k['antal_ordrar'])}</td>"
            f"<td>{_e(k['antal_rader'])}</td>"
            "</tr>"
        )

    summering_rader = []
    for nyckel in sorted(summering.keys() | leverantorsnamn.keys()):
        rad = summering.get(nyckel, {})
        namn = leverantorsnamn.get(nyckel, nyckel)
        celler = "".join(f"<td>{rad.get(s, 0)}</td>" for s in STATUS_ORDNING)
        totalt = sum(rad.values())
        summering_rader.append(f"<tr><td>{_e(namn)}</td>{celler}<td><b>{totalt}</b></td></tr>")

    order_rader = []
    for o in ordrar:
        namn = leverantorsnamn.get(o["leverantor_nyckel"], o["leverantor_nyckel"])
        status_klass = {"sent": "ok", "confirmed": "ok", "failed": "fel"}.get(o["status"], "")
        order_rader.append(
            "<tr>"
            f"<td>{_e(o['shopify_order_namn'])}</td>"
            f"<td>{_e(namn)}</td>"
            f"<td class='{status_klass}'>{_e(STATUS_LABEL.get(o['status'], o['status']))}</td>"
            f"<td>{_fmt_tid(o['skapad_at'])}</td>"
            f"<td>{_fmt_tid(o['skickad_at'])}</td>"
            f"<td>{_fmt_tid(o['bekraftad_at'])}</td>"
            f"<td>{_e(o['antal_rader'])}</td>"
            "</tr>"
        )

    trunkerat_notis = ""
    if totalt_antal_ordrar > len(ordrar):
        trunkerat_notis = (
            f"<p class='dimmed'>Visar de {len(ordrar)} senaste ordrarna av {totalt_antal_ordrar} totalt.</p>"
        )

    summering_header = "".join(f"<th>{STATUS_LABEL[s]}</th>" for s in STATUS_ORDNING)

    return f"""<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Leverantörsintegration — status</title>
<style>
  :root {{
    --bg: #f7f7f5; --card: #ffffff; --ink: #1c1c1a; --dimmed: #6b6b66;
    --border: #e2e2dd; --ok: #1f5c4e; --ok-bg: #e6f2ee; --fel: #a3311a; --fel-bg: #fbe9e5;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 20px 64px; background: var(--bg); color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .genererad {{ color: var(--dimmed); font-size: 0.85rem; margin: 0 0 28px; }}
  h2 {{ font-size: 1.05rem; margin: 32px 0 10px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  th {{ background: #efefec; font-weight: 600; font-size: 0.8rem; color: var(--dimmed); text-transform: uppercase; letter-spacing: 0.02em; }}
  tr:last-child td {{ border-bottom: none; }}
  td.ok {{ color: var(--ok); font-weight: 600; }}
  td.fel {{ color: var(--fel); font-weight: 600; }}
  .dimmed {{ color: var(--dimmed); font-size: 0.85rem; }}
  .scroll {{ overflow-x: auto; }}
  .notis {{
    background: #fff8e8; border: 1px solid #e8d9ab; border-radius: 8px; padding: 12px 16px;
    font-size: 0.85rem; color: #6b5a1e; margin-bottom: 28px;
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Leverantörsintegration — status</h1>
  <p class="genererad">Genererad {_fmt_tid(genererad)} UTC · uppdateras automatiskt varje timme</p>

  <div class="notis">
    Den här sidan är offentligt nåbar och innehåller därför medvetet inga kunduppgifter
    (namn, adress, telefon, e-post) — bara ordernummer, leverantör, status och tider.
  </div>

  <h2>Senaste körningar</h2>
  <div class="card scroll">
    <table>
      <tr><th>Jobb</th><th>Senast körd</th><th>Resultat</th><th>Ordrar</th><th>Rader</th></tr>
      {''.join(korning_rader)}
    </table>
  </div>

  <h2>Ordrar per leverantör</h2>
  <div class="card scroll">
    <table>
      <tr><th>Leverantör</th>{summering_header}<th>Totalt</th></tr>
      {''.join(summering_rader) or '<tr><td colspan="7" class="dimmed">Inga ordrar än</td></tr>'}
    </table>
  </div>

  <h2>Senaste ordrar</h2>
  {trunkerat_notis}
  <div class="card scroll">
    <table>
      <tr><th>Order</th><th>Leverantör</th><th>Status</th><th>Skapad</th><th>Skickad</th><th>Bekräftad</th><th>Rader</th></tr>
      {''.join(order_rader) or '<tr><td colspan="7" class="dimmed">Inga ordrar än</td></tr>'}
    </table>
  </div>
</div>
</body>
</html>
"""


def bygg_statussida(utdata_mapp: str | Path = "public") -> Path:
    config = load_config()
    leverantorsnamn = {nyckel: lv.namn for nyckel, lv in config.leverantorer.items()}

    with db.transaction() as cur:
        korningar = hamta_senaste_korningar(cur)
        summering = hamta_summering(cur)
        ordrar = hamta_ordrar(cur)
        totalt_antal_ordrar = hamta_totalt_antal_ordrar(cur)

    sida = bygg_html(
        genererad=dt.datetime.now(dt.timezone.utc),
        korningar=korningar,
        summering=summering,
        ordrar=ordrar,
        totalt_antal_ordrar=totalt_antal_ordrar,
        leverantorsnamn=leverantorsnamn,
    )

    utdata_mapp = Path(utdata_mapp)
    utdata_mapp.mkdir(parents=True, exist_ok=True)
    utdata_fil = utdata_mapp / "index.html"
    utdata_fil.write_text(sida, encoding="utf-8")
    return utdata_fil


if __name__ == "__main__":
    fil = bygg_statussida()
    print(f"Skrev {fil}")

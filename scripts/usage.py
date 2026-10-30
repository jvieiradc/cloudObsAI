"""Usage and estimated cost per day, from the audit table.

The cost is the SDK's estimate at list prices, not what the gateway bills: compare the tokens with its panel.
Run: docker compose exec app python scripts/usage.py [days]
"""
import sqlite3
import sys

from cloudobsai.config import load

days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
db = sqlite3.connect(load().data_dir / "cloudobsai.db")
print("dia\tmodo\tpedidos\terros\ttokens_entrada\ttokens_saida\tcusto_usd\tcusto_medio\tcusto_max")
rows = db.execute(
    """SELECT substr(created_at, 1, 10), mode, count(*), sum(result != 'ok'), sum(input_tokens), sum(output_tokens),
              round(sum(cost_usd), 4), round(avg(cost_usd), 4), round(max(cost_usd), 4)
       FROM audit WHERE created_at >= date('now', ?) GROUP BY 1, 2 ORDER BY 1, 2""",
    (f"-{days} days",),
)
for row in rows:
    print("\t".join("" if value is None else str(value) for value in row))
total = db.execute("SELECT count(*), round(sum(cost_usd), 4) FROM audit WHERE created_at >= date('now', ?)", (f"-{days} days",))
print("total: %s pedidos, US$ %s" % total.fetchone())

import sys, json, datetime
sys.path.insert(0, sys.argv[1])
import helm.advice as A, helm.bookview as B
print(B.__file__, file=sys.stderr)
d = A.build(db="/tmp/s144/board.db", today=datetime.date(2026,11,2))
d.pop("generated_at", None)
print(json.dumps(d, sort_keys=True, default=str))

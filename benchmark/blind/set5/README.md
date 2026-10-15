# Blind set 5

Written on 2026-10-15, after D57 (scrolled panels, unnamed draggable pictures, field states) and D59 (a
"says" claim needs evidence that says it), before any further change, on five sites Nightshift had never
run on: seleniumbase.io's demo page, Katalon's CURA Healthcare demo, qavbox.github.io, Applitools' demo
app and LeafGround (PrimeFaces components). Each file's first line says its right verdict, checked by
hand in a plain browser. 22 working flows and 8 wrong expectations.

Run once and report as it comes out (CURA_PASSWORD is the demo password CURA shows on its sign-in page):

    uv run nightshift run benchmark/blind/set5 --no-replay --no-record

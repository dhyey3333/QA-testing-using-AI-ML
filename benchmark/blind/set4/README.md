# Blind set 4

Written on 2026-10-13, before the blind-set-3 fixes (D56) were started, on six sites Nightshift had
never run on: qaplayground.dev, formy-project.herokuapp.com, qa-practice.com, commitquality.com,
example.cypress.io and demo.automationtesting.in, plus react-select.com's own demo. Each file's
first line says its right verdict, checked by hand in a plain browser. 23 working flows (drags, a
double-click, searchable dropdowns, unnamed icons, a covered button, a site that throws a
JavaScript error on every page load) and 7 wrong expectations.

Run once, after the fixes, and report as it comes out:

    uv run nightshift run benchmark/blind/set4 --no-replay --no-record

# Blind set 2

Written on 2026-10-09, before the false-alarm fixes from the first blind test (`../uitp`). Four sites
Nightshift had never run on: practice-automation.com, letcode.in, testpages.eviltester.com and
testautomationpractice.blogspot.com. Each file's first line says its right verdict, checked by hand
in a plain browser when it was written.

Rules: nothing in the agent, prompts or thresholds is changed while looking at these pages. Run it
once, after the fixes, and report the number as it comes out:

    uv run nightshift run benchmark/blind/set2 --no-replay --no-record

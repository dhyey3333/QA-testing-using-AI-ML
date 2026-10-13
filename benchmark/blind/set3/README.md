# Blind set 3

Written on 2026-10-13, after the exploring fixes from four practice sites (D55), on sites Nightshift
had never run on: WebDriverUniversity, Playwright's TodoMVC demo, the BrowserStack demo shop,
Selenium's test form and TestSheepNZ's calculator (its numbered builds have planted bugs). Each
file's first line says its right verdict, checked by hand in a plain browser. Three need a
double-click, a drag and a hover, which Nightshift can't do yet: they measure that gap honestly.

Plus explorations, each finding checked by hand: advantageonlineshopping.com (with --sign-up),
bstackdemo.com and zero.webappsecurity.com.

Run once, report as it comes out:

    uv run nightshift run benchmark/blind/set3 --no-replay --no-record

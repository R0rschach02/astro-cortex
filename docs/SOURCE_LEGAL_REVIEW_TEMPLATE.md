# SOURCE_LEGAL_REVIEW — Template per Data Source

Fill in one copy of this block per source and link it from
docs/SOURCE_LEGAL_REVIEW.md. Only once a status of "released" or
"clarified_after_inquiry" is DOCUMENTED may an ingest module be
activated (enabled=1 in anomaly_sources).

---

## Source: <NAME>

- **Status:** unclarified | clarified_after_inquiry | released
- **Checked on:** <YYYY-MM-DD>
- **Check method:** <e.g. "direct raw-HTML retrieval via curl",
  "Wayback Machine full-text snapshot of <date>",
  "email reply received <date>">
- **URLs checked:** <list every page visited, including 404s>
- **Verbatim quote (word-for-word, with URL):**
  > "<quote from the primary source — never translate the quote itself>"
- **Full-text searched for:** <e.g. "scrap", "reuse", "API", "licence">
  → <hit counts>
- **Conclusion:** <one sentence, no interpretation beyond the quote>
- **Next step:** <who asks whom for what>

Rules (see LESSONS.md case 11):
- Quote licenses only from the primary source; secondary claims (third-
  party sites, spec revisions) are NOT evidence.
- Without an explicit release the status is "unclarified" — never
  "probably fine".
- Document robots.txt, but never treat it as a license.

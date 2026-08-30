# SOURCE_LEGAL_REVIEW.md — UAP Sources, Primary-Source Legal Assessment

Checked on: 2026-08-30. Method: direct retrieval of the pages themselves
(curl, raw HTML) or Wayback Machine full text; all quotes are verbatim
from the retrieved documents — nothing adopted from secondary sources or
spec revisions. Per source, the search order was: reuse / license /
open-data pages first, legal notices and disclaimers second.

Rating scale: only "released" (explicit license), "clarified after
inquiry", or "unclarified". Nothing is ever rated "probably fine".

---

## 1) GEIPAN (CNES) — cnes-geipan.fr — UNCLARIFIED

Places checked:
- https://www.cnes-geipan.fr/fr (home page): the only legal link in the
  footer is `/fr/mentions_legales`. No open-data, license or reuse link
  is present anywhere on the page.
- https://www.cnes-geipan.fr/fr/mentions_legales — verbatim quote:

  > "Toutes les données, et plus généralement le contenu du site Internet
  > sont la propriété du CNES, ou d'un tiers et pour lesquels il a obtenu
  > le droit d'en disposer. Ils sont rendus accessibles tels quel, tels
  > que disponibles, et sans garantie d'aucune sorte"

  (Ownership reservation plus liability disclaimer; NO reuse permission,
  no license notice. Convenience translation: "All data and, more
  generally, the content of the website are the property of CNES, or of
  a third party for which it has obtained disposal rights. They are made
  available as is, as available, and without warranty of any kind.")
- https://www.cnes-geipan.fr/fr/web/deir (case database): only a ~4 kB
  SPA shell; zero hits for "licen", "réutilis", "csv", "export",
  "télécharg" in the served HTML.
- Official French open-data catalogue data.gouv.fr (API v2, per-dataset
  license field): search "geipan" returns 8 datasets, ALL from other
  organizations (GEOPAL, DDT Var, OpenHealth, ...), no GEIPAN/CNES
  dataset. The organization "Centre National d'Etudes Spatiales" exists
  there (ID 563a2cdf88ee385a94531575) but has 0 datasets.
- https://www.cnes-geipan.fr/sitemap.xml → serves an HTML fallback; no
  sitemap navigation was evaluable.

Conclusion: the "Licence Ouverte / Etalab" release asserted in a spec
revision is NOT substantiable from primary sources — neither on the site
itself nor in the official open-data catalogue. (Third-party projects
such as carteovni.fr ASSERTING that license are secondary sources and do
not count as evidence.)
Status: UNCLARIFIED. Before any retrieval: contact CNES/GEIPAN directly
(contact form / postmaster) and obtain written usage permission.

## 2) Enigma Labs — enigmalabs.io — UNCLARIFIED

Places checked:
- https://enigmalabs.io/terms (live retrieval: HTTP 403; full text via
  Wayback Machine snapshot of 2026-06-12:
  http://web.archive.org/web/20260612081939/https://enigmalabs.io/terms ,
  "Last updated: July 12, 2023"). Verbatim quotes:

  > "The Service and its original content (excluding Content provided by
  > You or other users), features and functionality are and will remain
  > the exclusive property of the Company and its licensors."

  > "By posting Content to the Service, You grant Us the royalty-free,
  > worldwide, perpetual right and license to use, modify, publicly
  > perform, publicly display, reproduce, create derivative works of and
  > distribute such Content [...] You agree that this license includes
  > the right for Us to make Your Content available to other users of
  > the Service, who may also use Your Content subject to these Terms."

- Full-text search across the ToS: "scrap" = 0 hits, "reuse" = 0 hits,
  no API clause (the only "API"-adjacent hit is the definition of
  "Application"). No rate-limit, no bulk-data, no attribution rules.

Conclusion: no explicit permission for programmatic access or third-
party reuse; the "exclusive property" clause plus complete silence on
automation provides no usage basis.
Status: UNCLARIFIED. Usable only after an official API/access grant.

## 3) ufo-hunters.com — UNCLARIFIED

Places checked:
- https://www.ufo-hunters.com/terms → HTTP 404; /terms-of-use → 404;
  /disclaimer → 404; /privacy-policy → 404; /about → 404.
  There is NO reachable terms-of-use page.
- https://www.ufo-hunters.com/sightings/about — verbatim quote of the
  only legal notice (disclaimer overlay):

  > "The information on this site that is not produced by
  > ufo-hunters.com is covered under the Copyright Disclaimer Under
  > Section 107 of the Copyright Act 1976"

  Footer: "UFO HUNTERS (c) 2026 UFO HUNTERS — HIGH ALTITUDE
  INTELLIGENCE DATASET". Full-text search on that page: "terms" = 0,
  "licen" = 0, "permission" = 0 hits.
- robots.txt: disallows only /sessions/*, /users/*, /password_resets/*,
  error pages and /articles/myspace — the sighting pages are NOT
  disallowed (robots.txt is additional information, never a license).

Conclusion: no license text, no terms, no documented bulk/API access;
the fair-use notice covers THIRD-PARTY content only and grants nothing
to third parties. Operators are named on the about page (inquiry
possible). Status: UNCLARIFIED.

---

## Overall status

| Source       | Status     | Next step                                  |
|--------------|------------|--------------------------------------------|
| GEIPAN       | unclarified| Request written permission from CNES/GEIPAN|
| Enigma Labs  | unclarified| Ask for official API access                |
| ufo-hunters  | unclarified| Contact the operators                      |
| NUFORC       | not checked (CTO inquiry pending) | wait       |

NO source module, NO scraper for any of these sources until the
respective status is "released" / "clarified after inquiry" and that
fact is documented here.

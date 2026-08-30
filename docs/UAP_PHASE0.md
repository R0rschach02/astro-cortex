# UAP Feature: Phase 0 — Source Clarification (status 2026-09)

Precondition: NO source code for UAP data sources exists (inventory
2026-09: zero hits for nuforc/uap/ufo across the repo including its full
history). This document is the decision basis before any source module
is written.

## NUFORC — NOT in the code path
Status: deliberately not implemented. Optional if the pending CTO
inquiry is answered positively (formal usage permission / API access).
No crawling of the website until then.

## Enigma Labs (enigmalabs.io)
ToS read (last updated July 12, 2023):
- Contains NO clause about API access, scraping or rate limits; equally
  no grant for third-party reuse.
- "The Service and its original content [...] are and will remain the
  exclusive property of the Company and its licensors." — dismissive
  towards unclarified automated access.
- Positive: user content remains the property of the users; their own
  blog ("data transparency") announces that sightings data will be kept
  free and public and that historical data will not be monetized.
Assessment: GREY ZONE — not usable without a clarified access path.
Recommendation: request official API access; until then no retrieval,
no scraping.

## GEIPAN (CNES, France)
- Published case data (anonymized testimonies, classification A/B/C/D):
  a spec revision claimed "Licence Ouverte / Etalab" with free reuse.
  The primary-source check on 2026-08-30 (see
  docs/SOURCE_LEGAL_REVIEW.md) found NO reuse/license page on
  cnes-geipan.fr and NO GEIPAN dataset in the official catalogue
  data.gouv.fr — the asserted license is not substantiable from primary
  sources (only a secondary claim by third-party sites).
- No formal public API; access would be via website search/downloads;
  third-party projects (CarteOvni and others) take exactly that path.
Assessment: UNCLARIFIED — no retrieval before written permission from
CNES/GEIPAN.

## ufo-hunters.com
- No findable terms of use (no terms page indexed; /terms, /disclaimer,
  /privacy-policy all return 404); the disclaimer relies on fair use
  (17 U.S.C. 107) for third-party content, the privacy policy declines
  responsibility for republishing — that is an indication of tolerance,
  NOT a license.
- No API, no documented bulk access.
Assessment: NOT USABLE without consultation — automated retrieval is
withheld until the operators (a contact email exists) consent.

## Consequence for Phase 1
The only immediately buildable source would be GEIPAN — and only after
documented permission. Enigma Labs after access clarification, NUFORC
after the CTO reply, ufo-hunters.com after operator consent. Until
then, the deterministic engine work (schema, satellite eras, meteor
showers, radiosonde schedule, signatures, classifier rules, skyfield
source — see app/anomaly/) proceeds independently of any UAP source.

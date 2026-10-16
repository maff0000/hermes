# Golden samples for the XAU_USD publication

**PENDING HELM PROTOCOL.** No sample has been captured. This directory is a placeholder so the evidence layout is fixed before the production phase.

Rules for what will be placed here after the protocol has run (PID section S5 and deliverable 4):

- one sanitised sample per key family and timeframe observed, named `<family>__<timeframe>.json`;
- sanitisation by a documented transformation (server names, addresses, connection strings, container identifiers, account identifiers and secrets removed or replaced with `<REDACTED>`; public market values kept; a bounded window only);
- `MANIFEST.json` lists the SHA-256 of every file, the capture time (UTC) and the sanitisation record; the raw captures stay private and are referenced by SHA-256 only;
- samples are never edited by hand; a sample that cannot be sanitised without losing its evidential value stays private and is referenced by hash.

Nothing synthetic is placed here. Synthetic specimens used for the HELIOS mapping probe are in `../../synthetic_probe_result.json` (labelled SYNTHETIC) and are not golden samples.

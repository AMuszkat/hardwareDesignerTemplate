# Review requests — {{TITLE}} ({{ID}})

You own this file; the concept session reads it, has its reviewers check a
frozen copy of your board, and publishes the verdict and the findings in
`handoff/15_reviews.md`. One request per `##` heading. It is the record
whether the relay is on or off (`../RELAY_PROTOCOL.md`, "Reviews").

**When:** after every schematic change (a set of sheets, a redraw, the fixes
for a review) and every PCB change (a placement group, a routing group, the
fixes for a review), once its own gates pass (the `schematic`, `placement` or
`routing` skill) — and a last `pcb: fab release` before the fab package.
Then ring concept with the request's line (relay on) or tell the user (relay
off), and build nothing on the change until the verdict.

**Format** — keep it exactly, the concept session scans for it:

    ## RV<n> — schematic | pcb: <what changed, one line>
    **Status:** requested | CHANGES (rev R) | PASS (rev R)
    **Scope:** the sheets / the group / the parts the change touched; "all" for everything.
    **Gates:** each gate you ran and its last line (check, parity, parity --handoff, ERC; place.py drc, board_setup check, route gates).
    **Waivers:** every waiver this change added, with its reason — or "none".
    **Replies:** after the verdict, one line per finding: `RV<n>.<k> fixed` | `RV<n>.<k> disputed — <reason>`.

- Numbers never repeat. The fixes for RV<n> are a new request whose Scope
  says "fixes for RV<n>" (and anything else that changed).
- **PASS:** set `PASS (rev R)` and delete everything under the heading but
  the status line; go on. `should` findings stay open in `15_reviews.md`
  until a later review sees them fixed.
- **CHANGES:** set `CHANGES (rev R)`, fix every `must` finding (or dispute
  it under **Replies**, with the reason and its evidence), then request
  again. A finding disputed and upheld twice goes to the user.
- A review the user or concept starts without a request is numbered `M<n>`
  in `15_reviews.md`; reply to it under a `## M<n> — replies` heading here.

---

<!-- First request goes here: ## RV1 — ... -->

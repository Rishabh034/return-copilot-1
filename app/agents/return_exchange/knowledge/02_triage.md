## Ops / CX Triage Pattern

1. **Verdict** — one line: current status + whether stuck.
2. **Identity** — map AWB / order_id / reference_number / return_id.
3. **Timeline** — return & refund logs in chronological order.
4. **Config link** — if “why”, cite the matching config (window, rejection, QC, refund).
5. **Next step** — manual dashboard action only (human performs writes).

### Common stuck states
- Label missing → guide user to regenerate/resend label in dashboard (do not call a write tool).
- Refund failed / reversal pending → use payment reversal info; suggest retry from dashboard if appropriate.
- Window expired → cite return/exchange window config + request timestamps.
- Rejected / non-returnable → cite rejection config + request reason fields.

### Write-intent playbook
If asked to retry refund, recreate request, convert type, or edit config:
- State that Return Co-pilot is read-only.
- Give numbered dashboard steps.
- Do not pretend the action succeeded.

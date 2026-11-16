# Email breach monitoring (0.1)

This opt-in feature checks selected saved email identities with Have I Been Pwned
(HIBP). It is an application integration, not a broker or a plugin subprocess.
It never creates opt-out requests or claims to delete leaked records.

## Set up

After installing and rebuilding the API and web services, open **Find your
listings → Email breach monitoring** for a person. An administrator with
`settings.system` can save the HIBP subscription API key there. The key is
write-only through the API and encrypted with the existing settings key.

Select that person's saved email identities, confirm that you are authorized,
and save the choices. Use **Check now** for the first check. The existing
APScheduler service checks hourly for profiles whose complete snapshot is
more than seven days old. Scheduling works independently of the opt-out sending
switch, but the application scheduler itself must be running.

Changing monitoring choices clears the old snapshot and cancels a running scan.
**Stop monitoring and clear findings** revokes consent and clears findings.
A late worker cannot restore a revoked snapshot. A failed process leaves a lease
that expires after one hour. Manual scans have a 15-minute cooldown; provider
errors have a longer retry delay, with HIBP's Retry-After respected.

## Data and access

Only explicitly selected saved email identities are sent to HIBP. Login and
system mailbox addresses are not implicitly included. This discloses those email
addresses to HIBP over HTTPS, directly from the API process. It does **not** use
the browser automation proxy. No names, phone numbers, passwords or addresses
are sent. The HIBP key is sent only in its API header; redirects are refused.

Monitoring choices, checked email snapshots and breach findings use the
application's encrypted field storage. Enable and maintain the application's
normal encryption keys. No provider exceptions, request URLs or key values
are logged. Existing per-person view/edit authorization applies. System settings
permission is required to save or remove the API key.

Findings are included in the person's privacy export and removed by hard
person deletion. Artifact retention applies; minors use the shorter configured
COPPA auxiliary-data retention period. Retention invalidates a deleted snapshot
so it cannot misleadingly become a clean result. Continued opt-in monitoring
can rediscover a previously purged breach on the next check.

If an email identity is edited or removed, its old results disappear from the
panel and the snapshot becomes due for refresh. New identities are never
silently added to monitoring.

## Limits

- This is known email breach monitoring, not a dark-web crawler or phone lookup.
- Public API results exclude sensitive/retired breaches. This integration also
  excludes unverified breaches. No matches is not proof that an address is safe.
- Findings appear in the person's panel; outbound email/push alerts are not
  implemented in this version.
- A paid HIBP email-search API subscription is needed for real email lookups.
  HIBP's free notification signup is linked in the panel instead.
- Rate limiting assumes the standard single API process, with seven seconds
  between requests. Multiple API processes or other users of the same key need
  a shared limiter. This version still handles provider 429 responses safely.
- A provider failure retains the last complete snapshot and displays failure;
  it never overwrites previous findings with a partial or false clean result.
- Results are not opt-out targets. A breach record is not evidence a broker
  currently lists the person, and cannot be removed through broker automation.

## Validation

`python3 -m unittest backend.tests.test_breach_monitor -v` from a checkout.
Inside the API container: `python -m app.tests.test_breach_monitor -v` with `/`
as working directory. Tests use made-up addresses and mocked provider responses.
The initial build also passed a live HIBP lookup using only HIBP's documented
synthetic test account and test key; no family's information was queried.

Official provider documentation: https://haveibeenpwned.com/API/v3
Free notifications: https://haveibeenpwned.com/NotifyMe
Pricing and subscription: https://haveibeenpwned.com/Subscription

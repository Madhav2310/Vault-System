# Post-Mortem: 9 September Login Outage

Written by the Platform Engineering team, 12 September 2025.

Customers could not sign in for 47 minutes, from 09:12 to 09:59 UTC.

Only the EU-West region was affected. Other regions kept working.

The root cause was an expired TLS certificate on the identity gateway. The renewal job had been disabled during an earlier migration and was never re-enabled.

We have re-enabled automatic renewal and added an alert that fires thirty days before any certificate expires.

Follow-up actions are tracked in the reliability backlog and reviewed weekly.

# Plover Payments: Primary Database Failover Runbook

Use this runbook when the primary PostgreSQL instance is unavailable for more than two minutes.

## Detection

The on-call engineer is paged by the health monitor after three failed checks, thirty seconds apart. Confirm the outage in the status dashboard before acting.

## Decision

Failover may be started by the on-call engineer without further approval if the replica lag is under five seconds. If the lag is higher, the incident commander must approve.

## Procedure

Promote the replica in the secondary zone using the `promote-replica` job. Update the connection alias, then restart the payment API workers one zone at a time. Expect a recovery time of about eight minutes.

## Verification

Check that the write test passes, that the payment success rate returns above 99%, and that replication to the new standby has started.

## Communication

Post an update in the incident channel every fifteen minutes. If customer payments are affected for more than thirty minutes, the communications lead publishes a status page notice.

## After the Incident

A written review is due within five working days.

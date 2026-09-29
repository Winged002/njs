# Upgrade to v3.3.0

v3.3.0 is an additive upgrade over v3.2.0. No destructive MongoDB migration is required.

## New Worker fields

Existing Workers continue to work. Missing learning fields default at runtime to:

- `learning_mode = observe`
- `learning_objective = conversion_rate`
- `learning_min_views = 80`
- `learning_lookback_days = 90`

A `learning_state` document is created when a learning report is first opened or when an enabled Worker refreshes its learning state during a scheduled/manual run.

## Deployment

Preserve `.env`, Mongo/Redis volumes, uploads, generated media, domain state, and SSO configuration. Rebuild web, worker, and beat so every process runs the same v3.3.0 code.

No historical analytics data is rewritten. v3.3 can immediately learn from existing v3.1+ attribution events that already contain a `newsjacking_worker_id`.

## Recommended rollout

1. Leave existing Workers in Observe mode initially.
2. Open each Worker’s Learning report after enough attributed traffic exists.
3. Use Recommend mode where a human should approve changes.
4. Enable Adaptive mode only for Workers with a meaningful sample and stable Product/Campaign configuration.

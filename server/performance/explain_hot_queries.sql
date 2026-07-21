\set ON_ERROR_STOP on

\if :{?page_size}
\else
\set page_size 50
\endif
\if :{?lease_batch_size}
\else
\set lease_batch_size 100
\endif
\if :{?origin_code}
\else
\set origin_code SHA
\endif
\if :{?destination_code}
\else
\set destination_code TYO
\endif

SELECT
    :page_size::integer BETWEEN 1 AND 100
    AND :lease_batch_size::integer BETWEEN 1 AND 100 AS inputs_valid
\gset
\if :inputs_valid
\else
\echo 'page_size and lease_batch_size must be between 1 and 100'
\quit
\endif

SELECT id AS target_user_id
FROM users
WHERE email LIKE 'perf+%@example.invalid'
ORDER BY email
LIMIT 1
\gset

\if :{?target_user_id}
\else
\echo 'No performance fixture found; run performance/generate_load.sql first'
\quit
\endif

SELECT search_query_id AS target_query_id
FROM subscriptions
WHERE user_id = :'target_user_id'::uuid
ORDER BY created_at DESC, id DESC
LIMIT 1
\gset

SELECT subscription.search_query_id AS target_filtered_query_id
FROM subscriptions AS subscription
JOIN subscription_filters AS filter_row
  ON filter_row.subscription_id = subscription.id
WHERE subscription.user_id = :'target_user_id'::uuid
  AND filter_row.airline_codes ? 'MU'
ORDER BY subscription.created_at DESC, subscription.id DESC
LIMIT 1
\gset

SELECT id AS target_filtered_run_id
FROM collection_runs
WHERE search_query_id = :'target_filtered_query_id'::uuid
  AND status = 'succeeded'
  AND finished_at IS NOT NULL
ORDER BY finished_at DESC, id DESC
LIMIT 1
\gset

SELECT subscription.search_query_id AS target_roundtrip_query_id
FROM subscriptions AS subscription
JOIN search_queries AS search_query
  ON search_query.id = subscription.search_query_id
WHERE subscription.user_id = :'target_user_id'::uuid
  AND search_query.trip_type = 'round_trip'
ORDER BY subscription.created_at DESC, subscription.id DESC
LIMIT 1
\gset

SELECT subscription.search_query_id AS target_oneway_query_id
FROM subscriptions AS subscription
JOIN search_queries AS search_query
  ON search_query.id = subscription.search_query_id
WHERE subscription.user_id = :'target_user_id'::uuid
  AND search_query.trip_type = 'one_way'
ORDER BY subscription.created_at DESC, subscription.id DESC
LIMIT 1
\gset

SELECT version();
SELECT name, setting, unit
FROM pg_settings
WHERE name IN (
    'effective_cache_size',
    'jit',
    'max_connections',
    'random_page_cost',
    'shared_buffers',
    'work_mem'
)
ORDER BY name;

-- hot-query: user-subscriptions
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT
    id,
    search_query_id,
    name,
    enabled,
    next_due_at,
    created_at
FROM subscriptions
WHERE user_id = :'target_user_id'::uuid
  AND (created_at, id) < ('infinity'::timestamptz, 'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid)
ORDER BY created_at DESC, id DESC
LIMIT :page_size;
-- end-hot-query

BEGIN;
-- hot-query: due-subscriptions
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT id, search_query_id, next_due_at
FROM subscriptions
WHERE enabled IS TRUE
  AND next_due_at IS NOT NULL
  AND next_due_at <= current_timestamp
  AND (next_due_at, id) > ('-infinity'::timestamptz, '00000000-0000-0000-0000-000000000000'::uuid)
ORDER BY next_due_at, id
FOR UPDATE SKIP LOCKED
LIMIT :lease_batch_size;
-- end-hot-query
ROLLBACK;

-- aggregate-query: route-market-snapshot-reference
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT
    search_query.id AS search_query_id,
    search_leg.departure_date AS service_date,
    latest_price.observed_at,
    latest_price.total_price_minor,
    latest_price.currency,
    latest_price.is_direct
FROM search_legs AS search_leg
JOIN search_queries AS search_query
  ON search_query.id = search_leg.search_query_id
JOIN latest_price_snapshots AS latest_price
  ON latest_price.search_query_id = search_query.id
WHERE search_leg.position = 0
  AND search_leg.origin_code = :'origin_code'
  AND search_leg.destination_code = :'destination_code'
  AND search_leg.departure_date >= current_date
  AND search_leg.departure_date < current_date + 180
  AND (
      latest_price.total_price_minor,
      search_leg.departure_date,
      search_query.id
  ) > (
      0,
      '-infinity'::date,
      '00000000-0000-0000-0000-000000000000'::uuid
  )
ORDER BY
    latest_price.total_price_minor,
    search_leg.departure_date,
    search_query.id
LIMIT :page_size;
-- end-aggregate-query

-- hot-query: calendar-date-matrix
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT
    departure_date,
    return_date,
    observed_at,
    lowest_price_minor,
    total_price_minor,
    currency
FROM latest_calendar_price_snapshots
WHERE search_query_id = :'target_oneway_query_id'::uuid
  AND currency = 'CNY'
  AND return_date IS NULL
  AND departure_date >= current_date
  AND departure_date < current_date + 180
  AND (
      departure_date,
      COALESCE(return_date, '-infinity'::date)
  ) > (
      '-infinity'::date,
      '-infinity'::date
  )
ORDER BY departure_date, COALESCE(return_date, '-infinity'::date)
LIMIT :page_size;
-- end-hot-query

-- aggregate-query: calendar-roundtrip-matrix
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT
    departure_date,
    return_date,
    observed_at,
    lowest_price_minor,
    total_price_minor,
    currency
FROM latest_calendar_price_snapshots
WHERE search_query_id = :'target_roundtrip_query_id'::uuid
  AND currency = 'CNY'
  AND departure_date >= current_date
  AND departure_date < current_date + 180
  AND return_date IS NOT NULL
  AND return_date >= current_date
  AND return_date < current_date + 187
  AND (departure_date, return_date) > (
      '-infinity'::date,
      '-infinity'::date
  )
ORDER BY departure_date, return_date
LIMIT :page_size;
-- end-aggregate-query

-- hot-query: price-history
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
WITH history_run_minima AS (
    SELECT
        collection_run_id,
        observed_at,
        min(total_price_minor) AS total_price_minor
    FROM price_observations
    WHERE search_query_id = :'target_query_id'::uuid
      AND observed_at >= current_timestamp - interval '21 days'
      AND observed_at < current_timestamp + interval '1 day'
      AND is_lowest IS TRUE
    GROUP BY collection_run_id, observed_at
)
SELECT
    collection_run_id,
    observed_at,
    total_price_minor
FROM history_run_minima
WHERE (observed_at, collection_run_id) > (
      '-infinity'::timestamptz,
      '00000000-0000-0000-0000-000000000000'::uuid
  )
ORDER BY observed_at, collection_run_id
LIMIT :page_size;
-- end-hot-query

-- aggregate-query: price-history-summary
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
WITH history_run_minima AS (
    SELECT
        collection_run_id,
        observed_at,
        min(total_price_minor) AS total_price_minor
    FROM price_observations
    WHERE search_query_id = :'target_query_id'::uuid
      AND currency = 'CNY'
      AND observed_at >= current_timestamp - interval '21 days'
      AND observed_at <= current_timestamp
      AND is_lowest IS TRUE
    GROUP BY collection_run_id, observed_at
)
SELECT
    min(total_price_minor),
    max(total_price_minor),
    avg(total_price_minor),
    count(*)
FROM history_run_minima;
-- end-aggregate-query

-- aggregate-query: price-history-day
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
WITH history_run_minima AS (
    SELECT
        collection_run_id,
        observed_at,
        min(total_price_minor) AS total_price_minor
    FROM price_observations
    WHERE search_query_id = :'target_query_id'::uuid
      AND currency = 'CNY'
      AND observed_at >= current_timestamp - interval '21 days'
      AND observed_at <= current_timestamp
      AND is_lowest IS TRUE
    GROUP BY collection_run_id, observed_at
), history_buckets AS (
    SELECT
        date_trunc('day', timezone('UTC', observed_at)) AS bucket,
        min(total_price_minor) AS lowest_price_minor,
        max(total_price_minor) AS highest_price_minor,
        avg(total_price_minor) AS average_price_minor,
        count(*) AS sample_count
    FROM history_run_minima
    GROUP BY bucket
)
SELECT *
FROM history_buckets
WHERE bucket > '-infinity'::timestamp
ORDER BY bucket
LIMIT :page_size;
-- end-aggregate-query

-- aggregate-query: filtered-price-history
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
WITH history_run_minima AS (
    SELECT
        observation.collection_run_id,
        observation.observed_at,
        min(observation.total_price_minor) AS total_price_minor
    FROM price_observations AS observation
    JOIN itineraries AS itinerary
      ON itinerary.id = observation.itinerary_id
    WHERE observation.search_query_id = :'target_query_id'::uuid
      AND observation.observed_at >= current_timestamp - interval '21 days'
      AND observation.observed_at < current_timestamp + interval '1 day'
      AND itinerary.search_query_id = :'target_query_id'::uuid
      AND itinerary.stop_count <= 1
      AND EXISTS (
          SELECT 1
          FROM segments AS segment
          WHERE segment.itinerary_id = itinerary.id
            AND segment.marketing_airline_code IN ('MU', 'CA')
      )
    GROUP BY observation.collection_run_id, observation.observed_at
)
SELECT collection_run_id, observed_at, total_price_minor
FROM history_run_minima
ORDER BY observed_at, collection_run_id
LIMIT :page_size;
-- end-aggregate-query

-- aggregate-query: fare-search-exact-total
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT count(*)
FROM fare_offers AS offer
JOIN itineraries AS itinerary
  ON itinerary.id = offer.itinerary_id
WHERE offer.collection_run_id = :'target_filtered_run_id'::uuid
  AND offer.currency = 'CNY'
  AND itinerary.stop_count <= 1
  AND EXISTS (
      SELECT 1
      FROM segments AS segment
      WHERE segment.itinerary_id = itinerary.id
        AND segment.marketing_airline_code = 'MU'
  );
-- end-aggregate-query

-- hot-query: fare-search-offers
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT offer.id, offer.total_price_minor, offer.currency, itinerary.id AS itinerary_id
FROM fare_offers AS offer
JOIN itineraries AS itinerary
  ON itinerary.id = offer.itinerary_id
WHERE offer.collection_run_id = :'target_filtered_run_id'::uuid
  AND offer.currency = 'CNY'
  AND itinerary.stop_count <= 1
  AND EXISTS (
      SELECT 1
      FROM segments AS segment
      WHERE segment.itinerary_id = itinerary.id
        AND segment.marketing_airline_code = 'MU'
  )
  AND (offer.total_price_minor, offer.id) > (
      0,
      '00000000-0000-0000-0000-000000000000'::uuid
  )
ORDER BY offer.total_price_minor, offer.id
LIMIT :page_size;
-- end-hot-query

-- aggregate-query: collection-health-last-success
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT finished_at
FROM collection_runs
WHERE search_query_id IN (
    SELECT search_query_id
    FROM subscriptions
    WHERE user_id = :'target_user_id'::uuid
      AND enabled IS TRUE
)
  AND status = 'succeeded'
  AND finished_at IS NOT NULL
  AND finished_at <= current_timestamp
ORDER BY finished_at DESC, id DESC
LIMIT 1;
-- end-aggregate-query

-- aggregate-query: collection-health-rate
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT
    count(*) FILTER (
        WHERE status = 'succeeded'
    ) AS successful_24h,
    count(*) AS terminal_24h
FROM collection_runs
WHERE search_query_id IN (
    SELECT search_query_id
    FROM subscriptions
    WHERE user_id = :'target_user_id'::uuid
      AND enabled IS TRUE
)
  AND status IN ('succeeded', 'failed', 'canceled')
  AND finished_at >= current_timestamp - interval '24 hours'
  AND finished_at <= current_timestamp;
-- end-aggregate-query

-- aggregate-query: collection-health-next-due
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT min(next_due_at)
FROM subscriptions
WHERE user_id = :'target_user_id'::uuid
  AND enabled IS TRUE
  AND next_due_at IS NOT NULL;
-- end-aggregate-query

-- aggregate-query: dashboard-subscription-counts
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT
    count(*) FILTER (WHERE enabled IS TRUE) AS active_subscriptions,
    count(DISTINCT search_query_id) AS routes_tracked
FROM subscriptions
WHERE user_id = :'target_user_id'::uuid;
-- end-aggregate-query

-- hot-query: collection-run-list
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT id, search_query_id, status, scheduled_at, finished_at, offer_count
FROM collection_runs
WHERE search_query_id IN (
    SELECT search_query_id
    FROM subscriptions
    WHERE user_id = :'target_user_id'::uuid
)
  AND (scheduled_at, id) < (
      'infinity'::timestamptz,
      'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid
  )
ORDER BY scheduled_at DESC, id DESC
LIMIT :page_size;
-- end-hot-query

BEGIN;
-- hot-query: collection-lease-pending
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT id, search_query_id, scheduled_at
FROM collection_runs
WHERE status = 'pending'
  AND scheduled_at <= current_timestamp
  AND (scheduled_at, id) > (
      '-infinity'::timestamptz,
      '00000000-0000-0000-0000-000000000000'::uuid
  )
ORDER BY scheduled_at, id
FOR UPDATE SKIP LOCKED
LIMIT :lease_batch_size;
-- end-hot-query
ROLLBACK;

BEGIN;
-- hot-query: collection-lease-recovery
EXPLAIN (ANALYZE, BUFFERS, SETTINGS, WAL)
SELECT id, search_query_id, lease_expires_at
FROM collection_runs
WHERE status IN ('leased', 'running')
  AND lease_expires_at IS NOT NULL
  AND lease_expires_at <= current_timestamp
  AND (lease_expires_at, id) > (
      '-infinity'::timestamptz,
      '00000000-0000-0000-0000-000000000000'::uuid
  )
ORDER BY lease_expires_at, id
FOR UPDATE SKIP LOCKED
LIMIT :lease_batch_size;
-- end-hot-query
ROLLBACK;

WITH RECURSIVE relation_tree AS (
    SELECT
        relation.oid AS root_oid,
        relation.oid AS relation_oid,
        relation.relname AS root_name
    FROM pg_class AS relation
    WHERE relation.relname IN (
        'collection_runs',
        'fare_offers',
        'itineraries',
        'latest_calendar_price_snapshots',
        'price_observations',
        'search_legs',
        'segments',
        'subscription_filters',
        'subscriptions'
    )

    UNION ALL

    SELECT
        relation_tree.root_oid,
        inheritance.inhrelid,
        relation_tree.root_name
    FROM relation_tree
    JOIN pg_inherits AS inheritance
      ON inheritance.inhparent = relation_tree.relation_oid
)
SELECT
    relation_tree.root_name AS relname,
    pg_size_pretty(sum(pg_relation_size(relation_tree.relation_oid))) AS table_size,
    pg_size_pretty(sum(pg_indexes_size(relation_tree.relation_oid))) AS index_size,
    sum(
        CASE WHEN relation.relkind = 'p' THEN 0 ELSE relation.reltuples END
    )::bigint AS estimated_rows
FROM relation_tree
JOIN pg_class AS relation
  ON relation.oid = relation_tree.relation_oid
GROUP BY relation_tree.root_oid, relation_tree.root_name
ORDER BY relation_tree.root_name;

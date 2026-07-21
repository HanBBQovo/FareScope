\set ON_ERROR_STOP on

\if :{?perf_confirm}
\else
\echo 'Refusing to seed: pass -v perf_confirm=I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE'
\quit
\endif

SELECT :'perf_confirm' = 'I_UNDERSTAND_THIS_IS_A_DISPOSABLE_DATABASE' AS perf_confirmed
\gset
\if :perf_confirmed
\else
\echo 'Refusing to seed: confirmation string does not match'
\quit
\endif

SELECT left(current_database(), 15) = 'farescope_perf_' AS perf_database_name_valid
\gset
\if :perf_database_name_valid
\else
\echo 'Refusing to seed: database name must start with farescope_perf_'
\quit
\endif

\if :{?users}
\else
\set users 500
\endif
\if :{?subscriptions_per_user}
\else
\set subscriptions_per_user 12
\endif
\if :{?query_count}
\else
\set query_count 2000
\endif
\if :{?observations_per_query}
\else
\set observations_per_query 240
\endif
\if :{?offers_per_query}
\else
\set offers_per_query 20
\endif
\if :{?queue_count}
\else
\set queue_count 5000
\endif
\if :{?history_days}
\else
\set history_days 21
\endif

SELECT
    :users::integer > 0
    AND :subscriptions_per_user::integer > 0
    AND :query_count::integer > 0
    AND :observations_per_query::integer > 0
    AND :offers_per_query::integer BETWEEN 1 AND 100
    AND :queue_count::integer > 0
    AND :history_days::integer BETWEEN 1 AND 27 AS inputs_valid
\gset
\if :inputs_valid
\else
\echo 'Invalid scale: counts must be positive, offers_per_query must be 1-100, and history_days must be 1-27'
\quit
\endif

BEGIN;
SET LOCAL synchronous_commit = off;
SET LOCAL statement_timeout = 0;

CREATE TEMP TABLE perf_seed_config ON COMMIT DROP AS
SELECT date_trunc('day', current_timestamp) AS seed_anchor;

INSERT INTO providers (id, code, display_name, enabled, adapter_version)
VALUES (
    '00000000-0000-0000-0000-000000000001'::uuid,
    'ctrip',
    'Ctrip',
    true,
    'performance-fixture'
)
ON CONFLICT DO NOTHING;

INSERT INTO users (
    id,
    username,
    normalized_username,
    email,
    password_hash,
    display_name,
    role,
    status,
    last_login_at,
    created_at,
    updated_at
)
SELECT
    md5('farescope-perf-user-' || user_number)::uuid,
    format('perf-user-%s', user_number),
    format('perf-user-%s', user_number),
    format('perf+%s@example.invalid', user_number),
    NULL,
    format('Performance User %s', user_number),
    CASE WHEN user_number = 1 THEN 'admin' ELSE 'member' END,
    'active',
    NULL,
    seed_anchor - (user_number % 365) * interval '1 day',
    seed_anchor - (user_number % 365) * interval '1 day'
FROM generate_series(1, :users) AS series(user_number)
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

CREATE TEMP TABLE perf_search_seed ON COMMIT DROP AS
SELECT
    query_number,
    md5('farescope-perf-search-' || query_number)::uuid AS search_query_id,
    CASE WHEN query_number % 3 = 0 THEN 'round_trip' ELSE 'one_way' END AS trip_type,
    CASE query_number % 4
        WHEN 0 THEN 'SHA'
        WHEN 1 THEN 'PVG'
        WHEN 2 THEN 'SHA'
        ELSE 'BJS'
    END AS origin_code,
    CASE query_number % 4
        WHEN 0 THEN 'TYO'
        WHEN 1 THEN 'NRT'
        WHEN 2 THEN 'KIX'
        ELSE 'TYO'
    END AS destination_code,
    current_date + 30 + (query_number % 90) AS departure_date,
    query_number % 2 = 0 AS direct_only
FROM generate_series(1, :query_count) AS series(query_number);

INSERT INTO search_queries (
    id,
    provider,
    query_hash,
    trip_type,
    adults,
    children,
    infants,
    cabin,
    currency,
    direct_only,
    normalized_query,
    created_at,
    updated_at
)
SELECT
    search_query_id,
    'ctrip',
    md5('farescope-perf-query-a-' || query_number)
        || md5('farescope-perf-query-b-' || query_number),
    trip_type,
    1,
    0,
    0,
    'economy',
    'CNY',
    direct_only,
    jsonb_build_object(
        'perf_fixture', true,
        'origin', origin_code,
        'destination', destination_code,
        'departure_date', departure_date,
        'direct_only', direct_only
    ),
    seed_anchor - (query_number % 720) * interval '1 minute',
    seed_anchor - (query_number % 720) * interval '1 minute'
FROM perf_search_seed
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

INSERT INTO search_legs (
    id,
    search_query_id,
    position,
    origin_code,
    destination_code,
    departure_date
)
SELECT
    md5('farescope-perf-leg-outbound-' || query_number)::uuid,
    search_query_id,
    0,
    origin_code,
    destination_code,
    departure_date
FROM perf_search_seed
ON CONFLICT DO NOTHING;

INSERT INTO search_legs (
    id,
    search_query_id,
    position,
    origin_code,
    destination_code,
    departure_date
)
SELECT
    md5('farescope-perf-leg-return-' || query_number)::uuid,
    search_query_id,
    1,
    destination_code,
    origin_code,
    departure_date + 7
FROM perf_search_seed
WHERE trip_type = 'round_trip'
ON CONFLICT DO NOTHING;

CREATE TEMP TABLE perf_subscription_seed ON COMMIT DROP AS
SELECT
    subscription_number,
    ((subscription_number - 1) / :subscriptions_per_user) + 1 AS user_number,
    ((subscription_number - 1) % :query_count) + 1 AS query_number
FROM generate_series(
    1,
    :users * :subscriptions_per_user
) AS series(subscription_number);

INSERT INTO subscriptions (
    id,
    user_id,
    search_query_id,
    name,
    enabled,
    poll_interval_seconds,
    next_due_at,
    last_collected_at,
    tags,
    created_at,
    updated_at
)
SELECT
    md5('farescope-perf-subscription-' || subscription_number)::uuid,
    md5('farescope-perf-user-' || user_number)::uuid,
    md5('farescope-perf-search-' || query_number)::uuid,
    format('Performance Subscription %s', subscription_number),
    subscription_number % 20 <> 0,
    21600,
    CASE
        WHEN subscription_number % 5 = 0
            THEN current_timestamp + (subscription_number % 600) * interval '1 second'
        ELSE current_timestamp - (subscription_number % 600) * interval '1 second'
    END,
    current_timestamp - interval '6 hours',
    jsonb_build_array('performance'),
    seed_anchor - subscription_number * interval '1 second',
    seed_anchor - subscription_number * interval '1 second'
FROM perf_subscription_seed
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

INSERT INTO subscription_filters (
    id,
    subscription_id,
    airline_codes,
    origin_airport_codes,
    destination_airport_codes,
    max_price_minor,
    currency,
    max_stops,
    max_duration_minutes,
    departure_time_start_minutes,
    departure_time_end_minutes,
    additional_filters,
    created_at,
    updated_at
)
SELECT
    md5('farescope-perf-subscription-filter-' || subscription_number)::uuid,
    md5('farescope-perf-subscription-' || subscription_number)::uuid,
    CASE
        WHEN subscription_number % 4 = 0 THEN jsonb_build_array('MU')
        WHEN subscription_number % 4 = 1 THEN jsonb_build_array('CA', 'NH')
        ELSE '[]'::jsonb
    END,
    '[]'::jsonb,
    '[]'::jsonb,
    CASE WHEN subscription_number % 7 = 0 THEN 180000 ELSE NULL END,
    CASE WHEN subscription_number % 7 = 0 THEN 'CNY' ELSE NULL END,
    CASE WHEN subscription_number % 5 = 0 THEN 1 ELSE NULL END,
    CASE WHEN subscription_number % 6 = 0 THEN 480 ELSE NULL END,
    CASE WHEN subscription_number % 8 = 0 THEN 360 ELSE NULL END,
    CASE WHEN subscription_number % 8 = 0 THEN 720 ELSE NULL END,
    jsonb_build_object('perf_fixture', true),
    seed_anchor - subscription_number * interval '1 second',
    seed_anchor - subscription_number * interval '1 second'
FROM perf_subscription_seed
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

CREATE TEMP TABLE perf_offer_seed ON COMMIT DROP AS
SELECT
    search_seed.*,
    offer_number,
    search_seed.direct_only OR offer_number % 3 = 0 AS is_direct_offer,
    CASE WHEN search_seed.trip_type = 'round_trip' THEN 2 ELSE 1 END AS leg_count,
    CASE
        WHEN offer_number = 1
            THEN md5('farescope-perf-itinerary-' || search_seed.query_number)::uuid
        ELSE md5(
            'farescope-perf-itinerary-'
            || search_seed.query_number
            || '-'
            || offer_number
        )::uuid
    END AS itinerary_id,
    CASE
        WHEN offer_number = 1
            THEN md5('farescope-perf-offer-' || search_seed.query_number)::uuid
        ELSE md5(
            'farescope-perf-offer-'
            || search_seed.query_number
            || '-'
            || offer_number
        )::uuid
    END AS offer_id
FROM perf_search_seed AS search_seed
CROSS JOIN generate_series(1, :offers_per_query) AS offers(offer_number);

INSERT INTO collection_runs (
    id,
    search_query_id,
    provider_id,
    idempotency_key,
    status,
    attempt,
    max_attempts,
    scheduled_at,
    lease_owner,
    lease_expires_at,
    started_at,
    finished_at,
    upstream_status,
    schema_fingerprint,
    itinerary_count,
    offer_count,
    error_code,
    error_message,
    run_metadata,
    created_at,
    updated_at
)
SELECT
    md5('farescope-perf-run-success-' || query_number)::uuid,
    search_query_id,
    '00000000-0000-0000-0000-000000000001'::uuid,
    format('perf:success:%s', query_number),
    'succeeded',
    1,
    3,
    seed_anchor - interval '5 minutes',
    NULL,
    NULL,
    seed_anchor - interval '5 minutes',
    seed_anchor - interval '4 minutes',
    'success',
    'perf-schema-v1',
    :offers_per_query,
    :offers_per_query,
    NULL,
    NULL,
    jsonb_build_object('perf_fixture', true),
    seed_anchor - interval '5 minutes',
    seed_anchor - interval '4 minutes'
FROM perf_search_seed
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

INSERT INTO collection_runs (
    id,
    search_query_id,
    provider_id,
    idempotency_key,
    status,
    attempt,
    max_attempts,
    scheduled_at,
    lease_owner,
    lease_expires_at,
    started_at,
    finished_at,
    upstream_status,
    schema_fingerprint,
    itinerary_count,
    offer_count,
    error_code,
    error_message,
    run_metadata,
    created_at,
    updated_at
)
SELECT
    md5('farescope-perf-run-queue-' || queue_number)::uuid,
    md5(
        'farescope-perf-search-'
        || (((queue_number - 1) % :query_count) + 1)
    )::uuid,
    '00000000-0000-0000-0000-000000000001'::uuid,
    format('perf:queue:%s', queue_number),
    CASE
        WHEN queue_number % 10 = 0 THEN 'running'
        WHEN queue_number % 5 = 0 THEN 'leased'
        ELSE 'pending'
    END,
    CASE WHEN queue_number % 5 = 0 THEN 1 ELSE 0 END,
    3,
    current_timestamp - (queue_number % 3600) * interval '1 second',
    CASE WHEN queue_number % 5 = 0 THEN 'perf-worker' ELSE NULL END,
    CASE
        WHEN queue_number % 5 = 0
            THEN current_timestamp - (queue_number % 600) * interval '1 second'
        ELSE NULL
    END,
    CASE WHEN queue_number % 10 = 0 THEN current_timestamp - interval '10 minutes' ELSE NULL END,
    NULL,
    NULL,
    NULL,
    0,
    0,
    NULL,
    NULL,
    jsonb_build_object('perf_fixture', true),
    current_timestamp - (queue_number % 3600) * interval '1 second',
    current_timestamp - (queue_number % 3600) * interval '1 second'
FROM generate_series(1, :queue_count) AS series(queue_number)
ON CONFLICT DO NOTHING;

INSERT INTO schema_observations (
    id,
    provider_id,
    collection_run_id,
    endpoint,
    schema_fingerprint,
    field_summary,
    first_seen_at,
    last_seen_at,
    occurrence_count,
    created_at
)
SELECT
    md5('farescope-perf-schema-' || observation_number)::uuid,
    '00000000-0000-0000-0000-000000000001'::uuid,
    md5(
        'farescope-perf-run-success-'
        || (((observation_number - 1) % :query_count) + 1)
    )::uuid,
    CASE observation_number % 4
        WHEN 0 THEN '/itinerary/api/12808/lowestPrice'
        WHEN 1 THEN '/international/search/api/search/batchSearch'
        WHEN 2 THEN '/itinerary/api/12808/products'
        ELSE '/international/search/api/flightlist'
    END,
    md5('farescope-perf-schema-fingerprint-' || observation_number),
    jsonb_build_object(
        'shape',
        jsonb_build_object(
            'data', 'object',
            'status', 'string',
            format('fixture_field_%s', observation_number % 20), 'array'
        ),
        'perf_fixture',
        true
    ),
    current_timestamp - observation_number * interval '12 hours',
    current_timestamp - observation_number * interval '5 minutes',
    observation_number * 3,
    current_timestamp - observation_number * interval '12 hours'
FROM generate_series(1, 200) AS observations(observation_number)
ON CONFLICT DO NOTHING;

INSERT INTO itineraries (
    id,
    collection_run_id,
    search_query_id,
    provider_id,
    provider_itinerary_id,
    fingerprint,
    total_duration_minutes,
    stop_count,
    is_direct,
    leg_count,
    itinerary_metadata,
    created_at
)
SELECT
    itinerary_id,
    md5('farescope-perf-run-success-' || query_number)::uuid,
    search_query_id,
    '00000000-0000-0000-0000-000000000001'::uuid,
    format('perf:%s:%s', query_number, offer_number),
    format('perf-itinerary-%s-%s', query_number, offer_number),
    leg_count * (180 + offer_number % 8 * 15),
    CASE WHEN is_direct_offer THEN 0 ELSE leg_count END,
    is_direct_offer,
    leg_count,
    jsonb_build_object('perf_fixture', true, 'offer_number', offer_number),
    seed_anchor - interval '4 minutes'
FROM perf_offer_seed
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

INSERT INTO segments (
    id,
    itinerary_id,
    position,
    leg_position,
    marketing_airline_code,
    operating_airline_code,
    flight_number,
    origin_airport_code,
    destination_airport_code,
    departure_at_utc,
    arrival_at_utc,
    departure_local,
    arrival_local,
    departure_timezone,
    arrival_timezone,
    duration_minutes,
    aircraft_code,
    segment_metadata
)
SELECT
    md5(
        'farescope-perf-segment-'
        || query_number
        || '-'
        || offer_number
        || '-'
        || leg_position
        || '-'
        || segment_number
    )::uuid,
    itinerary_id,
    leg_position * 2 + segment_number,
    leg_position,
    CASE offer_number % 4
        WHEN 0 THEN 'MU'
        WHEN 1 THEN 'CA'
        WHEN 2 THEN 'JL'
        ELSE 'NH'
    END,
    NULL,
    format('PF%s%s%s', query_number, leg_position, segment_number),
    CASE
        WHEN segment_number = 0
            THEN CASE WHEN leg_position = 0 THEN origin_code ELSE destination_code END
        ELSE 'ICN'
    END,
    CASE
        WHEN segment_number = segments_per_leg - 1
            THEN CASE WHEN leg_position = 0 THEN destination_code ELSE origin_code END
        ELSE 'ICN'
    END,
    (
        departure_date
        + leg_position * 7
    )::timestamp
        + interval '8 hours'
        + offer_number * interval '3 minutes'
        + segment_number * interval '2 hours',
    (
        departure_date
        + leg_position * 7
    )::timestamp
        + interval '9 hours 30 minutes'
        + offer_number * interval '3 minutes'
        + segment_number * interval '2 hours',
    (
        departure_date
        + leg_position * 7
    )::timestamp
        + interval '8 hours'
        + offer_number * interval '3 minutes'
        + segment_number * interval '2 hours',
    (
        departure_date
        + leg_position * 7
    )::timestamp
        + interval '9 hours 30 minutes'
        + offer_number * interval '3 minutes'
        + segment_number * interval '2 hours',
    'Asia/Shanghai',
    'Asia/Tokyo',
    90,
    NULL,
    jsonb_build_object('perf_fixture', true, 'offer_number', offer_number)
FROM perf_offer_seed
CROSS JOIN LATERAL generate_series(0, leg_count - 1) AS legs(leg_position)
CROSS JOIN LATERAL (
    SELECT CASE WHEN is_direct_offer THEN 1 ELSE 2 END AS segments_per_leg
) AS segment_config
CROSS JOIN LATERAL generate_series(
    0,
    segment_config.segments_per_leg - 1
) AS segment_numbers(segment_number)
ON CONFLICT DO NOTHING;

INSERT INTO fare_offers (
    id,
    collection_run_id,
    itinerary_id,
    provider_offer_id,
    fingerprint,
    cabin,
    fare_family,
    currency,
    total_price_minor,
    base_price_minor,
    tax_minor,
    seats_remaining,
    baggage,
    refund_change_rules,
    expires_at,
    offer_metadata,
    created_at
)
SELECT
    offer_id,
    md5('farescope-perf-run-success-' || query_number)::uuid,
    itinerary_id,
    format('perf:%s:%s', query_number, offer_number),
    format('perf-offer-%s-%s', query_number, offer_number),
    'economy',
    'performance',
    'CNY',
    100000 + (query_number % 5000) * 100 + (offer_number - 1) * 750,
    80000 + (query_number % 5000) * 100 + (offer_number - 1) * 750,
    20000,
    9,
    NULL,
    NULL,
    seed_anchor + interval '1 day',
    jsonb_build_object('perf_fixture', true, 'offer_number', offer_number),
    seed_anchor - interval '4 minutes'
FROM perf_offer_seed
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

INSERT INTO latest_price_snapshots (
    id,
    search_query_id,
    provider_id,
    collection_run_id,
    itinerary_id,
    fare_offer_id,
    observed_at,
    currency,
    total_price_minor,
    is_direct,
    created_at,
    updated_at
)
SELECT
    md5('farescope-perf-latest-' || query_number)::uuid,
    search_query_id,
    '00000000-0000-0000-0000-000000000001'::uuid,
    md5('farescope-perf-run-success-' || query_number)::uuid,
    md5('farescope-perf-itinerary-' || query_number)::uuid,
    md5('farescope-perf-offer-' || query_number)::uuid,
    seed_anchor - interval '4 minutes',
    'CNY',
    100000 + (query_number % 5000) * 100,
    direct_only,
    seed_anchor - interval '4 minutes',
    seed_anchor - interval '4 minutes'
FROM perf_search_seed
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

INSERT INTO latest_calendar_price_snapshots (
    id,
    search_query_id,
    collection_run_id,
    provider_id,
    departure_date,
    return_date,
    currency,
    lowest_price_minor,
    total_price_minor,
    observed_at,
    source_endpoint,
    direct_verified,
    created_at,
    updated_at
)
SELECT
    md5(
        'farescope-perf-calendar-latest-'
        || search_seed.query_number
        || '-'
        || day_offset
    )::uuid,
    search_seed.search_query_id,
    md5('farescope-perf-run-success-' || search_seed.query_number)::uuid,
    '00000000-0000-0000-0000-000000000001'::uuid,
    search_seed.departure_date + day_offset,
    CASE
        WHEN search_seed.trip_type = 'round_trip'
            THEN search_seed.departure_date + day_offset + 7
        ELSE NULL
    END,
    'CNY',
    100000 + (search_seed.query_number % 5000) * 100 + day_offset * 25,
    NULL,
    seed_anchor - interval '4 minutes',
    'performance-fixture',
    false,
    seed_anchor - interval '4 minutes',
    seed_anchor - interval '4 minutes'
FROM perf_search_seed AS search_seed
CROSS JOIN generate_series(0, 29) AS calendar_days(day_offset)
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

INSERT INTO price_observations (
    id,
    observed_at,
    search_query_id,
    collection_run_id,
    itinerary_id,
    fare_offer_id,
    provider_id,
    offer_fingerprint,
    currency,
    total_price_minor,
    is_lowest,
    is_direct,
    created_at
)
SELECT
    md5(
        'farescope-perf-price-'
        || search_seed.query_number
        || '-'
        || observation_number
    )::uuid,
    seed_anchor
        - (
            (observation_number - 1)::double precision
            / greatest(:observations_per_query - 1, 1)
        ) * :history_days * interval '1 day',
    search_seed.search_query_id,
    md5('farescope-perf-run-success-' || search_seed.query_number)::uuid,
    CASE
        WHEN selected_offer.offer_number = 1
            THEN md5('farescope-perf-itinerary-' || search_seed.query_number)::uuid
        ELSE md5(
            'farescope-perf-itinerary-'
            || search_seed.query_number
            || '-'
            || selected_offer.offer_number
        )::uuid
    END,
    CASE
        WHEN selected_offer.offer_number = 1
            THEN md5('farescope-perf-offer-' || search_seed.query_number)::uuid
        ELSE md5(
            'farescope-perf-offer-'
            || search_seed.query_number
            || '-'
            || selected_offer.offer_number
        )::uuid
    END,
    '00000000-0000-0000-0000-000000000001'::uuid,
    format('perf:%s', search_seed.query_number),
    'CNY',
    100000
        + (search_seed.query_number % 5000) * 100
        + (selected_offer.offer_number - 1) * 750
        + ((observation_number % 17) - 8) * 50,
    true,
    search_seed.direct_only OR selected_offer.offer_number % 3 = 0,
    seed_anchor
        - (
            (observation_number - 1)::double precision
            / greatest(:observations_per_query - 1, 1)
        ) * :history_days * interval '1 day'
FROM perf_search_seed AS search_seed
CROSS JOIN generate_series(
    1,
    :observations_per_query
) AS observations(observation_number)
CROSS JOIN LATERAL (
    SELECT ((observation_number - 1) % :offers_per_query) + 1 AS offer_number
) AS selected_offer
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

INSERT INTO daily_price_aggregates (
    id,
    search_query_id,
    service_date,
    currency,
    is_direct,
    lowest_price_minor,
    sample_count,
    first_observed_at,
    last_observed_at,
    created_at,
    updated_at
)
SELECT
    md5('farescope-perf-daily-' || query_number)::uuid,
    search_query_id,
    departure_date,
    'CNY',
    direct_only,
    100000 + (query_number % 5000) * 100 - 400,
    :observations_per_query,
    seed_anchor - :history_days * interval '1 day',
    seed_anchor,
    seed_anchor,
    seed_anchor
FROM perf_search_seed
CROSS JOIN perf_seed_config
ON CONFLICT DO NOTHING;

COMMIT;

ANALYZE users;
ANALYZE search_queries;
ANALYZE search_legs;
ANALYZE subscriptions;
ANALYZE subscription_filters;
ANALYZE collection_runs;
ANALYZE schema_observations;
ANALYZE itineraries;
ANALYZE segments;
ANALYZE fare_offers;
ANALYZE latest_price_snapshots;
ANALYZE latest_calendar_price_snapshots;
ANALYZE price_observations;
ANALYZE daily_price_aggregates;

SELECT 'users' AS relation, count(*) AS rows
FROM users
WHERE normalized_username LIKE 'perf-user-%'
UNION ALL
SELECT 'subscriptions', count(*)
FROM subscriptions
WHERE tags @> '["performance"]'::jsonb
UNION ALL
SELECT 'subscription filters', count(*)
FROM subscription_filters
WHERE additional_filters @> '{"perf_fixture": true}'::jsonb
UNION ALL
SELECT 'canonical searches', count(*)
FROM search_queries
WHERE normalized_query @> '{"perf_fixture": true}'::jsonb
UNION ALL
SELECT 'collection queue', count(*)
FROM collection_runs
WHERE idempotency_key LIKE 'perf:queue:%'
UNION ALL
SELECT 'price observations', count(*)
FROM price_observations
WHERE offer_fingerprint LIKE 'perf:%'
UNION ALL
SELECT 'fare offers', count(*)
FROM fare_offers
WHERE offer_metadata @> '{"perf_fixture": true}'::jsonb
UNION ALL
SELECT 'schema observations', count(*)
FROM schema_observations
WHERE field_summary @> '{"perf_fixture": true}'::jsonb
UNION ALL
SELECT 'latest calendar prices', count(*)
FROM latest_calendar_price_snapshots
WHERE source_endpoint = 'performance-fixture'
ORDER BY relation;

-- Run manually against the application schema selected in your SQL client.
-- Read-only: no documents, filenames, emails, fingerprints or connection secrets.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '20s';

-- The two jobs completed on 2026-10-25 in the worker's CloudWatch log.
-- Identifiers are deterministically derived from SHA-256(job_id).
WITH events AS (
    SELECT 'registered' AS identity_type, analysis_id AS processing_id,
           created_at, conversion_type, status, transactions_count,
           layout_inference_name, layout_inference_confidence, selected_parser,
           parser_selection_reason, parser_confidence_band, parser_coverage_rate,
           quality_status, quality_rule_version, quality_reason_codes_json,
           warning_types_json, canonical_warning_transactions_count,
           balance_consistency_failed, canonical_capture_status, canonical_capture_reason
    FROM user_conversions
    WHERE analysis_id IN ('an_d376f93c200f', 'an_aadc800dd129')
    UNION ALL
    SELECT 'anonymous', id, created_at, conversion_type, status, transactions_count,
           layout_inference_name, layout_inference_confidence, selected_parser,
           parser_selection_reason, parser_confidence_band, parser_coverage_rate,
           quality_status, quality_rule_version, quality_reason_codes_json,
           warning_types_json, canonical_warning_transactions_count,
           balance_consistency_failed, canonical_capture_status, canonical_capture_reason
    FROM anonymous_conversion_events
    WHERE id IN ('anon_evt_d376f93c200fd91f6008ce74', 'anon_evt_aadc800dd1291f088a5bee26')
)
SELECT * FROM events ORDER BY created_at;

-- Compare complete local days, keeping volume/type/layout/rule separate.
-- A review rate is relative to successful conversions, not technical failures.
-- Missing historical assessments remain unknown; this query never reassesses them.
WITH events AS (
    SELECT 'registered' AS identity_type, created_at, conversion_type, status,
           layout_inference_name, quality_rule_version, quality_status, error_code,
           parser_confidence_band, quality_reason_codes_json
    FROM user_conversions
    WHERE created_at::timestamptz >= '2026-10-19T03:00:00Z'::timestamptz
      AND created_at::timestamptz < '2026-10-26T03:00:00Z'::timestamptz
    UNION ALL
    SELECT 'anonymous', created_at, conversion_type, status,
           layout_inference_name, quality_rule_version, quality_status, error_code,
           parser_confidence_band, quality_reason_codes_json
    FROM anonymous_conversion_events
    WHERE created_at::timestamptz >= '2026-10-19T03:00:00Z'::timestamptz
      AND created_at::timestamptz < '2026-10-26T03:00:00Z'::timestamptz
), eligible AS (
    SELECT *, lower(btrim(status)) IN ('sucesso', 'success', 'completed') AS succeeded
    FROM events
    WHERE coalesce(error_code, '') NOT IN (
        'file_too_large', 'monthly_pages_quota_exceeded', 'pages_limit_exceeded',
        'quota_exceeded', 'weekly_quota_exceeded'
    )
      AND lower(btrim(status)) NOT IN ('processando', 'processing', 'pending')
)
SELECT (created_at::timestamptz AT TIME ZONE 'America/Sao_Paulo')::date AS local_day,
       identity_type, conversion_type, layout_inference_name, quality_rule_version,
       count(*) AS completed_attempts,
       count(*) FILTER (WHERE succeeded) AS successes,
       count(*) FILTER (WHERE NOT succeeded) AS failures,
       count(*) FILTER (WHERE succeeded AND quality_status = 'review') AS reviews,
       count(*) FILTER (WHERE succeeded AND quality_status = 'clean') AS clean,
       count(*) FILTER (WHERE succeeded AND coalesce(quality_status, '') NOT IN ('review', 'clean')) AS unassessed,
       count(*) FILTER (WHERE succeeded AND parser_confidence_band IN ('low', 'medium')) AS low_or_medium_parser,
       count(*) FILTER (
           WHERE succeeded AND quality_status = 'review'
             AND coalesce(quality_reason_codes_json, '[]')::jsonb = '["parser_requires_review"]'::jsonb
       ) AS reviews_only_parser_confidence,
       round(100.0 * count(*) FILTER (WHERE succeeded AND quality_status = 'review')
             / nullif(count(*) FILTER (WHERE succeeded), 0), 1) AS review_percent_of_successes
FROM eligible
GROUP BY 1, 2, 3, 4, 5
ORDER BY 1, 2, 3, 4, 5;

ROLLBACK;

-- Reporting view for Power BI on top of skycargo.awb_analytics.
--
-- Keeping the report bound to a VIEW (not the base table) lets you evolve the
-- physical table without breaking the published model, and centralizes a few
-- presentation-friendly derived columns. Run this AFTER schema.sql, from inside
-- the VNet (private endpoint) or from pgAdmin while public access is enabled.

SET search_path TO skycargo, public;

CREATE OR REPLACE VIEW skycargo.vw_awb_analytics AS
SELECT
    a.id,
    a.doc_id,
    a.awb_number,
    a.document_name,
    a.flight_number,
    a.doc_date,
    COALESCE(NULLIF(a.source, ''), 'unknown')        AS source,
    a.final_state,
    (a.final_state = 'completed')                    AS is_completed,
    (a.final_state = 'failed')                        AS is_failed,
    a.validation_passed,
    COALESCE(a.failed_check_count, 0)                 AS failed_check_count,
    a.ocr_engine,
    a.ocr_confidence,
    a.page_count,
    COALESCE(a.retry_attempts, 0)                     AS retry_attempts,
    a.received_at,
    a.processed_at,
    a.processing_seconds,
    a.processed_date,
    a.output_json_url,
    a.output_md_url
FROM skycargo.awb_analytics AS a;

COMMENT ON VIEW skycargo.vw_awb_analytics IS
    'Presentation view for the Power BI AWB analytics report (DirectQuery source).';

-- Grant the Power BI service principal / reporting login read access here, e.g.:
--   GRANT USAGE  ON SCHEMA skycargo TO "<reporting-principal>";
--   GRANT SELECT ON skycargo.vw_awb_analytics TO "<reporting-principal>";

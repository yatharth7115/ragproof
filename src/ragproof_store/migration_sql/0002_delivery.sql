CREATE TABLE ragproof_event_outbox (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payload jsonb NOT NULL,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ragproof_outbox_pending_idx ON ragproof_event_outbox (id) WHERE published_at IS NULL;

CREATE FUNCTION ragproof_record_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    event jsonb;
BEGIN
    IF TG_TABLE_NAME = 'ragproof_traces' THEN
        event := jsonb_build_object('event','trace.ingested','trace_id',NEW.trace_id,
          'response_id', NEW.canonical_trace->'generation'->>'response_id',
          'tenant_id',NEW.tenant_id,'project_id',NEW.project_id);
    ELSIF TG_TABLE_NAME = 'ragproof_verifications' THEN
        IF NEW.status <> 'completed' THEN RETURN NEW; END IF;
        event := jsonb_build_object('event','verification.completed','trace_id',NEW.trace_id,
          'response_id',NEW.response_id,'evaluator_name',NEW.evaluator_name,'evaluator_version',NEW.evaluator_version);
    ELSIF TG_TABLE_NAME = 'ragproof_diagnoses' THEN
        event := jsonb_build_object('event','diagnosis.created','trace_id',NEW.trace_id,
          'response_id',NEW.response_id,'diagnosis_id',NEW.diagnosis_id,'primary_category',NEW.primary_category);
    ELSIF TG_TABLE_NAME = 'ragproof_replays' THEN
        event := jsonb_build_object('event','replay.completed','replay_id',NEW.replay_id,
          'diagnosis_id',NEW.diagnosis_id,'original_trace_id',NEW.original_trace_id,'outcome',NEW.outcome);
    ELSIF TG_TABLE_NAME = 'ragproof_regression_cases' THEN
        event := jsonb_build_object('event','regression.created','case_id',NEW.case_id,
          'replay_id',NEW.replay_id,'category',NEW.category);
    ELSIF TG_TABLE_NAME = 'ragproof_quality_gate_runs' THEN
        event := jsonb_build_object('event','quality_gate.completed','gate_run_id',NEW.gate_run_id,
          'tenant_id',NEW.tenant_id,'project_id',NEW.project_id,'outcome',NEW.outcome,
          'candidate_name',NEW.candidate_name,'total_cases',NEW.total_cases,'failed_cases',NEW.failed_cases);
    ELSE RETURN NEW;
    END IF;
    INSERT INTO ragproof_event_outbox(payload) VALUES(event);
    RETURN NEW;
END;
CREATE TRIGGER ragproof_trace_event AFTER INSERT ON ragproof_traces FOR EACH ROW EXECUTE FUNCTION ragproof_record_event();
CREATE TRIGGER ragproof_verification_event AFTER INSERT ON ragproof_verifications FOR EACH ROW EXECUTE FUNCTION ragproof_record_event();
CREATE TRIGGER ragproof_diagnosis_event AFTER INSERT ON ragproof_diagnoses FOR EACH ROW EXECUTE FUNCTION ragproof_record_event();
CREATE TRIGGER ragproof_replay_event AFTER INSERT ON ragproof_replays FOR EACH ROW EXECUTE FUNCTION ragproof_record_event();
CREATE TRIGGER ragproof_regression_event AFTER INSERT ON ragproof_regression_cases FOR EACH ROW EXECUTE FUNCTION ragproof_record_event();
CREATE TRIGGER ragproof_gate_event AFTER INSERT ON ragproof_quality_gate_runs FOR EACH ROW EXECUTE FUNCTION ragproof_record_event();

CREATE TABLE ragproof_webhook_deliveries (
    outbox_id bigint PRIMARY KEY REFERENCES ragproof_event_outbox(id),
    attempts integer NOT NULL DEFAULT 0,
    delivered_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_status integer
);

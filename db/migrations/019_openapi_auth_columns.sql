-- Static security scheme declarations and credential envelopes for imported specs (docs/44)
ALTER TABLE openapi_imports
    ADD COLUMN IF NOT EXISTS security_schemes JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS credential_envelopes JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN openapi_imports.security_schemes IS
    'Supported security schemes declared by the spec: apiKey header/query and bearer (docs/44)';
COMMENT ON COLUMN openapi_imports.credential_envelopes IS
    'Scheme name to SecretProvider envelope; plaintext never leaves the adapter call (docs/44, T26)';

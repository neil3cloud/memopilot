-- Remove the three hardcoded seed entries from provider_capabilities.
-- Real configured providers are added via POST /v1/providers/capabilities only.
DELETE FROM provider_capabilities
WHERE (model_id = 'gpt-4o-mini'      AND source = 'openai')
   OR (model_id = 'claude-sonnet-4.5' AND source = 'anthropic')
   OR (model_id = 'llama3.1'          AND source = 'ollama');

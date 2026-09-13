-- =====================================================
-- Migration 001: Enable pgvector extension
-- File: 001_enable_pgvector.sql
-- Date: 2026-09-13 17:05
-- Depends on: none
-- Run: psql -d atlas -f db/migrations/001_enable_pgvector.sql
-- =====================================================
-- Note: pgvector backs long-term fact memory (doc 11 §2.3
--       memory_fact, doc 06 §6.3). Extension first, tables in
--       later migrations per the doc 11 S1 table DDL rollout.
-- -----------------------------------------------------

CREATE EXTENSION IF NOT EXISTS vector;

COMMENT ON EXTENSION vector IS 'vector type and similarity search for long-term fact memory';

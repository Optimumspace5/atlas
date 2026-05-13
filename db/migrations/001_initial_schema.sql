-- Atlas Database Schema
-- File: db/migrations/001_initial_schema.sql
-- Author: Clarence Lee
-- Date: 2026-05-13
--
-- Purpose:
-- This file defines the initial PostgreSQL schema for Atlas, a knowledge gap aware
-- investing and trading book recommender system.
--
-- It creates the core tables for:
-- 1. Canonical book metadata
-- 2. Taxonomy concepts
-- 3. Book-to-concept annotations
-- 4. Users
-- 5. User reading history
--
-- Book-concept annotation strength convention:
-- confirmed   = 1.0 : The book meaningfully and repeatedly teaches the concept.
-- weak        = 0.5 : The concept is present but secondary or not deeply developed.
-- conditional = 0.3 : The concept is plausible but requires further manual verification.
--
-- Recommendation logic should primarily rely on confirmed and weak annotations.
-- Conditional annotations are stored for audit transparency and should not be
-- treated as strong evidence of coverage unless later promoted.

-- -----------------------------------------------------------------------------
-- Extensions
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- -----------------------------------------------------------------------------
-- Table: books
-- -----------------------------------------------------------------------------
CREATE TABLE books (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(512) NOT NULL,
    author VARCHAR(512) NOT NULL,
    isbn_13 CHAR(13) UNIQUE,
    description TEXT ,
    page_count INTEGER ,
    publication_year INTEGER ,
    cover_url TEXT ,
    source VARCHAR(64) NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- Table: concepts
-- -----------------------------------------------------------------------------

CREATE TABLE concepts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(256) NOT NULL,
    slug VARCHAR(256) NOT NULL UNIQUE,
    description TEXT ,
    level INTEGER NOT NULL CHECK (level IN (0, 1)),
    parent_id UUID REFERENCES concepts(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- Table: book_concept_annotations
-- -----------------------------------------------------------------------------

CREATE TABLE book_concept_annotations (
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    concept_id UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    strength FLOAT NOT NULL CHECK (strength IN (1.0, 0.5, 0.3)),
    annotation_type VARCHAR(32) NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (book_id, concept_id, annotation_type)
);

-- -----------------------------------------------------------------------------
-- Table: users
-- -----------------------------------------------------------------------------

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(256) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- Table: user_books
-- -----------------------------------------------------------------------------

CREATE TABLE user_books (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    date_read DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (user_id, book_id)
);

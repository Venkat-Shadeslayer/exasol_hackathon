"""Unit coverage for the Exasol scoring logic that needs no live server.

The SQL these builders emit is what actually ranks the corpus, so it is worth
pinning independently of a reachable database. Anything requiring a real
instance lives in ``scripts/verify_exasol.py`` instead.
"""

from __future__ import annotations

import math
import re

import pytest

from scholarmotion.config.settings import Settings
from scholarmotion.persistence.exasol import ExasolConfig, normalise
from scholarmotion.retrieval import exasol_retrieval as ex


def test_normalise_produces_unit_vector():
    result = normalise([3.0, 4.0])
    assert result == [0.6, 0.8]
    assert math.isclose(sum(value * value for value in result), 1.0)


def test_normalise_handles_zero_vector():
    assert normalise([0.0, 0.0]) == [0.0, 0.0]


def test_normalised_dot_product_equals_cosine():
    """The SQL path sums products with no norm division, so this must hold."""
    left, right = [1.0, 2.0, 3.0], [4.0, 5.0, 6.0]
    dot = sum(a * b for a, b in zip(normalise(left), normalise(right)))
    expected = sum(a * b for a, b in zip(left, right)) / (
        math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    )
    assert math.isclose(dot, expected)


def test_scoring_weights_sum_to_one():
    total = ex.VECTOR_WEIGHT + ex.LEXICAL_WEIGHT + ex.CONCEPT_WEIGHT + ex.PHRASE_WEIGHT
    assert math.isclose(total, 1.0)


def test_filters_bind_every_value():
    clause, params = ex._filters("p1", 12, "physics")
    assert "{project_id}" in clause and "{class_level!d}" in clause
    assert "{subject}" in clause
    assert params == {"project_id": "p1", "class_level": 12, "subject": "physics"}


def test_filters_omit_unset_values():
    clause, params = ex._filters(None, None, None)
    assert clause == ""
    assert params == {}


def test_lexical_expression_normalises_by_term_count():
    expression = ex._lexical_expression("magnetic flux")
    assert expression.count("INSTR") == 2
    assert expression.endswith("/ 2.0)")


def test_reserved_words_are_quoted():
    """SECTION, TEXT and VALUE are reserved in Exasol and must stay quoted."""
    statement = ex._sql_statement("", ex._lexical_expression("flux"), "0", ex._query_values([0.1]))
    assert 'c."TEXT"' in statement and 'c."SECTION"' in statement
    assert 'q."VALUE"' in statement
    # An unquoted occurrence would be a syntax error against a real instance.
    assert re.search(r"(?<![\".])\bTEXT\b(?!\")", statement) is None


def test_lexical_expression_drops_stopwords_and_short_tokens():
    expression = ex._lexical_expression("the flux of a coil")
    assert "'the'" not in expression
    assert "'flux'" in expression and "'coil'" in expression


def test_lexical_expression_handles_no_usable_terms():
    assert ex._lexical_expression("the and a") == "0"


def test_concept_expression_matches_json_quoted_tags():
    expression = ex._concept_expression(["flux"])
    assert '\'"flux"\'' in expression


def test_concept_expression_empty_is_neutral():
    assert ex._concept_expression([]) == "0"


@pytest.mark.parametrize(
    "hostile",
    ["o'brien", "'; DROP TABLE SOURCE_CHUNKS; --", "x' OR '1'='1"],
)
def test_hostile_query_cannot_break_out_of_a_sql_literal(hostile):
    """Lexical terms are inlined, so no input may introduce a quote.

    ``_lexical_expression`` extracts terms with ``[a-z0-9]{3,}``, which cannot
    match a quote. Every literal in the emitted fragment must therefore be a
    balanced, quote-free pair.
    """
    fragment = ex._lexical_expression(hostile)
    literals = re.findall(r"'([^']*)'", fragment)
    assert fragment.count("'") == 2 * len(literals)  # balanced, none escaped
    for literal in literals:
        assert re.fullmatch(r"[a-z0-9]{3,}", literal), literal


def test_hostile_concept_tags_are_discarded():
    """Concept tags are not regex-extracted, so quote-bearing ones are dropped."""
    assert ex._concept_expression(["o'brien", "'; DROP TABLE X; --"]) == "0"
    assert "'\"flux\"'" in ex._concept_expression(["o'brien", "flux"])


def test_placeholders_use_pyexasol_syntax():
    """pyexasol interpolates {name}; a bare :name is sent to the server raw and
    fails with 'Feature not supported: host parameter specification'."""
    statement = ex._udf_statement(ex._filters("p1", 12, "physics")[0], "0", "0")
    assert ":limit" not in statement and ":phrase" not in statement
    assert "{limit!d}" in statement and "{phrase}" in statement


def test_query_values_emit_numeric_literals_only():
    rendered = ex._query_values([1.0, 2.0, 2.0])
    assert rendered.startswith("(0, ")
    for token in rendered.replace("(", "").replace(")", "").split(","):
        float(token.strip())


def test_udf_statement_calls_the_udf_and_binds_the_vector():
    statement = ex._udf_statement("", "0", "0")
    assert "COSINE_SIMILARITY(c.EMBEDDING_JSON, {query_vector})" in statement
    assert "EMBEDDING_JSON IS NOT NULL" in statement
    assert "ORDER BY SCORE DESC" in statement


def test_sql_statement_joins_the_long_embedding_table():
    statement = ex._sql_statement("", "0", "0", ex._query_values([0.1, 0.2]))
    assert "CHUNK_EMBEDDINGS" in statement
    assert 'SUM(e."VALUE" * q."VALUE")' in statement
    assert "GROUP BY e.CHUNK_ID" in statement
    assert "COSINE_SIMILARITY" not in statement


def test_both_statements_apply_the_same_filters():
    clause, _ = ex._filters("p1", 12, "physics")
    udf = ex._udf_statement(clause, "0", "0")
    sql = ex._sql_statement(clause, "0", "0", ex._query_values([0.1]))
    assert clause.strip() in udf and clause.strip() in sql


def test_row_mapping_parses_tags_and_coerces_numerics():
    chunk = ex._to_chunk(
        {
            "CHUNK_ID": "c1",
            "DOCUMENT_ID": "d1",
            "DOCUMENT_KIND": "ncert",
            "CLASS_LEVEL": 12,
            "SUBJECT": "physics",
            "CHAPTER": "EMI",
            "SECTION": "6.2",
            "PAGE": 204,
            "CONTENT_TYPE": "equation",
            "TEXT": "Faraday's law",
            "CONCEPT_TAGS": '["flux", "emf"]',
            "SCORE": 0.87,
        },
        ("exasol_udf_cosine",),
    )
    assert chunk.chunk["concept_tags"] == ["flux", "emf"]
    assert chunk.chunk["class_level"] == 12 and chunk.chunk["page"] == 204
    assert chunk.score == pytest.approx(0.87)
    assert chunk.reasons == ("exasol_udf_cosine",)


def test_row_mapping_tolerates_nulls_and_bad_tag_json():
    chunk = ex._to_chunk(
        {"CHUNK_ID": "c1", "CLASS_LEVEL": None, "PAGE": None, "CONCEPT_TAGS": "not json"},
        ("exasol_sql_dot_product",),
    )
    assert chunk.chunk["class_level"] is None and chunk.chunk["page"] is None
    assert chunk.chunk["concept_tags"] == []
    assert chunk.score == 0.0


def test_config_reads_settings():
    settings = Settings(_env_file=None)
    config = ExasolConfig.from_settings(settings)
    assert config.dsn == settings.exasol_dsn
    assert config.schema == settings.exasol_schema
    assert config.use_udf == settings.exasol_use_udf

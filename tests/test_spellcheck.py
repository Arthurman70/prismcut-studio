"""core/spellcheck.py - Qt-free. Exercises the real pyspellchecker library
(pure Python, no network/subprocess/system dependency, same spirit as
using real PIL elsewhere in this suite) except for the "library
unavailable" degrade path, which is simulated via monkeypatch."""
from prismcut.core import spellcheck as sc_mod


def test_misspelled_spans_flags_real_misspellings_and_leaves_correct_text_alone():
    text = "This is a corect sentance, nothing else wrong here."

    spans = sc_mod.misspelled_spans(text)

    words = [text[s:e] for s, e in spans]
    assert "corect" in words
    assert "sentance" in words
    for correct in ("This", "is", "a", "nothing", "else", "wrong", "here"):
        assert correct not in words


def test_misspelled_spans_spans_point_back_to_the_exact_substring():
    text = "A corect word."

    spans = sc_mod.misspelled_spans(text)

    assert len(spans) == 1
    s, e = spans[0]
    assert text[s:e] == "corect"


def test_misspelled_spans_tolerates_punctuation_and_apostrophes():
    """Trailing punctuation must not get glued onto a word and falsely
    flag it (e.g. "word." treated as a literal, unknown token), and
    contractions must survive as one recognized word, not two fragments
    each side of the apostrophe."""
    text = "Don't panic, it's fine (really)! Well, sentance is not."

    spans = sc_mod.misspelled_spans(text)

    words = [text[s:e] for s, e in spans]
    assert words == ["sentance"]


def test_misspelled_spans_returns_empty_for_empty_text():
    assert sc_mod.misspelled_spans("") == []


def test_misspelled_spans_returns_empty_when_nothing_is_misspelled():
    assert sc_mod.misspelled_spans("A short correct sentence.") == []


def test_misspelled_spans_degrades_to_empty_when_the_library_is_unavailable(monkeypatch):
    monkeypatch.setattr(sc_mod, "_get_checker", lambda: None)

    assert sc_mod.misspelled_spans("corect sentance") == []


def test_suggestions_returns_a_real_correction_first():
    assert sc_mod.suggestions("corect")[0] == "correct"


def test_suggestions_respects_the_limit():
    out = sc_mod.suggestions("teh", limit=2)
    assert len(out) <= 2


def test_suggestions_returns_empty_for_an_empty_word():
    assert sc_mod.suggestions("") == []


def test_suggestions_degrades_to_empty_when_the_library_is_unavailable(monkeypatch):
    monkeypatch.setattr(sc_mod, "_get_checker", lambda: None)

    assert sc_mod.suggestions("corect") == []

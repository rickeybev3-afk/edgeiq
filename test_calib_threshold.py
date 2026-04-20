"""Tests for calib_threshold.resolve_calib_threshold().

Covers the full resolution priority order:
  1. Per-key env var  CALIB_MIN_TRADES_<KEY>  (wins over everything)
  2. Legacy alias     SQUEEZE_CALIB_MIN_TRADES (honoured only for 'squeeze')
  3. Default of 30   (returned when nothing is set)

Invalid (non-integer or non-positive) values must be skipped so that the
resolver falls through to the next priority level.
"""

import calib_threshold


# ---------------------------------------------------------------------------
# Priority 3: default
# ---------------------------------------------------------------------------

class TestDefault:
    def test_returns_30_when_no_env_vars_set(self, monkeypatch):
        monkeypatch.delenv("CALIB_MIN_TRADES_SQUEEZE", raising=False)
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        monkeypatch.delenv("CALIB_MIN_TRADES_GAP_DOWN", raising=False)
        assert calib_threshold.resolve_calib_threshold("squeeze") == 30

    def test_returns_30_for_unknown_key(self, monkeypatch):
        monkeypatch.delenv("CALIB_MIN_TRADES_UNKNOWN_KEY", raising=False)
        assert calib_threshold.resolve_calib_threshold("unknown-key") == 30


# ---------------------------------------------------------------------------
# Priority 1: per-key env var
# ---------------------------------------------------------------------------

class TestPerKeyEnvVar:
    def test_per_key_var_is_used(self, monkeypatch):
        monkeypatch.setenv("CALIB_MIN_TRADES_SQUEEZE", "50")
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        assert calib_threshold.resolve_calib_threshold("squeeze") == 50

    def test_hyphenated_key_normalised_to_underscores(self, monkeypatch):
        monkeypatch.setenv("CALIB_MIN_TRADES_GAP_DOWN", "20")
        assert calib_threshold.resolve_calib_threshold("gap-down") == 20

    def test_per_key_beats_legacy_alias(self, monkeypatch):
        """CALIB_MIN_TRADES_SQUEEZE must win over SQUEEZE_CALIB_MIN_TRADES."""
        monkeypatch.setenv("CALIB_MIN_TRADES_SQUEEZE", "75")
        monkeypatch.setenv("SQUEEZE_CALIB_MIN_TRADES", "10")
        assert calib_threshold.resolve_calib_threshold("squeeze") == 75

    def test_per_key_beats_default(self, monkeypatch):
        monkeypatch.setenv("CALIB_MIN_TRADES_SQUEEZE", "1")
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        assert calib_threshold.resolve_calib_threshold("squeeze") == 1


# ---------------------------------------------------------------------------
# Priority 2: legacy squeeze alias
# ---------------------------------------------------------------------------

class TestLegacySqueezeAlias:
    def test_legacy_alias_used_for_squeeze(self, monkeypatch):
        monkeypatch.delenv("CALIB_MIN_TRADES_SQUEEZE", raising=False)
        monkeypatch.setenv("SQUEEZE_CALIB_MIN_TRADES", "40")
        assert calib_threshold.resolve_calib_threshold("squeeze") == 40

    def test_legacy_alias_ignored_for_non_squeeze(self, monkeypatch):
        """SQUEEZE_CALIB_MIN_TRADES must NOT affect screeners other than squeeze."""
        monkeypatch.delenv("CALIB_MIN_TRADES_GAP_DOWN", raising=False)
        monkeypatch.setenv("SQUEEZE_CALIB_MIN_TRADES", "99")
        assert calib_threshold.resolve_calib_threshold("gap-down") == 30

    def test_legacy_alias_falls_through_to_default_when_absent(self, monkeypatch):
        monkeypatch.delenv("CALIB_MIN_TRADES_SQUEEZE", raising=False)
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        assert calib_threshold.resolve_calib_threshold("squeeze") == 30


# ---------------------------------------------------------------------------
# Invalid values are skipped
# ---------------------------------------------------------------------------

class TestInvalidValues:
    def test_non_integer_per_key_falls_through_to_default(self, monkeypatch):
        monkeypatch.setenv("CALIB_MIN_TRADES_SQUEEZE", "abc")
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        assert calib_threshold.resolve_calib_threshold("squeeze") == 30

    def test_zero_per_key_falls_through(self, monkeypatch):
        """Zero is not a positive int and must be skipped."""
        monkeypatch.setenv("CALIB_MIN_TRADES_SQUEEZE", "0")
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        assert calib_threshold.resolve_calib_threshold("squeeze") == 30

    def test_negative_per_key_falls_through(self, monkeypatch):
        monkeypatch.setenv("CALIB_MIN_TRADES_SQUEEZE", "-5")
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        assert calib_threshold.resolve_calib_threshold("squeeze") == 30

    def test_non_integer_per_key_falls_through_to_legacy(self, monkeypatch):
        """If per-key var is invalid, resolver must still honour legacy alias."""
        monkeypatch.setenv("CALIB_MIN_TRADES_SQUEEZE", "bad")
        monkeypatch.setenv("SQUEEZE_CALIB_MIN_TRADES", "55")
        assert calib_threshold.resolve_calib_threshold("squeeze") == 55

    def test_non_integer_legacy_falls_through_to_default(self, monkeypatch):
        monkeypatch.delenv("CALIB_MIN_TRADES_SQUEEZE", raising=False)
        monkeypatch.setenv("SQUEEZE_CALIB_MIN_TRADES", "not-a-number")
        assert calib_threshold.resolve_calib_threshold("squeeze") == 30

    def test_negative_legacy_falls_through_to_default(self, monkeypatch):
        monkeypatch.delenv("CALIB_MIN_TRADES_SQUEEZE", raising=False)
        monkeypatch.setenv("SQUEEZE_CALIB_MIN_TRADES", "-1")
        assert calib_threshold.resolve_calib_threshold("squeeze") == 30

    def test_whitespace_only_per_key_treated_as_absent(self, monkeypatch):
        monkeypatch.setenv("CALIB_MIN_TRADES_SQUEEZE", "   ")
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        assert calib_threshold.resolve_calib_threshold("squeeze") == 30

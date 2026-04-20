"""Tests for calib_threshold.resolve_calib_threshold().

Covers the full resolution priority order:
  1. Per-key env var  CALIB_MIN_TRADES_<KEY>  (wins over everything)
  2. Legacy alias     SQUEEZE_CALIB_MIN_TRADES (honoured only for 'squeeze')
  3. Default of 30   (returned when nothing is set)

Invalid (non-integer or non-positive) values must be skipped so that the
resolver falls through to the next priority level.

Call-site integration tests at the bottom of this file actually import and
invoke the relevant functions from ``deploy_server.py`` and
``nightly_tiered_pnl_refresh.py`` to verify that the screener key strings
used at each call site reach ``resolve_calib_threshold`` intact and that
env-var overrides are honoured end-to-end.
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


# ---------------------------------------------------------------------------
# Call-site integration: deploy_server.py
# ---------------------------------------------------------------------------
#
# deploy_server.Handler._screener_calibration() defines a SCREENERS list and
# calls _resolve_calib_threshold(s["key"]) for every entry before writing the
# resolved value into the JSON response as "threshold".
#
# These tests instantiate the Handler class (bypassing __init__ to avoid
# real socket/request setup) and invoke _screener_calibration() directly.
# With SUPABASE_URL unset the method skips the network call and returns a
# trivial "Supabase not configured" payload — enough to verify that the
# threshold field for each screener key reflects the env-var override.
# ---------------------------------------------------------------------------

import io
import json
import types as _types
import deploy_server as _deploy_server


def _make_handler():
    """Create a bare Handler instance with mocked HTTP primitives.

    Returns (handler, body_parts) where body_parts is a list that will be
    populated with each bytes argument passed to handler.wfile.write().
    """
    handler = object.__new__(_deploy_server.Handler)
    body_parts = []
    handler.send_response = lambda code: None
    handler.send_header = lambda k, v: None
    handler.end_headers = lambda: None
    handler.wfile = _types.SimpleNamespace(write=body_parts.append)
    return handler, body_parts


def _call_screener_calibration(monkeypatch, env_overrides=None):
    """Call _screener_calibration() with the supplied env overrides.

    Supabase is deliberately not configured so the method returns immediately
    after resolving thresholds, without any network I/O.

    Returns the parsed JSON dict ``{"screeners": [...]}``.
    """
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("VITE_SUPABASE_ANON_KEY", raising=False)
    for k, v in (env_overrides or {}).items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)

    handler, body_parts = _make_handler()
    handler._screener_calibration()
    raw = b"".join(body_parts)
    return json.loads(raw.decode())


def _thresholds_by_key(response):
    """Return {screener_key: threshold} from the parsed response dict."""
    return {s["key"]: s["threshold"] for s in response["screeners"]}


class TestDeployServerCallSite:
    """Integration tests that call Handler._screener_calibration() directly.

    Each test verifies that the threshold appearing in the JSON response for a
    given screener key reflects the correct env-var override — proving that the
    call site passes the key string through to resolve_calib_threshold() intact.
    """

    def test_all_screener_keys_present_in_response(self, monkeypatch):
        """_screener_calibration() must return an entry for every screener it defines."""
        resp = _call_screener_calibration(monkeypatch)
        keys = {s["key"] for s in resp["screeners"]}
        # At minimum squeeze and gap_down must be present; other/trend may be too.
        assert "squeeze" in keys, f"'squeeze' missing from screener keys: {keys}"
        assert "gap_down" in keys, f"'gap_down' missing from screener keys: {keys}"

    def test_squeeze_threshold_honours_env_var(self, monkeypatch):
        """When CALIB_MIN_TRADES_SQUEEZE=45 the squeeze entry in the response must show 45."""
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        resp = _call_screener_calibration(
            monkeypatch, {"CALIB_MIN_TRADES_SQUEEZE": "45"}
        )
        thresholds = _thresholds_by_key(resp)
        assert thresholds["squeeze"] == 45, (
            f"squeeze threshold = {thresholds['squeeze']!r}, expected 45. "
            "A key mismatch at the _screener_calibration call site would cause this."
        )

    def test_gap_down_threshold_honours_env_var(self, monkeypatch):
        """When CALIB_MIN_TRADES_GAP_DOWN=60 the gap_down entry must show 60."""
        resp = _call_screener_calibration(
            monkeypatch, {"CALIB_MIN_TRADES_GAP_DOWN": "60"}
        )
        thresholds = _thresholds_by_key(resp)
        assert thresholds["gap_down"] == 60, (
            f"gap_down threshold = {thresholds['gap_down']!r}, expected 60. "
            "A key mismatch at the _screener_calibration call site would cause this."
        )

    def test_all_screeners_default_to_30_when_no_env_vars(self, monkeypatch):
        """Without env-var overrides every screener threshold must equal the default 30."""
        clear = {
            "CALIB_MIN_TRADES_SQUEEZE": None,
            "SQUEEZE_CALIB_MIN_TRADES": None,
            "CALIB_MIN_TRADES_GAP_DOWN": None,
            "CALIB_MIN_TRADES_OTHER": None,
            "CALIB_MIN_TRADES_TREND": None,
        }
        resp = _call_screener_calibration(monkeypatch, clear)
        for entry in resp["screeners"]:
            assert entry["threshold"] == 30, (
                f"screener {entry['key']!r} threshold = {entry['threshold']!r}, "
                "expected 30 when no env var is set."
            )

    def test_very_low_threshold_appears_in_response(self, monkeypatch):
        """A threshold of 1 must not be silently replaced with 30 in the response."""
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        resp = _call_screener_calibration(
            monkeypatch, {"CALIB_MIN_TRADES_SQUEEZE": "1"}
        )
        assert _thresholds_by_key(resp)["squeeze"] == 1

    def test_very_high_threshold_appears_in_response(self, monkeypatch):
        """A threshold of 10000 must not be capped in the response."""
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        resp = _call_screener_calibration(
            monkeypatch, {"CALIB_MIN_TRADES_SQUEEZE": "10000"}
        )
        assert _thresholds_by_key(resp)["squeeze"] == 10000

    def test_invalid_env_var_falls_back_to_30_in_response(self, monkeypatch):
        """A non-integer env var must be skipped so the response shows 30."""
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)
        resp = _call_screener_calibration(
            monkeypatch, {"CALIB_MIN_TRADES_SQUEEZE": "not-a-number"}
        )
        assert _thresholds_by_key(resp)["squeeze"] == 30


# ---------------------------------------------------------------------------
# Call-site integration: nightly_tiered_pnl_refresh.py
# ---------------------------------------------------------------------------
#
# Three paths in nightly_tiered_pnl_refresh reach resolve_calib_threshold:
#
#   A. _get_squeeze_calib_min_trades()
#        DB lookup → on failure falls back to resolve_calib_threshold("squeeze")
#
#   B. _check_gap_down_calibration_due()
#        calls _check_screener_calibration_due("gap_down", ..., min_trades=None)
#        which calls resolve_calib_threshold("gap_down")
#
#   C. _check_screener_calibration_due(key, ..., min_trades=None)
#        for arbitrary screener keys from _SP_MULT_TABLE
#        calls resolve_calib_threshold(key)
#
# Tests A-C exercise the actual nightly module functions with mocked
# dependencies so the real key strings and env-var resolution are verified
# without Supabase I/O.
# ---------------------------------------------------------------------------

from unittest.mock import patch as _patch
import nightly_tiered_pnl_refresh as _nightly


class TestNightlyRefreshCallSite:
    # ── Path A: _get_squeeze_calib_min_trades() ──────────────────────────────

    def test_squeeze_calib_falls_back_to_env_var_when_backend_unavailable(
        self, monkeypatch
    ):
        """When the backend DB lookup fails, _get_squeeze_calib_min_trades must fall
        back to resolve_calib_threshold('squeeze') and honour CALIB_MIN_TRADES_SQUEEZE."""
        monkeypatch.setenv("CALIB_MIN_TRADES_SQUEEZE", "42")
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)

        def _fail():
            raise RuntimeError("no DB in test")

        import backend as _backend
        with _patch.object(_backend, "resolve_squeeze_calib_min_trades_effective", _fail):
            result = _nightly._get_squeeze_calib_min_trades()

        assert result == 42, (
            f"Expected 42 from CALIB_MIN_TRADES_SQUEEZE but got {result}. "
            "The squeeze fallback path is not honouring the env-var override."
        )

    def test_squeeze_calib_fallback_returns_30_when_no_env_var(self, monkeypatch):
        """Without env-var override, the fallback path must return the default 30."""
        monkeypatch.delenv("CALIB_MIN_TRADES_SQUEEZE", raising=False)
        monkeypatch.delenv("SQUEEZE_CALIB_MIN_TRADES", raising=False)

        def _fail():
            raise RuntimeError("no DB in test")

        import backend as _backend
        with _patch.object(_backend, "resolve_squeeze_calib_min_trades_effective", _fail):
            result = _nightly._get_squeeze_calib_min_trades()

        assert result == 30

    def test_squeeze_calib_fallback_honours_legacy_alias(self, monkeypatch):
        """On the fallback path, SQUEEZE_CALIB_MIN_TRADES must still be honoured."""
        monkeypatch.delenv("CALIB_MIN_TRADES_SQUEEZE", raising=False)
        monkeypatch.setenv("SQUEEZE_CALIB_MIN_TRADES", "35")

        def _fail():
            raise RuntimeError("no DB in test")

        import backend as _backend
        with _patch.object(_backend, "resolve_squeeze_calib_min_trades_effective", _fail):
            result = _nightly._get_squeeze_calib_min_trades()

        assert result == 35

    # ── Path B: _check_gap_down_calibration_due() ────────────────────────────

    def test_gap_down_calibration_passes_correct_key_to_helper(self):
        """_check_gap_down_calibration_due must pass 'gap_down' as the screener key."""
        with _patch.object(
            _nightly, "_check_screener_calibration_due"
        ) as mock_helper:
            _nightly._check_gap_down_calibration_due()

        assert mock_helper.called, "_check_screener_calibration_due was not called"
        key_arg = mock_helper.call_args[0][0]
        assert key_arg == "gap_down", (
            f"_check_gap_down_calibration_due passed key {key_arg!r}; "
            "expected 'gap_down'. A typo in this call site would cause the wrong "
            "env var (CALIB_MIN_TRADES_<WRONG_KEY>) to be consulted."
        )

    def test_gap_down_calibration_passes_no_explicit_min_trades(self):
        """_check_gap_down_calibration_due must leave min_trades=None so that threshold
        resolution is delegated to resolve_calib_threshold('gap_down')."""
        with _patch.object(
            _nightly, "_check_screener_calibration_due"
        ) as mock_helper:
            _nightly._check_gap_down_calibration_due()

        kwargs = mock_helper.call_args[1]
        # min_trades should not be passed as a positional or keyword argument
        # (default is None inside _check_screener_calibration_due)
        assert "min_trades" not in kwargs, (
            f"_check_gap_down_calibration_due passed explicit min_trades={kwargs['min_trades']!r}. "
            "This short-circuits resolve_calib_threshold; omit min_trades to let the "
            "env-var resolution run."
        )
        positional_args = mock_helper.call_args[0]
        # min_trades is the 3rd positional parameter; only key and script are expected
        assert len(positional_args) == 2, (
            f"Expected 2 positional args (key, script) but got {len(positional_args)}: "
            f"{positional_args!r}"
        )

    # ── Path C: _check_screener_calibration_due(key, min_trades=None) ────────
    #
    # _check_screener_calibration_due resolves the threshold via
    # resolve_calib_threshold(key) BEFORE it touches Supabase.  After threshold
    # resolution it tries to import backend and immediately returns if
    # backend.supabase is None.  We patch backend.supabase to None so the
    # function exits cleanly without any Supabase I/O or state-file writes,
    # while still exercising the threshold-resolution logic we care about.

    @staticmethod
    def _spy_and_patch_backend(monkeypatch):
        """Return (resolved_dict, context_manager) that patches both
        nightly's resolve_calib_threshold (spy) and backend.supabase (None).

        Usage::

            resolved, ctx = self._spy_and_patch_backend(monkeypatch)
            with ctx:
                _nightly._check_screener_calibration_due(...)
            assert resolved["gap_down"] == expected
        """
        import contextlib
        import backend as _backend

        resolved = {}
        original = _nightly.resolve_calib_threshold

        def spy(key):
            result = original(key)
            resolved[key] = result
            return result

        @contextlib.contextmanager
        def _ctx():
            with _patch.object(_nightly, "resolve_calib_threshold", side_effect=spy):
                with _patch.object(_backend, "supabase", None):
                    yield

        return resolved, _ctx()

    def test_gap_down_threshold_resolved_from_env_when_min_trades_is_none(
        self, monkeypatch
    ):
        """When called with min_trades=None, _check_screener_calibration_due must call
        resolve_calib_threshold('gap_down') and the env-var override must win.

        backend.supabase is patched to None so the function returns early after
        threshold resolution without writing any state files.
        """
        monkeypatch.setenv("CALIB_MIN_TRADES_GAP_DOWN", "55")

        resolved, ctx = self._spy_and_patch_backend(monkeypatch)
        with ctx:
            _nightly._check_screener_calibration_due(
                "gap_down", "calibrate_gap_down_mult.py"
            )

        assert "gap_down" in resolved, (
            "resolve_calib_threshold was not called with 'gap_down'. "
            "Check that min_trades is None at the gap_down call site."
        )
        assert resolved["gap_down"] == 55, (
            f"resolve_calib_threshold('gap_down') returned {resolved['gap_down']!r}; "
            "expected 55 from CALIB_MIN_TRADES_GAP_DOWN."
        )

    def test_very_low_gap_down_threshold_resolved_end_to_end(self, monkeypatch):
        """A threshold of 1 must be returned by the resolver when the env var says 1."""
        monkeypatch.setenv("CALIB_MIN_TRADES_GAP_DOWN", "1")

        resolved, ctx = self._spy_and_patch_backend(monkeypatch)
        with ctx:
            _nightly._check_screener_calibration_due(
                "gap_down", "calibrate_gap_down_mult.py"
            )

        assert resolved.get("gap_down") == 1

    def test_invalid_env_var_causes_fallback_to_30_end_to_end(self, monkeypatch):
        """An invalid env var must cause the resolver to return 30, not crash."""
        monkeypatch.setenv("CALIB_MIN_TRADES_GAP_DOWN", "not-a-number")

        resolved, ctx = self._spy_and_patch_backend(monkeypatch)
        with ctx:
            _nightly._check_screener_calibration_due(
                "gap_down", "calibrate_gap_down_mult.py"
            )

        assert resolved.get("gap_down") == 30

"""Smoke check for the main entry-point module.

The project plan deliberately defers integration tests for `src.main`
to a later phase — at this stage we only confirm that the module
imports cleanly and exposes its expected public surface, so a broken
syntax error or missing dependency cannot land silently.
"""

from __future__ import annotations

import importlib


def test_main_module_imports_without_error():
    """`python -m src.main` must at minimum parse and import."""
    module = importlib.import_module("src.main")
    # The CLI entry-point and the helpers `src.main.main`/`main_async`
    # rely on for wiring should all be present.
    for attr in ("main", "main_async", "setup_logging", "process_card",
                 "reader_loop", "_build_arg_parser"):
        assert hasattr(module, attr), f"src.main is missing attribute: {attr}"


def test_arg_parser_accepts_simulate_card():
    """A minimal sanity check that `--simulate-card UID` is wired up.

    Kept here rather than in a dedicated CLI test module because the
    project plan explicitly says "no integration tests for main yet";
    a single argparse smoke test is the lightest possible touch.
    """
    from src.main import _build_arg_parser

    ns = _build_arg_parser().parse_args(["--simulate-card", "AAAA"])
    assert ns.simulate_card == "AAAA"

    ns = _build_arg_parser().parse_args([])
    assert ns.simulate_card is None

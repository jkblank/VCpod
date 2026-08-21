# Auto-imported by Python at interpreter startup whenever this directory is
# on sys.path (via PYTHONPATH) -- used to apply fetcher_apple._net's
# IPv4-only DNS patch inside the standalone `gamdl` CLI subprocess, which
# runs as its own separate Python process and so can't be reached by an
# in-process monkeypatch applied in the parent (see download.py's
# `_run_gamdl`/`_run_gamdl_single_track`, and fetcher_apple/_net.py for why
# this is needed at all).
from fetcher_apple._net import force_ipv4_dns

force_ipv4_dns()

"""Launcher without a console window (used by 'Start with Windows').
pythonw has no stdout/stderr, which breaks uvicorn's logging, so output goes to collected/agent_log.txt."""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.makedirs(os.path.join(ROOT, 'collected'), exist_ok=True)
log = open(os.path.join(ROOT, 'collected', 'agent_log.txt'), 'a', buffering=1, encoding='utf-8')
if sys.stdout is None or not hasattr(sys.stdout, 'isatty'):
    sys.stdout = log
if sys.stderr is None or not hasattr(sys.stderr, 'isatty'):
    sys.stderr = log

try:
    from agent.tray import main
    main()
except Exception:
    traceback.print_exc(file=log)
    raise

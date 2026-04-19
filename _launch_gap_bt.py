import subprocess, sys, os

log_path = "/tmp/gap_bt_365.log"
p = subprocess.Popen(
    [sys.executable, "batch_backtest.py", "--screener", "gap", "--days", "365"],
    stdout=open(log_path, "w"),
    stderr=subprocess.STDOUT,
    start_new_session=True,
    cwd=os.path.dirname(os.path.abspath(__file__)),
)
print(f"PID:{p.pid} LOG:{log_path}")

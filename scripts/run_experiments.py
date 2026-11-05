"""Run alternating topology experiments; retains raw samples and environment."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys


def command(*args):
    return subprocess.check_output(args,text=True).strip()


def main():
    root = Path(__file__).resolve().parent.parent
    if Path.cwd()!=root:
        raise SystemExit("Run from the payment-engine directory")
    environment = {"started_at":datetime.now(timezone.utc).isoformat(),"host":platform.platform(),
        "docker":json.loads(command("docker","info","--format",'{{json .}}')),
        "source_sha256":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path("payments").glob("*")) if p.is_file()}}
    # Keep only public hardware/runtime information, not daemon configuration.
    info = environment["docker"]
    environment["docker"] = {k:info.get(k) for k in ("ServerVersion","NCPU","MemTotal","Architecture","OperatingSystem")}
    Path("reports/environment.json").write_text(json.dumps(environment,indent=2)+"\n")
    summary = []
    try:
        for repeat in range(1,4):
            for workers in (1,4):
                subprocess.run(["docker","compose","up","-d","--scale",f"worker={workers}"],check=True,stdout=subprocess.DEVNULL)
                actual = len(command("docker","compose","ps","-q","worker").splitlines())
                assert actual==workers
                destination = f"reports/{workers}-workers-run{repeat}.json"
                completed = subprocess.run([sys.executable,"scripts/benchmark.py","--workers-label",str(actual),
                    "--count","3000","--concurrency","40","--output",destination])
                report = json.loads(Path(destination).read_text())
                summary.append({k:report[k] for k in ("duration_seconds","completed","failed","transport_errors","completed_per_second","completion_p95_ms","completion_p99_ms")}
                    | {"workers":workers,"repeat":repeat,"raw_report":destination,"exit_code":completed.returncode})
                Path("reports/scaling-summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    finally:
        subprocess.run(["docker","compose","up","-d","--scale","worker=1"],check=True,stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()

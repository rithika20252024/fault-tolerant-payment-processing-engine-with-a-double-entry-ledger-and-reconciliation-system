"""Local interactive configuration. Never accepts or prints a key on argv."""
from getpass import getpass
import os
from pathlib import Path
import tempfile


def main():
    destination = Path(__file__).resolve().parent.parent / ".env"
    template = destination if destination.exists() else destination.with_name(".env.example")
    lines = template.read_text().splitlines()
    key = getpass("Stripe sandbox secret key (sk_test_…): ").strip()
    if not key.startswith("sk_test_"):
        raise SystemExit("Only sk_test_ sandbox keys are accepted; no changes made")
    webhook = getpass("CLI webhook signing secret (whsec_…, or leave empty for now): ").strip()
    if webhook and not webhook.startswith("whsec_"):
        raise SystemExit("Invalid signing secret; no changes made")
    if any(char in key+webhook for char in "\r\n\x00"):
        raise SystemExit("Invalid credential format")
    values = {"PROVIDER":"stripe","STRIPE_SECRET_KEY":key,"STRIPE_WEBHOOK_SECRET":webhook}
    found = set()
    for i,line in enumerate(lines):
        name = line.partition("=")[0]
        if name in values:
            lines[i] = name+"="+values[name]
            found.add(name)
    lines.extend(name+"="+value for name,value in values.items() if name not in found)
    fd, temporary = tempfile.mkstemp(prefix=".env-",dir=destination.parent)
    try:
        with os.fdopen(fd,"w") as f:
            f.write("\n".join(lines)+"\n")
        os.chmod(temporary,0o600)
        os.replace(temporary,destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Sandbox configuration saved locally. Recreate API and worker containers to apply it.")


if __name__=="__main__":
    main()

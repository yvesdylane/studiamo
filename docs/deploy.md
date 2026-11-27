# Running Studiamo on a server

## First time

```bash
git clone git@github.com:yvesdylane/studiamo.git
cd studiamo
curl -LsSf https://astral.sh/uv/install.sh | sh     # installs uv
source ~/.bashrc
uv sync --frozen                                    # exact versions from uv.lock
```

Copy your `.env` to the server (never through git) and make sure it has a real
`SECRET_KEY`:

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))" >> .env
```

`AI_KEYS_SECRET` must stay the same as wherever users saved AI keys, or those
keys can no longer be read (see `.env.example`).

## Start

```bash
uv run gunicorn --bind 0.0.0.0:3000 --workers 1 --threads 8 --timeout 900 main:app
```

- `0.0.0.0`: reachable from other machines.
- One worker, 8 threads: the app runs its database migrations at start-up, and
  several workers starting together would run them at the same time.
- `--timeout 900`: AI imports can take a few minutes.

Do not use `python main.py` on a server: it is Flask's development server with
the debugger on.

Keep it running after you log out (good enough for testing):

```bash
nohup uv run gunicorn --bind 0.0.0.0:3000 --workers 1 --threads 8 --timeout 900 main:app > studiamo.log 2>&1 &
tail -f studiamo.log                 # watch the log (Ctrl+C stops watching only)
pkill -f "gunicorn.*main:app"        # stop it
```

## Is it reachable?

```bash
curl -I http://localhost:3000/       # on the server: 200 OK
```

Then open `http://SERVER_IP:3000` from another machine. If it does not load,
the port is closed:

- the cloud provider's firewall (Oracle Cloud: the subnet's Security List;
  AWS: the security group) must allow TCP 3000 in;
- on the machine itself: `sudo ufw allow 3000/tcp`, or on Oracle's Ubuntu
  images `sudo iptables -I INPUT -p tcp --dport 3000 -j ACCEPT`.

## Update

```bash
git pull
uv sync --frozen
pkill -f "gunicorn.*main:app"
nohup uv run gunicorn --bind 0.0.0.0:3000 --workers 1 --threads 8 --timeout 900 main:app > studiamo.log 2>&1 &
```

New migrations run by themselves when the app starts.

## Not yet working on http://SERVER_IP:3000

- Google login: Google only accepts registered addresses (a domain with https).
- Emails to anyone but you: verify a domain in Resend and set `MAIL_FROM` on it.
- Behind nginx with https, set `TRUST_PROXY=1` and `SESSION_COOKIE_SECURE=1`.

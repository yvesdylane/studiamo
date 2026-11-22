"""Run the site:  uv run python main.py   (or PyCharm's "main" configuration)

HOST and PORT come from .env (defaults: localhost, 3000). The Google callback
follows the address the site runs on, so changing PORT needs no other edit here.
"""

import os

from src.app import create_app

app = create_app()  # also loads .env

if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "localhost"), port=int(os.getenv("PORT", "3000")), debug=True)

from pathlib import Path

from dotenv import load_dotenv

from .server import create_app

HOST = "127.0.0.1"
PORT = 8765


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    create_app().run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()

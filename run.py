"""MaklerHub-u işə salır.

    python run.py              → http://127.0.0.1:5000
    python run.py --port 8080
    python run.py --host 0.0.0.0   (şəbəkədəki digər cihazlar üçün)

`waitress` quraşdırılıbsa (requirements.txt-də var), istehsal səviyyəli server
istifadə olunur; yoxdursa Flask-ın daxili serveri işləyir.
"""
import argparse
import sys
from dotenv import load_dotenv
load_dotenv()
from maklerhub import create_app


def main():
    parser = argparse.ArgumentParser(description="MaklerHub serveri")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true", help="Flask debug rejimi (inkişaf üçün)")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    app = create_app()
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print("\n  MaklerHub işə düşdü →", url)
    print()

    if args.debug:
        app.run(host=args.host, port=args.port, debug=True, use_reloader=False)
        return
    try:
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=8)
    except ImportError:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

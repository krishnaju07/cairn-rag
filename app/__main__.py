import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description="Run Cairn.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--reload", action="store_true", help="restart when the code changes")
    arguments = parser.parse_args()
    uvicorn.run(
        "app.main:app",
        host=arguments.host,
        port=arguments.port,
        reload=arguments.reload,
        reload_dirs=["app"] if arguments.reload else None,
    )


if __name__ == "__main__":
    main()

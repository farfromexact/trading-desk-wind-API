"""Check the local WindPy installation and make one small data request."""
import sys


def main():
    print(f"Python: {sys.executable}", flush=True)
    try:
        import WindPy
        from WindPy import w
    except Exception as exc:
        print(f"WindPy import failed: {exc}", file=sys.stderr)
        return 1

    print(f"WindPy: {WindPy.__file__}", flush=True)
    try:
        result = w.start(waitTime=15)
        print(f"Connection error code: {result.ErrorCode}; {result.Data}", flush=True)
        if result.ErrorCode != 0:
            return 1
        result = w.wss("000001.SZ", "sec_name")
        print(f"Data request error code: {result.ErrorCode}", flush=True)
        if result.ErrorCode != 0:
            return 1
        print(f"000001.SZ sec_name: {result.Data}", flush=True)
        return 0
    except Exception as exc:
        print(f"Wind check failed: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            w.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

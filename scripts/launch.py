"""Local launcher. Uses this project's interpreter and waits for server readiness."""
import argparse
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]

def available_port(start=8501):
    for port in range(start, start + 20):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise RuntimeError('No available local port in range 8501-8520.')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    port = available_port()
    address = f'http://127.0.0.1:{port}'
    print(f'Starting log diagnosis agent: {address}', flush=True)
    print('Keep this window open. Press Ctrl+C to stop.', flush=True)
    process = subprocess.Popen([
        sys.executable, '-B', '-m', 'streamlit', 'run', str(ROOT / 'app.py'),
        '--server.address', '127.0.0.1', '--server.port', str(port),
        '--server.headless', 'true', '--browser.gatherUsageStats', 'false',
    ], cwd=ROOT)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                print('Startup failed. See the error above.', flush=True)
                return process.returncode or 1
            try:
                with opener.open(address + '/_stcore/health', timeout=1) as response:
                    if response.status == 200 and response.read().strip() == b'ok':
                        print(f'READY: {address}', flush=True)
                        if not args.no_browser:
                            webbrowser.open(address)
                        break
            except OSError:
                pass
            time.sleep(0.3)
        else:
            print('Startup timed out. See the error above.', flush=True)
            process.terminate()
            return 1
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

if __name__ == '__main__':
    raise SystemExit(main())

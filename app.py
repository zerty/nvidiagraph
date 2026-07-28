"""
NVIDIA GPU Monitor - Real-time GPU metrics dashboard
Parses nvidia-smi every second and streams data to connected clients via WebSocket.
Data is buffered in-memory only (not saved to disk).
"""

import hmac
import os
import threading
import time
import subprocess
import json
from collections import deque
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'nvidia-gpu-monitor-secret-key')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Configuration from environment variables
MONITOR_PASSWORD = os.environ.get('MONITOR_PASSWORD', '').strip()
MAX_HISTORY = int(os.environ.get('MAX_HISTORY', '600'))  # ~10 minutes at 1 sample/second
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '5000'))

# In-memory buffer (not persisted to disk)
gpu_history = {}  # {gpu_id: deque of metric dicts}
gpu_process_history = {}  # {gpu_id: deque of process dicts}
lock = threading.Lock()

# Control flag for the monitoring thread
monitoring = True


def parse_nvidia_smi():
    """
    Parse nvidia-smi output and return a list of GPU metrics dictionaries.
    Uses query mode for cleaner structured output.
    """
    try:
        # Run nvidia-smi in query mode for structured output
        result = subprocess.run(
            [
                'nvidia-smi',
                '--query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.current.graphics,clocks.current.memory,clocks.current.video',
                '--format=csv,noheader,nounits'
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            print(f"nvidia-smi error: {result.stderr.strip()}")
            return []

        gpus = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue

            # Parse CSV line - handle quoted fields (GPU name can contain commas)
            values = parse_csv_line(line)

            if len(values) >= 13:
                gpu = {
                    'index': int(values[0]),
                    'name': values[1],
                    'gpu_util': float(values[2]),
                    'mem_util': float(values[3]),
                    'mem_used': float(values[4]),
                    'mem_total': float(values[5]),
                    'temperature': float(values[6]),
                    'power_draw': float(values[7]),
                    'power_limit': float(values[8]),
                    'fan_speed': float(values[9]) if values[9] != 'N/A' else 0,
                    'gpu_clock': float(values[10]),
                    'mem_clock': float(values[11]),
                    'video_clock': float(values[12]),
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'epoch': time.time()
                }
                gpus.append(gpu)
        return gpus

    except FileNotFoundError:
        print("nvidia-smi not found. Is the NVIDIA driver installed?")
        return []
    except subprocess.TimeoutExpired:
        print("nvidia-smi timed out")
        return []
    except Exception as e:
        print(f"Error parsing nvidia-smi: {e}")
        return []


def parse_csv_line(line):
    """Parse a CSV line handling quoted fields."""
    values = []
    current = ''
    in_quotes = False

    for char in line:
        if char == '"':
            in_quotes = not in_quotes
        elif char == ',' and not in_quotes:
            values.append(current.strip().strip('"'))
            current = ''
        else:
            current += char

    values.append(current.strip().strip('"'))
    return values


def parse_gpu_processes():
    """
    Parse nvidia-smi compute-apps query to get list of processes using GPUs.
    Returns a dict mapping GPU index to list of process dicts.
    """
    try:
        result = subprocess.run(
            [
                'nvidia-smi',
                '--query-compute-apps=pid,used_memory,name,gpu_uuid',
                '--format=csv,noheader,nounits'
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return {}

        # Also get GPU index -> UUID mapping
        gpu_index_map = {}
        idx_result = subprocess.run(
            [
                'nvidia-smi',
                '--query-gpu=index,gpu_uuid',
                '--format=csv,noheader,nounits'
            ],
            capture_output=True,
            text=True,
            timeout=5
        )
        if idx_result.returncode == 0:
            for line in idx_result.stdout.strip().split('\n'):
                parts = parse_csv_line(line)
                if len(parts) >= 2:
                    gpu_index_map[parts[1].strip()] = int(parts[0])

        processes = {}  # {gpu_index: [process dicts]}

        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue

            values = parse_csv_line(line)
            if len(values) >= 3:
                pid = values[0].strip()
                mem_used = values[1].strip()
                name = values[2].strip() if len(values) > 2 else 'Unknown'
                gpu_uuid = values[3].strip() if len(values) > 3 else ''

                gpu_index = gpu_index_map.get(gpu_uuid, 0)

                if gpu_index not in processes:
                    processes[gpu_index] = []

                processes[gpu_index].append({
                    'pid': pid,
                    'mem_used': mem_used,
                    'name': name
                })

        return processes

    except FileNotFoundError:
        return {}
    except subprocess.TimeoutExpired:
        return {}
    except Exception as e:
        print(f"Error parsing GPU processes: {e}")
        return {}


def monitoring_loop():
    """Background thread that continuously parses nvidia-smi and broadcasts data."""
    global monitoring

    while monitoring:
        try:
            gpus = parse_nvidia_smi()

            if gpus:
                with lock:
                    for gpu in gpus:
                        gpu_id = gpu['index']

                        if gpu_id not in gpu_history:
                            gpu_history[gpu_id] = deque(maxlen=MAX_HISTORY)

                        gpu_history[gpu_id].append(gpu)

                # Get GPU processes
                gpu_processes = parse_gpu_processes()

                # Store process data in history buffer
                with lock:
                    for gpu in gpus:
                        gpu_id = gpu['index']
                        if gpu_id not in gpu_process_history:
                            gpu_process_history[gpu_id] = deque(maxlen=MAX_HISTORY)
                        process_snapshot = {
                            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'epoch': time.time(),
                            'processes': gpu_processes.get(gpu_id, [])
                        }
                        gpu_process_history[gpu_id].append(process_snapshot)

                # Broadcast latest data to all connected clients
                socketio.emit('gpu_data', {
                    'gpus': gpus,
                    'processes': gpu_processes,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }, namespace='/')

                # Send history for each GPU (last 10 minutes for real-time graphs)
                with lock:
                    history_data = {}
                    for gpu_id, history in gpu_history.items():
                        # Send last 600 entries for smooth real-time graphs
                        history_data[gpu_id] = list(history)[-600:]

                socketio.emit('gpu_history', history_data, namespace='/')

        except Exception as e:
            print(f"Monitoring loop error: {e}")

        time.sleep(1)  # Sample every second


def login_required(f):
    """Decorator that redirects to login if password is set and user is not authenticated."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if MONITOR_PASSWORD and not session.get('authenticated'):
            return redirect(url_for('login_page', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/')
@login_required
def index():
    """Serve the main dashboard page."""
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """Login page — only shown when MONITOR_PASSWORD is set."""
    # If no password is configured, skip login
    if not MONITOR_PASSWORD:
        return redirect(url_for('index'))

    next_url = request.args.get('next', url_for('index'))

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # Sanitize: reject empty or whitespace-only passwords
        if not password:
            return render_template('login.html', error='Password cannot be empty', next=next_url), 400

        # Timing-safe comparison to prevent timing attacks
        if hmac.compare_digest(password, MONITOR_PASSWORD):
            session['authenticated'] = True
            return redirect(next_url or url_for('index'))
        else:
            return render_template('login.html', error='Incorrect password', next=next_url), 401

    return render_template('login.html', error=None)


@app.route('/logout')
def logout():
    """Clear the authentication session."""
    session.pop('authenticated', None)
    return redirect(url_for('login_page'))


@socketio.on('connect')
def handle_connect():
    """Handle client connection — enforce login if password is set."""
    print(f"Client connected: {request.sid}")

    # If password is set and user is not authenticated, require login
    if MONITOR_PASSWORD and not session.get('authenticated'):
        emit('require_login')
        return

    emit('connected', {'message': 'Connected to GPU monitor'})

    # Send current GPU count info
    with lock:
        emit('gpu_info', {
            'gpu_count': len(gpu_history),
            'history_length': {str(k): len(v) for k, v in gpu_history.items()}
        })


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print(f"Client disconnected: {request.sid}")


@socketio.on('request_full_history')
def handle_full_history(data):
    """Send full history to client on request."""
    with lock:
        history_data = {}
        for gpu_id, history in gpu_history.items():
            history_data[str(gpu_id)] = list(history)
    emit('gpu_full_history', history_data)


@socketio.on('kill_process')
def handle_kill_process(data):
    """Kill a process by PID using kill -9."""
    pid = data.get('pid')
    if not pid:
        emit('kill_result', {'success': False, 'message': 'No PID provided'})
        return

    # Sanitize PID: must be a positive integer
    try:
        pid = str(int(pid))
    except (ValueError, TypeError):
        emit('kill_result', {'success': False, 'message': 'Invalid PID format'})
        return

    # Verify PID is in the current GPU processes list
    with lock:
        pid_found = False
        for gpu_id, process_deque in gpu_process_history.items():
            if process_deque:
                latest = process_deque[-1]
                for proc in latest.get('processes', []):
                    if str(proc['pid']) == str(pid):
                        pid_found = True
                        break
            if pid_found:
                break

    if not pid_found:
        emit('kill_result', {'success': False, 'pid': pid, 'message': f'PID {pid} not found in active GPU processes'})
        return

    try:
        result = subprocess.run(
            ['kill', '-9', str(pid)],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(f"Killed process {pid}")
            emit('kill_result', {'success': True, 'pid': pid, 'message': f'Process {pid} killed'})
        else:
            msg = result.stderr.strip() or 'Unknown error'
            emit('kill_result', {'success': False, 'pid': pid, 'message': msg})
    except Exception as e:
        emit('kill_result', {'success': False, 'pid': pid, 'message': str(e)})


def start_monitoring():
    """Start the monitoring background thread."""
    thread = threading.Thread(target=monitoring_loop, daemon=True)
    thread.start()
    print("GPU monitoring started (1 sample/second)")


if __name__ == '__main__':
    start_monitoring()
    socketio.run(app, host=HOST, port=PORT, debug=False)

# NVIDIA GPU Monitor

> **AI-Assisted Development** — This project was developed with the assistance of AI tools.

Real-time GPU monitoring dashboard that parses `nvidia-smi` every second and displays interactive graphs via WebSocket. Includes active process monitoring per GPU with the ability to kill processes directly from the dashboard.

## Features

- **Real-time monitoring**: Parses `nvidia-smi` every second using structured query mode
- **In-memory buffering**: Configurable buffer size (up to 600 data points by default, ~10 minutes)
- **Interactive graphs**: Real-time Chart.js graphs for all metrics (60-second rolling window)
- **Process monitoring**: Lists active GPU compute processes with PID, VRAM usage, and process name
- **Process management**: Kill GPU processes directly from the dashboard (requires `pid: host`)
- **Authentication**: Optional password-based login (enabled via `MONITOR_PASSWORD` env var)
- **Multi-GPU support**: Automatically detects and monitors all available GPUs
- **WebSocket streaming**: Live data updates via Socket.IO without page refresh
- **Responsive UI**: Dark-themed dashboard optimized for desktop and mobile
- **Dockerized**: Production-ready with Gunicorn + Eventlet

## Quick Start

### Prerequisites

- Docker & Docker Compose installed
- NVIDIA Container Toolkit configured
- NVIDIA GPU with drivers installed

### Run with Docker Compose

```bash
docker compose up --build
```

The dashboard will be available at: **http://localhost:5000**

### Build & Run Manually

```bash
# Build the image
docker build -t nvidiagraph .

# Run the container
docker run -p 5000:5000 --gpus all nvidiagraph
```

### Development (Local)

```bash
# Install dependencies
pip install -r requirements.txt

# Run the Flask development server
python app.py
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Client Browser                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │  GPU Cards   │  │  Real-time  │  │  History    │ │
│  │  (Stats)     │  │  Charts     │  │  (60s)      │ │
│  └─────────────┘  └─────────────┘  └─────────────┘ │
└──────────────────────┬──────────────────────────────┘
                       │ WebSocket (Socket.IO)
┌──────────────────────▼──────────────────────────────┐
│              Flask + Socket.IO Server               │
│  ┌─────────────────────────────────────────────┐    │
│  │  Background Thread (1s interval)            │    │
│  │  ┌──────────────┐    ┌──────────────────┐   │    │
│  │  │ nvidia-smi   │───▶│ Parse & Buffer   │   │    │
│  │  │ (query mode) │    │ (in-memory)      │   │    │
│  │  └──────────────┘    └────────┬─────────┘   │    │
│  └───────────────────────────────┼─────────────┘    │
│                                  │ emit              │
│  ┌───────────────────────────────▼─────────────┐    │
│  │  Gunicorn + Eventlet Workers                │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Host interface the server binds to. |
| `PORT` | `5000` | Port the server listens on. |
| `MONITOR_PASSWORD` | `""` (empty) | Password for dashboard login. Leave empty to disable authentication. Whitespace is automatically trimmed; compared with timing-safe check (`hmac.compare_digest`). |
| `MAX_HISTORY` | `600` | Max data points buffered per GPU (~10 min at 1 sample/s). |
| `SECRET_KEY` | random default | Flask secret key for session management (auto-generated if not set). |

> **Note**: When `MONITOR_PASSWORD` is set to a non-empty value, a login page will be shown before accessing the dashboard. The WebSocket connection will also require authentication.

### Docker Compose Options

Edit `docker-compose.yml` to customize:

```yaml
services:
  nvidiagraph:
    ports:
      - "8080:5000"  # Change host port mapping
    environment:
      - NVIDIA_VISIBLE_DEVICES=all        # Limit to specific GPUs if needed
      - MONITOR_PASSWORD=your_password    # Enable login authentication
      - MAX_HISTORY=300                   # Reduce buffer to ~5 minutes
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1  # Use specific GPU count (default: all)
              capabilities: [gpu]
```

### Key Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_HISTORY` | `600` | Max data points buffered per GPU (~10 min at 1 sample/s) |
| Sample interval | `1 second` | How often `nvidia-smi` is queried |
| Chart window | `600 entries` | Data points sent to client for real-time graphs |

## Troubleshooting

### No GPUs detected

1. Verify NVIDIA drivers are installed on the host:
   ```bash
   nvidia-smi
   ```

2. Verify NVIDIA Container Toolkit is installed:
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.5.1-base-ubuntu22.04 nvidia-smi
   ```

3. Check container logs:
   ```bash
   docker compose logs
   ```

### WebSocket connection issues

- Ensure port 5000 is accessible from the browser
- Check firewall settings
- CORS is set to `*` (all origins allowed) in the app config

### Login page doesn't appear

- Make sure `MONITOR_PASSWORD` is set to a non-empty value in `docker-compose.yml`
- Restart the container after changing environment variables: `docker compose up --build`
- Clear your browser cookies/session if switching between authenticated and non-authenticated modes

### Process killing doesn't work

- The container uses `pid: host` to access host PIDs — ensure this is set in `docker-compose.yml`
- Process killing uses `kill -9` which requires appropriate permissions
- Only PIDs currently active in the GPU process list can be killed (PID verification is performed)

### `nvidia-smi` not found in container

The Dockerfile uses the `nvidia/cuda:12.5.1-runtime-ubuntu22.04` base image which includes `nvidia-smi`. If still not found, uncomment the volume mounts in `docker-compose.yml` to bind-mount `nvidia-smi` from the host.

## Socket.IO Events

### Server → Client

| Event | Description |
|-------|-------------|
| `connected` | Sent on initial connection with a welcome message |
| `gpu_info` | GPU count and history length per GPU |
| `gpu_data` | Latest GPU metrics + active processes (every ~1s) |
| `gpu_history` | Full in-memory history for real-time chart updates |
| `gpu_full_history` | Complete history sent on `request_full_history` |
| `kill_result` | Result of a `kill_process` request (success/failure) |

### Client → Server

| Event | Description |
|-------|-------------|
| `request_full_history` | Request all buffered history data |
| `kill_process` | Kill a GPU process by PID (`{ pid: "1234" }`) |

## Tech Stack

- **Backend**: Python 3, Flask 3.0.0, Flask-SocketIO 5.3.6
- **Server**: Gunicorn 21.2.0 + Eventlet 0.35.1 (WebSocket support)
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Charts**: Chart.js 4.4.1 with chartjs-adapter-date-fns 3.0.0
- **Real-time**: Socket.IO 4.7.5 (WebSocket)
- **Container**: Docker with NVIDIA CUDA 12.5.1 runtime (Ubuntu 22.04)

## License

MIT

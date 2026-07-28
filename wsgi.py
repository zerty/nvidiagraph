"""
WSGI Entry Point for Gunicorn
"""
from app import app, socketio, start_monitoring

# Start monitoring when the server starts
start_monitoring()

# Gunicorn with eventlet/gevent needs the socketio app
application = app

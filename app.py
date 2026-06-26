import csv
import io
import json
import time
import math
import random
from collections import deque
from threading import Thread, Lock
from flask import Flask, render_template, Response, jsonify
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cansat_secret_2026'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

thread = None
thread_lock = Lock()
start_time = None
packet_count = 0
mission_running = True

MAX_HISTORY = 500
telemetry_history = deque(maxlen=MAX_HISTORY)
serial_log = deque(maxlen=200)


def append_serial(message: str) -> None:
    entry = {'time': time.strftime('%H:%M:%S', time.localtime()), 'message': message}
    serial_log.append(entry)
    socketio.emit('serial_log', entry)


def simulate_cansat_telemetry():
    global packet_count, start_time, mission_running
    start_time = time.time()
    mission_running = True

    while mission_running:
        time.sleep(1.0)
        packet_count += 1
        elapsed = time.time() - start_time

        if elapsed < 30:
            state = '3 (ASCENT)'
            actual_alt = 1000 * math.sin((elapsed / 30) * (math.pi / 2)) + random.uniform(-5, 5)
            pressure = 101325 - (actual_alt * 11.3)
            descent_rate = -33.3
        elif elapsed < 60:
            state = '5 (DESCENT)'
            time_in_stage = elapsed - 30
            actual_alt = 1000 - (20 * time_in_stage) + random.uniform(-3, 3)
            pressure = 101325 - (actual_alt * 11.3)
            descent_rate = 20.0
        elif elapsed < 110:
            state = '6 (AEROBREAK_RELEASE)'
            time_in_stage = elapsed - 60
            actual_alt = 400 - (8 * time_in_stage) + random.uniform(-1, 1)
            if actual_alt < 0:
                actual_alt = 0
            pressure = 101325 - (actual_alt * 11.3)
            descent_rate = 2.5
        else:
            state = '7 (IMPACT)'
            actual_alt = 0.0
            pressure = 101325
            descent_rate = 0.0

        if elapsed < 30:
            ideal_alt = 1000.0
        else:
            ideal_alt = max(0.0, 1000.0 - (1000.0 / 80.0) * (elapsed - 30))

        temp = 25.0 - (actual_alt * 0.0065) + random.uniform(-0.2, 0.2)
        voltage = max(6.0, 8.4 - (elapsed * 0.005))

        soil_types = ['Loamy', 'Clay Loam', 'Sandy Loam', 'Silt Loam', 'Clay']
        seed_varieties = ['Wheat', 'Mustard', 'Rice', 'Barley']
        if elapsed < 60:
            soil_type = ''
            seed_dropped = ''
        elif elapsed < 65:
            soil_type = ''
            seed_dropped = 'Deploying...'
        elif elapsed < 110:
            soil_type = ''
            seed_dropped = f'Yes — {random.choice(seed_varieties)}'
        else:
            soil_type = random.choice(soil_types)
            seed_dropped = f'Yes — {random.choice(seed_varieties)}'

        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)
        mission_time_str = f'{hours:02d}:{minutes:02d}:{seconds:02d}'

        telemetry = {
            'team_id': '2026-IN-SPACE-CAN-7USAT-042',
            'timestamp': time.strftime('%H:%M:%S', time.localtime()),
            'mission_time': int(elapsed),
            'mission_time_str': mission_time_str,
            'packet_count': packet_count,
            'actual_altitude': round(max(0.0, actual_alt), 1),
            'ideal_altitude': round(ideal_alt, 1),
            'pressure': round(pressure, 1),
            'temp': round(temp, 1),
            'voltage': round(voltage, 2),
            'descent_rate': round(descent_rate, 1),
            'software_state': state,
            'gps_lat': round(26.8467 + (elapsed * 0.00001), 5),
            'gps_lon': round(80.9462 + (elapsed * 0.000005), 5),
            'gps_alt': round(max(0.0, actual_alt) + random.uniform(-2, 2), 1),
            'gnss_sats': random.randint(6, 12),
            'roll': round(random.uniform(-5, 5) if elapsed > 30 else random.uniform(-45, 45), 1),
            'pitch': round(random.uniform(-5, 5) if elapsed > 30 else random.uniform(-45, 45), 1),
            'yaw': round(elapsed % 360, 1),
            'soil_type': soil_type,
            'seed_dropped': seed_dropped,
        }

        telemetry_history.append(telemetry.copy())
        socketio.emit('telemetry_update', telemetry)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/export/csv')
def export_csv():
    if not telemetry_history:
        return jsonify({'error': 'No telemetry data yet'}), 404

    output = io.StringIO()
    fieldnames = list(telemetry_history[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(telemetry_history)

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=cansat_telemetry.csv'},
    )


@app.route('/api/export/graphs')
def export_graphs():
    if not telemetry_history:
        return jsonify({'error': 'No telemetry data yet'}), 404

    series = {
        'timestamps': [r['timestamp'] for r in telemetry_history],
        'ideal_altitude': [r['ideal_altitude'] for r in telemetry_history],
        'actual_altitude': [r['actual_altitude'] for r in telemetry_history],
        'temperature': [r['temp'] for r in telemetry_history],
        'pressure': [r['pressure'] for r in telemetry_history],
        'voltage': [r['voltage'] for r in telemetry_history],
    }
    payload = json.dumps(series, indent=2)
    return Response(
        payload,
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=cansat_graphs.json'},
    )


@app.route('/api/sync-pc-time', methods=['POST'])
def sync_pc_time():
    now = time.strftime('%H:%M:%S', time.localtime())
    append_serial(f'SYNC: PC time aligned to {now}')
    return jsonify({'status': 'ok', 'pc_time': now})


@socketio.on('connect')
def handle_connect():
    global thread
    with thread_lock:
        if thread is None:
            thread = Thread(target=simulate_cansat_telemetry, daemon=True)
            thread.start()
    for entry in list(serial_log)[-50:]:
        socketio.emit('serial_log', entry)


@socketio.on('user_command')
def handle_user_command(data):
    cmd = (data or {}).get('command', 'UNKNOWN')
    append_serial(f'CMD: {cmd}')


if __name__ == '__main__':
    append_serial('SYSTEM: Ground control online — demo telemetry active')
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

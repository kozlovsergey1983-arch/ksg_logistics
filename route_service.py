"""Road-distance estimates using cached Photon geocoding and OSRM driving routes."""
import hashlib
import json
import math
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import abort, g, jsonify, request

ROUTE_SCHEMA = [
    'CREATE TABLE IF NOT EXISTS route_cache (key TEXT PRIMARY KEY, data TEXT NOT NULL, updated BIGINT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS route_gate (id INTEGER PRIMARY KEY, next_request BIGINT NOT NULL)',
    'INSERT INTO route_gate VALUES (1,0) ON CONFLICT(id) DO NOTHING',
]
CACHE_SECONDS = 7 * 86400


def normalize(value):
    return ' '.join(value.casefold().split())


def fetch_json(url):
    req = Request(url, headers={'User-Agent': 'KSG-CRM/1.0 (road distance lookup)', 'Accept': 'application/json'})
    with urlopen(req, timeout=6) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Слишком большой ответ сервиса маршрутов')
        return json.loads(raw)


def register_routes(app, db):
    def get_cache(key):
        with db() as q:
            row = q('SELECT data FROM route_cache WHERE key=? AND updated>?', (key, int(time.time())-CACHE_SECONDS)).fetchone()
        return json.loads(row['data']) if row else None

    def put_cache(key, value):
        with db() as q:
            q('INSERT INTO route_cache VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data,updated=excluded.updated',
              (key, json.dumps(value, ensure_ascii=False, allow_nan=False), int(time.time())))

    def geocode(place):
        key = 'city:' + hashlib.sha256(normalize(place).encode()).hexdigest()
        cached = get_cache(key)
        if cached:
            return cached
        result = fetch_json('https://photon.komoot.io/api/?' + urlencode({'q': place, 'limit': 1}))
        features = result.get('features') or []
        if not features:
            raise ValueError(f'Не найден город «{place}». Укажи город и регион')
        first = features[0]
        coords = first['geometry']['coordinates']
        if not isinstance(coords, list) or len(coords) < 2:
            raise ValueError('Сервис не вернул координаты города')
        lon, lat = float(coords[0]), float(coords[1])
        if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError('Сервис вернул неверные координаты')
        properties = first.get('properties', {})
        label = ', '.join(str(properties[k]) for k in ('name', 'state', 'country') if properties.get(k))
        value = dict(lon=lon, lat=lat, label=label or place)
        put_cache(key, value)
        return value

    @app.post('/api/routes/estimate')
    def route_estimate():
        payload = request.get_json()
        origin, destination = payload.get('from'), payload.get('to')
        for value in (origin, destination):
            if not isinstance(value, str) or not 2 <= len(value.strip()) <= 200:
                abort(400, 'Укажи города отправления и назначения: от 2 до 200 символов')
        key = 'route:' + hashlib.sha256((normalize(origin)+'\0'+normalize(destination)).encode()).hexdigest()
        cached = get_cache(key)
        if cached:
            return jsonify(cached)
        # A shared database gate also limits requests across multiple Gunicorn workers.
        now = int(time.time())
        with db() as q:
            allowed = q('UPDATE route_gate SET next_request=? WHERE id=1 AND next_request<=?', (now+2, now))
            if not allowed.rowcount:
                abort(429, 'Расчёт маршрутов занят. Повтори через несколько секунд')
        try:
            if app.config.get('ROUTE_LOOKUP'):
                result = app.config['ROUTE_LOOKUP'](origin.strip(), destination.strip())
            else:
                start, finish = geocode(origin.strip()), geocode(destination.strip())
                coords = f"{start['lon']},{start['lat']};{finish['lon']},{finish['lat']}"
                response = fetch_json('https://router.project-osrm.org/route/v1/driving/' + coords + '?overview=false&steps=false')
                routes = response.get('routes') or []
                if response.get('code') != 'Ok' or not routes:
                    raise ValueError('Дорожный маршрут не найден. Уточни города и регионы')
                meters = float(routes[0]['distance'])
                if not math.isfinite(meters) or meters < 0:
                    raise ValueError('Сервис вернул неверное расстояние')
                result = dict(distanceKm=round(meters/1000, 1), start=start, finish=finish,
                              source='OSRM', calculatedAt=int(time.time()))
            put_cache(key, result)
            return jsonify(result)
        except ValueError as exc:
            abort(422, str(exc))
        except Exception:
            app.logger.warning('Road distance provider unavailable', exc_info=True)
            abort(502, 'Сервис маршрутов временно недоступен. Расчёт можно повторить позже')

"""Settlement suggestions and road distances for explicitly selected coordinates."""
import hashlib
import json
import math
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import abort, jsonify, request
from itsdangerous import BadSignature, URLSafeSerializer

ROUTE_SCHEMA = [
    'CREATE TABLE IF NOT EXISTS route_cache (key TEXT PRIMARY KEY, data TEXT NOT NULL, updated BIGINT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS route_gate (id INTEGER PRIMARY KEY, next_request BIGINT NOT NULL)',
    'INSERT INTO route_gate VALUES (1,0) ON CONFLICT(id) DO NOTHING',
    'INSERT INTO route_gate VALUES (2,0) ON CONFLICT(id) DO NOTHING',
]
CACHE_SECONDS = 7 * 86400
SETTLEMENTS = {'city', 'town', 'village', 'hamlet'}


def normalize(value):
    return ' '.join(value.casefold().split())


def fetch_json(url):
    req = Request(url, headers={'User-Agent': 'KSG-CRM/1.0 (settlement lookup)', 'Accept': 'application/json'})
    with urlopen(req, timeout=6) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Слишком большой ответ сервиса маршрутов')
        return json.loads(raw)


def valid_point(point):
    if not isinstance(point, dict) or not isinstance(point.get('label'), str) or not 1 <= len(point['label']) <= 500:
        raise ValueError('Неверный населённый пункт. Выбери его заново из подсказок')
    for key, limit in (('lon', 180), ('lat', 90)):
        number = point.get(key)
        if type(number) not in (int, float) or not math.isfinite(number) or not -limit <= number <= limit:
            raise ValueError('Неверные координаты населённого пункта')
    return {k: point[k] for k in ('lon', 'lat', 'label')}


def location_token(app, point):
    return URLSafeSerializer(app.config['SECRET_KEY'], salt='ksg-settlement-v2').dumps(valid_point(point))


def selected_point(app, token, label):
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise ValueError('Выбери оба города из подсказок: нужны подтверждённые координаты')
    try:
        point = valid_point(URLSafeSerializer(app.config['SECRET_KEY'], salt='ksg-settlement-v2').loads(token))
    except (BadSignature, ValueError, TypeError):
        raise ValueError('Выбери населённый пункт заново из подсказок') from None
    if not isinstance(label, str) or normalize(point['label']) != normalize(label):
        raise ValueError('Название изменилось. Выбери населённый пункт заново из подсказок')
    return point


def validate_item_locations(app, item):
    for name in ('from', 'to'):
        token = item.get(name + 'Location')
        if token:
            selected_point(app, token, item[name])


def settlement_options(features):
    options, seen = [], set()
    rank = {'city': 0, 'town': 1, 'village': 2, 'hamlet': 3}
    features = [f for f in features if isinstance(f, dict) and isinstance(f.get('properties'), dict)]
    features.sort(key=lambda f: rank.get(f['properties'].get('osm_value'), 9))
    for feature in features:
        try:
            props = feature['properties']
            if props.get('osm_key') != 'place' or props.get('osm_value') not in SETTLEMENTS or not props.get('name'):
                continue
            lon, lat = feature['geometry']['coordinates'][:2]
            pieces = []
            for k in ('name', 'district', 'county', 'state', 'country'):
                value = str(props.get(k) or '').strip()
                if value and value.casefold() not in {p.casefold() for p in pieces}:
                    pieces.append(value)
            point = valid_point(dict(lon=lon, lat=lat, label=', '.join(pieces)))
            key = (round(lon, 5), round(lat, 5), normalize(props['name']))
            if key not in seen:
                seen.add(key)
                options.append(point)
            if len(options) == 8:
                break
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    return options


def register_routes(app, db):
    def get_cache(key):
        with db() as q:
            row = q('SELECT data FROM route_cache WHERE key=? AND updated>?', (key, int(time.time())-CACHE_SECONDS)).fetchone()
        return json.loads(row['data']) if row else None

    def put_cache(key, value):
        with db() as q:
            q('INSERT INTO route_cache VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data,updated=excluded.updated',
              (key, json.dumps(value, ensure_ascii=False, allow_nan=False), int(time.time())))

    def gate(identifier, seconds):
        now = int(time.time())
        with db() as q:
            allowed = q('UPDATE route_gate SET next_request=? WHERE id=? AND next_request<=?', (now+seconds, identifier, now))
            if not allowed.rowcount:
                abort(429, 'Поиск занят. Подожди несколько секунд')

    @app.get('/api/places')
    def place_suggestions():
        query = request.args.get('query', '').strip()
        if not 2 <= len(query) <= 200:
            abort(400, 'Введи от 2 до 200 символов названия города')
        key = 'settlements:v2:' + hashlib.sha256(normalize(query).encode()).hexdigest()
        options = get_cache(key)
        if options is None:
            gate(2, 1)
            try:
                lookup = app.config.get('PLACE_LOOKUP')
                response = lookup(query) if lookup else fetch_json('https://photon.komoot.io/api/?' + urlencode({'q': query, 'limit': 20}))
                options = settlement_options(response.get('features') or [])
                put_cache(key, options)
            except Exception:
                app.logger.warning('Settlement provider unavailable', exc_info=True)
                abort(502, 'Подсказки временно недоступны. Повтори поиск позже')
        return jsonify(suggestions=[dict(point, token=location_token(app, point)) for point in options], attribution='© OpenStreetMap contributors / Photon')

    @app.post('/api/routes/estimate')
    def route_estimate():
        payload = request.get_json()
        try:
            start = selected_point(app, payload.get('fromLocation'), payload.get('from'))
            finish = selected_point(app, payload.get('toLocation'), payload.get('to'))
        except ValueError as exc:
            abort(400, str(exc))
        coords = f"{start['lon']},{start['lat']};{finish['lon']},{finish['lat']}"
        key = 'route:coordinates:v2:' + hashlib.sha256(coords.encode()).hexdigest()
        cached = get_cache(key)
        if cached:
            return jsonify(dict(cached, start=start, finish=finish))
        gate(1, 2)
        try:
            if app.config.get('ROUTE_LOOKUP'):
                result = app.config['ROUTE_LOOKUP'](start['label'], finish['label'])
                result = dict(result, start=start, finish=finish)
            else:
                response = fetch_json('https://router.project-osrm.org/route/v1/driving/' + coords + '?overview=false&steps=false')
                routes = response.get('routes') or []
                if response.get('code') != 'Ok' or not routes:
                    raise ValueError('Дорожный маршрут между выбранными городами не найден')
                meters = float(routes[0]['distance'])
                if not math.isfinite(meters) or meters < 0:
                    raise ValueError('Сервис вернул неверное расстояние')
                result = dict(distanceKm=round(meters/1000, 1), start=start, finish=finish, source='OSRM', calculatedAt=int(time.time()))
            put_cache(key, result)
            return jsonify(result)
        except ValueError as exc:
            abort(422, str(exc))
        except Exception:
            app.logger.warning('Road distance provider unavailable', exc_info=True)
            abort(502, 'Сервис маршрутов временно недоступен. Расчёт можно повторить позже')

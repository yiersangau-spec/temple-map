"""
temple_web/app.py — 宮廟足跡地圖 Web App
- 公開地圖（任何人可看）
- 密碼保護的編輯功能（新增 / 刪除 / 加次數）
- 資料存在 GitHub repo 的 temples.json
"""

import os, json, re
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

EDIT_PASSWORD = os.environ.get('EDIT_PASSWORD', 'changeme')
GITHUB_TOKEN  = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO   = os.environ.get('GITHUB_REPO', '')   # e.g. "username/temple-map"
GITHUB_FILE   = 'temples.json'


# ── GitHub 資料存取 ────────────────────────────────────────────────────────────

def gh_headers():
    return {'Authorization': f'token {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json'}


def load_temples():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return _load_local()
    url = f'https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}'
    try:
        r = requests.get(url, headers=gh_headers(), timeout=10)
        if r.status_code == 200:
            import base64
            content = base64.b64decode(r.json()['content']).decode('utf-8')
            return json.loads(content), r.json()['sha']
        elif r.status_code == 404:
            return [], None
    except Exception as e:
        print(f'GitHub read error: {e}')
    return _load_local(), None


def save_temples(data, sha=None):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return _save_local(data)
    import base64
    content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
    url = f'https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}'
    payload = {
        'message': f'update temples ({len(data)} entries)',
        'content': content,
    }
    if sha:
        payload['sha'] = sha
    try:
        r = requests.put(url, headers=gh_headers(), json=payload, timeout=10)
        return r.status_code in (200, 201)
    except Exception as e:
        print(f'GitHub write error: {e}')
        return False


def _load_local():
    path = os.path.join(os.path.dirname(__file__), 'temples.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return []


def _save_local(data):
    path = os.path.join(os.path.dirname(__file__), 'temples.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True


# ── Google Maps 座標擷取 ────────────────────────────────────────────────────────

def extract_coords(url: str):
    """從 Google Maps URL 擷取座標，回傳 (lat, lng) 或 (None, None)"""
    # 追蹤短網址（用 GET 才能正確追蹤 maps.app.goo.gl）
    if 'goo.gl' in url or 'maps.app.goo.gl' in url:
        try:
            r = requests.get(url, allow_redirects=True, timeout=10)
            url = r.url
        except Exception:
            pass

    # @lat,lng,zoom
    m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if m:
        return float(m.group(1)), float(m.group(2))

    # ?q=lat,lng
    m = re.search(r'[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if m:
        return float(m.group(1)), float(m.group(2))

    # /place/name/@lat,lng
    m = re.search(r'place/[^/]+/@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if m:
        return float(m.group(1)), float(m.group(2))

    return None, None


def geocode_name(name: str):
    """用 Nominatim 地名查座標（OpenStreetMap，免費）"""
    try:
        r = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': name + ' 台灣', 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'TempleMap/1.0'},
            timeout=10
        )
        results = r.json()
        if results:
            return float(results[0]['lat']), float(results[0]['lon'])
    except Exception:
        pass
    return None, None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/temples')
def api_temples():
    data, _ = load_temples() if GITHUB_TOKEN else (load_temples(), None)
    if isinstance(data, tuple):
        data = data[0]
    return jsonify(data)


@app.route('/api/add', methods=['POST'])
def api_add():
    body = request.json or {}
    name     = body.get('name', '').strip()
    gmaps    = body.get('gmaps', '').strip()
    notes    = body.get('notes', '').strip()
    visits   = int(body.get('visits', 1))

    if not name:
        return jsonify({'error': '請填入廟名'}), 400

    # 座標來源：Google Maps URL > 地名搜尋
    lat, lng = None, None
    if gmaps:
        lat, lng = extract_coords(gmaps)
    if lat is None:
        lat, lng = geocode_name(name)
    if lat is None:
        return jsonify({'error': '找不到座標，請提供 Google Maps 連結'}), 400

    result = load_temples()
    data, sha = result if isinstance(result, tuple) else (result, None)

    for t in data:
        if t['name'] == name:
            return jsonify({'error': f'「{name}」已存在'}), 409

    data.append({'name': name, 'lat': lat, 'lng': lng, 'visits': visits, 'notes': notes})
    save_temples(data, sha)
    return jsonify({'ok': True, 'lat': lat, 'lng': lng})


@app.route('/api/visit', methods=['POST'])
def api_visit():
    body = request.json or {}
    name = body.get('name', '').strip()
    result = load_temples()
    data, sha = result if isinstance(result, tuple) else (result, None)

    for t in data:
        if t['name'] == name:
            t['visits'] = t.get('visits', 1) + 1
            save_temples(data, sha)
            return jsonify({'ok': True, 'visits': t['visits']})

    return jsonify({'error': '找不到此廟'}), 404


@app.route('/api/delete', methods=['POST'])
def api_delete():
    body = request.json or {}
    name = body.get('name', '').strip()
    result = load_temples()
    data, sha = result if isinstance(result, tuple) else (result, None)

    new_data = [t for t in data if t['name'] != name]
    if len(new_data) == len(data):
        return jsonify({'error': '找不到此廟'}), 404

    save_temples(new_data, sha)
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(debug=True, port=5050)

"""Apple Japan refurbished stock monitor. Never performs a purchase."""
import argparse
import copy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = 'https://www.apple.com'
LIST = BASE + '/jp/shop/refurbished/iphone'
AGENT = 'RefurbishedStockMonitor/1.0'


class MonitorError(Exception):
    pass


def product_url(url):
    p = urlsplit(urljoin(BASE, url))
    if p.scheme != 'https' or p.netloc != 'www.apple.com' or not p.path.startswith('/jp/shop/product/'):
        raise MonitorError('Apple日本の商品URLではありません')
    return p._replace(query='', fragment='').geturl()


def targets(name, capacity, price):
    # A full model match excludes Plus, Air, Pro and unknown suffixes.
    model = re.search(r'iPhone\s*(\d+)(e)?(?=\s|[\d]|$)', name, re.I)
    if not model or int(model[1]) < 15 or re.search(r'\b(Pro|Max|Plus|Air|mini)\b', name, re.I):
        return []
    tail = name[model.end():].strip()
    if tail and re.match(r'[A-Za-z]', tail):
        return []
    if price > 100000 or price <= 0:
        return []
    return (['本人用'] if capacity >= 256 else []) + (['母親用'] if capacity >= 128 else [])


class Apple:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = AGENT
        self.last = 0
        self.robots = RobotFileParser()
        self.robots.parse(self.get(BASE + '/robots.txt', check=False).splitlines())

    def get(self, url, check=True):
        if check and not self.robots.can_fetch(AGENT, url):
            raise MonitorError('robots.txtがアクセスを許可していません: ' + url)
        delay = max(2, (self.robots.crawl_delay(AGENT) or 0)) if check else 2
        time.sleep(max(0, delay - (time.monotonic() - self.last)))
        try:
            r = self.session.get(url, timeout=30, allow_redirects=False)
            self.last = time.monotonic()
            if r.status_code != 200:
                raise MonitorError(f'Apple HTTP {r.status_code}: {url}（再試行・回避しません）')
            if 'text/' not in r.headers.get('Content-Type', ''):
                raise MonitorError('想定外のContent-Type: ' + url)
            return r.text
        except requests.RequestException as exc:
            raise MonitorError('Appleへの接続に失敗: ' + type(exc).__name__) from exc

    def scan(self):
        html = self.get(LIST)
        soup = BeautifulSoup(html, 'html.parser')
        if not soup.title or 'iPhone' not in soup.get_text():
            raise MonitorError('商品一覧を確認できません（ブロック・形式変更の可能性）')
        urls = sorted({product_url(a['href']) for a in soup.select('a[href]')
                       if '/jp/shop/product/' in a['href'] and 'iphone' in (a.get_text() + a['href']).lower()})
        if not urls:
            # Empty lists require a positive, explicit statement; never infer from missing markup.
            if not re.search(r'(現在.*(在庫がありません|取り扱っていません)|該当する商品はありません)', soup.get_text(' ', strip=True)):
                raise MonitorError('商品リンクも明示的な在庫なし表示も確認できません')
        if len(urls) > 100:
            raise MonitorError('商品リンクが想定上限を超えました')
        found = {}
        for url in urls:
            item = parse_detail(self.get(url), url)
            if item and item['targets']:
                found[url] = item
        return found


def json_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from json_objects(child)


def parse_detail(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    products = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            products.extend(o for o in json_objects(json.loads(script.string or script.get_text()))
                            if 'Product' in ([o.get('@type')] if isinstance(o.get('@type'), str) else o.get('@type', [])))
        except (ValueError, TypeError):
            raise MonitorError('商品構造化データを解析できません: ' + url)
    if len(products) != 1:
        raise MonitorError('商品を一意に確認できません: ' + url)
    p = products[0]
    name = p.get('name', '')
    cap = re.search(r'(\d+)\s*(GB|TB)', name, re.I)
    if not cap:
        raise MonitorError('容量を確認できません: ' + url)
    capacity = int(cap[1]) * (1024 if cap[2].upper() == 'TB' else 1)
    offer = p.get('offers')
    if isinstance(offer, list) and len(offer) == 1:
        offer = offer[0]
    if not isinstance(offer, dict) or offer.get('priceCurrency') != 'JPY':
        raise MonitorError('円建て価格を確認できません: ' + url)
    try:
        price = Decimal(str(offer['price']))
        if not price.is_finite() or price != int(price) or price <= 0:
            raise ValueError()
    except (KeyError, InvalidOperation, ValueError, OverflowError):
        raise MonitorError('価格を確認できません: ' + url)
    text = soup.get_text(' ', strip=True)
    if not re.search(r'税込|消費税込|税金を含', text):
        raise MonitorError('税込表示を確認できません: ' + url)
    availability = offer.get('availability', '').rsplit('/', 1)[-1]
    if availability in ('OutOfStock', 'SoldOut', 'Discontinued'):
        return None
    if availability != 'InStock':
        raise MonitorError('購入可能状態が不明: ' + url)
    buttons = soup.select('button, input[type="submit"]')
    enabled = any(not b.has_attr('disabled') and b.get('aria-disabled') != 'true'
                  and re.search(r'バッグに追加|カートに追加', b.get_text(' ', strip=True) + b.get('value', ''))
                  for b in buttons)
    if not enabled:
        raise MonitorError('有効な購入ボタンを確認できません: ' + url)
    if offer.get('url') and product_url(offer['url']) != url:
        raise MonitorError('在庫情報の商品URLが一致しません: ' + url)
    return dict(name=name, capacity=capacity, price=int(price), url=url,
                targets=targets(name, capacity, price))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def evolve(old, current, now):
    state = copy.deepcopy(old) if old else {'version': 1, 'stock': {}, 'pending': {}}
    if state.get('version') != 1 or not isinstance(state.get('stock'), dict) or not isinstance(state.get('pending'), dict):
        raise MonitorError('状態ファイルの形式が不正です。自動初期化しません')
    pending = state['pending']
    # Cancel undelivered alerts once the product is no longer confirmed available.
    for key in list(pending):
        if pending[key]['item']['url'] not in current:
            del pending[key]
    if old is not None:
        for url, item in current.items():
            if url not in state['stock']:
                event = str(uuid.uuid4())
                pending[event] = {'item': item, 'detected_at': now, 'retry_key': str(uuid.uuid4())}
    state.update(stock=current, checked_at=now)
    return state


def message(event):
    i = event['item']
    return (f"Apple認定整備済・新規入荷／再入荷（{ '・'.join(i['targets']) }）\n"
            f"{i['name']}\n容量: {i['capacity']}GB\n税込価格: {i['price']:,}円\n"
            f"{i['url']}\n検知日時: {event['detected_at']}\n購入時に在庫・価格をご確認ください。")


def deliver(state, path):
    token, recipient = os.getenv('LINE_CHANNEL_ACCESS_TOKEN'), os.getenv('LINE_TO')
    if not token and not recipient:
        print('LINE未設定: 通知は保留します。認証情報なしで監視できます。')
        return
    if not token or not recipient:
        raise MonitorError('LINE Secretsは2項目とも設定してください')
    for key, event in list(state['pending'].items()):
        detected = datetime.fromisoformat(event['detected_at'])
        if (datetime.now(ZoneInfo('Asia/Tokyo')) - detected).total_seconds() > 23 * 3600:
            raise MonitorError('保留通知が23時間を超えました。LINE重複防止期限のため自動再送を停止。READMEを確認してください')
        try:
            r = requests.post('https://api.line.me/v2/bot/message/push', timeout=30,
                              headers={'Authorization': 'Bearer ' + token, 'X-Line-Retry-Key': event['retry_key']},
                              json={'to': recipient, 'messages': [{'type': 'text', 'text': message(event)}]})
        except requests.RequestException as exc:
            raise MonitorError('LINE接続失敗（次回同じretry keyで再送）') from exc
        if r.status_code != 200 and not (r.status_code == 409 and r.headers.get('x-line-accepted-request-id')):
            raise MonitorError(f'LINE送信失敗 HTTP {r.status_code}（認証・上限・送信先を確認）')
        del state['pending'][key]
        atomic_json(path, state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', default='state.json')
    ap.add_argument('--dry-run', action='store_true', help='LINE送信も状態変更もしない')
    args = ap.parse_args()
    try:
        path = Path(args.state)
        old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if path.exists() and not isinstance(old, dict):
            raise MonitorError('状態ファイルが不正です')
        current = Apple().scan()
        now = datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds')
        state = evolve(old, current, now)
        report = {'checked_at': now, 'available': list(current.values()), 'pending': len(state['pending']), 'baseline': old is None}
        atomic_json('report.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not args.dry_run:
            # Durable outbox before network side effects; workflow must persist even on failure.
            atomic_json(path, state)
            deliver(state, path)
    except (MonitorError, ValueError, OSError) as exc:
        print('監視失敗: ' + str(exc), file=sys.stderr)
        atomic_json('report.json', {'error': str(exc)})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

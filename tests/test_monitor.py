import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from monitor import targets, parse_detail, evolve, MonitorError, deliver, product_url

URL = 'https://www.apple.com/jp/shop/product/EXAMPLE/iphone'
ITEM = dict(name='iPhone 15 256GB 整備済製品', capacity=256, price=99800, url=URL, targets=['本人用', '母親用'])


def detail(availability='InStock', button='<button>バッグに追加</button>', **changes):
    p = {'@type': 'Product', 'name': ITEM['name'], 'offers': {'price': '99800', 'priceCurrency': 'JPY', 'availability': 'https://schema.org/' + availability, 'url': URL}}
    p['offers'].update(changes)
    return '<script type="application/ld+json">' + json.dumps(p) + '</script><p>税込</p>' + button


class Tests(unittest.TestCase):
    def test_filters(self):
        self.assertEqual(targets('iPhone 16e', 256, 100000), ['本人用', '母親用'])
        self.assertEqual(targets('iPhone 15', 128, 99000), ['母親用'])
        for name in ['iPhone 14', 'iPhone 16 Pro', 'iPhone 15 Pro Max', 'iPhone 15 Plus', 'iPhone 17 Air', 'iPhone SE', 'iPhone 16 Ultra']:
            self.assertEqual(targets(name, 512, 90000), [])
        self.assertEqual(targets('iPhone 15', 256, 100001), [])
        self.assertEqual(targets('iPhone 15', 64, 90000), [])

    def test_confirmed_detail(self):
        self.assertEqual(parse_detail(detail(), URL), ITEM)
        self.assertIsNone(parse_detail(detail('OutOfStock'), URL))

    def test_uncertain_is_error(self):
        for html in ['<html>Access Denied</html>', detail('PreOrder'), detail(button='<button disabled>バッグに追加</button>'), detail(priceCurrency='USD'), detail(price='NaN'), detail().replace('税込', ''), detail(url='https://evil.example/jp/shop/product/x')]:
            with self.subTest(html=html), self.assertRaises(MonitorError):
                parse_detail(html, URL)

    def test_transitions_and_outbox(self):
        initial = evolve(None, {URL: ITEM}, 'first')
        self.assertFalse(initial['pending'])
        self.assertFalse(evolve(initial, {URL: ITEM}, 'same')['pending'])
        empty = evolve(initial, {}, 'sold')
        restock = evolve(empty, {URL: ITEM}, 'back')
        self.assertEqual(len(restock['pending']), 1)
        again = evolve(restock, {URL: ITEM}, 'same')
        self.assertEqual(restock['pending'], again['pending'])
        self.assertFalse(evolve(restock, {}, 'gone')['pending'])
        self.assertEqual(len(evolve(evolve(None, {}, 'first'), {URL: ITEM}, 'new')['pending']), 1)

    @patch.dict('os.environ', {'LINE_CHANNEL_ACCESS_TOKEN': 'test', 'LINE_TO': 'test'})
    @patch('monitor.requests.post')
    def test_failed_delivery_keeps_retry_key(self, post):
        state = evolve(evolve(None, {}, 'first'), {URL: ITEM}, datetime.now(ZoneInfo('Asia/Tokyo')).isoformat())
        key = next(iter(state['pending'].values()))['retry_key']
        post.return_value = Mock(status_code=500)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'state.json'
            with self.assertRaises(MonitorError):
                deliver(state, path)
            self.assertEqual(next(iter(state['pending'].values()))['retry_key'], key)
            post.return_value = Mock(status_code=409, headers={'x-line-accepted-request-id': 'accepted'})
            deliver(state, path)
            self.assertFalse(state['pending'])
            self.assertEqual(post.call_args.kwargs['headers']['X-Line-Retry-Key'], key)

    def test_url_guard(self):
        with self.assertRaises(MonitorError):
            product_url('https://evil.example/jp/shop/product/x')

    def test_corrupt_state(self):
        with self.assertRaises(MonitorError):
            evolve({'version': 7}, {}, 'now')


if __name__ == '__main__':
    unittest.main()

import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from collector_transport import PoliteFetch


class TransportTests(unittest.TestCase):
    def test_pause_timeout_single_attempt(self):
        get = Mock(return_value=b"xml")
        with patch('collector_transport.time.monotonic', side_effect=[10, 12, 17]), patch('collector_transport.time.sleep') as sleep:
            fetch = PoliteFetch(get)
            fetch('https://example.test/one', attempts=3)
            fetch('https://example.test/two')
        sleep.assert_called_once_with(3)
        self.assertTrue(all(c.kwargs == dict(timeout=30, attempts=1) for c in get.call_args_list))

    def test_rate_limit_and_timeout_stop_other_seeds_without_retry(self):
        for error in [HTTPError('https://example.test',429,'Limit',{'Retry-After':'120'},None), TimeoutError('timeout')]:
            get = Mock(side_effect=error)
            fetch = PoliteFetch(get)
            for url in ['https://example.test/one','https://example.test/two']:
                with self.assertRaises(Exception): fetch(url)
            self.assertEqual(get.call_count,1)
            self.assertEqual(fetch.count,1)
            self.assertIsNotNone(fetch.stopped)

    def test_parallel_catalog_workers_share_one_request_queue(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        active, peak = 0, 0
        def get(url, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            threading.Event().wait(.01)
            active -= 1
            return b"xml"
        fetch = PoliteFetch(get)
        with patch('collector_transport.time.sleep'):
            with ThreadPoolExecutor(max_workers=3) as pool:
                self.assertEqual(list(pool.map(fetch, ['one','two','three'])), [b'xml']*3)
        self.assertEqual(peak,1)

    def test_catalog_env_switch_uses_shared_limiter(self):
        import sync
        with patch.dict('os.environ', {'SEDO_SFR_POLITE':'1'}), patch.object(sync,'_polite_fetch',return_value=b'ok') as limiter:
            self.assertEqual(sync.get('https://example.test',attempts=3),b'ok')
            limiter.assert_called_once_with('https://example.test')

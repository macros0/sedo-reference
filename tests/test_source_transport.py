import io
import unittest
import zipfile
from unittest.mock import patch
from urllib.request import Request

import sync


class SourceTransportTests(unittest.TestCase):
    def test_redirect_checks_each_destination_before_request(self):
        seed = 'https://ecp.sfr.gov.ru/sedo/soap/SedoGateway?wsdl'
        handler = sync.SourceRedirect(seed)
        allowed = 'https://ecp.sfr.gov.ru/sedo/soap/types.xsd'
        self.assertEqual(handler.redirect_request(Request(seed), None, 302, '', {}, allowed).full_url, allowed)
        response = io.BytesIO(b"redirect")
        handler.redirect_request(Request(seed), response, 302, '', {}, allowed)
        self.assertEqual(response.read(), b"")
        explicit_port = 'https://ecp.sfr.gov.ru:443/sedo/soap/types.xsd'
        self.assertEqual(handler.redirect_request(Request(seed), None, 302, '', {}, explicit_port).full_url, explicit_port)
        for url in ['http://ecp.sfr.gov.ru/sedo/soap/types.xsd',
                    'https://ecp-test.sfr.gov.ru/sedo/soap/types.xsd',
                    'https://ecp.sfr.gov.ru:444/sedo/soap/types.xsd',
                    'https://ecp.sfr.gov.ru/other/types.xsd',
                    'https://ecp.sfr.gov.ru/sedo/soap/%2e%2e/secret.xsd']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(Request(seed), None, 302, '', {}, url)

    def test_catalog_policy_rejects_scheme_port_and_escape(self):
        seed = sync.SITES['prod']
        sync.source_url('https://lk.sfr.gov.ru/specs_sedo/next.zip', seed)
        for url in ['http://lk.sfr.gov.ru/specs_sedo/next.zip',
                    'https://lk.sfr.gov.ru:8443/specs_sedo/next.zip',
                    'https://lk.sfr.gov.ru/specs_sedo/%2e%2e/private.zip',
                    'https://user@lk.sfr.gov.ru/specs_sedo/next.zip']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                sync.source_url(url, seed)

    def test_archive_budget_checked_before_reading_entries(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('one.xsd', b'abc')
            archive.writestr('two.xsd', b'def')
        with patch.object(sync, 'MAX_ARCHIVE_BYTES', 5), patch.object(zipfile.ZipFile, 'read') as read:
            with self.assertRaisesRegex(ValueError, 'archive'):
                sync.inspect_file(output.getvalue(), 'zip')
            read.assert_not_called()
        with patch.object(sync, 'MAX_ARCHIVE_FILES', 1), self.assertRaises(ValueError):
            sync.inspect_file(output.getvalue(), 'zip')

    def test_duplicate_names_cannot_repeat_the_last_large_entry(self):
        import warnings
        output = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(output, 'w') as archive:
            warnings.simplefilter('ignore', UserWarning)
            archive.writestr('same.xsd', b'')
            archive.writestr('same.xsd', b'')
            archive.writestr('same.xsd', b'<schema/>')
        with patch.object(zipfile.ZipFile, 'read', return_value=b'<schema/>') as read:
            with self.assertRaisesRegex(ValueError, '[Dd]uplicate'):
                sync.inspect_file(output.getvalue(), 'zip')
            read.assert_not_called()

    def test_url_kind_is_independent_of_windows_unc_paths(self):
        self.assertEqual(sync.source_kind('https://lk.fss.ru//mchd.html'), 'html')

    def test_rejected_initial_source_cannot_open_connection(self):
        with patch.object(sync, 'build_opener') as opener:
            with self.assertRaises(ValueError):
                sync._get('https://elsewhere.test/specs_sedo/schema.zip')
            opener.assert_not_called()
